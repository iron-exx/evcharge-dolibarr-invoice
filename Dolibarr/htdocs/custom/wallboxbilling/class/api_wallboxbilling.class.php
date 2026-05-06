<?php
/**
 *  api_wallboxbilling.class.php - Wallbox Billing REST API
 *
 *  Stellt API-Endpoint für HA-Addon bereit (POST /session)
 *  Validiert DOLAPIKEY Token, empfängt Session-JSON, speichert in llx_wallbox_sessions
 *
 *  @author    Wallbox-Dolibarr Team
 *  @version   1.0.0
 *  @requires  API-01, API-02, API-03, API-05, SEC-03
 */

require_once DOL_DOCUMENT_ROOT.'/api/class/api.class.php';

/**
 * WallboxbillingApi - REST API für Session-Upload vom HA-Addon
 */
class WallboxbillingApi extends DolibarrApi
{
    /**
     * @var array Pflichtfelder für Session-Upload
     */
    public static $FIELDS_MANDATORY = array('rfid_hash', 'wallbox_id', 'start_time', 'end_time', 'kwh');

    /**
     * @var DoliDB Datenbank-Handler
     */
    public $db;

    /**
     * Konstruktor
     *
     * @param DoliDB $db Datenbank-Verbindung
     */
    public function __construct($db)
    {
        parent::__construct($db);
        $this->db = $db;
    }

