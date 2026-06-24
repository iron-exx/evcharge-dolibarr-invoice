<?php
/**
 * api_wallboxbilling.class.php — Wallbox Billing REST API v2
 *
 * POST /api/index.php/wallboxbilling/session
 *
 * Empfängt eine Lade-Session vom HA-Addon und schreibt sie direkt als
 * Zeile in die Spesenabrechnung (llx_expensereport_det) des Benutzers.
 * Kein Zwischenspeicher — keine llx_wallbox_sessions.
 *
 * Ablauf:
 *   1. Pflichtfelder prüfen (rfid_hash, wallbox_id, start_time, end_time, kwh)
 *   2. Benutzer + Preis aus llx_wallbox_rfid auflösen
 *   3. Offene Spesenabrechnung (draft) für diesen Benutzer+Monat suchen oder neu anlegen
 *   4. Zeile in llx_expensereport_det einfügen
 *   5. Summen in llx_expensereport aktualisieren
 *   6. {"success": true, "expensereport_id": X, "line_id": Y} zurückgeben
 */

require_once DOL_DOCUMENT_ROOT.'/api/class/api.class.php';

class WallboxbillingApi extends DolibarrApi
{
    public static $FIELDS_MANDATORY = array('rfid_hash', 'wallbox_id', 'start_time', 'end_time', 'kwh');

    public $db;

    public function __construct($db)
    {
        parent::__construct($db);
        $this->db = $db;
    }

    /**
     * Lade-Session empfangen und direkt in Spesenabrechnung eintragen
     *
     * @param object|null $request_data JSON-Body
     * @return array {"success": true, "expensereport_id": int, "line_id": int}
     */
    public function postSession($request_data = null)
    {
        if ($request_data === null) {
            $request_data = (object) $_POST;
        }

        $this->_checkMandatoryParameters($request_data, self::$FIELDS_MANDATORY);

        $rfid_hash  = (string) $request_data->rfid_hash;
        $wallbox_id = (string) $request_data->wallbox_id;
        $kwh        = (float)  $request_data->kwh;

        // Zeitstempel parsen
        try {
            $start_ts = (new DateTime((string) $request_data->start_time))->getTimestamp();
            $end_ts   = (new DateTime((string) $request_data->end_time))->getTimestamp();
        } catch (Exception $e) {
            throw new RestException(400, 'Invalid start_time or end_time (ISO 8601 required)');
        }

        // Validierungen
        if (!preg_match('/^[a-f0-9]{64}$/i', $rfid_hash)) {
            throw new RestException(400, 'Invalid rfid_hash (64-char hex SHA-256 required)');
        }
        if (strlen($wallbox_id) > 50) {
            throw new RestException(400, 'wallbox_id too long (max 50 chars)');
        }
        if ($end_ts <= $start_ts) {
            throw new RestException(400, 'end_time must be after start_time');
        }
        if ($kwh < 0 || !is_finite($kwh)) {
            throw new RestException(400, 'Invalid kwh value');
        }

        // Benutzer + Preis aus RFID-Zuordnung ermitteln
        $res = $this->db->query(
            "SELECT fk_user, price_kwh FROM ".MAIN_DB_PREFIX."wallbox_rfid"
           ." WHERE rfid_hash='".$this->db->escape($rfid_hash)."' LIMIT 1"
        );
        if (!$res || $this->db->num_rows($res) == 0) {
            throw new RestException(404, 'RFID not registered in Dolibarr');
        }
        $row       = $this->db->fetch_object($res);
        $fk_user   = (int)   $row->fk_user;
        $price_kwh = (float) $row->price_kwh ?: (float) getDolGlobalString('WALLBOXBILLING_DEFAULT_PRICE', 0.30);
        $total_ht  = round($kwh * $price_kwh, 2);

        // Offene Spesenabrechnung für Benutzer+Monat suchen oder neu anlegen
        $year  = (int) date('Y', $end_ts);
        $month = (int) date('n', $end_ts);
        $report_id = $this->_findOrCreateReport($fk_user, $year, $month);
        if (!$report_id) {
            throw new RestException(500, 'Could not find or create expense report');
        }

        // Ausgabentyp-ID ermitteln (TF_OTHER bevorzugt)
        $fk_type = $this->_getExpenseTypeId();

        // Nächste Zeilennummer im Report
        $res_rank = $this->db->query(
            "SELECT COALESCE(MAX(rang), 0) + 1 AS n FROM ".MAIN_DB_PREFIX."expensereport_det"
           ." WHERE fk_expensereport=".(int)$report_id
        );
        $rang = ($res_rank && ($r = $this->db->fetch_object($res_rank))) ? (int)$r->n : 1;

        // Zeile einfügen
        $comment  = $this->db->escape(
            'Wallbox '.$wallbox_id.': '.number_format($kwh, 2, '.', '').' kWh'
        );
        $date_sql = $this->db->idate($end_ts);

        $resql = $this->db->query(
            "INSERT INTO ".MAIN_DB_PREFIX."expensereport_det"
           ." (fk_expensereport, date, fk_c_type_fees, rang, comments,"
           ."  qty, value_unit, total_ht, tva_tx, total_tva, total_ttc, rule_warning_validated)"
           ." VALUES ("
           .(int)$report_id.", '".$date_sql."', ".(int)$fk_type.", ".(int)$rang.", '".$comment."',"
           .(float)$kwh.", ".(float)$price_kwh.", ".(float)$total_ht.","
           ." 0, 0, ".(float)$total_ht.", 0)"
        );
        if (!$resql) {
            dol_syslog("WallboxBilling postSession: expensereport_det INSERT failed: ".$this->db->lasterror(), LOG_ERR);
            throw new RestException(500, 'DB error: '.$this->db->lasterror());
        }
        $line_id = (int) $this->db->last_insert_id(MAIN_DB_PREFIX.'expensereport_det');

        // Report-Summen aktualisieren
        $now = $this->db->idate(dol_now());
        $this->db->query(
            "UPDATE ".MAIN_DB_PREFIX."expensereport"
           ." SET total_ht  = total_ht  + ".(float)$total_ht.","
           ."     total_ttc = total_ttc + ".(float)$total_ht.","
           ."     date_modif = '".$now."'"
           ." WHERE rowid=".(int)$report_id
        );

        dol_syslog(
            "WallboxBilling: session recorded"
            ." expensereport_id=$report_id line_id=$line_id"
            ." user=$fk_user kwh=$kwh total=$total_ht",
            LOG_INFO
        );

        return array(
            'success'          => true,
            'expensereport_id' => $report_id,
            'line_id'          => $line_id,
        );
    }

