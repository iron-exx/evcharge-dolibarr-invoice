<?php
/**
 *  modWallboxbilling.class.php - Wallbox Billing Modul Descriptor
 *
 *  @author    Wallbox-Dolibarr Team
 *  @version   1.0.0
 */

require_once DOL_DOCUMENT_ROOT.'/core/modules/DolibarrModules.class.php';

/**
 * Klasse für Wallbox Billing Modul
 */
class modWallboxbilling extends DolibarrModules
{
    /**
     * Konstruktor
     */
    public function __construct($db)
    {
        global $langs, $conf;

        $this->db = $db;
        $this->numero = 104000; // Modul-Nummer (frei wählbar, > 100000)
        $this->rights_class = 'wallboxbilling';
        $this->family = "financial"; // Familie: Finanzen
        $this->module_position = 80; // Position im Menü

        $this->name = array(
            'en_US' => 'Wallbox Billing',
            'de_DE' => 'Wallbox-Abrechnung'
        );

        $this->description = array(
            'en_US' => 'RFID-based billing for EV charging sessions',
            'de_DE' => 'RFID-basierte Abrechnung von Wallbox-Ladevorgängen'
        );

        $this->version = '1.0.0';
        $this->const_name = 'MAIN_MODULE_'.strtoupper($this->name['en_US']);
        $this->special = 0;
        $this->picto = 'wallbox@wallboxbilling'; // Icon aus img/ Verzeichnis

        // Abhängigkeiten
        $this->depends = array(); // Keine besonderen Abhängigkeiten
        $this->requiredby = array();
        $this->conflictwith = array();
        $this->langfiles = array("wallboxbilling.lang");

        // API-Endpunkt Registrierung (API-01, API-02)
        // Der API-Endpoint wird über Dolibarr REST API exponiert:
        // POST /api/index.php/wallboxbilling/session
        $this->api_class = array('WallboxbillingApi');

        // Berechtigungen definieren (D-08, SEC-04)
        $this->rights = array();

        $r = 0;

        // wallboxbilling.user - Normale Nutzer (können eigene Sessions sehen)
        $this->rights[$r][0] = 104001; // ID für Berechtigung
        $this->rights[$r][1] = 'View own charging sessions'; // Beschreibung (en)
        $this->rights[$r][2] = 'r'; // Leserecht
        $this->rights[$r][3] = 0; // Nicht aktiv standardmäßig
        $this->rights[$r][4] = 'wallboxbilling.user'; // Berechtigungs-Key
        $r++;

        // wallboxbilling.admin - Admins (können alle Sessions verwalten)
        $this->rights[$r][0] = 104002;
        $this->rights[$r][1] = 'Manage all charging sessions and users';
        $this->rights[$r][2] = 'w'; // Schreibrecht
        $this->rights[$r][3] = 0;
        $this->rights[$r][4] = 'wallboxbilling.admin';
        $r++;

        // wallboxbilling.billing - Billing (können Abrechnungen erstellen)
        $this->rights[$r][0] = 104003;
        $this->rights[$r][1] = 'Create monthly billing and invoices';
        $this->rights[$r][2] = 'w';
        $this->rights[$r][3] = 0;
        $this->rights[$r][4] = 'wallboxbilling.billing';
        $r++;

        // Modul-Initialisierung
        $this->init();
    }

