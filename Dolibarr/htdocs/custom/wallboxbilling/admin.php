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

// --- Action-Handler (VOR HTML-Output, D-13, D-16) ---
$action = GETPOST('action', 'alpha');
$tab = GETPOST('tab', 'aZ09');
if (empty($tab)) $tab = 'status';  // D-02: Default = Status-Tab

// Action: Konfiguration speichern
if ($action == 'update') {
    $new_price = GETPOST('WALLBOXBILLING_DEFAULT_PRICE', 'alpha');
    dolibarr_set_const($db, 'WALLBOXBILLING_DEFAULT_PRICE', $new_price, 'chaine', 0, '', $conf->entity);
    setEventMessages($langs->trans('Saved'), null, 'mesgs');
}

// Action: RFID speichern
if ($action == 'update_rfid') {
    $user_id = GETPOST('user_id', 'int');
    $rfid_hex = GETPOST('rfid_hex', 'alpha');
    $price_kwh = GETPOST('price_kwh', 'alpha');
    $cost_center = GETPOST('cost_center', 'alpha');

    if ($user_id > 0) {
        $rfid_hash = '';
        if (!empty($rfid_hex)) {
            $rfid_hash = hash('sha256', $rfid_hex);
            dol_syslog("Wallbox: Saving RFID hash for user_id=".$user_id." hash=".substr($rfid_hash, 0, 16)."...", LOG_INFO);
            setEventMessages($langs->trans('RFIDHashSaved'), null, 'mesgs');
        }
    }
}

// Action: Session manuell beenden (D-12, D-13, D-14, D-16)
if ($action == 'stop_session') {
    $session_id = GETPOST('session_id', 'int');
    $ha_url = getDolGlobalString('WALLBOXBILLING_HA_URL', '');

    if ($session_id > 0 && !empty($ha_url)) {
        $ch = curl_init($ha_url . '/session/stop');
        curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
        curl_setopt($ch, CURLOPT_POST, true);
        curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode(array('session_id' => (int)$session_id)));
        curl_setopt($ch, CURLOPT_HTTPHEADER, array('Content-Type: application/json'));
        curl_setopt($ch, CURLOPT_TIMEOUT, 4);
        curl_setopt($ch, CURLOPT_CONNECTTIMEOUT, 4);
        $response = curl_exec($ch);
        $http_code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $curl_error = curl_error($ch);
        curl_close($ch);

        if ($curl_error || $http_code != 200) {
            $err_detail = $curl_error ? $curl_error : 'HTTP '.$http_code;
            setEventMessages($langs->trans('StopSessionFailed').': '.$err_detail, null, 'errors');
            dol_syslog("Wallbox stop_session failed for session_id=".$session_id.": ".$err_detail, LOG_ERR);
        } else {
            setEventMessages($langs->trans('StopSessionSuccess'), null, 'mesgs');
        }
    } else {
        setEventMessages($langs->trans('StopSessionInvalidOrNoURL'), null, 'errors');
    }
    // Redirect back to status tab to reload table (D-16)
    header('Location: '.$_SERVER['PHP_SELF'].'?tab=status');
    exit;
}

// --- HTML Output ---
$page_title = $langs->trans('WallboxBillingSetup');
llxHeader('', $page_title);

$form = new Form($db);

print load_fiche_titre($page_title, '', 'title_setup.png');

// Tab-Array aufbauen (D-01: drei Tabs: Status | Konfiguration | RFID)
$head = array();
$h = 0;

$head[$h][0] = $_SERVER['PHP_SELF'].'?tab=status';
$head[$h][1] = $langs->trans('WallboxStatus');
$head[$h][2] = 'status';
$h++;

$head[$h][0] = $_SERVER['PHP_SELF'].'?tab=config';
$head[$h][1] = $langs->trans('WallboxConfiguration');
$head[$h][2] = 'config';
$h++;

$head[$h][0] = $_SERVER['PHP_SELF'].'?tab=rfid';
$head[$h][1] = $langs->trans('WallboxUserRFIDManagement');
$head[$h][2] = 'rfid';
$h++;