    /**
     * GET /health
     */
    public function getHealth()
    {
        return array('status' => 'ok', 'module' => 'wallboxbilling', 'version' => '2.0.0');
    }

    // -------------------------------------------------------------------------

    private function _findOrCreateReport($fk_user, $year, $month)
    {
        // Vorhandene Entwurfs-Abrechnung suchen
        $res = $this->db->query(
            "SELECT rowid FROM ".MAIN_DB_PREFIX."expensereport"
           ." WHERE fk_user_author=".(int)$fk_user
           ."   AND YEAR(date_debut)=".(int)$year
           ."   AND MONTH(date_debut)=".(int)$month
           ."   AND fk_statut=0"
           ." ORDER BY rowid DESC LIMIT 1"
        );
        if ($res && $this->db->num_rows($res) > 0) {
            return (int) $this->db->fetch_object($res)->rowid;
        }

        // Neue Abrechnung anlegen
        $date_debut_ts = mktime(0, 0, 0, $month, 1, $year);
        $last_day      = (int) date('t', $date_debut_ts);
        $date_fin_ts   = mktime(23, 59, 59, $month, $last_day, $year);
        $ref           = $this->db->escape('WB-'.$year.str_pad($month, 2, '0', STR_PAD_LEFT).'-'.$fk_user);
        $note          = $this->db->escape('Wallbox-Abrechnung '.date('Y-m', $date_debut_ts));
        $now           = $this->db->idate(dol_now());

        $ok = $this->db->query(
            "INSERT INTO ".MAIN_DB_PREFIX."expensereport"
           ." (ref, entity, fk_user_author, date_create, date_debut, date_fin,"
           ."  fk_statut, total_ht, total_ttc, total_tva, paid, note_private)"
           ." VALUES ('".$ref."', ".(int)$GLOBALS['conf']->entity.", ".(int)$fk_user.","
           ." '".$now."', '".$this->db->idate($date_debut_ts)."', '".$this->db->idate($date_fin_ts)."',"
           ." 0, 0, 0, 0, 0, '".$note."')"
        );
        if (!$ok) {
            // Race-Condition: nochmal suchen
            $res2 = $this->db->query(
                "SELECT rowid FROM ".MAIN_DB_PREFIX."expensereport"
               ." WHERE fk_user_author=".(int)$fk_user
               ."   AND YEAR(date_debut)=".(int)$year
               ."   AND MONTH(date_debut)=".(int)$month
               ."   AND fk_statut=0"
               ." ORDER BY rowid DESC LIMIT 1"
            );
            if ($res2 && $this->db->num_rows($res2) > 0) {
                return (int) $this->db->fetch_object($res2)->rowid;
            }
            dol_syslog("WallboxBilling: CREATE expensereport failed: ".$this->db->lasterror(), LOG_ERR);
            return 0;
        }
        return (int) $this->db->last_insert_id(MAIN_DB_PREFIX.'expensereport');
    }

    private function _getExpenseTypeId()
    {
        $res = $this->db->query(
            "SELECT rowid FROM ".MAIN_DB_PREFIX."c_type_fees"
           ." WHERE code='TF_OTHER' AND active=1 ORDER BY rowid LIMIT 1"
        );
        if ($res && ($obj = $this->db->fetch_object($res))) {
            return (int) $obj->rowid;
        }
        // Fallback: erste verfügbare Kategorie
        $res = $this->db->query(
            "SELECT rowid FROM ".MAIN_DB_PREFIX."c_type_fees WHERE active=1 ORDER BY rowid LIMIT 1"
        );
        if ($res && ($obj = $this->db->fetch_object($res))) {
            return (int) $obj->rowid;
        }
        return 1;
    }
}
