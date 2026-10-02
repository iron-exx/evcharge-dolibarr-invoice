<?php
/**
 * employees.php — Liste der Mitarbeiter mit zugeordnetem RFID-Tag
 *
 * GET /custom/wallboxbilling/employees.php
 * Header: X-Wallbox-Token oder DOLAPIKEY: <WALLBOXBILLING_API_TOKEN>
 *
 * Liefert NUR login + Name — SEC-01: niemals rfid_hash ausgeben.
 * Wird vom HA-Addon genutzt, um beim manuellen Erfassen einen Mitarbeiter
 * statt eines rohen RFID-Codes auswählen zu können.
 */

if (!defined('NOLOGIN')) {
    define('NOLOGIN', 1);
}
if (!defined('NOCSRFCHECK')) {
    define('NOCSRFCHECK', 1);
}
if (!defined('NOTOKENRENEWAL')) {
    define('NOTOKENRENEWAL', 1);
}
if (!defined('NOREQUIREMENU')) {
    define('NOREQUIREMENU', 1);
}
if (!defined('NOREQUIREHTML')) {
    define('NOREQUIREHTML', 1);
}
if (!defined('NOREQUIREAJAX')) {
    define('NOREQUIREAJAX', 1);
}

$res = false;
if (!$res && isset($_SERVER["CONTEXT_DOCUMENT_ROOT"])) {
    $res = @include $_SERVER["CONTEXT_DOCUMENT_ROOT"]."/main.inc.php";
}
if (!$res) {
    $tmp = isset($_SERVER["SCRIPT_FILENAME"]) ? $_SERVER["SCRIPT_FILENAME"] : __FILE__;
    $i = strlen($tmp);
    while (!$res && $i > 0) {
        $i = strrpos($tmp, '/', $i - strlen($tmp) - 1);
        if ($i !== false && file_exists(substr($tmp, 0, ($i + 1))."main.inc.php")) {
            $res = @include substr($tmp, 0, ($i + 1))."main.inc.php";
        }
    }
}
if (!$res && file_exists("../../main.inc.php")) {
    $res = @include "../../main.inc.php";
}
if (!$res && file_exists("../../../main.inc.php")) {
    $res = @include "../../../main.inc.php";
}
if (!$res) {
    http_response_code(500);
    exit;
}

header('Content-Type: application/json; charset=utf-8');

function wbemp_json_exit($code, $body)
{
    http_response_code($code);
    echo json_encode($body);
    exit;
}

// --- Auth: dasselbe Shared-Token wie receive.php ---------------------------
$expected_token = getDolGlobalString('WALLBOXBILLING_API_TOKEN');
$given_token = '';
foreach (array('HTTP_DOLAPIKEY', 'HTTP_X_WALLBOX_TOKEN', 'HTTP_X_WALLBOXBILLING_TOKEN') as $hkey) {
    if (!empty($_SERVER[$hkey])) {
        $given_token = $_SERVER[$hkey];
        break;
    }
}
if ($given_token === '' && !empty($_GET['token'])) {
    $given_token = $_GET['token'];
}

if (empty($expected_token) || !hash_equals($expected_token, (string) $given_token)) {
    dol_syslog('WallboxBilling employees.php: unauthorized attempt', LOG_WARNING);
    wbemp_json_exit(401, array('success' => false, 'error' => 'Unauthorized'));
}

// Modul-Version für "Verbindung testen" im Addon (erst nach erfolgreicher Auth)
dol_include_once('/wallboxbilling/core/modules/modWallboxbilling.class.php');
if (class_exists('modWallboxbilling')) {
    $wbmod = new modWallboxbilling($db);
    header('X-Wallboxbilling-Version: '.$wbmod->version);
}

// --- Mitarbeiter mit mindestens einem zugeordneten Tag ---------------------
// SEC-01: rfid_hash wird hier NIE mit ausgegeben, nur login + Name.
$res_emp = $db->query(
    "SELECT DISTINCT u.login, u.firstname, u.lastname"
   ." FROM ".MAIN_DB_PREFIX."wallbox_rfid wr"
   ." INNER JOIN ".MAIN_DB_PREFIX."user u ON u.rowid = wr.fk_user"
   ." WHERE u.statut = 1"
   ." ORDER BY u.login"
);

$employees = array();
if ($res_emp) {
    while ($obj = $db->fetch_object($res_emp)) {
        $employees[] = array(
            'login' => $obj->login,
            'name'  => trim($obj->firstname.' '.$obj->lastname),
        );
    }
    $db->free($res_emp);
}

wbemp_json_exit(200, array('success' => true, 'employees' => $employees));
