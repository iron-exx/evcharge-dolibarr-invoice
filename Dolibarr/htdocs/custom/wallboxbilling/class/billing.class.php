<?php
/**
 * billing.class.php - WallboxBilling Abrechnungsklasse
 *
 * Monatliche automatische Abrechnung von Wallbox-Sessions
 *
 * @author    Wallbox-Dolibarr Team
 * @version   1.0.0
 */

require_once DOL_DOCUMENT_ROOT.'/core/class/commonobject.class.php';

/**
 * WallboxBilling Klasse für monatliche Abrechnung
 */
class WallboxBilling extends CommonObject
{
    /**
     * Datenbank-Objekt
     * @var DoliDB
     */
    public $db;

    /**
     * Fehlermeldung
     * @var string
     */
    public $error;

    /**
     * Fehler-Array
     * @var array
     */
    public $errors = array();

    /**
     * Konstruktor
     *
     * @param DoliDB $db Datenbank-Objekt
     */
    public function __construct($db)
    {
        $this->db = $db;
    }

    /**
     * Monatliche Abrechnung ausführen
     *
     * Rechnet alle Sessions des Vormonats (oder spezifizierten Monats) ab
     * und gruppiert nach Benutzer
     *
     * @param User   $user  Benutzer der die Abrechnung ausführt
     * @param int    $month Monat (1-12, 0 = Vormonat)
     * @param int    $year  Jahr (0 = aktuelles Jahr)
     * @return int|array Gibt BillingHistory-Objekt zurück oder Fehler-Array
     */
    public function runMonthlyBilling($user, $month = 0, $year = 0)
    {
        global $langs;

        $this->error = '';
        $this->errors = array();

        // Zeitraum berechnen
        $dateRange = $this->getMonthDateRange($month, $year);
        $startDate = $dateRange['start'];
        $endDate = $dateRange['end'];

        dol_syslog("WallboxBilling: Starte Abrechnung für Zeitraum ".$startDate." bis ".$endDate, LOG_INFO);

        // Sessions nach Benutzer gruppiert abfragen (BIL-02)
        $sql = "SELECT s.fk_user, s.rfid_hash, u.login, u.lastname, u.firstname, u.email,";
        $sql .= " SUM(s.kwh) as total_kwh, s.price_per_kwh,";
        $sql .= " SUM(s.total_cost) as total_cost, COUNT(*) as session_count";
        $sql .= " FROM ".MAIN_PREFIX."wallbox_sessions s";
        $sql .= " LEFT JOIN ".MAIN_PREFIX."user u ON s.fk_user = u.rowid";
        $sql .= " WHERE s.start_time >= '".$this->db->escape($startDate)."'";
        $sql .= " AND s.end_time <= '".$this->db->escape($endDate)."'";
        $sql .= " AND s.status = 'completed'";
        $sql .= " GROUP BY s.fk_user, s.price_per_kwh";
        $sql .= " ORDER BY u.login, s.start_time";

        $resql = $this->db->query($sql);
        if (!$resql) {
            $this->error = "Fehler beim Laden der Sessions: ".$this->db->lasterror();
            dol_syslog("WallboxBilling Error: ".$this->error, LOG_ERR);
            return -1;
        }

        $num = $this->db->num_rows($resql);
        if ($num == 0) {
            dol_syslog("WallboxBilling: Keine Sessions im Zeitraum gefunden", LOG_INFO);
            return array(); // Keine Sessions zu berechnen
        }

        $billingResults = array();
        $billingMonth = ($month == 0) ? date('n') - 1 : $month;
        $billingYear = ($year == 0) ? date('Y') : $year;

        // Alle Benutzer abrechnen
        while ($obj = $this->db->fetch_object($resql)) {
            if ($obj->fk_user <= 0) {
                continue; // Überspringe Sessions ohne Benutzer
            }

            // Kosten berechnen: kWh × price_per_kwh (BIL-03)
            $totalKwh = floatval($obj->total_kwh);
            $pricePerKwh = floatval($obj->price_per_kwh);
            $totalCost = $totalKwh * $pricePerKwh;

            // Session-Details abrufen
            $sessionDetails = $this->getSessionDetails($obj->fk_user, $startDate, $endDate);

            // Billing History erstellen
            $billingId = $this->createBillingHistory(
                $obj->fk_user,
                $billingMonth,
                $billingYear,
                $totalKwh,
                $pricePerKwh,
                $totalCost,
                $obj->session_count,
                $sessionDetails,
                $user->id
            );

            if ($billingId > 0) {
                $billingResults[] = array(
                    'billing_id' => $billingId,
                    'user_id' => $obj->fk_user,
                    'user_login' => $obj->login,
                    'user_name' => $obj->firstname.' '.$obj->lastname,
                    'total_kwh' => $totalKwh,
                    'price_per_kwh' => $pricePerKwh,
                    'total_cost' => $totalCost,
                    'session_count' => $obj->session_count
                );
                dol_syslog("WallboxBilling: Abrechnung erstellt für User ".$obj->login." - ".$totalCost." EUR", LOG_INFO);
            }
        }

        dol_syslog("WallboxBilling: Abrechnung abgeschlossen. " . count($billingResults) . " Benutzer abgerechnet", LOG_INFO);

        return $billingResults;
    }

