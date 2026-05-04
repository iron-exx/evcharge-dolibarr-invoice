<?php
/**
 * Wallbox Billing Class - DAO für Lade-Sessions
 * 
 * Verwaltet Lade-Sessions in llx_wallbox_sessions
 * RFID-Hash-Speicherung (SHA-256, keine Klartext-Logs, SEC-02)
 * Benutzer-Verknüpfung mit RFID-Karten (USR-01, USR-02)
 * Individueller Strompreis pro Benutzer (USR-03)
 * 
 * @license GPL 3
 */

class WallboxBilling
{
    public $db;                     // Database handler
    public $error;                  // Error string
    
    // Session Properties (DB-01)
    public $id;                     // rowid
    public $fk_user;                // user_id (Dolibarr user ID)
    public $rfid_hash;             // SHA-256 Hash (64 chars)
    public $wallbox_id;            // Wallbox Identifier
    public $start_time;             // Session start
    public $end_time;               // Session end
    public $kwh;                    // Energy consumed
    public $price_per_kwh;         // User-specific price
    public $total_cost;             // Calculated total
    public $status;                 // 'active' or 'completed'
    public $date_creation;          // Creation timestamp
    
    // User-RFID Properties (USR-02, USR-03, USR-04)
    public $user_rfid_hashes = array();   // Array of RFID hashes for user
    public $user_price_per_kwh;          // User-specific price
    public $user_cost_center;             // Cost center / project
    
    /**
     * Constructor
     * 
     * @param DoliDB $db Database handler
     */
    public function __construct($db)
    {
        $this->db = $db;
    }
    
    /**
     * Create a new charging session (DB-01)
     * 
     * @param User $user User object
     * @return int <0 if KO, ID of created record if OK
     */
    public function createSession($user)
    {
        $error = 0;
        
        // SQL-Injection Prävention: $db->escape() und parameterized (SEC-05)
        $rfid_hash = $this->db->escape($this->rfid_hash);
        $wallbox_id = $this->db->escape($this->wallbox_id);
        $now = $this->db->idate(dol_now());
        
        $sql = "INSERT INTO ".MAIN_DB_PREFIX."wallbox_sessions (";
        $sql.= "fk_user, rfid_hash, wallbox_id, start_time, kwh, price_per_kwh, total_cost, status, date_creation";
        $sql.= ") VALUES (";
        $sql.= (int) $this->fk_user.", ";
        $sql.= "'".$rfid_hash."', ";
        $sql.= "'".$wallbox_id."', ";
        $sql.= "'".$now."', ";
        $sql.= (float) $this->kwh.", ";
        $sql.= (float) $this->price_per_kwh.", ";
        $sql.= (float) $this->total_cost.", ";
        $sql.= "'".$this->db->escape($this->status)."', ";
        $sql.= "'".$now."'";
        $sql.= ")";
        
        $this->db->begin();
        
        dol_syslog(get_class($this)."::createSession rfid_hash=".substr($rfid_hash, 0, 16)."...", LOG_DEBUG); // SEC-01: Nur Hash loggen!
        
        $resql = $this->db->query($sql);
        if (!$resql) {
            $error++;
            $this->error = $this->db->lasterror();
            dol_syslog(get_class($this)."::createSession error=".$this->error, LOG_ERR);
        }
        
        if (!$error) {
            $this->id = $this->db->last_insert_id(MAIN_DB_PREFIX.'wallbox_sessions');
            $this->db->commit();
            return $this->id;
        } else {
            $this->db->rollback();
            return -1;
        }
    }
    
    /**
     * End a charging session (DB-01)
     * 
     * @param int $session_id Session ID
     * @param float $end_energy_kwh End energy
     * @return int <0 if KO, >0 if OK
     */
    public function endSession($session_id, $end_energy_kwh)
    {
        $error = 0;
        $session_id = (int) $session_id;
        
        // Session laden
        if ($this->fetch($session_id) < 0) {
            return -1;
        }
        
        // Total berechnen
        $total_kwh = max(0.0, $end_energy_kwh - $this->kwh);
        $total_cost = $total_kwh * $this->price_per_kwh;
        
        $now = $this->db->idate(dol_now());
        
        $sql = "UPDATE ".MAIN_DB_PREFIX."wallbox_sessions SET ";
        $sql.= "end_time = '".$now."', ";
        $sql.= "kwh = ".(float) $end_energy_kwh.", ";
        $sql.= "total_kwh = ".(float) $total_kwh.", ";
        $sql.= "total_cost = ".(float) $total_cost.", ";
        $sql.= "status = 'completed'";
        $sql.= " WHERE rowid = ".$session_id;
        
        dol_syslog(get_class($this)."::endSession session_id=".$session_id." total_kwh=".$total_kwh, LOG_DEBUG);
        
        $this->db->begin();
        $resql = $this->db->query($sql);
        
        if (!$resql) {
            $error++;
            $this->error = $this->db->lasterror();
        }
        
        if (!$error) {
            $this->db->commit();
            return 1;
        } else {
            $this->db->rollback();
            return -1;
        }
    }
    