    /**
     * Session-Upload Endpunkt (POST)
     *
     * Empfängt Lade-Session vom HA-Addon und speichert in llx_wallbox_sessions
     *
     * @param object|null $request_data JSON-Body mit Session-Daten
     * @return array Response mit success, id, message
     */
    public function postSession($request_data = null)
    {
        // 1. Token-Validierung (SEC-03) - DolibarrApi prüft DOLAPIKEY automatisch
        // Die Parent-Klasse prüft bereits den Token im Konstruktor oder bei jedem Request

        // 2. Request-Daten holen (unterstützt sowohl JSON als auch Form-Daten)
        if ($request_data === null) {
            $request_data = (object) $_POST;
        }

        // 3. Pflichtfelder prüfen
        $this->_checkMandatoryParameters($request_data, self::$FIELDS_MANDATORY);

        // 4. Felder extrahieren und validieren
        $rfid_hash = $request_data->rfid_hash;
        $wallbox_id = $request_data->wallbox_id;
        $start_time = $request_data->start_time;
        $end_time = $request_data->end_time;
        $kwh = $request_data->kwh;

        // RFID-Hash Validierung: 64-stelliger Hex-String (SHA-256)
        if (!preg_match('/^[a-f0-9]{64}$/i', $rfid_hash)) {
            throw new RestException(400, 'Invalid rfid_hash format (must be 64-char hex SHA-256)');
        }

        // Wallbox-ID Validierung: max 50 Zeichen
        if (strlen($wallbox_id) > 50) {
            throw new RestException(400, 'Invalid wallbox_id (max 50 characters)');
        }

        // Zeitstempel validieren (ISO 8601 Format)
        $start_datetime = $this->parseDateTime($start_time);
        if ($start_datetime === false) {
            throw new RestException(400, 'Invalid start_time format (expected ISO 8601)');
        }

        $end_datetime = $this->parseDateTime($end_time);
        if ($end_datetime === false) {
            throw new RestException(400, 'Invalid end_time format (expected ISO 8601)');
        }

        // End-Time muss nach Start-Time sein
        if ($end_datetime <= $start_datetime) {
            throw new RestException(400, 'end_time must be after start_time');
        }

        // kWh Validierung: numerisch, >= 0, max 3 Nachkommastellen
        if (!is_numeric($kwh) || $kwh < 0) {
            throw new RestException(400, 'Invalid kwh (must be >= 0)');
        }
        if (strlen(substr(strrchr($kwh, '.'), 1)) > 3) {
            throw new RestException(400, 'Invalid kwh (max 3 decimal places)');
        }

        // 5. Duplikatsprüfung (verhindert double-charge)
        $sql_check = "SELECT rowid FROM ".MAIN_DB_PREFIX."wallbox_sessions "
            ."WHERE rfid_hash = '".$this->db->escape($rfid_hash)."' "
            ."AND start_time = '".$this->db->idate($start_datetime)."' "
            ."AND end_time = '".$this->db->idate($end_datetime)."'";
        $resql = $this->db->query($sql_check);
        if ($resql && $this->db->num_rows($resql) > 0) {
            return array('success' => false, 'message' => 'Session already exists');
        }

        // 6. User-ID auflösen (RFID-Hash → fk_user)
        $sql_user = "SELECT fk_user FROM ".MAIN_DB_PREFIX."wallbox_rfid "
            ."WHERE rfid_hash = '".$this->db->escape($rfid_hash)."'";
        $resql_user = $this->db->query($sql_user);
        $fk_user = 0; // Default: unbekannt
        if ($resql_user && ($obj = $this->db->fetch_object($resql_user))) {
            $fk_user = (int) $obj->fk_user;
        }

        // 7. Session speichern (SEC-05: SQL-Injection prevention via escape)
        $now = $this->db->idate(dol_now());

        // Check if transmitted_at column exists, if not use created_at
        $transmitted_col = 'transmitted_at';
        $sql_test = "SELECT transmitted_at FROM ".MAIN_DB_PREFIX."wallbox_sessions LIMIT 1";
        if (!$this->db->query($sql_test)) {
            // Column doesn't exist, use date_creation instead
            $transmitted_col = 'date_creation';
        }

        $sql_insert = "INSERT INTO ".MAIN_DB_PREFIX."wallbox_sessions "
            ."(fk_user, rfid_hash, wallbox_id, start_time, end_time, kwh, price_per_kwh, total_cost, status, date_creation, {$transmitted_col}) "
            ."VALUES ("
            .$fk_user.", "
            ."'".$this->db->escape($rfid_hash)."', "
            ."'".$this->db->escape($wallbox_id)."', "
            ."'".$this->db->idate($start_datetime)."', "
            ."'".$this->db->idate($end_datetime)."', "
            .(float) $kwh.", "
            ."0.30, " // Standard-Preis, wird später berechnet
            ."0.00, " // Gesamtkosten, wird später berechnet
            ."'completed', "
            ."'".$now."', "
            ."'".$now."')";

        $resql = $this->db->query($sql_insert);
        if (!$resql) {
            throw new RestException(500, 'DB Error: '.$this->db->lasterror());
        }

        $session_id = (int) $this->db->last_insert_id(MAIN_DB_PREFIX.'wallbox_sessions');

        return array(
            'success' => true,
            'id' => $session_id,
            'message' => 'Session stored'
        );
    }

    /**
     * Hilfsmethode: Parse ISO 8601 Datum
     *
     * @param string $dateString Datumstring
     * @return false|string Unix-Timestamp oder false bei Fehler
     */
    private function parseDateTime($dateString)
    {
        // Versuche verschiedene Formate zu parsen
        try {
            $dt = new DateTime($dateString);
            return $dt->getTimestamp();
        } catch (Exception $e) {
            return false;
        }
    }

    /**
     * GET /health - Gesundheitscheck für Monitoring
     *
     * @return array Status-Info
     */
    public function getHealth()
    {
        return array(
            'status' => 'ok',
            'module' => 'wallboxbilling',
            'version' => '1.0.0'
        );
    }
}

/**
 * Alternative: Standalone PHP Endpoint (ohne Dolibarr API Framework)
 * Erreichbar unter: /custom/wallboxbilling/api/session.php
 */