    /**
     * Session-Details für einen Benutzer im Zeitraum abrufen
     *
     * @param int    $fkUser  Benutzer-ID
     * @param string $start   Start-Datum
     * @param string $end     End-Datum
     * @return array          Array mit Session-Details
     */
    private function getSessionDetails($fkUser, $start, $end)
    {
        $sql = "SELECT rowid, start_time, end_time, kwh, price_per_kwh, total_cost, wallbox_id";
        $sql .= " FROM ".MAIN_PREFIX."wallbox_sessions";
        $sql .= " WHERE fk_user = " . intval($fkUser);
        $sql .= " AND start_time >= '".$this->db->escape($start)."'";
        $sql .= " AND end_time <= '".$this->db->escape($end)."'";
        $sql .= " AND status = 'completed'";

        $resql = $this->db->query($sql);
        if (!$resql) {
            return array();
        }

        $sessions = array();
        while ($obj = $this->db->fetch_object($resql)) {
            $sessions[] = array(
                'session_id' => $obj->rowid,
                'start_time' => $obj->start_time,
                'end_time' => $obj->end_time,
                'kwh' => floatval($obj->kwh),
                'price_per_kwh' => floatval($obj->price_per_kwh),
                'total_cost' => floatval($obj->total_cost),
                'wallbox_id' => $obj->wallbox_id
            );
        }

        return $sessions;
    }

    /**
     * Billing History erstellen
     *
     * Speichert die Abrechnung in llx_wallbox_billing_history
     *
     * @param int    $fkUser        Benutzer-ID
     * @param int    $billingMonth  Abrechnungsmonat
     * @param int    $billingYear   Abrechnungsjahr
     * @param float  $totalKwh      Gesamt-kWh
     * @param float  $pricePerKwh   Preis pro kWh
     * @param float  $totalCost     Gesamtpreis
     * @param int    $sessionCount  Anzahl Sessions
     * @param array  $sessionDetails Session-Details (JSON)
     * @param int    $fkUserCreator Ersteller-ID
     * @return int                  ID des Eintrags oder Fehler
     */
    public function createBillingHistory(
        $fkUser,
        $billingMonth,
        $billingYear,
        $totalKwh,
        $pricePerKwh,
        $totalCost,
        $sessionCount,
        $sessionDetails,
        $fkUserCreator
    ) {
        // Prüfen ob bereits vorhanden (verhindert Doppelabrechnung)
        $checkSql = "SELECT rowid FROM ".MAIN_PREFIX."wallbox_billing_history";
        $checkSql .= " WHERE fk_user = ".intval($fkUser);
        $checkSql .= " AND billing_month = ".intval($billingMonth);
        $checkSql .= " AND billing_year = ".intval($billingYear);

        $resCheck = $this->db->query($checkSql);
        if ($resCheck && $this->db->num_rows($resCheck) > 0) {
            $this->error = "Abrechnung für diesen Monat bereits vorhanden";
            dol_syslog("WallboxBilling: ".$this->error, LOG_WARNING);
            return -1;
        }

        // JSON kodierte Session-Details
        $sessionDetailsJson = json_encode($sessionDetails);

        // SQL Insert
        $sql = "INSERT INTO ".MAIN_PREFIX."wallbox_billing_history (";
        $sql .= "fk_user, billing_month, billing_year, total_kwh, price_per_kwh,";
        $sql .= "total_cost, session_count, session_details, fk_user_creator, date_creation, status";
        $sql .= ") VALUES (";
        $sql .= intval($fkUser).", ";
        $sql .= intval($billingMonth).", ";
        $sql .= intval($billingYear).", ";
        $sql .= floatval($totalKwh).", ";
        $sql .= floatval($pricePerKwh).", ";
        $sql .= floatval($totalCost).", ";
        $sql .= intval($sessionCount).", ";
        $sql .= "'".$this->db->escape($sessionDetailsJson)."', ";
        $sql .= intval($fkUserCreator).", ";
        $sql .= "NOW(), ";
        $sql .= "1"; // Status: erstellt
        $sql .= ")";

        $resql = $this->db->query($sql);
        if (!$resql) {
            $this->error = "Fehler beim Erstellen der Billing History: ".$this->db->lasterror();
            dol_syslog("WallboxBilling Error: ".$this->error, LOG_ERR);
            return -1;
        }

        $billingId = $this->db->last_insert_id(MAIN_PREFIX."wallbox_billing_history");
        return $billingId;
    }

