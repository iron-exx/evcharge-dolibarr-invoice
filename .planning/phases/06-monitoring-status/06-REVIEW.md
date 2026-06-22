---
phase: 06-monitoring-status
reviewed: 2026-06-22T00:00:00Z
depth: standard
files_reviewed: 8
files_reviewed_list:
  - Dolibarr/htdocs/custom/wallboxbilling/admin.php
  - Dolibarr/htdocs/custom/wallboxbilling/class/api_wallboxbilling.class.php
  - Dolibarr/htdocs/custom/wallboxbilling/core/modules/modWallboxbilling.class.php
  - Homeassistant/main.py
  - Homeassistant/session_manager.py
  - Homeassistant/tests/conftest.py
  - Homeassistant/tests/test_health.py
  - Homeassistant/tests/test_session_status.py
findings:
  critical: 6
  warning: 8
  info: 5
  total: 19
status: issues_found
---

# Phase 06: Code Review Report

**Reviewed:** 2026-06-22T00:00:00Z
**Depth:** standard
**Files Reviewed:** 8
**Status:** issues_found

## Summary

This phase adds the monitoring / status dashboard (admin.php three-tab rewrite), health-check and session-stop HTTP endpoints in the HA addon, and the corresponding upload_status/upload_error SQLite and MySQL columns. The code is largely functional, but carries several blockers: an infinite recursion in the module initialiser, a missing CSRF check on the session-stop action, a broken session-stop handler that transmits all pending sessions instead of stopping a specific one, incorrect duplicate-detect logic in the standalone PHP endpoint, and an unsafe hardcoded `sys.path` injection. Several additional warnings affect robustness and maintainability.

---

## Critical Issues

### CR-01: Infinite recursion — `modWallboxbilling::init()` calls `__construct()` unconditionally

**File:** `Dolibarr/htdocs/custom/wallboxbilling/core/modules/modWallboxbilling.class.php:185`

**Issue:** `init()` ends with `return $this->__construct($this->db);`. `__construct()` always calls `$this->init()`, so every call to either method recurses without bound until a PHP stack-overflow fatal error is thrown. The module will crash on every page load that triggers module discovery.

**Fix:** Remove the `return $this->__construct($this->db);` line from `init()`. Schema setup belongs in `install()` / `upgrade()`, not in the constructor-called `init()`. If schema initialisation on every load is truly required, extract it to a separate private helper and call that helper from `__construct()` directly — without routing through `init()`.

```php
// REMOVE this line from init():
return $this->__construct($this->db);
// Replace with:
return 1;
```

---

### CR-02: Missing CSRF token validation for `stop_session` action

**File:** `Dolibarr/htdocs/custom/wallboxbilling/admin.php:48-78`

**Issue:** The `stop_session` action handler reads `session_id` from POST and fires a cURL call to the HA addon, but it never validates the Dolibarr CSRF token (`newToken()` / `checkToken()`). An attacker who can trick an authenticated admin into visiting a malicious page can trigger arbitrary session stops via a cross-site request. The form in the HTML at line 228-232 embeds a token in the hidden field, but the server side never calls `checkToken()` before acting on the POST.

**Fix:** Add a CSRF check at the top of the `stop_session` branch (and the other two action branches for consistency):

```php
if ($action == 'stop_session') {
    checkToken(); // ← add this — throws accessforbidden() on mismatch
    $session_id = GETPOST('session_id', 'int');
    // …rest of handler…
}
```

---

### CR-03: `handle_session_stop` does not stop the requested session — it transmits all pending sessions

**File:** `Homeassistant/main.py:291-312`

**Issue:** The `handle_session_stop` handler receives a `session_id` in the JSON body but never passes it to any session-ending logic. Instead it calls `session_manager.transmit_completed_sessions(api_client)`, which bulk-transmits all untransmitted completed sessions. The requested session is never force-stopped; any active session with that ID continues running. This means the admin action "Stop Session" in Dolibarr silently does nothing to the session it targets.

**Fix:** Implement actual session termination. At minimum, mark the identified session as completed in SQLite and then transmit it:

```python
async def handle_session_stop(request):
    global session_manager, api_client
    try:
        data = await request.json()
        session_id = int(data.get('session_id', 0))
        if not session_id:
            return web.json_response({"error": "session_id required"}, status=400)
        if not api_client:
            return web.json_response({"error": "API client not configured"}, status=503)

        # Force-stop the specific session
        session_manager.mark_session_incomplete(session_id, reason='admin_stop')
        result = session_manager.transmit_completed_sessions(api_client)
        return web.json_response(
            {"status": "ok", "session_id": session_id,
             "transmitted": result.get("transmitted", 0)},
            status=200
        )
    except (ValueError, TypeError):
        return web.json_response({"error": "invalid session_id"}, status=400)
    except Exception as e:
        _LOGGER.error("session/stop Fehler: %s", e)
        return web.json_response({"error": str(e)}, status=500)
```

---

### CR-04: Standalone PHP endpoint — duplicate-check uses uninitialised `$db` variable

**File:** `Dolibarr/htdocs/custom/wallboxbilling/class/api_wallboxbilling.class.php:303-311`

**Issue:** In the standalone `session.php` code path (lines 226+), `$db` is used at line 303 for the duplicate-check query, but `$db` is only available after `require_once DOL_DOCUMENT_ROOT.'/main.inc.php'` which is performed at line 250. On line 303 `$db` should be accessible, however the CSRF/auth check at line 241 uses `getenv('DOLAPIKEY')` but **never verifies this key against the Dolibarr database**. Any non-empty string in the `DOLAPIKEY` header passes validation. An attacker who supplies any non-empty token can insert arbitrary sessions.

**Fix:** Validate the API key against the Dolibarr user table after loading `main.inc.php`:

```php
// After require_once DOL_DOCUMENT_ROOT.'/main.inc.php';
$api_key = getenv('DOLAPIKEY') ?: ($_SERVER['HTTP_DOLAPIKEY'] ?? '');
// Validate against database
$sql_key = "SELECT rowid FROM ".MAIN_DB_PREFIX."user WHERE api_key='".$db->escape($api_key)."' AND statut=1";
$res_key = $db->query($sql_key);
if (!$res_key || $db->num_rows($res_key) == 0) {
    http_response_code(401);
    echo json_encode(['error' => 'Invalid DOLAPIKEY']);
    exit;
}
```

---

### CR-05: Hardcoded absolute `sys.path` injection — breaks portability and is a security risk

**File:** `Homeassistant/main.py:19` and `Homeassistant/session_manager.py:19`

**Issue:** Both files execute `sys.path.insert(0, '/usr/local/bin')` unconditionally. This inserts a system-writable, globally-shared directory at the front of the Python import path before any other directory. Any file named `utils.py` or `hash.py` placed in `/usr/local/bin` by any process will shadow the intended module. In a container environment this is a supply-chain injection vector. It also means the code fails completely when run outside the HA addon environment (e.g., in CI or local dev), breaking the test suite unless the `utils` package is separately mocked.