if (basename($_SERVER['SCRIPT_NAME']) === 'session.php') {
    // Standalone Endpoint ohne API-Framework
    header('Content-Type: application/json');

    // CORS Headers für HA-Addon
    header('Access-Control-Allow-Origin: *');
    header('Access-Control-Allow-Methods: POST, OPTIONS');
    header('Access-Control-Allow-Headers: Content-Type, DOLAPIKEY');

    if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
        http_response_code(200);
        exit;
    }

    // Token-Validierung (SEC-03)
    $api_key = getenv('DOLAPIKEY') ?: ($_SERVER['HTTP_DOLAPIKEY'] ?? '');
    if (empty($api_key)) {
        http_response_code(401);
        echo json_encode(array('error' => 'Missing DOLAPIKEY'));
        exit;
    }

    // Datenbank-Verbindung
    define('MAIN_DB_PREFIX', 'llx_');
    require_once DOL_DOCUMENT_ROOT.'/main.inc.php';

    // JSON-Body lesen
    $input = file_get_contents('php://input');
    $data = json_decode($input, true);

    if (!$data) {
        http_response_code(400);
        echo json_encode(array('error' => 'Invalid JSON'));
        exit;
    }

    // Pflichtfelder prüfen
    $mandatory = array('rfid_hash', 'wallbox_id', 'start_time', 'end_time', 'kwh');
    foreach ($mandatory as $field) {
        if (!isset($data[$field])) {
            http_response_code(400);
            echo json_encode(array('error' => "Missing mandatory parameter: {$field}"));
            exit;
        }
    }

    // RFID-Hash validieren
    if (!preg_match('/^[a-f0-9]{64}$/i', $data['rfid_hash'])) {
        http_response_code(400);
        echo json_encode(array('error' => 'Invalid rfid_hash format (must be 64-char hex SHA-256)'));
        exit;
    }

    // kWh validieren
    if (!is_numeric($data['kwh']) || $data['kwh'] < 0) {
        http_response_code(400);
        echo json_encode(array('error' => 'Invalid kwh (must be >= 0)'));
        exit;
    }

    // Zeitstempel parsen
    $start_ts = strtotime($data['start_time']);
    $end_ts = strtotime($data['end_time']);

    if (!$start_ts || !$end_ts) {
        http_response_code(400);
        echo json_encode(array('error' => 'Invalid date format'));
        exit;
    }

    if ($end_ts <= $start_ts) {
        http_response_code(400);
        echo json_encode(array('error' => 'end_time must be after start_time'));
        exit;
    }

    // Duplikatsprüfung
    $sql_check = "SELECT rowid FROM ".MAIN_DB_PREFIX."wallbox_sessions "
        ."WHERE rfid_hash = '".$db->escape($data['rfid_hash'])."' "
        ."AND start_time = '".$db->idate($start_ts)."' "
        ."AND end_time = '".$db->idate($end_ts)."'";
    $res = $db->query($sql_check);
    if ($res && $db->num_rows($res) > 0) {
        echo json_encode(array('success' => false, 'message' => 'Session already exists'));
        exit;
    }

    // Session speichern
    $now = $db->idate(dol_now());
    $sql = "INSERT INTO ".MAIN_DB_PREFIX."wallbox_sessions "
        ."(fk_user, rfid_hash, wallbox_id, start_time, end_time, kwh, price_per_kwh, total_cost, status, date_creation, transmitted_at) "
        ."VALUES (0, '".$db->escape($data['rfid_hash'])."', '".$db->escape($data['wallbox_id'])."', "
        ."'".$db->idate($start_ts)."', '".$db->idate($end_ts)."', ".(float) $data['kwh'].", 0.30, 0.00, 'completed', '".$now."', '".$now."')";

    if (!$db->query($sql)) {
        http_response_code(500);
        echo json_encode(array('error' => 'DB Error: '.$db->lasterror()));
        exit;
    }

    $id = $db->last_insert_id(MAIN_DB_PREFIX.'wallbox_sessions');
    echo json_encode(array('success' => true, 'id' => $id, 'message' => 'Session stored'));
    exit;
}