    /**
     * Monats-Zeitraum berechnen
     *
     * @param int $month Monat (1-12, 0 = Vormonat)
     * @param int $year  Jahr (0 = aktuelles Jahr)
     * @return array Array mit 'start' und 'end' Datum
     */
    public function getMonthDateRange($month = 0, $year = 0)
    {
        // Vormonat wenn nicht spezifiziert
        if ($month == 0) {
            $month = date('n') - 1;
            if ($month == 0) {
                $month = 12;
                $year = date('Y') - 1;
            }
        }

        // Aktuelles Jahr wenn nicht spezifiziert
        if ($year == 0) {
            $year = date('Y');
        }

        // Erster Tag des Monats
        $startDate = sprintf('%04d-%02d-01 00:00:00', $year, $month);

        // Letzter Tag des Monats
        $endDate = date('Y-m-t 23:59:59', mktime(0, 0, 0, $month, 1, $year));

        return array(
            'start' => $startDate,
            'end' => $endDate,
            'month' => $month,
            'year' => $year
        );
    }

    /**
     * Datum formatieren (deutsches Format)
     *
     * @param string $timestamp SQL-Datum/Zeit
     * @return string Formatiertes Datum
     */
    public function formatDate($timestamp)
    {
        if (empty($timestamp)) {
            return '';
        }
        $date = new DateTime($timestamp);
        return $date->format('d.m.Y');
    }

    /**
     * SQL-Value escapen
     *
     * @param string $str Zu escapender String
     * @return string Escapter String
     */
    public function escape($str)
    {
        return $this->db->escape($str);
    }

    /**
     * Alle Billing-Historien für einen Benutzer abrufen
     *
     * @param int $fkUser Benutzer-ID
     * @return array Array mit Billing-Einträgen
     */
    public function getUserBillingHistory($fkUser)
    {
        $sql = "SELECT * FROM ".MAIN_PREFIX."wallbox_billing_history";
        $sql .= " WHERE fk_user = ".intval($fkUser);
        $sql .= " ORDER BY billing_year DESC, billing_month DESC";

        $resql = $this->db->query($sql);
        if (!$resql) {
            $this->error = "Fehler beim Laden der Billing History: ".$this->db->lasterror();
            return array();
        }

        $results = array();
        while ($obj = $this->db->fetch_object($resql)) {
            $results[] = $obj;
        }

        return $results;
    }

    /**
     * Alle offenen (nicht bezahlten) Abrechnungen abrufen
     *
     * @return array Array mit offenen Billing-Einträgen
     */
    public function getOpenBillings()
    {
        $sql = "SELECT b.*, u.login, u.lastname, u.firstname, u.email";
        $sql .= " FROM ".MAIN_PREFIX."wallbox_billing_history b";
        $sql .= " LEFT JOIN ".MAIN_PREFIX."user u ON b.fk_user = u.rowid";
        $sql .= " WHERE b.status = 1"; // Status: erstellt (offen)
        $sql .= " ORDER BY b.billing_year DESC, b.billing_month DESC, u.login";

        $resql = $this->db->query($sql);
        if (!$resql) {
            $this->error = "Fehler beim Laden der offenen Abrechnungen: ".$this->db->lasterror();
            return array();
        }

        $results = array();
        while ($obj = $this->db->fetch_object($resql)) {
            $results[] = $obj;
        }

        return $results;
    }
}
?>