**Fix:** Use a path relative to the package root:

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from utils.hash import hash_rfid
```

Or, preferably, restructure `utils/` as a proper package relative to `Homeassistant/` and remove the `sys.path` manipulation entirely.

---

### CR-06: `kwh` decimal-places check silently ignores integers (no decimal point)

**File:** `Dolibarr/htdocs/custom/wallboxbilling/class/api_wallboxbilling.class.php:99-101`

**Issue:** The check `strlen(substr(strrchr($kwh, '.'), 1)) > 3` is used to enforce a maximum of 3 decimal places. When `$kwh` contains no decimal point (e.g., `"5"` or `"12"`), `strrchr($kwh, '.')` returns `false`, `substr(false, 1)` returns `""` in older PHP but emits a deprecation notice in PHP 8.0 and a `TypeError` in PHP 8.1+ (strict mode), causing a fatal uncaught exception and a 500 response for any integer kWh value. Additionally, `strlen("")` returns `0` which passes — but the TypeError path means this crashes on PHP 8.1+.

**Fix:**

```php
$kwh_str = (string) $kwh;
$dot_pos = strpos($kwh_str, '.');
if ($dot_pos !== false && strlen($kwh_str) - $dot_pos - 1 > 3) {
    throw new RestException(400, 'Invalid kwh (max 3 decimal places)');
}
```

---

## Warnings

### WR-01: `init()` schema DDL uses MySQL-only `CREATE INDEX IF NOT EXISTS` — not portable and ignored on errors

**File:** `Dolibarr/htdocs/custom/wallboxbilling/core/modules/modWallboxbilling.class.php:176-183`

**Issue:** MySQL/MariaDB does not support `CREATE INDEX IF NOT EXISTS` (unlike PostgreSQL/SQLite). These statements will silently fail if the index already exists, but more critically, they will throw an error on any MySQL version below 8.0 if the index does not yet exist but the table does. The `foreach` loop calls `$this->db->query()` without checking the return value, so failures are silently swallowed. Additionally, there is no table prefix on these `CREATE INDEX` statements (the table name uses the raw string `llx_` rather than `MAIN_DB_PREFIX`).

**Fix:** Use `ALTER TABLE … ADD INDEX IF NOT EXISTS` (MySQL 8+) or wrap each index creation with a prior `SHOW INDEX FROM … WHERE Key_name = '…'` check, identical to how `transmitted_at` is added in the `install()` method. Also replace the hardcoded `llx_` prefix with `MAIN_DB_PREFIX`.

---

### WR-02: `update_rfid` action computes a hash but never writes it to the database

**File:** `Dolibarr/htdocs/custom/wallboxbilling/admin.php:31-45`

**Issue:** The `update_rfid` handler receives `user_id`, `rfid_hex`, `price_kwh`, and `cost_center` from POST. It computes `$rfid_hash` and logs it, but performs zero database writes. The RFID tab therefore never persists any data — every "Save" click silently does nothing. This is a silent data-loss bug, not just a missing feature.

**Fix:** Add an `INSERT … ON DUPLICATE KEY UPDATE` (or equivalent) to write the RFID mapping and the per-user price and cost centre into `llx_wallbox_rfid`. The per-user price and cost_center fields would also need to be added to that table if not already present. At minimum, add the insert and emit a proper success/error message.

---

### WR-03: `transmit_completed_sessions` breaks on first failure — skips all remaining sessions

**File:** `Homeassistant/session_manager.py:471`

**Issue:** The `break` statement on line 471 aborts the transmission loop on the first API failure. If the first session in the batch fails (e.g., due to a transient HTTP error), all subsequent sessions are never attempted. All those sessions will remain with `upload_status=NULL` / `transmitted_at=NULL` and will be retried on the next run, but any sessions after the first one in the same run are skipped unnecessarily.

**Fix:** Remove the `break` and let all sessions be attempted independently. Log errors per-session:

```python
# Remove: break
# The loop continues — each session is attempted independently
```

---

### WR-04: `periodic_transmission` task leaks if `api_client` is set at startup then becomes unavailable

**File:** `Homeassistant/main.py:401-403`

**Issue:** `transmission_task` is stored as a local variable and never cancelled or awaited in the `finally` block. If `ha_ws.subscribe_entities()` raises an exception or returns, the event loop may be torn down while the background task is still running, causing an "unhandled exception in task" warning and potential data corruption if a DB write is mid-flight. The `api_client` is also set to `None` in the task body (comment line 393) but the actual assignment is missing (it's only in a comment as a TODO), so reconnect protection is never active.

**Fix:** Store the task reference in a module-level or outer-scope variable and cancel it in the `finally` block:

```python
transmission_task = None
# …in main():
if api_client:
    transmission_task = asyncio.create_task(periodic_transmission())
# …in finally:
finally:
    if transmission_task and not transmission_task.done():
        transmission_task.cancel()
        try:
            await transmission_task
        except asyncio.CancelledError:
            pass
    if health_runner:
        await health_runner.cleanup()
    await ha_ws.disconnect()
