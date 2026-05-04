<?php
/**
 *  bill.php - Abrechnung-Vorschau (Skeleton, Vorbereitung BIL-01)
 */

require_once '../../../main.inc.php';
$langs->load('wallboxbilling.lang');

if (!$user->rights->wallboxbilling->billing) {
    accessforbidden();
}

llxHeader('', $langs->trans('MonthlyBilling'));

print load_fiche_titre($langs->trans('MonthlyBilling'), '', 'title_wallbox.png');

// Vorbereitung für Phase 4: Abrechnung
print '<p>'.$langs->trans('BillingPreviewWillBeHere').'</p>';

llxFooter();
?>
