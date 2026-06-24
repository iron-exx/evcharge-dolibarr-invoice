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

// ---- Custom Design System Styles ----
print '<style>
/* ============================================================
   Wallbox Billing — Design System v2
   Primary #6366F1 · Success #10B981 · Error #EF4444
   ============================================================ */

/* Health status card */
.wb-health {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 16px 20px;
  border-radius: 8px;
  border: 1px solid;
  margin-bottom: 24px;
  line-height: 1.4;
}
.wb-health-ok   { background:#ECFDF5; border-color:#6EE7B7; color:#065F46; }
.wb-health-err  { background:#FEF2F2; border-color:#FCA5A5; color:#991B1B; }
.wb-health-warn { background:#FFFBEB; border-color:#FCD34D; color:#92400E; }
.wb-health-info { background:#F0F9FF; border-color:#BAE6FD; color:#0C4A6E; }
.wb-health svg  { flex-shrink:0; margin-top:1px; }
.wb-health-body { display:flex; flex-direction:column; gap:3px; }
.wb-health-title { font-size:14px; font-weight:600; }
.wb-health-detail { font-size:12px; opacity:.75; font-family:monospace; }

/* Tables */
.wb-wrap { overflow-x:auto; margin-bottom:24px; }
.wb-t {
  width:100%;
  border-collapse:collapse;
  font-size:13.5px;
  white-space:nowrap;
}
.wb-t thead th {
  background:#F8FAFC;
  color:#475569;
  font-weight:700;
  font-size:11.5px;
  text-transform:uppercase;
  letter-spacing:.05em;
  padding:10px 14px;
  border-bottom:2px solid #E2E8F0;
  text-align:left;
}
.wb-t tbody tr { border-bottom:1px solid #F1F5F9; transition:background 120ms ease; }
.wb-t tbody tr:hover { background:#F8FAFC; }
.wb-t tbody td { padding:10px 14px; color:#1E293B; vertical-align:middle; }
.wb-t tfoot td { padding:8px 14px; color:#94A3B8; font-size:12px; border-top:1px solid #E2E8F0; }
.wb-t-cell-mono { font-family:monospace; font-size:12px; }

/* Status badges */
.wb-badge {
  display:inline-flex; align-items:center; gap:5px;
  padding:3px 9px; border-radius:20px;
  font-size:11.5px; font-weight:700; letter-spacing:.02em;
  white-space:nowrap;
}
.wb-badge::before {
  content:""; display:inline-block;
  width:6px; height:6px; border-radius:50%; background:currentColor;
}
.wb-badge-ok         { background:#ECFDF5; color:#059669; }
.wb-badge-error      { background:#FEF2F2; color:#DC2626; }
.wb-badge-dead_letter{ background:#FDF4FF; color:#9333EA; }
.wb-badge-pending    { background:#FFF7ED; color:#D97706; }
.wb-badge-neutral    { background:#F1F5F9; color:#64748B; }

/* Buttons */
.wb-btn {
  display:inline-flex; align-items:center; gap:5px;
  padding:6px 13px; border-radius:6px;
  font-size:12.5px; font-weight:500; cursor:pointer;
  border:1px solid transparent;
  transition:background 150ms ease, border-color 150ms ease;
  white-space:nowrap; text-decoration:none;
}
.wb-btn:focus { outline:2px solid #6366F1; outline-offset:2px; }
.wb-btn-stop  { background:#FEF2F2; color:#DC2626; border-color:#FECACA; }
.wb-btn-stop:hover { background:#FEE2E2; border-color:#FCA5A5; }
.wb-btn-retry { background:#EFF6FF; color:#2563EB; border-color:#BFDBFE; }
.wb-btn-retry:hover { background:#DBEAFE; border-color:#93C5FD; }
.wb-btn-save  { background:#6366F1; color:#fff; border-color:#6366F1; }
.wb-btn-save:hover { background:#4F46E5; border-color:#4F46E5; }
.wb-btn svg   { flex-shrink:0; }

/* Retry count pill */
.wb-pill {
  display:inline-flex; align-items:center; justify-content:center;
  min-width:22px; height:22px; padding:0 6px; border-radius:11px;
  font-size:11px; font-weight:700;
  background:#F1F5F9; color:#64748B;
}
.wb-pill-hi { background:#FEF2F2; color:#DC2626; }

/* Error cell */
.wb-err-text {
  font-size:12px; font-family:monospace; color:#991B1B;
  max-width:280px; white-space:nowrap;
  overflow:hidden; text-overflow:ellipsis; display:block;
}

/* Empty state */
.wb-empty {
  text-align:center; padding:48px 20px;
  color:#94A3B8; font-size:13.5px;
}

/* Form card */
.wb-card {
  background:#fff; border:1px solid #E2E8F0;
  border-radius:10px; padding:24px; margin-bottom:20px;
}
.wb-card-title {
  font-size:14px; font-weight:700; color:#0F172A;
  margin:0 0 18px; padding-bottom:12px;
  border-bottom:1px solid #F1F5F9;
  display:flex; align-items:center; gap:8px;
}

/* Form grid */
.wb-form-row {
  display:grid; grid-template-columns:200px 1fr;
  gap:12px 16px; align-items:center; margin-bottom:12px;
}
.wb-form-label { font-size:13px; font-weight:500; color:#374151; }
.wb-input {
  padding:8px 12px; border:1px solid #D1D5DB;
  border-radius:6px; font-size:13px; color:#1E293B;
  background:#fff; box-sizing:border-box; width:100%; max-width:320px;
  transition:border-color 150ms, box-shadow 150ms;
}
.wb-input:focus {
  border-color:#6366F1; outline:none;
  box-shadow:0 0 0 3px rgba(99,102,241,.12);
}
.wb-input-sm { max-width:140px; }
.wb-input-xs { max-width:100px; }

/* Section divider */
.wb-section { margin-top:28px; margin-bottom:10px; }
.wb-section-title {
  font-size:13px; font-weight:700; color:#64748B;
  text-transform:uppercase; letter-spacing:.06em;
  padding-bottom:8px; border-bottom:1px solid #F1F5F9;
  display:flex; align-items:center; gap:6px;
}

/* Permission code pills */
.wb-code {
  display:inline-block; padding:2px 7px;
  background:#EEF2FF; color:#6366F1; border-radius:4px;
  font-family:monospace; font-size:11.5px; font-weight:600;
  white-space:nowrap;
}

/* Responsive */
@media(max-width:768px) {
  .wb-form-row { grid-template-columns:1fr; }
  .wb-input { max-width:100%; }
}
@media(prefers-reduced-motion:reduce) {
  .wb-t tbody tr, .wb-btn, .wb-input { transition:none; }
}
</style>';

print load_fiche_titre($page_title, '', 'title_setup.png');

// Tab-Array aufbauen (D-01: vier Tabs: Status | Konfiguration | RFID | Fehlgeschlagen)
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

    // Health status card (D-05)
    if ($health_result['status'] == 'ok') {
        $health_class = 'wb-health-ok';
        $health_icon = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>';
        $health_title = $langs->trans('Reachable');
        $health_detail = htmlspecialchars($ha_url, ENT_QUOTES, 'UTF-8');
    } elseif ($health_result['status'] == 'unreachable') {
        $health_class = 'wb-health-err';
        $health_icon = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>';
        $health_title = $langs->trans('Unreachable');
        $health_detail = htmlspecialchars($health_result['detail'], ENT_QUOTES, 'UTF-8');
    } elseif ($health_result['status'] == 'error') {
        $health_class = 'wb-health-warn';
        $health_icon = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>';
        $health_title = $langs->trans('Error').': '.htmlspecialchars($health_result['detail'], ENT_QUOTES, 'UTF-8');
        $health_detail = htmlspecialchars($ha_url, ENT_QUOTES, 'UTF-8');
    } else {
        $health_class = 'wb-health-info';
        $health_icon = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>';
        $health_title = $langs->trans('NotConfigured').' — WALLBOXBILLING_HA_URL';
        $health_detail = $langs->trans('WallboxConfiguration');
    }

    print '<div class="wb-health '.$health_class.'">';
    print $health_icon;
    print '<div class="wb-health-body">';
    print '<span class="wb-health-title">'.htmlspecialchars($langs->trans('APIStatus'), ENT_QUOTES, 'UTF-8').': '.$health_title.'</span>';
    if (!empty($health_detail)) {
        print '<span class="wb-health-detail">'.$health_detail.'</span>';
    }
    print '</div></div>';

    // --- Session-Tabelle (MON-02, MON-03, D-06, D-07, D-08) ---
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

    print '<div class="wb-wrap">';
    print '<table class="wb-t">';
    print '<thead><tr>';
    print '<th>'.$langs->trans('Date').'</th>';
    print '<th>'.$langs->trans('WallboxID').'</th>';
    print '<th>'.$langs->trans('kWh').'</th>';
    print '<th>'.$langs->trans('User').'</th>';
    print '<th>'.$langs->trans('UploadStatus').'</th>';
    print '<th>'.$langs->trans('Error').'</th>';
    print '<th>'.$langs->trans('Action').'</th>';
    print '</tr></thead>';
    print '<tbody>';

    if ($resql) {
        $num = $db->num_rows($resql);
        if ($num == 0) {
            print '<tr><td colspan="7"><div class="wb-empty">';
            print '<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="display:block;margin:0 auto 10px;opacity:.4"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 9h6M9 13h4"/></svg>';
            print htmlspecialchars($langs->trans('NoSessionsFound'), ENT_QUOTES, 'UTF-8');
            print '</div></td></tr>';
        }
        $i = 0;
        while ($i < $num) {
            $obj = $db->fetch_object($resql);
            $status_val = $obj->upload_status ?? 'pending';

            // Badge class mapping
            $badge_class_map = array(
                'ok'          => 'wb-badge-ok',
                'error'       => 'wb-badge-error',
                'dead_letter' => 'wb-badge-dead_letter',
                'pending'     => 'wb-badge-pending',
            );
            $badge_class = isset($badge_class_map[$status_val]) ? $badge_class_map[$status_val] : 'wb-badge-neutral';

            print '<tr>';
            print '<td class="wb-t-cell-mono">'.htmlspecialchars($obj->start_time ?? '', ENT_QUOTES, 'UTF-8').'</td>';
            print '<td>'.htmlspecialchars($obj->wallbox_id ?? '', ENT_QUOTES, 'UTF-8').'</td>';
            print '<td style="font-variant-numeric:tabular-nums">'.htmlspecialchars(number_format((float)($obj->kwh ?? 0), 2), ENT_QUOTES, 'UTF-8').' kWh</td>';
            print '<td>'.htmlspecialchars($obj->user_name ?? $langs->trans('Unknown'), ENT_QUOTES, 'UTF-8').'</td>';
            print '<td><span class="wb-badge '.$badge_class.'">'.htmlspecialchars($status_val, ENT_QUOTES, 'UTF-8').'</span></td>';
            print '<td>';
            if (!empty($obj->upload_error)) {
                $err_raw = $obj->upload_error;
                $err_display = htmlspecialchars(mb_substr($err_raw, 0, 80), ENT_QUOTES, 'UTF-8');
                if (mb_strlen($err_raw) > 80) $err_display .= '…';
                print '<span class="wb-err-text" title="'.htmlspecialchars($err_raw, ENT_QUOTES, 'UTF-8').'">'.$err_display.'</span>';
            }
            print '</td>';
            print '<td>';
            if ($obj->upload_status == 'pending') {
                print '<form method="POST" action="'.$_SERVER['PHP_SELF'].'?tab=status" style="display:inline;margin:0">';
                print '<input type="hidden" name="token" value="'.newToken().'">';
                print '<input type="hidden" name="action" value="stop_session">';
                print '<input type="hidden" name="session_id" value="'.((int)$obj->rowid).'">';
                print '<button type="submit" class="wb-btn wb-btn-stop" aria-label="'.htmlspecialchars($langs->trans('StopSession'), ENT_QUOTES, 'UTF-8').'">';
                print '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="4" width="16" height="16" rx="2"/></svg>';
                print ' '.htmlspecialchars($langs->trans('StopSession'), ENT_QUOTES, 'UTF-8');
                print '</button></form>';
            }
            print '</td>';
            print '</tr>';
            $i++;
        }
        $db->free($resql);
    } else {
        print '<tr><td colspan="7" style="padding:12px 14px;color:#DC2626;">';
        print htmlspecialchars($langs->trans('DatabaseError').': '.$db->lasterror(), ENT_QUOTES, 'UTF-8');
        print '</td></tr>';
        dol_syslog("Wallbox admin.php status tab SQL error: ".$db->lasterror(), LOG_ERR);
    }

    print '</tbody></table></div>';


// =====================================================================
// TAB: KONFIGURATION
// =====================================================================
} elseif ($tab == 'config') {

    print '<form method="POST" action="'.$_SERVER['PHP_SELF'].'?tab=config">';
    print '<input type="hidden" name="token" value="'.newToken().'">';
    print '<input type="hidden" name="action" value="update">';

    print '<div class="wb-card">';
    print '<h3 class="wb-card-title">';
    print '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M4.93 4.93a10 10 0 0 0 0 14.14"/></svg>';
    print htmlspecialchars($langs->trans('WallboxConfiguration'), ENT_QUOTES, 'UTF-8');
    print '</h3>';

    // Default price
    print '<div class="wb-form-row">';
    print '<label class="wb-form-label" for="wb_price">'.htmlspecialchars($langs->trans('DefaultPricePerKwh'), ENT_QUOTES, 'UTF-8').'</label>';
    print '<div style="display:flex;align-items:center;gap:8px">';
    print '<input type="text" id="wb_price" name="WALLBOXBILLING_DEFAULT_PRICE" class="wb-input wb-input-sm"';
    print ' value="'.htmlspecialchars(getDolGlobalString('WALLBOXBILLING_DEFAULT_PRICE'), ENT_QUOTES, 'UTF-8').'"';
    print ' placeholder="0.30">';
    print '<span style="font-size:13px;color:#64748B">€/kWh</span>';
    print '</div></div>';

    // Admin email
    print '<div class="wb-form-row">';
    print '<label class="wb-form-label" for="wb_email">Admin-E-Mail (Upload-Alerts)</label>';
    print '<input type="email" id="wb_email" name="WALLBOXBILLING_ADMIN_EMAIL" class="wb-input"';
    print ' value="'.htmlspecialchars(getDolGlobalString('WALLBOXBILLING_ADMIN_EMAIL'), ENT_QUOTES, 'UTF-8').'"';
    print ' placeholder="admin@example.com">';
    print '</div>';

    print '<div style="margin-top:20px;padding-top:16px;border-top:1px solid #F1F5F9">';
    print '<button type="submit" class="wb-btn wb-btn-save">';
    print '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
    print ' '.htmlspecialchars($langs->trans('Save'), ENT_QUOTES, 'UTF-8');
    print '</button>';
    print '</div>';

    print '</div>';
    print '</form>';


// =====================================================================
// TAB: RFID
// =====================================================================
} elseif ($tab == 'rfid') {

    print '<form method="POST" action="'.$_SERVER['PHP_SELF'].'?tab=rfid">';
    print '<input type="hidden" name="token" value="'.newToken().'">';
    print '<input type="hidden" name="action" value="update_rfid">';

    print '<div class="wb-wrap">';
    print '<table class="wb-t">';
    print '<thead><tr>';
    print '<th>'.$langs->trans('User').'</th>';
    print '<th>'.$langs->trans('RFIDHex').'</th>';
    print '<th>'.$langs->trans('RFIDHash').' (Vorschau)</th>';
    print '<th>'.$langs->trans('PricePerKWh').'</th>';
    print '<th>'.$langs->trans('CostCenter').'</th>';
    print '<th>'.$langs->trans('Action').'</th>';
    print '</tr></thead>';
    print '<tbody>';

    $sql_users = "SELECT u.rowid, u.login, u.lastname, u.firstname";
    $sql_users.= " FROM ".MAIN_DB_PREFIX."user as u";
    $sql_users.= " WHERE u.statut = 1";
    $sql_users.= " ORDER BY u.login";

    $resql_users = $db->query($sql_users);
    if ($resql_users) {
        $num = $db->num_rows($resql_users);
        if ($num == 0) {
            print '<tr><td colspan="6"><div class="wb-empty">Keine aktiven Benutzer gefunden.</div></td></tr>';
        }
        $i = 0;
        while ($i < $num) {
            $obj = $db->fetch_object($resql_users);
            $rfid_hex = GETPOST('rfid_hex_'.$obj->rowid, 'alpha');
            $price_kwh = GETPOST('price_kwh_'.$obj->rowid, 'alpha');
            $cost_center = GETPOST('cost_center_'.$obj->rowid, 'alpha');

            $rfid_preview = '';
            if (!empty($rfid_hex)) {
                $rfid_preview = substr(hash('sha256', $rfid_hex), 0, 16).'…';
            }

            print '<tr>';
            print '<td><span style="font-weight:500">'.htmlspecialchars($obj->firstname.' '.$obj->lastname, ENT_QUOTES, 'UTF-8').'</span>';
            print '<br><span style="font-size:11.5px;color:#94A3B8">'.htmlspecialchars($obj->login, ENT_QUOTES, 'UTF-8').'</span></td>';
            print '<td><input type="text" name="rfid_hex_'.$obj->rowid.'" class="wb-input wb-input-sm"';
            print ' value="'.htmlspecialchars($rfid_hex, ENT_QUOTES, 'UTF-8').'" placeholder="EFCD083E">';
            print '</td>';
            print '<td>';
            if (!empty($rfid_preview)) {
                print '<span class="wb-code">'.htmlspecialchars($rfid_preview, ENT_QUOTES, 'UTF-8').'</span>';
            } else {
                print '<span style="color:#CBD5E1;font-size:12px">—</span>';
            }
            print '</td>';
            print '<td><div style="display:flex;align-items:center;gap:6px">';
            print '<input type="text" name="price_kwh_'.$obj->rowid.'" class="wb-input wb-input-xs"';
            print ' value="'.htmlspecialchars($price_kwh, ENT_QUOTES, 'UTF-8').'" placeholder="0.30">';
            print '<span style="font-size:12px;color:#64748B">€/kWh</span>';
            print '</div></td>';
            print '<td><input type="text" name="cost_center_'.$obj->rowid.'" class="wb-input wb-input-sm"';
            print ' value="'.htmlspecialchars($cost_center, ENT_QUOTES, 'UTF-8').'" placeholder="Projekt ABC">';
            print '</td>';
            print '<td>';
            print '<button type="submit" name="save_'.$obj->rowid.'" class="wb-btn wb-btn-save">';
            print '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
            print ' '.htmlspecialchars($langs->trans('Save'), ENT_QUOTES, 'UTF-8');
            print '</button>';
            print '</td>';
            print '</tr>';
            $i++;
        }
        $db->free($resql_users);
    }

    print '</tbody></table></div>';
    print '</form>';

    // Berechtigungen (SEC-04)
    print '<div class="wb-section">';
    print '<div class="wb-section-title">';
    print '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>';
    print htmlspecialchars($langs->trans('Permissions'), ENT_QUOTES, 'UTF-8');
    print '</div></div>';

    print '<div class="wb-wrap">';
    print '<table class="wb-t" style="margin-top:4px">';
    print '<thead><tr>';
    print '<th>'.htmlspecialchars($langs->trans('Permission'), ENT_QUOTES, 'UTF-8').'</th>';
    print '<th>'.htmlspecialchars($langs->trans('Description'), ENT_QUOTES, 'UTF-8').'</th>';
    print '</tr></thead><tbody>';
    print '<tr><td><span class="wb-code">wallboxbilling.user</span></td><td>'.htmlspecialchars($langs->trans('ViewOwnSessions'), ENT_QUOTES, 'UTF-8').'</td></tr>';
    print '<tr><td><span class="wb-code">wallboxbilling.admin</span></td><td>'.htmlspecialchars($langs->trans('ManageAllSessions'), ENT_QUOTES, 'UTF-8').'</td></tr>';
    print '<tr><td><span class="wb-code">wallboxbilling.billing</span></td><td>'.htmlspecialchars($langs->trans('CreateBilling'), ENT_QUOTES, 'UTF-8').'</td></tr>';
    print '</tbody></table></div>';


// =====================================================================
// TAB: FEHLGESCHLAGEN / DEAD-LETTER (RET-02)
// =====================================================================
} elseif ($tab == 'deadletter') {

    $ha_url = getDolGlobalString('WALLBOXBILLING_HA_URL', '');

    if (empty($ha_url)) {
        print '<div class="wb-health wb-health-info">';
        print '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>';
        print '<div class="wb-health-body">';
        print '<span class="wb-health-title">'.htmlspecialchars($langs->trans('WallboxHANotConfigured'), ENT_QUOTES, 'UTF-8').'</span>';
        print '<span class="wb-health-detail">WALLBOXBILLING_HA_URL '.htmlspecialchars($langs->trans('NotConfigured'), ENT_QUOTES, 'UTF-8').'</span>';
        print '</div></div>';
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

        if ($dl_curl_error || $dl_http_code != 200) {
            $unreachable_detail = $dl_curl_error
                ? htmlspecialchars($dl_curl_error, ENT_QUOTES, 'UTF-8')
                : 'HTTP '.$dl_http_code;
            print '<div class="wb-health wb-health-err">';
            print '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>';
            print '<div class="wb-health-body">';
            print '<span class="wb-health-title">'.htmlspecialchars($langs->trans('WallboxHAUnreachable'), ENT_QUOTES, 'UTF-8').'</span>';
            print '<span class="wb-health-detail">'.$unreachable_detail.'</span>';
            print '</div></div>';
        } else {
            $dl_entries = json_decode($dl_response, true);

            print '<div class="wb-wrap">';
            print '<table class="wb-t">';
            print '<thead><tr>';
            print '<th>'.htmlspecialchars($langs->trans('WallboxDeadLetterCreated'), ENT_QUOTES, 'UTF-8').'</th>';
            print '<th>'.htmlspecialchars($langs->trans('WallboxID'), ENT_QUOTES, 'UTF-8').'</th>';
            print '<th>'.htmlspecialchars($langs->trans('kWh'), ENT_QUOTES, 'UTF-8').'</th>';
            print '<th>'.htmlspecialchars($langs->trans('Error'), ENT_QUOTES, 'UTF-8').'</th>';
            print '<th>'.htmlspecialchars($langs->trans('WallboxRetryCount'), ENT_QUOTES, 'UTF-8').'</th>';
            print '<th>'.htmlspecialchars($langs->trans('Action'), ENT_QUOTES, 'UTF-8').'</th>';
            print '</tr></thead>';
            print '<tbody>';

            if (empty($dl_entries)) {
                print '<tr><td colspan="6"><div class="wb-empty">';
                print '<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="display:block;margin:0 auto 10px;opacity:.4"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>';
                print htmlspecialchars($langs->trans('WallboxNoDeadLetterEntries'), ENT_QUOTES, 'UTF-8');
                print '</div></td></tr>';
            } else {
                foreach ($dl_entries as $entry) {
                    $retry_count = (int)($entry['retry_count'] ?? 0);
                    $pill_class = $retry_count >= 3 ? 'wb-pill wb-pill-hi' : 'wb-pill';

                    print '<tr>';
                    // created_at
                    print '<td class="wb-t-cell-mono">'.htmlspecialchars($entry['created_at'] ?? '', ENT_QUOTES, 'UTF-8').'</td>';
                    // wallbox_id
                    print '<td>'.htmlspecialchars($entry['wallbox_id'] ?? '', ENT_QUOTES, 'UTF-8').'</td>';
                    // total_kwh
                    print '<td style="font-variant-numeric:tabular-nums">'.number_format((float)($entry['total_kwh'] ?? 0), 2).' kWh</td>';
                    // error_msg — auf 80 Zeichen kürzen (XSS-Prävention)
                    $err_raw = $entry['error_msg'] ?? '';
                    $err_display = htmlspecialchars(mb_substr($err_raw, 0, 80), ENT_QUOTES, 'UTF-8');
                    if (mb_strlen($err_raw) > 80) $err_display .= '…';
                    print '<td><span class="wb-err-text" title="'.htmlspecialchars($err_raw, ENT_QUOTES, 'UTF-8').'">'.$err_display.'</span></td>';
                    // retry_count
                    print '<td><span class="'.$pill_class.'">'.$retry_count.'</span></td>';
                    // Wiederholen-Formular (ein Formular pro Zeile, CSRF D-05)
                    print '<td>';
                    print '<form method="POST" action="'.$_SERVER['PHP_SELF'].'?tab=deadletter" style="display:inline;margin:0">';
                    print '<input type="hidden" name="token" value="'.newToken().'">';
                    print '<input type="hidden" name="action" value="retry_dead_letter">';
                    print '<input type="hidden" name="dead_letter_id" value="'.((int)$entry['id']).'">';
                    print '<button type="submit" class="wb-btn wb-btn-retry" aria-label="'.htmlspecialchars($langs->trans('WallboxRetryAction'), ENT_QUOTES, 'UTF-8').'">';
                    print '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.5"/></svg>';
                    print ' '.htmlspecialchars($langs->trans('WallboxRetryAction'), ENT_QUOTES, 'UTF-8');
                    print '</button>';
                    print '</form>';
                    print '</td>';
                    print '</tr>';
                }
            }

            print '</tbody></table></div>';
        }
    }
}

// Tab-Bereich schliessen
print dol_fiche_end();

llxFooter();
?>
