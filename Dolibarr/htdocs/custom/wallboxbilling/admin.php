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
    checkToken();
    $new_price = GETPOST('WALLBOXBILLING_DEFAULT_PRICE', 'alpha');
    dolibarr_set_const($db, 'WALLBOXBILLING_DEFAULT_PRICE', $new_price, 'chaine', 0, '', $conf->entity);
    $admin_email = GETPOST('WALLBOXBILLING_ADMIN_EMAIL', 'email');
    dolibarr_set_const($db, 'WALLBOXBILLING_ADMIN_EMAIL', $admin_email, 'chaine', 0, '', $conf->entity);
    setEventMessages($langs->trans('Saved'), null, 'mesgs');
}

// Action: RFID speichern
if ($action == 'update_rfid') {
    checkToken();
    // Detect which per-user save button was clicked (form uses name="save_{user_id}")
    $user_id = 0;
    foreach ($_POST as $key => $val) {
        if (preg_match('/^save_(\d+)$/', $key, $m)) {
            $user_id = (int)$m[1];
            break;
        }
    }
    $rfid_hex = GETPOST('rfid_hex_'.$user_id, 'alpha');
    $price_kwh = GETPOST('price_kwh_'.$user_id, 'alpha');
    $cost_center = GETPOST('cost_center_'.$user_id, 'alpha');

    if ($user_id > 0 && !empty($rfid_hex)) {
        $rfid_hash = hash('sha256', $rfid_hex);
        dol_syslog("Wallbox: Saving RFID hash for user_id=".$user_id." hash=".substr($rfid_hash, 0, 16)."...", LOG_INFO);
        // Persist or update the RFID mapping in llx_wallbox_rfid (CR-02)
        $sql = "INSERT INTO ".MAIN_DB_PREFIX."wallbox_rfid (fk_user, rfid_hash, price_kwh, cost_center)";
        $sql .= " VALUES (".(int)$user_id.", '".$db->escape($rfid_hash)."',";
        $sql .= " '".$db->escape($price_kwh)."', '".$db->escape($cost_center)."')";
        $sql .= " ON DUPLICATE KEY UPDATE rfid_hash=VALUES(rfid_hash),";
        $sql .= "  price_kwh=VALUES(price_kwh), cost_center=VALUES(cost_center)";
        $resql = $db->query($sql);
        if ($resql) {
            setEventMessages($langs->trans('RFIDHashSaved'), null, 'mesgs');
        } else {
            setEventMessages($langs->trans('DatabaseError').': '.$db->lasterror(), null, 'errors');
            dol_syslog("Wallbox update_rfid SQL error: ".$db->lasterror(), LOG_ERR);
        }
    }
}