```

---

### WR-05: `get_state()` uses hardcoded message ID `2` and races with `subscribe_entities()`

**File:** `Homeassistant/main.py:151-163`

**Issue:** `get_state()` sends a WebSocket message with `id: 2` and immediately reads the next message from the socket. `subscribe_entities()` also uses hardcoded `id: 1` and holds the same `self._ws` object. If `subscribe_entities()` is active and `get_state()` is called from `sensor_callback()`, the `await self._ws.receive_json()` in `get_state()` will race with the event loop of `subscribe_entities()` consuming the response. The response to the `get_states` request may be consumed by `subscribe_entities()` (appearing as an unknown event type and being ignored), while `get_state()` receives an unrelated event message and misinterprets it. This leads to silent data corruption: `start_energy` and `end_energy` may both be `0.0`.

**Fix:** Implement a proper message-ID counter and a correlation map (`{msg_id: Future}`) so that responses are routed to the correct waiter. This is a standard pattern for HA WebSocket clients.

---

### WR-06: `_init_database` uses module-level `_LOGGER` before instance `self._logger` is created — but also references `self._logger` on a non-existent attribute

**File:** `Homeassistant/session_manager.py:124`

**Issue:** `_init_database()` is called from `__init__()` at line 60, before `self._logger = logging.getLogger(__name__)` is set at line 62. Line 124 calls `self._logger.info(…)`. This will raise `AttributeError: 'SessionManager' object has no attribute '_logger'` on every instantiation. The module-level `_LOGGER` is defined at line 31 and is used in `_init_database()` at line 96, but line 124 incorrectly switches to `self._logger`.

**Fix:** Either move `self._logger = logging.getLogger(__name__)` to the first line of `__init__()` before the call to `_init_database()`, or use the module-level `_LOGGER` consistently throughout `_init_database()`:

```python
def __init__(self, db_path: str = "/data/sessions.db"):
    self.db_path = db_path
    self._logger = logging.getLogger(__name__)  # ← move BEFORE _init_database()
    self._init_database()
    self._last_rfid_time: Dict[str, float] = {}