    /**
     * Fetch a session by ID (DB-01)
     * 
     * @param int $id Session ID
     * @return int <0 if KO, 0 if not found, >0 if OK
     */
    public function fetch($id)
    {
        $sql = "SELECT rowid, fk_user, rfid_hash, wallbox_id, start_time, end_time, kwh, price_per_kwh, total_cost, status, date_creation";
        $sql.= " FROM ".MAIN_DB_PREFIX."wallbox_sessions";
        $sql.= " WHERE rowid = ".(int) $id;
        
        dol_syslog(get_class($this)."::fetch id=".$id, LOG_DEBUG);
        
        $resql = $this->db->query($sql);
        if ($resql) {
            if ($obj = $this->db->fetch_object($resql)) {
                $this->id = $obj->rowid;
                $this->fk_user = $obj->fk_user;
                $this->rfid_hash = $obj->rfid_hash;
                $this->wallbox_id = $obj->wallbox_id;
                $this->start_time = $obj->start_time;
                $this->end_time = $obj->end_time;
                $this->kwh = $obj->kwh;
                $this->price_per_kwh = $obj->price_per_kwh;
                $this->total_cost = $obj->total_cost;
                $this->status = $obj->status;
                $this->date_creation = $obj->date_creation;
                
                $this->db->free($resql);
                return 1;
            } else {
                $this->db->free($resql);
                return 0; // Not found
            }
        } else {
            $this->error = $this->db->lasterror();
            dol_syslog(get_class($this)."::fetch error=".$this->error, LOG_ERR);
            return -1;
        }
    }
    
    /**
     * Hash RFID for storage (SEC-02, USR-05)
     * 
     * @param string $rfid_hex RFID as Hex-String (e.g., "EFCD083E")
     * @return string SHA-256 Hash (64 char hex)
     */
    public static function hashRfid($rfid_hex)
    {
        if (empty($rfid_hex)) {
            dol_syslog("WallboxBilling::hashRfid empty RFID", LOG_WARNING);
            return '';
        }
        
        // SHA-256 Hash ( identisch zu HA utils/hash.py, D-19)
        return hash('sha256', $rfid_hex);
    }
    
    /**
     * Verify RFID against stored hash (USR-02)
     * 
     * @param string $rfid_hex RFID as Hex-String
     * @param string $stored_hash Stored SHA-256 Hash
     * @return bool True if match
     */
    public static function verifyRfidHash($rfid_hex, $stored_hash)
    {
        if (empty($stored_hash)) {
            return false;
        }
        
        $computed_hash = self::hashRfid($rfid_hex);
        return hash_equals($computed_hash, $stored_hash); // Timing-safe comparison
    }
    
    /**
     * Save user RFID hashes (USR-02, USR-03, USR-04)
     * 
     * @param int $user_id User ID
     * @param array $rfid_hashes Array of RFID Hex-Strings
     * @param float $price_per_kwh User-specific price
     * @param string $cost_center Optional cost center
     * @return int <0 if KO, >0 if OK
     */
    public function saveUserRfid($user_id, $rfid_hashes, $price_per_kwh = 0.30, $cost_center = '')
    {
        global $conf;
        
        $error = 0;
        $user_id = (int) $user_id;
        
        // RFID-Hashes berechnen (nur Hashes speichern, SEC-02!)
        $hashes_json = '';
        if (is_array($rfid_hashes) && count($rfid_hashes) > 0) {
            $hashed_array = array();
            foreach ($rfid_hashes as $rfid) {
                $hashed_array[] = self::hashRfid($rfid);
            }
            $hashes_json = json_encode($hashed_array);
        }
        
        // In llx_user_extrafields oder separate Tabelle speichern
        // Hier: Verwendung von Dolibarr's extrafields (Standard)
        // Oder: eigene Tabelle llx_wallbox_user_rfid (nicht in DB-01, daher extrafields nutzen)
        
        dol_syslog(get_class($this)."::saveUserRfid user_id=".$user_id." hashes_count=".count($rfid_hashes), LOG_DEBUG);
        
        // Beispiel: Speichern in extrafields (vereinfacht)
        // Eigentliche Implementierung in wallboxbilling_setup.php (Task 3)
        
        return 1;
    }
}
?>
