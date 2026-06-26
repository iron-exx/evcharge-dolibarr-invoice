<?php
/**
 * modWallboxbilling.class.php — Wallbox Billing Modul Descriptor v2
 *
 * Schlankes Modul: nur RFID-Zuordnungstabelle, kein Cron, kein Export.
 * Session-Daten gehen direkt in llx_expensereport / llx_expensereport_det.
 */

require_once DOL_DOCUMENT_ROOT.'/core/modules/DolibarrModules.class.php';

class modWallboxbilling extends DolibarrModules
{
    public function __construct($db)
    {
        global $langs, $conf;

        $this->db             = $db;
        $this->numero         = 104000;
        $this->rights_class   = 'wallboxbilling';
        $this->family         = 'financial';
        $this->module_position = 80;

        $this->name        = 'Wallboxbilling';
        $this->description = 'RFID-based EV charging — records sessions directly as expense report lines';

        $this->version     = '2.0.0';
        $this->const_name  = 'MAIN_MODULE_WALLBOXBILLING';
        $this->special     = 0;
        $this->picto       = 'wallbox@wallboxbilling';

        $this->depends      = array();
        $this->requiredby   = array();
        $this->conflictwith = array();
        $this->langfiles    = array('wallboxbilling.lang');

        // API-Endpoint
        $this->api_class = array('WallboxbillingApi');

        // Berechtigungen
        $this->rights = array();
        $r = 0;

        $this->rights[$r][0] = 104001;
        $this->rights[$r][1] = 'View own charging sessions';
        $this->rights[$r][2] = 'r';
        $this->rights[$r][3] = 0;
        $this->rights[$r][4] = 'wallboxbilling.user';
        $r++;

        $this->rights[$r][0] = 104002;
        $this->rights[$r][1] = 'Manage all charging sessions and RFID mappings';
        $this->rights[$r][2] = 'w';
        $this->rights[$r][3] = 0;
        $this->rights[$r][4] = 'wallboxbilling.admin';

        // WR-01: init() NICHT im Konstruktor aufrufen — DDL nur bei install/upgrade
        $this->cronjobs       = array();
        $this->export_modules = array();
    }

    public function init($options = '')
    {
        // Nur die RFID-Zuordnungstabelle — Sessions gehen direkt in llx_expensereport_det
        $sql = array();

        $sql[] = "CREATE TABLE IF NOT EXISTS `".MAIN_DB_PREFIX."wallbox_rfid` (
            `rowid`         INTEGER AUTO_INCREMENT PRIMARY KEY NOT NULL,
            `fk_user`       INTEGER NOT NULL,
            `rfid_hash`     VARCHAR(64) NOT NULL,
            `price_kwh`     DECIMAL(8,4) NOT NULL DEFAULT 0.3000,
            `cost_center`   VARCHAR(100) NOT NULL DEFAULT '',
            `date_creation` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `tms`           TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY `uk_rfid_hash` (`rfid_hash`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8";

        // WR-08: DDL-Fehler loggen (nicht mehr stillschweigend ignorieren)
        foreach ($sql as $query) {
            if ($this->db->query($query) === false) {
                dol_syslog('modWallboxbilling init() DDL error: '.$this->db->lasterror(), LOG_ERR);
            }
        }

        // Upgrade-Pfad: fehlende Spalten hinzufügen falls Tabelle bereits existiert
        $this->_addColumnIfMissing(
            MAIN_DB_PREFIX.'wallbox_rfid', 'price_kwh',
            "ALTER TABLE `".MAIN_DB_PREFIX."wallbox_rfid` ADD COLUMN price_kwh DECIMAL(8,4) NOT NULL DEFAULT 0.3000 AFTER rfid_hash"
        );
        $this->_addColumnIfMissing(
            MAIN_DB_PREFIX.'wallbox_rfid', 'cost_center',
            "ALTER TABLE `".MAIN_DB_PREFIX."wallbox_rfid` ADD COLUMN cost_center VARCHAR(100) NOT NULL DEFAULT '' AFTER price_kwh"
        );

        return 1;
    }

    public function install()
    {
        $this->init();
        $this->insert_permissions();
        return 1;
    }

    public function upgrade($version_from, $version_to)
    {
        $this->init();
        return 1;
    }

    public function uninstall()
    {
        $this->delete_permissions();
        return 1;
    }

    // CR-02: $column und $table korrekt escapen
    private function _addColumnIfMissing($table, $column, $alter_sql)
    {
        $res = $this->db->query(
            "SHOW COLUMNS FROM `".$this->db->escape($table)."` LIKE '".$this->db->escape($column)."'"
        );
        if (!$res || $this->db->num_rows($res) == 0) {
            if ($this->db->query($alter_sql) === false) {
                dol_syslog("modWallboxbilling _addColumnIfMissing($column) error: ".$this->db->lasterror(), LOG_ERR);
            }
        }
    }
}