```

---

### WR-07: HTML table structure broken for the `unreachable` health status case

**File:** `Dolibarr/htdocs/custom/wallboxbilling/admin.php:145-165`

**Issue:** When `$health_result['status'] == 'unreachable'`, the code at line 151 prints `</td><td>` with the detail inline inside the first `<td>` block. Then at lines 158-163 the code checks again and, finding the status is `unreachable`, skips printing anything in the second `<td>`. But the first `<td>` was never closed before the `</td><td>` was emitted — it was opened at line 145 as part of `<tr class="oddeven"><td>`. The result is a nested `<td>` (invalid HTML) followed by an extra unclosed `<td>`. All subsequent table rows will be misaligned in the browser.

**Fix:** Restructure to print complete, balanced `<td>` pairs:

```php
print '<tr class="oddeven">';
print '<td>';
if ($health_result['status'] == 'ok') {
    print '<span style="color:green">&#x2705; '.$langs->trans('Reachable').'</span>';
} elseif ($health_result['status'] == 'unreachable') {
    print '<span style="color:red">&#x274C; '.$langs->trans('Unreachable').'</span>';
} elseif ($health_result['status'] == 'error') {
    print '<span style="color:orange">&#x26A0;&#xFE0F; '.$langs->trans('Error').'</span>';
} else {
    print $langs->trans('NotConfigured').' (WALLBOXBILLING_HA_URL)';
}
print '</td><td>';
print htmlspecialchars($health_result['detail'] ?? '', ENT_QUOTES, 'UTF-8');
print '</td></tr>';
```

---

### WR-08: `test_session_status.py` tests access `sm._conn` which does not exist on `SessionManager`

**File:** `Homeassistant/tests/test_session_status.py:20,33,39,53,59`

**Issue:** All three test functions reference `sm._conn` (e.g., `sm._conn.execute(…)`, `sm._conn.commit()`). `SessionManager` has no `_conn` attribute — it opens a new `sqlite3.connect()` connection for each operation and closes it immediately. When Wave 1 implementation runs these tests, they will all raise `AttributeError: 'SessionManager' object has no attribute '_conn'` rather than providing meaningful test results, even though they are marked `xfail`. The tests are testing an interface that does not exist.

**Fix:** Either add a persistent `self._conn` connection to `SessionManager` (appropriate for tests) or rewrite the test assertions to use a separate direct SQLite connection to the same `:memory:` database — but since the `SessionManager` always opens its own connections, a shared in-memory DB would need `check_same_thread=False` and the same connection object. The cleanest fix is to add a `_get_conn()` helper that returns a persistent connection in test mode:

```python
# In conftest.py fixture, after yield sm:
# Open a direct connection to the same db_path for assertions
conn = sqlite3.connect(sm.db_path)
# Pass both sm and conn to the test
```

---

## Info

### IN-01: `WALLBOXBILLING_DEFAULT_PRICE` stored and displayed without sanitising as a numeric value

**File:** `Dolibarr/htdocs/custom/wallboxbilling/admin.php:25-26`

**Issue:** The price is saved via `GETPOST('WALLBOXBILLING_DEFAULT_PRICE', 'alpha')` which allows any alphabetic string, then displayed directly in a text input. A non-numeric value such as `"abc"` would be stored and could cause downstream calculation errors when the billing module reads it.

**Fix:** Use `GETPOST('WALLBOXBILLING_DEFAULT_PRICE', 'alpha')` but validate with `is_numeric()` before saving and emit an error if validation fails.

---

### IN-02: `const_name` contains a space — will produce an invalid PHP constant name

**File:** `Dolibarr/htdocs/custom/wallboxbilling/core/modules/modWallboxbilling.class.php:40`

**Issue:** `$this->const_name = 'MAIN_MODULE_'.strtoupper($this->name['en_US']);` evaluates to `'MAIN_MODULE_WALLBOX BILLING'` (with a space), which is not a valid PHP constant name. If Dolibarr uses `define($this->const_name, …)`, this will silently fail (PHP will not create the constant).

**Fix:**

```php
$this->const_name = 'MAIN_MODULE_' . strtoupper(str_replace(' ', '_', $this->name['en_US']));
// Evaluates to: MAIN_MODULE_WALLBOX_BILLING
```

---

### IN-03: Schema duplication — `CREATE TABLE IF NOT EXISTS` DDL is copy-pasted identically in `init()`, `install()`, and the `upgrade()` guard — 3 copies to maintain

**File:** `Dolibarr/htdocs/custom/wallboxbilling/core/modules/modWallboxbilling.class.php:127-174, 201-247, 292-306`

**Issue:** The full DDL for all three tables is copy-pasted verbatim. Any schema change must be applied in three places; it is extremely likely they will drift.

**Fix:** Extract DDL into private helper methods (e.g., `_get_create_sessions_sql()`) and call them from all three lifecycle methods.

---

### IN-04: `test_health.py` spawns subprocesses to test aiohttp — bypasses the fixture infrastructure

**File:** `Homeassistant/tests/test_health.py:21-96`

**Issue:** Tests 1-3 spawn a separate `python3 -c` subprocess and parse stdout for `"Test N PASS"`. This is brittle (platform-dependent, subprocess startup latency, no pytest reporting), completely bypasses the `health_app` fixture in `conftest.py`, and means any import error in the subprocess produces a false-positive pass because `result.stdout` might be empty (the assertion checks for `"Test N PASS" in result.stdout` — if the subprocess crashes before printing, the assertion fails correctly, but any unrelated print in stdout could in theory cause a false pass). The correct pattern is to use `pytest-asyncio` with the `health_app` fixture.

**Fix:** Rewrite using the existing `health_app` fixture and `pytest-asyncio`:

```python
@pytest.mark.asyncio
async def test_health_returns_200_with_correct_body(health_app):
    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(health_app)) as client:
        resp = await client.get('/health')
        assert resp.status == 200
        data = await resp.json()
        assert data == {'status': 'ok', 'addon': 'wallbox-dolibarr'}
```

---

### IN-05: TODO comment acknowledges missing reconnect logic for API client

**File:** `Homeassistant/main.py:394`

**Issue:** `# TODO: Reconnect-Logik in Zukunft` — once the API client is set to `None` due to transmission failures, it is never re-initialised for the lifetime of the process. All future sessions are silently buffered but never transmitted until the addon restarts.

**Fix:** Implement a reconnect attempt on the next transmission cycle rather than permanently disabling the client. At minimum, remove the TODO and file it as a tracked issue.

---

_Reviewed: 2026-06-22T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
