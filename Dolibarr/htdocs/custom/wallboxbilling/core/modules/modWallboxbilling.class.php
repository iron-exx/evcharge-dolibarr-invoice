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
        // Tabellen erstellen
        $sql = array();
        $sql[] = file_get_contents(DOL_DOCUMENT_ROOT.'/custom/wallboxbilling/sql/llx_wallboxbilling_sessions.sql');

        return $this->__construct($this->db);
    }

    /**
     * Modul-Installation
     */
    public function install()
    {
        global $db, $conf;

        $error = 0;

        // SQL-Dateien ausführen
        $sql_file = DOL_DOCUMENT_ROOT.'/custom/wallboxbilling/sql/llx_wallboxbilling_sessions.sql';
        if (file_exists($sql_file)) {
            $result = $db->query(file_get_contents($sql_file));
            if (!$result) {
                dol_syslog("WallboxBilling install error: ".$db->lasterror, LOG_ERR);
                $error++;
            }
        }

        // Berechtigungen einrichten (D-08, SEC-04)
        $this->insert_permissions();

        return ($error == 0) ? 1 : 0;
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