// Tab-Leiste rendern — $tab ist bereits gesetzt (Default 'status', D-02)
print dol_get_fiche_head($head, $tab, $langs->trans('WallboxBillingSetup'), -1, 'title_setup');


// =====================================================================
// TAB: STATUS (D-02: Default)
// =====================================================================
if ($tab == 'status') {

    // --- API Health-Check (MON-01, D-03, D-04, D-05) ---
    $ha_url = getDolGlobalString('WALLBOXBILLING_HA_URL', '');
    $health_result = array('status' => 'unconfigured', 'detail' => '');

    if (!empty($ha_url)) {
        $ch = curl_init($ha_url . '/health');
        curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
        curl_setopt($ch, CURLOPT_TIMEOUT, 4);
        curl_setopt($ch, CURLOPT_CONNECTTIMEOUT, 4);
        $response = curl_exec($ch);
        $http_code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $curl_error = curl_error($ch);
        curl_close($ch);

        if ($curl_error) {
            $health_result = array('status' => 'unreachable', 'detail' => $curl_error);
        } elseif ($http_code == 200) {
            $health_result = array('status' => 'ok', 'detail' => '');
        } else {
            $health_result = array('status' => 'error', 'detail' => 'HTTP '.$http_code);
        }
    }

    // Anzeige API-Status (D-05: checkmark/cross/warning)
    print '<div class="div-table-responsive">';
    print '<table class="noborder centpercent">';
    print '<tr class="liste_titre">';
    print '<td colspan="2">'.$langs->trans('APIStatus').'</td>';
    print '</tr>';
    print '<tr class="oddeven"><td>';

    if ($health_result['status'] == 'ok') {
        print '<span style="color:green">&#x2705; '.$langs->trans('Reachable').'</span>';
    } elseif ($health_result['status'] == 'unreachable') {
        print '<span style="color:red">&#x274C; '.$langs->trans('Unreachable').'</span>';
        print '</td><td>'.htmlspecialchars($health_result['detail'], ENT_QUOTES, 'UTF-8');
    } elseif ($health_result['status'] == 'error') {
        print '<span style="color:orange">&#x26A0;&#xFE0F; '.$langs->trans('Error').': '.htmlspecialchars($health_result['detail'], ENT_QUOTES, 'UTF-8').'</span>';
    } else {
        print $langs->trans('NotConfigured').' (WALLBOXBILLING_HA_URL)';
    }

    print '</td><td>';
    if ($health_result['status'] == 'unreachable') {
        // already printed in td above
    } else {
        print htmlspecialchars($health_result['detail'] ?? '', ENT_QUOTES, 'UTF-8');
    }
    print '</td></tr>';
    print '</table>';
    print '</div>';

    print '<br>';

    // --- Session-Tabelle (MON-02, MON-03, D-06, D-07, D-08) ---
    print '<div class="div-table-responsive">';
    print '<table class="noborder centpercent">';
    print '<tr class="liste_titre">';
    print '<td>'.$langs->trans('Date').'</td>';
    print '<td>'.$langs->trans('WallboxID').'</td>';
    print '<td>'.$langs->trans('kWh').'</td>';
    print '<td>'.$langs->trans('User').'</td>';
    print '<td>'.$langs->trans('UploadStatus').'</td>';
    print '<td>'.$langs->trans('Error').'</td>';
    print '<td>'.$langs->trans('Action').'</td>';
    print '</tr>';

    // Letzte 25 abgeschlossene Sessions (D-06)
    // LEFT JOIN auf llx_wallbox_rfid + llx_user fuer Klarname (D-08, SEC-01, SEC-02)
    $sql = "SELECT s.rowid, s.start_time, s.wallbox_id, s.kwh,";
    $sql.= " s.upload_status, s.upload_error,";
    $sql.= " COALESCE(CONCAT(u.firstname, ' ', u.lastname), '".$db->escape($langs->trans('Unknown'))."') as user_name";
    $sql.= " FROM ".MAIN_DB_PREFIX."wallbox_sessions as s";
    $sql.= " LEFT JOIN ".MAIN_DB_PREFIX."wallbox_rfid as r ON s.rfid_hash = r.rfid_hash";
    $sql.= " LEFT JOIN ".MAIN_DB_PREFIX."user as u ON r.fk_user = u.rowid";
    $sql.= " WHERE s.status = 'completed'";
    $sql.= " ORDER BY s.rowid DESC";
    $sql.= " LIMIT 25";

    $resql = $db->query($sql);
    if ($resql) {
        $num = $db->num_rows($resql);
        if ($num == 0) {
            print '<tr class="oddeven"><td colspan="7">'.$langs->trans('NoSessionsFound').'</td></tr>';
        }
        $i = 0;
        while ($i < $num) {
            $obj = $db->fetch_object($resql);

            // Status-Badge Farbe
            $status_label = htmlspecialchars($obj->upload_status ?? 'pending', ENT_QUOTES, 'UTF-8');
            if ($obj->upload_status == 'ok') {
                $status_html = '<span style="color:green">'.$status_label.'</span>';
            } elseif ($obj->upload_status == 'error') {
                $status_html = '<span style="color:red">'.$status_label.'</span>';
            } else {
                $status_html = '<span style="color:orange">'.$status_label.'</span>';
            }

            print '<tr class="oddeven">';
            print '<td>'.htmlspecialchars($obj->start_time ?? '', ENT_QUOTES, 'UTF-8').'</td>';
            print '<td>'.htmlspecialchars($obj->wallbox_id ?? '', ENT_QUOTES, 'UTF-8').'</td>';
            print '<td>'.htmlspecialchars(number_format((float)($obj->kwh ?? 0), 2), ENT_QUOTES, 'UTF-8').'</td>';
            print '<td>'.htmlspecialchars($obj->user_name ?? $langs->trans('Unknown'), ENT_QUOTES, 'UTF-8').'</td>';
            print '<td>'.$status_html.'</td>';
            // upload_error: spezifisch oder leer (MON-03)
            print '<td>'.htmlspecialchars($obj->upload_error ?? '', ENT_QUOTES, 'UTF-8').'</td>';

            // Action: Session beenden für pending Sessions (D-12, D-13)
            print '<td>';
            if ($obj->upload_status == 'pending') {
                print '<form method="POST" action="'.$_SERVER['PHP_SELF'].'?tab=status" style="display:inline">';
                print '<input type="hidden" name="token" value="'.newToken().'">';
                print '<input type="hidden" name="action" value="stop_session">';
                print '<input type="hidden" name="session_id" value="'.((int)$obj->rowid).'">';
                print '<input type="submit" class="button smallpaddingimp" value="'.htmlspecialchars($langs->trans('StopSession'), ENT_QUOTES, 'UTF-8').'">';
                print '</form>';
            }
            print '</td>';
            print '</tr>';

            $i++;
        }
        $db->free($resql);
    } else {
        print '<tr class="oddeven"><td colspan="7"><span style="color:red">'.$langs->trans('DatabaseError').': '.htmlspecialchars($db->lasterror(), ENT_QUOTES, 'UTF-8').'</span></td></tr>';
        dol_syslog("Wallbox admin.php status tab SQL error: ".$db->lasterror(), LOG_ERR);
    }

    print '</table>';
    print '</div>';


// =====================================================================
// TAB: KONFIGURATION (existing config form, D-01)
// =====================================================================
} elseif ($tab == 'config') {

    print '<form method="POST" action="'.$_SERVER['PHP_SELF'].'?tab=config">';
    print '<input type="hidden" name="token" value="'.newToken().'">';
    print '<input type="hidden" name="action" value="update">';

    print '<table class="noborder centpercent">';
    print '<tr class="liste_titre">';
    print '<td colspan="2">'.$langs->trans('WallboxConfiguration').'</td>';
    print '</tr>';

    print '<tr><td>'.$langs->trans('DefaultPricePerKwh').'</td>';
    print '<td><input type="text" name="WALLBOXBILLING_DEFAULT_PRICE" value="'.getDolGlobalString('WALLBOXBILLING_DEFAULT_PRICE').'"></td></tr>';

    print '</table>';
    print '<div class="center"><input type="submit" class="button" value="'.$langs->trans('Save').'"></div>';
    print '</form>';


// =====================================================================
// TAB: RFID (existing RFID form, D-01)
// =====================================================================
} elseif ($tab == 'rfid') {

    print '<form method="POST" action="'.$_SERVER['PHP_SELF'].'?tab=rfid">';
    print '<input type="hidden" name="token" value="'.newToken().'">';
    print '<input type="hidden" name="action" value="update_rfid">';

    print '<table class="noborder centpercent">';
    print '<tr class="liste_titre">';
    print '<td>'.$langs->trans('User').'</td>';
    print '<td>'.$langs->trans('RFIDHex').'</td>';
    print '<td>'.$langs->trans('RFIDHash').'</td>';
    print '<td>'.$langs->trans('PricePerKWh').'</td>';
    print '<td>'.$langs->trans('CostCenter').'</td>';
    print '<td>'.$langs->trans('Action').'</td>';
    print '</tr>';

    $sql_users = "SELECT u.rowid, u.login, u.lastname, u.firstname";
    $sql_users.= " FROM ".MAIN_DB_PREFIX."user as u";
    $sql_users.= " WHERE u.statut = 1";
    $sql_users.= " ORDER BY u.login";

    $resql_users = $db->query($sql_users);
    if ($resql_users) {
        $num = $db->num_rows($resql_users);
        $i = 0;
        while ($i < $num) {
            $obj = $db->fetch_object($resql_users);
            $rfid_hex = GETPOST('rfid_hex_'.$obj->rowid, 'alpha');
            $price_kwh = GETPOST('price_kwh_'.$obj->rowid, 'alpha');
            $cost_center = GETPOST('cost_center_'.$obj->rowid, 'alpha');

            $rfid_preview = '';
            if (!empty($rfid_hex)) {
                $rfid_preview = substr(hash('sha256', $rfid_hex), 0, 16).'...';
            }

            print '<tr class="oddeven">';
            print '<td>'.htmlspecialchars($obj->login.' ('.$obj->firstname.' '.$obj->lastname.')', ENT_QUOTES, 'UTF-8').'</td>';
            print '<td><input type="text" name="rfid_hex_'.$obj->rowid.'" value="'.htmlspecialchars($rfid_hex, ENT_QUOTES, 'UTF-8').'" size="20" placeholder="EFCD083E"></td>';
            print '<td><span class="small">'.htmlspecialchars($rfid_preview, ENT_QUOTES, 'UTF-8').'</span></td>';
            print '<td><input type="text" name="price_kwh_'.$obj->rowid.'" value="'.htmlspecialchars($price_kwh, ENT_QUOTES, 'UTF-8').'" size="10" placeholder="0.30"> €/kWh</td>';
            print '<td><input type="text" name="cost_center_'.$obj->rowid.'" value="'.htmlspecialchars($cost_center, ENT_QUOTES, 'UTF-8').'" size="20" placeholder="Projekt ABC"></td>';
            print '<td><input type="submit" class="button" name="save_'.$obj->rowid.'" value="'.$langs->trans('Save').'"></td>';
            print '</tr>';

            $i++;
        }
        $db->free($resql_users);
    }

    print '</table>';
    print '</form>';

    // Rechte-Verwaltung anzeigen (SEC-04)
    print '<br>';
    print load_fiche_titre($langs->trans('Permissions'), '', 'title_setup.png');
    print '<table class="noborder centpercent">';
    print '<tr class="liste_titre"><td>'.$langs->trans('Permission').'</td><td>'.$langs->trans('Description').'</td></tr>';
    print '<tr class="oddeven"><td>wallboxbilling.user</td><td>'.$langs->trans('ViewOwnSessions').'</td></tr>';
    print '<tr class="oddeven"><td>wallboxbilling.admin</td><td>'.$langs->trans('ManageAllSessions').'</td></tr>';
    print '<tr class="oddeven"><td>wallboxbilling.billing</td><td>'.$langs->trans('CreateBilling').'</td></tr>';
    print '</table>';
}

// Tab-Bereich schliessen
print dol_fiche_end();

llxFooter();
?>