// Action: Session manuell beenden (D-12, D-13, D-14, D-16)
if ($action == 'stop_session') {
    checkToken();
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

// Action: Fehlgeschlagene Übertragung erneut senden (RET-02, D-13, D-14, D-16)
if ($action == 'retry_dead_letter') {
    checkToken();
    $dead_letter_id = GETPOST('dead_letter_id', 'int');
    $ha_url = getDolGlobalString('WALLBOXBILLING_HA_URL', '');

    if ($dead_letter_id > 0 && !empty($ha_url)) {
        $ch = curl_init($ha_url . '/session/retry');
        curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
        curl_setopt($ch, CURLOPT_POST, true);
        curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode(array('dead_letter_id' => (int)$dead_letter_id)));
        curl_setopt($ch, CURLOPT_HTTPHEADER, array('Content-Type: application/json'));
        curl_setopt($ch, CURLOPT_TIMEOUT, 4);
        curl_setopt($ch, CURLOPT_CONNECTTIMEOUT, 4);
        $response = curl_exec($ch);
        $http_code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $curl_error = curl_error($ch);
        curl_close($ch);

        if ($curl_error || $http_code != 200) {
            $err_detail = $curl_error ? $curl_error : 'HTTP '.$http_code;
            setEventMessages($langs->trans('RetryDeadLetterFailed').': '.$err_detail, null, 'errors');
            dol_syslog("Wallbox retry_dead_letter failed for dead_letter_id=".$dead_letter_id.": ".$err_detail, LOG_ERR);
        } else {
            $resp_data = json_decode($response, true);
            if (!empty($resp_data['success'])) {
                setEventMessages($langs->trans('RetryDeadLetterSuccess'), null, 'mesgs');
            } else {
                $api_err = !empty($resp_data['error']) ? $resp_data['error'] : 'unknown';
                setEventMessages($langs->trans('RetryDeadLetterFailed').': '.$api_err, null, 'errors');
            }
        }
    } else {
        setEventMessages($langs->trans('WallboxHAUnreachable'), null, 'errors');
    }
    // PRG: Redirect zum Deadletter-Tab um Tabelle neu zu laden (D-16)
    header('Location: '.$_SERVER['PHP_SELF'].'?tab=deadletter');
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

$head[$h][0] = $_SERVER['PHP_SELF'].'?tab=deadletter';
$head[$h][1] = $langs->trans('WallboxDeadLetter');
$head[$h][2] = 'deadletter';
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
    print '<tr class="oddeven">';

    if ($health_result['status'] == 'ok') {
        print '<td><span style="color:green">&#x2705; '.$langs->trans('Reachable').'</span></td>';
        print '<td></td>';
    } elseif ($health_result['status'] == 'unreachable') {
        print '<td><span style="color:red">&#x274C; '.$langs->trans('Unreachable').'</span></td>';
        print '<td>'.htmlspecialchars($health_result['detail'], ENT_QUOTES, 'UTF-8').'</td>';
    } elseif ($health_result['status'] == 'error') {
        print '<td><span style="color:orange">&#x26A0;&#xFE0F; '.$langs->trans('Error').': '.htmlspecialchars($health_result['detail'], ENT_QUOTES, 'UTF-8').'</span></td>';
        print '<td></td>';
    } else {
        print '<td>'.$langs->trans('NotConfigured').' (WALLBOXBILLING_HA_URL)</td>';
        print '<td></td>';
    }

    print '</tr>';
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
    print '<td><input type="text" name="WALLBOXBILLING_DEFAULT_PRICE" value="'
        .htmlspecialchars(getDolGlobalString('WALLBOXBILLING_DEFAULT_PRICE'), ENT_QUOTES, 'UTF-8')
        .'"></td></tr>';

    print '<tr><td>Admin-E-Mail für Upload-Alerts</td>';
    print '<td><input type="email" name="WALLBOXBILLING_ADMIN_EMAIL" value="'
        .htmlspecialchars(getDolGlobalString('WALLBOXBILLING_ADMIN_EMAIL'), ENT_QUOTES, 'UTF-8')
        .'" placeholder="admin@example.com"></td></tr>';

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


// =====================================================================
// TAB: FEHLGESCHLAGEN / DEAD-LETTER (RET-02)
// =====================================================================
} elseif ($tab == 'deadletter') {

    $ha_url = getDolGlobalString('WALLBOXBILLING_HA_URL', '');

    print load_fiche_titre($langs->trans('WallboxDeadLetterQueue'), '', '');

    if (empty($ha_url)) {
        print '<div class="info">'.$langs->trans('WallboxHANotConfigured').'</div>';
    } else {
        // Fehlgeschlagene Einträge von HA-Addon abrufen (GET /dead-letter/list)
        $dl_ch = curl_init($ha_url . '/dead-letter/list');
        curl_setopt($dl_ch, CURLOPT_RETURNTRANSFER, true);
        curl_setopt($dl_ch, CURLOPT_TIMEOUT, 4);
        curl_setopt($dl_ch, CURLOPT_CONNECTTIMEOUT, 4);
        $dl_response = curl_exec($dl_ch);
        $dl_http_code = curl_getinfo($dl_ch, CURLINFO_HTTP_CODE);
        $dl_curl_error = curl_error($dl_ch);
        curl_close($dl_ch);

        print '<div class="div-table-responsive">';
        print '<table class="noborder centpercent">';
        print '<tr class="liste_titre">';
        print '<td>'.$langs->trans('WallboxDeadLetterCreated').'</td>';
        print '<td>'.$langs->trans('WallboxID').'</td>';
        print '<td>'.$langs->trans('kWh').'</td>';
        print '<td>'.$langs->trans('Error').'</td>';
        print '<td>'.$langs->trans('WallboxRetryCount').'</td>';
        print '<td>'.$langs->trans('Action').'</td>';
        print '</tr>';

        if ($dl_curl_error || $dl_http_code != 200) {
            // HA nicht erreichbar — Fehlerzeile, andere Tabs bleiben zugänglich
            $unreachable_detail = $dl_curl_error ? htmlspecialchars($dl_curl_error, ENT_QUOTES, 'UTF-8') : 'HTTP '.$dl_http_code;
            print '<tr class="oddeven">';
            print '<td colspan="6"><span style="color:red">'.$langs->trans('WallboxHAUnreachable').': '.$unreachable_detail.'</span></td>';
            print '</tr>';
        } else {
            $dl_entries = json_decode($dl_response, true);
            if (empty($dl_entries)) {
                // Leer-Zustand: keine ausstehenden Einträge
                print '<tr class="oddeven">';
                print '<td colspan="6">'.$langs->trans('WallboxNoDeadLetterEntries').'</td>';
                print '</tr>';
            } else {
                foreach ($dl_entries as $entry) {
                    print '<tr class="oddeven">';
                    // Spalte 1: created_at
                    print '<td>'.htmlspecialchars($entry['created_at'] ?? '', ENT_QUOTES, 'UTF-8').'</td>';
                    // Spalte 2: wallbox_id
                    print '<td>'.htmlspecialchars($entry['wallbox_id'] ?? '', ENT_QUOTES, 'UTF-8').'</td>';
                    // Spalte 3: total_kwh formatiert
                    print '<td>'.number_format((float)($entry['total_kwh'] ?? 0), 2).'</td>';
                    // Spalte 4: error_msg — auf 80 Zeichen kürzen, HTML-escapen (T-08-04, XSS-Prävention)
                    $err_raw = $entry['error_msg'] ?? '';
                    $err_display = htmlspecialchars(mb_substr($err_raw, 0, 80), ENT_QUOTES, 'UTF-8');
                    if (mb_strlen($err_raw) > 80) {
                        $err_display .= '...';
                    }
                    print '<td style="color:red">'.$err_display.'</td>';
                    // Spalte 5: retry_count
                    print '<td>'.(int)($entry['retry_count'] ?? 0).'</td>';
                    // Spalte 6: Wiederholen-Formular (ein Formular pro Zeile, CSRF-Token D-05/T-08-02)
                    print '<td>';
                    print '<form method="POST" action="'.$_SERVER['PHP_SELF'].'?tab=deadletter" style="display:inline">';
                    print '<input type="hidden" name="token" value="'.newToken().'">';
                    print '<input type="hidden" name="action" value="retry_dead_letter">';
                    print '<input type="hidden" name="dead_letter_id" value="'.((int)$entry['id']).'">';
                    print '<input type="submit" class="button smallpaddingimp" value="'.htmlspecialchars($langs->trans('WallboxRetryAction'), ENT_QUOTES, 'UTF-8').'">';
                    print '</form>';
                    print '</td>';
                    print '</tr>';
                }
            }
        }
        print '</table>';
        print '</div>';
    }
}

// Tab-Bereich schliessen
print dol_fiche_end();

llxFooter();
?>
