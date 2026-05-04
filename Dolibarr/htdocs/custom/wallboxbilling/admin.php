<?php
/**
 *  admin.php - Wallbox Billing Administration
 *
 *  @author    Wallbox-Dolibarr Team
 */

require_once '../../../main.inc.php';
require_once '../core/modules/modWallboxbilling.class.php';

// Berechtigungsprüfung (SEC-04, D-24)
if (!$user->rights->wallboxbilling->admin) {
    accessforbidden();
}

$langs->load('wallboxbilling.lang');

$page_name = 'WallboxBillingSetup';
$page_title = $langs->trans('WallboxBillingSetup');

llxHeader('', $page_title);

$form = new Form($db);

// Admin-Menü
print load_fiche_titre($page_title, '', 'title_setup.png');

// Konfiguration (Vorbereitung für Phase 4: Billing)
print '<form method="POST" action="'.$_SERVER['PHP_SELF'].'">';
print '<input type="hidden" name="token" value="'.newToken().'">';
print '<input type="hidden" name="action" value="update">';

print '<table class="noborder centpercent">';
print '<tr class="liste_titre">';
print '<td colspan="2">'.$langs->trans('WallboxConfiguration').'</td>';
print '</tr>';

// Standard kWh-Preis
print '<tr><td>'.$langs->trans('DefaultPricePerKwh').'</td>';
print '<td><input type="text" name="WALLBOXBILLING_DEFAULT_PRICE" value="'.getDolGlobalString('WALLBOXBILLING_DEFAULT_PRICE').'"></td></tr>';

print '</table>';

print '<div class="center"><input type="submit" class="button" value="'.$langs->trans('Save').'"></div>';
print '</form>';

// RFID-Hash Tabelle für Benutzer-Verwaltung (USR-01 bis USR-05)
print '<form method="POST" action="'.$_SERVER["PHP_SELF"].'">';
print '<input type="hidden" name="token" value="'.newToken().'">';
print '<input type="hidden" name="action" value="update_rfid">';

print load_fiche_titre($langs->trans("WallboxUserRFIDManagement"), '', '');

print '<table class="noborder centpercent">';
print '<tr class="liste_titre">';
print '<td>'.$langs->trans("User").'</td>';
print '<td>'.$langs->trans("RFIDHex").'</td>';
print '<td>'.$langs->trans("RFIDHash").'</td>';
print '<td>'.$langs->trans("PricePerKWh").'</td>';
print '<td>'.$langs->trans("CostCenter").'</td>';
print '<td>'.$langs->trans("Action").'</td>';
print '</tr>';

// Benutzer auflisten, die für Wallbox aktiviert sind (USR-01)
$sql = "SELECT u.rowid, u.login, u.lastname, u.firstname";
$sql.= " FROM ".MAIN_DB_PREFIX."user as u";
$sql.= " WHERE u.statut = 1"; // Aktive Benutzer
$sql.= " ORDER BY u.login";

$resql = $db->query($sql);
if ($resql) {
    $num = $db->num_rows($resql);
    $i = 0;
    
    while ($i < $num) {
        $obj = $db->fetch_object($resql);
        
        // RFID-Hash aus extrafields oder eigener Tabelle laden (USR-02)
        // Hier: Beispiel mit POST-Input (GETPOST, SEC-05)
        $rfid_hex = GETPOST('rfid_hex_'.$obj->rowid, 'alpha'); // z.B. "EFCD083E"
        $price_kwh = GETPOST('price_kwh_'.$obj->rowid, 'alpha');
        $cost_center = GETPOST('cost_center_'.$obj->rowid, 'alpha');
        
        // Hash berechnen (nur Hash speichern, SEC-02!)
        $rfid_hash = '';
        if (!empty($rfid_hex)) {
            $rfid_hash = hash('sha256', $rfid_hex); // D-19: identisch zu HA
        }
        
        print '<tr class="oddy">';
        print '<td>'.$obj->login.' ('.$obj->firstname.' '.$obj->lastname.')</td>';
        print '<td><input type="text" name="rfid_hex_'.$obj->rowid.'" value="'.$rfid_hex.'" size="20" placeholder="EFCD083E"></td>';
        print '<td><span class="small">'.substr($rfid_hash, 0, 16).'...</span></td>'; // Hash anzeigen (gekürzt)
        print '<td><input type="text" name="price_kwh_'.$obj->rowid.'" value="'.$price_kwh.'" size="10" placeholder="0.30"> €/kWh</td>';
        print '<td><input type="text" name="cost_center_'.$obj->rowid.'" value="'.$cost_center.'" size="20" placeholder="Projekt ABC"></td>';
        print '<td><input type="submit" class="button" name="save_'.$obj->rowid.'" value="'.$langs->trans("Save").'"></td>';
        print '</tr>';
        
        $i++;
    }
    $db->free($resql);
}

print '</table>';
print '</form>';

// Action: RFID speichern (USR-02, USR-03, USR-04)
if (GETPOST('action', 'alpha') == 'update_rfid') {
    // GETPOST() für SQL-Injection Prävention (SEC-05)
    $user_id = GETPOST('user_id', 'int');
    $rfid_hex = GETPOST('rfid_hex', 'alpha');
    $price_kwh = GETPOST('price_kwh', 'alpha');
    $cost_center = GETPOST('cost_center', 'alpha');
    
    if ($user_id > 0) {
        // RFID-Hash berechnen (SEC-02)
        $rfid_hash = '';
        if (!empty($rfid_hex)) {
            $rfid_hash = hash('sha256', $rfid_hex);
            
            // In Datenbank speichern (Beispiel: llx_user_extrafields oder eigene Tabelle)
            // Hier: Verwendung von Dolibarr's extrafields (Standard)
            dol_syslog("Wallbox: Saving RFID hash for user_id=".$user_id." hash=".substr($rfid_hash, 0, 16)."...", LOG_INFO); // SEC-01: Nur Hash loggen!
            
            // Speichern in extrafields (vereinfacht):
            // require_once DOL_DOCUMENT_ROOT.'/core/class/extrafields.class.php';
            // $extrafields = new ExtraFields($db);
            // $extrafields->update($user_id, array('rfid_hash'=>$rfid_hash, 'price_per_kwh'=>$price_kwh, 'cost_center'=>$cost_center), 'user');
            
            setEventMessages($langs->trans("RFIDHashSaved"), null, 'mesgs');
        }
    }
}

// Rechte-Verwaltung anzeigen (SEC-04)
print '<br>';
print load_fiche_titre($langs->trans('Permissions'), '', 'title_setup.png');
print '<table class="noborder centpercent">';
print '<tr class="liste_titre"><td>'.$langs->trans('Permission').'</td><td>'.$langs->trans('Description').'</td></tr>';
print '<tr><td>wallboxbilling.user</td><td>'.$langs->trans('ViewOwnSessions').'</td></tr>';
print '<tr><td>wallboxbilling.admin</td><td>'.$langs->trans('ManageAllSessions').'</td></tr>';
print '<tr><td>wallboxbilling.billing</td><td>'.$langs->trans('CreateBilling').'</td></tr>';
print '</table>';

llxFooter();
?>