    /**
     * Modul-Initialisierung (D-07, DB-03)
     */
    public function init()
    {
        // SQL-Tabellen erstellen
        $sql = array();

        // Haupt-Sessions Tabelle
        $sql[] = "CREATE TABLE IF NOT EXISTS `llx_wallbox_sessions` (
            `rowid` INTEGER AUTO_INCREMENT PRIMARY KEY NOT NULL,
            `fk_user` INTEGER NOT NULL DEFAULT 0,
            `rfid_hash` VARCHAR(64) NOT NULL,
            `wallbox_id` VARCHAR(50) NOT NULL DEFAULT 'alfen_eve',
            `start_time` DATETIME NOT NULL,
            `end_time` DATETIME NULL,
            `kwh` REAL NOT NULL DEFAULT 0.0,
            `price_per_kwh` REAL NOT NULL DEFAULT 0.30,
            `total_cost` REAL NOT NULL DEFAULT 0.0,
            `status` VARCHAR(20) NOT NULL DEFAULT 'active',
            `date_creation` DATETIME NOT NULL,
            `transmitted_at` DATETIME NULL,
            `tms` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8";

        // RFID-Tabelle für User-Zuordnung
        $sql[] = "CREATE TABLE IF NOT EXISTS `llx_wallbox_rfid` (
            `rowid` INTEGER AUTO_INCREMENT PRIMARY KEY NOT NULL,
            `fk_user` INTEGER NOT NULL,
            `rfid_hash` VARCHAR(64) NOT NULL,
            `label` VARCHAR(255) DEFAULT '',
            `active` TINYINT(1) NOT NULL DEFAULT 1,
            `date_creation` DATETIME NOT NULL,
            `tms` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY `uk_rfid_hash` (`rfid_hash`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8";

        // Indizes erstellen
        $sql[] = "CREATE INDEX IF NOT EXISTS idx_wallbox_sessions_rfid ON llx_wallbox_sessions(rfid_hash)";
        $sql[] = "CREATE INDEX IF NOT EXISTS idx_wallbox_sessions_user ON llx_wallbox_sessions(fk_user)";
        $sql[] = "CREATE INDEX IF NOT EXISTS idx_wallbox_sessions_status ON llx_wallbox_sessions(status)";
        $sql[] = "CREATE INDEX IF NOT EXISTS idx_wallbox_sessions_transmitted ON llx_wallbox_sessions(transmitted_at)";

        foreach ($sql as $query) {
            $this->db->query($query);
        }

        return $this->__construct($this->db);
    }

    /**
     * Modul-Installation
     */
    public function install()
    {
        global $db, $conf;

        $error = 0;

        // SQL-Tabellen erstellen
        $sql = array();

        // Haupt-Sessions Tabelle (falls noch nicht vorhanden)
        $sql[] = "CREATE TABLE IF NOT EXISTS `llx_wallbox_sessions` (
            `rowid` INTEGER AUTO_INCREMENT PRIMARY KEY NOT NULL,
            `fk_user` INTEGER NOT NULL DEFAULT 0,
            `rfid_hash` VARCHAR(64) NOT NULL,
            `wallbox_id` VARCHAR(50) NOT NULL DEFAULT 'alfen_eve',
            `start_time` DATETIME NOT NULL,
            `end_time` DATETIME NULL,
            `kwh` REAL NOT NULL DEFAULT 0.0,
            `price_per_kwh` REAL NOT NULL DEFAULT 0.30,
            `total_cost` REAL NOT NULL DEFAULT 0.0,
            `status` VARCHAR(20) NOT NULL DEFAULT 'active',
            `date_creation` DATETIME NOT NULL,
            `transmitted_at` DATETIME NULL,
            `tms` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8";

        // RFID-Tabelle für User-Zuordnung
        $sql[] = "CREATE TABLE IF NOT EXISTS `llx_wallbox_rfid` (
            `rowid` INTEGER AUTO_INCREMENT PRIMARY KEY NOT NULL,
            `fk_user` INTEGER NOT NULL,
            `rfid_hash` VARCHAR(64) NOT NULL,
            `label` VARCHAR(255) DEFAULT '',
            `active` TINYINT(1) NOT NULL DEFAULT 1,
            `date_creation` DATETIME NOT NULL,
            `tms` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY `uk_rfid_hash` (`rfid_hash`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8";

        // Indizes
        $sql[] = "CREATE INDEX IF NOT EXISTS idx_wallbox_sessions_rfid ON llx_wallbox_sessions(rfid_hash)";
        $sql[] = "CREATE INDEX IF NOT EXISTS idx_wallbox_sessions_user ON llx_wallbox_sessions(fk_user)";
        $sql[] = "CREATE INDEX IF NOT EXISTS idx_wallbox_sessions_status ON llx_wallbox_sessions(status)";
        $sql[] = "CREATE INDEX IF NOT EXISTS idx_wallbox_sessions_transmitted ON llx_wallbox_sessions(transmitted_at)";

        foreach ($sql as $query) {
            $result = $db->query($query);
            if (!$result) {
                dol_syslog("WallboxBilling SQL error: ".$db->lasterror, LOG_ERR);
                // Non-fatal - table might already exist
            }
        }

        // transmitted_at Spalte hinzufügen falls nicht vorhanden (API-05, D-03)
        $check_col = "SHOW COLUMNS FROM llx_wallbox_sessions LIKE 'transmitted_at'";
        $res = $db->query($check_col);
        if (!$res || $db->num_rows($res) == 0) {
            $db->query("ALTER TABLE llx_wallbox_sessions ADD COLUMN transmitted_at DATETIME NULL AFTER date_creation");
        }

        // Berechtigungen einrichten (D-08, SEC-04)
        $this->insert_permissions();

        return ($error == 0) ? 1 : 0;
    }

    /**
     * Modul-Upgrade (für bestehende Installationen)
     */
    public function upgrade($version_from, $version_to)
    {
        global $db;

        // Upgrade 3.0.0: transmitted_at Feld hinzufügen
        $check_col = "SHOW COLUMNS FROM llx_wallbox_sessions LIKE 'transmitted_at'";
        $res = $db->query($check_col);
        if (!$res || $db->num_rows($res) == 0) {
            $db->query("ALTER TABLE llx_wallbox_sessions ADD COLUMN transmitted_at DATETIME NULL");
            $db->query("CREATE INDEX IF NOT EXISTS idx_wallbox_sessions_transmitted ON llx_wallbox_sessions(transmitted_at)");
        }

        return 1;
    }

    /**
     * Modul-Deinstallation
     */
    public function uninstall()
    {
        global $db;

        $error = 0;

        // Berechtigungen entfernen
        $this->delete_permissions();

        return ($error == 0) ? 1 : 0;
    }
}
?>
