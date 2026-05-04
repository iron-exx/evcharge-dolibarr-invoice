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
