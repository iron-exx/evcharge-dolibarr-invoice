<?php
/**
 *  card.php - User-Link zu RFID (Skeleton, Vorbereitung USR-02)
 */

require_once '../../../main.inc.php';
$langs->load('wallboxbilling.lang');

llxHeader('', $langs->trans('LinkUserToRFID'));

print load_fiche_titre($langs->trans('LinkUserToRFID'), '', 'title_wallbox.png');

// Vorbereitung für Phase 2: User mit RFID verknüpfen
print '<p>'.$langs->trans('UserRFIDLinkWillBeHere').'</p>';

llxFooter();
?>
