"""Einstellungen, Admin-Passwort, System-Log, Backup/Wiederherstellung und
Systeminfo (Standalone).

Schreibend nur angemeldet — das erzwingt die Middleware in admin.web.
Backup und Wiederherstellung verlangen zusätzlich das Admin-Passwort: das
Backup enthält Dolibarr-Token und OCPP-Passwörter im Klartext.
"""
import asyncio
import io
import json
import logging
import os
import platform
import shutil
import sqlite3
import tempfile
import time
import zipfile
from datetime import datetime

from aiohttp import web

from app_settings import VALID_LOG_LEVELS

from . import logs, validate
from .web import _e, _env_warning, _login_cookie, _msg, _page, _restart_box

_LOGGER = logging.getLogger(__name__)

# options.json-Feld → (Bezeichnung, Vorgabe, min, max, Typ). Ports und
# Bind-Adressen fehlen bewusst: die hängen an der Portfreigabe in der
# docker-compose.yml/.env — hier geändert, wäre die Oberfläche weg.
FIELDS = (
    ('min_session_kwh', 'Mindestmenge je Ladung (kWh)', 0.05, 0.0, 5.0, float),
    ('max_session_hours', 'Warnung bei Ladung länger als (h)', 24, 1, 168, int),
    ('api.transmit_interval', 'Übertragung an Dolibarr alle (s)', 300, 30, 86400, int),
    ('ocpp_heartbeat_interval', 'OCPP-Heartbeat (s)', 300, 30, 3600, int),
    ('debounce_seconds', 'RFID-Entprellung (s)', 7, 1, 120, int),
    ('max_plausible_kw', 'Plausibilitätsgrenze Leistung (kW)', 50.0, 1.0, 400.0, float),
    ('max_discard_hours', 'Kurz-Session-Fenster (h)', 0.25, 0.0, 24.0, float),
    ('pending_auth_window', 'Gültigkeit vorgehaltener Karte (s)', 600, 10, 86400, int),
    ('api_timeout', 'Dolibarr-Zeitlimit (s)', 30, 5, 300, int),
    ('api_retries', 'Dolibarr-Wiederholungen', 5, 0, 10, int),
    ('api_backoff', 'Dolibarr-Wartefaktor', 0.5, 0.0, 30.0, float),
    ('learn_ttl_seconds', 'Lernmodus: Klartext-Haltezeit (s)', 600.0, 30.0, 3600.0, float),
    ('learn_max_entries', 'Lernmodus: max. Karten in der Liste', 10, 1, 100, int),
    ('trend_days', 'Tagesstreifen: Fenster (Tage)', 14, 1, 90, int),
)
_RESTART = 'Einstellungen'
BACKUP_FILES = ('options.json', 'sessions.db', 'admin.json', 'secret.key', 'audit.log')
_MAX_RESTORE_BYTES = 200 * 1024 * 1024

_CSS = """
input[type=checkbox]{width:auto;vertical-align:middle;margin-right:6px}
.grid2{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:4px 14px}
.logbox{font:11px/1.45 ui-monospace,monospace;white-space:pre-wrap;word-break:break-all;background:var(--bg);
  border:1px solid var(--border);border-radius:8px;padding:10px;max-height:70vh;overflow:auto}
.logbox .l40,.logbox .l50{color:var(--error)}.logbox .l30{color:var(--warn)}.logbox .l10{color:var(--muted)}
"""


def _get(config, field):
    node = config
    for part in field.split('.'):
        node = node.get(part) if isinstance(node, dict) else None
    return node


def _parse(raw, label, lo, hi, cast):
    try:
        value = cast(str(raw).strip().replace(',', '.'))
    except ValueError:
        raise ValueError(f'{label}: Zahl angeben')
    if not lo <= value <= hi:
        raise ValueError(f'{label}: {lo}–{hi}')
    return value


def _size(path) -> str:
    try:
        n = os.path.getsize(path)
    except OSError:
        return '–'
    return f'{n / 1024 / 1024:.2f} MB' if n >= 1024 * 1024 else f'{n / 1024:.0f} KB'


def _uptime() -> str:
    s = int(time.time() - logs.STARTED)
    return f'{s // 86400} d {s % 86400 // 3600} h {s % 3600 // 60} min'


def make_backup(data_dir: str) -> bytes:
    """ZIP der Daten. Die Datenbank über die SQLite-Backup-API — eine bloße
    Dateikopie wäre bei laufendem Schreiben (WAL) inkonsistent."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for name in BACKUP_FILES:
            path = os.path.join(data_dir, name)
            if not os.path.exists(path):
                continue
            if name == 'sessions.db':
                with tempfile.TemporaryDirectory() as tmp:
                    copy = os.path.join(tmp, name)
                    src, dst = sqlite3.connect(path), sqlite3.connect(copy)
                    try:
                        src.backup(dst)
                    finally:
                        src.close()
                        dst.close()
                    z.write(copy, name)
            else:
                z.write(path, name)
    return buf.getvalue()


def check_backup(data: bytes) -> dict:
    """Prüft ein hochgeladenes Backup → {name: bytes}. ValueError mit Klartext-Grund."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError('keine ZIP-Datei')
    files = {}
    for info in z.infolist():
        if info.filename not in BACKUP_FILES:
            raise ValueError(f'unerwartete Datei im Backup: {info.filename}')
        if info.file_size > _MAX_RESTORE_BYTES:
            raise ValueError(f'{info.filename} ist zu groß')
        files[info.filename] = z.read(info)
    if 'options.json' not in files:
        raise ValueError('options.json fehlt – das ist kein ExpenseCharge-Backup')
    try:
        if not isinstance(json.loads(files['options.json']), dict):
            raise ValueError
    except ValueError:
        raise ValueError('options.json ist kein gültiges JSON-Objekt')
    if 'admin.json' in files:
        try:
            acc = json.loads(files['admin.json'])
            if not (acc.get('username') and acc.get('password')):
                raise ValueError
        except (ValueError, AttributeError):
            raise ValueError('admin.json ist beschädigt')
        if 'secret.key' not in files:
            raise ValueError('admin.json ohne secret.key')
    if 'sessions.db' in files:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'check.db')
            with open(path, 'wb') as f:
                f.write(files['sessions.db'])
            try:
                conn = sqlite3.connect(path)
                ok = conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
                has = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions'").fetchone()
                conn.close()
            except sqlite3.DatabaseError:
                ok, has = False, None
            if not (ok and has):
                raise ValueError('sessions.db ist beschädigt oder keine ExpenseCharge-Datenbank')
    return files


def restore_files(data_dir: str, files: dict) -> str:
    """Sichert den jetzigen Stand und schreibt das Backup zurück. → Name der Sicherung."""
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    keep = f'backup-vor-wiederherstellung-{stamp}.zip'
    fd = os.open(os.path.join(data_dir, keep), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(make_backup(data_dir))
    for name, content in files.items():
        path = os.path.join(data_dir, name)
        tmp = path + '.restore'
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        if name == 'sessions.db':
            # ponytail: alte WAL-Dateien weg, sonst spielt SQLite sie in die neue
            # Datenbank ein. Danach startet der Prozess sofort neu.
            for suffix in ('-wal', '-shm'):
                try:
                    os.remove(path + suffix)
                except FileNotFoundError:
                    pass
        os.replace(tmp, path)
    return keep


def register(app: web.Application, ctx) -> None:
    r = app.router
    buffer = logs.install()

    def page(request, title, body, active='settings'):
        return _page(ctx, request, title, f'<style>{_CSS}</style>' + body, active=active)

    def password_ok(request, form) -> str:
        """'' wenn das Admin-Passwort stimmt, sonst die Fehlermeldung."""
        key = request.remote or 'unbekannt'
        wait = ctx.limiter.locked_for(key)
        if wait:
            return f'Zu viele Fehlversuche – bitte {wait // 60 + 1} Minute(n) warten.'
        if not ctx.accounts.check(request['user'] or '', form.get('password') or ''):
            ctx.limiter.failure(key)
            return 'Admin-Passwort falsch.'
        ctx.limiter.success(key)
        return ''

    # -- Einstellungen ----------------------------------------------------------------
    def settings_body(request, values=None, error='', info=''):
        values = values or {}
        level = values.get('log_level') or ctx.config.get('log_level') or 'INFO'
        inputs = ''
        for field, label, default, lo, hi, _cast in FIELDS:
            current = values.get(field, _get(ctx.config, field))
            inputs += (f'<div><label class="flabel">{_e(label)}</label><input name="{_e(field)}" inputmode="decimal" '
                       f'value="{_e(current if current is not None else "")}" placeholder="{_e(default)}">'
                       f'<div class="hint">{lo}–{hi}, Vorgabe {default}</div></div>')
        recommended = ctx.config.get('ocpp_apply_recommended_config')
        api = ctx.config.get('api') or {}
        state = ctx.api_state or {}
        srv = ctx.ocpp() if ctx.ocpp else None
        counts = ctx.session_manager.session_counts()
        disk = shutil.disk_usage(ctx.data_dir)
        info_rows = [
            ('Version', os.getenv('EXPENSECHARGE_VERSION') or 'dev'),
            ('Laufzeit', _uptime()),
            ('Betriebsart', ctx.config.get('session_source', 'ha_sensors')),
            ('Python', platform.python_version()),
            ('Wallboxen verbunden', f'{len(srv.charge_points) if srv else 0} von '
                                    f'{len(ctx.config.get("ocpp_charge_points") or [])}'),
            ('Dolibarr', f'{api.get("dolibarr_url") or "–"} · '
                         f'{"erreichbar" if state.get("client") else "nicht verbunden"}'),
            ('Ladevorgänge', f'{sum(v for k, v in counts.items() if k != "pending")} gesamt, '
                             f'{counts.get("pending", 0)} ausstehend, {counts.get("incomplete", 0)} unvollständig'),
            ('Datenbank', f'{_size(ctx.session_manager.db_path)} ({ctx.session_manager.db_path})'),
            ('Datenverzeichnis', f'{ctx.data_dir} · frei {disk.free / 1024 ** 3:.1f} GB'),
        ]
        info_html = ''.join(f'<li><b>{_e(k)}</b> {_e(v)}</li>' for k, v in info_rows)
        return (f"""{_msg('err', error)}{_msg('ok', info)}{_restart_box(ctx)}
{_env_warning(['log_level'] + [f for f, *_ in FIELDS])}
<form method="POST" action="/settings">
  <div class="grid2">
    <div><label class="flabel">Protokoll-Detailgrad</label><select name="log_level">{''.join(
        f'<option{" selected" if lv == level else ""}>{lv}</option>' for lv in VALID_LOG_LEVELS)}</select>
      <div class="hint">wirkt sofort</div></div>
    {inputs}
  </div>
  <label class="hint"><input type="checkbox" name="ocpp_apply_recommended_config" value="1"
    {'checked' if recommended else ''}> Empfohlene OCPP-Einstellungen nach jedem Wallbox-Start setzen</label>
  <div class="hint">Alles außer dem Detailgrad wirkt nach einem Neustart. Leere Felder = Vorgabe.
  Ports und Bind-Adressen stehen in der <code>.env</code>.</div>
  <button class="btn-save" type="submit">Speichern</button>
</form>
<div class="row2"><a class="btn-2nd" href="/setup/2">Dolibarr-Zugang</a><a class="btn-2nd" href="/wallboxes">Wallboxen</a>
<a class="btn-2nd" href="/setup">Einrichtungsassistent</a></div>
</div><div class="card"><div class="card-title">Admin-Passwort</div>
<form method="POST" action="/settings/password">
  <label class="flabel">Aktuelles Passwort</label><input name="password" type="password" autocomplete="current-password" required>
  <label class="flabel">Neues Passwort (mind. {validate.MIN_ADMIN_PASSWORD} Zeichen)</label>
  <input name="new" type="password" autocomplete="new-password" required>
  <label class="flabel">Neues Passwort wiederholen</label><input name="new2" type="password" autocomplete="new-password" required>
  <button class="btn-2nd" type="submit">Passwort ändern</button>
  <div class="hint">Meldet alle anderen Sitzungen ab.</div>
</form>
</div><div class="card"><div class="card-title">Backup</div>
<p class="hint">Enthält Konfiguration (mit Dolibarr-Token und Wallbox-Passwörtern im Klartext), alle
Ladevorgänge, Admin-Konto und Änderungsprotokoll – sicher aufbewahren.</p>
<form method="POST" action="/settings/backup">
  <label class="flabel">Admin-Passwort</label><input name="password" type="password" autocomplete="current-password" required>
  <button class="btn-2nd" type="submit">Backup herunterladen</button>
</form>
<label class="flabel" style="margin-top:14px">Wiederherstellen</label>
<form method="POST" action="/settings/restore?_csrf={_e(request['csrf'])}" enctype="multipart/form-data"
  onsubmit="return confirm('Alle Daten durch das Backup ersetzen und neu starten? Der jetzige Stand wird vorher gesichert.')">
  <input type="file" name="file" accept=".zip,application/zip" required>
  <label class="flabel">Admin-Passwort</label><input name="password" type="password" autocomplete="current-password" required>
  <button class="btn-2nd" type="submit">Wiederherstellen und neu starten</button>
  <div class="hint">Der jetzige Stand landet vorher als <code>backup-vor-wiederherstellung-….zip</code> im
  Datenverzeichnis.</div>
</form>
</div><div class="card"><div class="card-title">Systeminfo</div>
<ul class="checks">{info_html}</ul>
<div class="row2"><a class="btn-2nd" href="/logs">System-Log</a><a class="btn-2nd" href="/system">Diagnose</a></div>""")

    async def settings_page(request):
        return page(request, 'Einstellungen', settings_body(request))

    async def settings_post(request):
        form = await request.post()
        changes = {}
        try:
            level = form.get('log_level', 'INFO')
            if level not in VALID_LOG_LEVELS:
                raise ValueError('Protokoll-Detailgrad: ungültig')
            changes['log_level'] = level
            stored = ctx.store.load()
            for field, label, default, lo, hi, cast in FIELDS:
                raw = (form.get(field) or '').strip()
                if raw:
                    changes[field] = _parse(raw, label, lo, hi, cast)
                elif _get(stored, field) is not None:
                    changes[field] = default   # leer = zurück zur Vorgabe
            changes['ocpp_apply_recommended_config'] = bool(form.get('ocpp_apply_recommended_config'))
        except ValueError as exc:
            return page(request, 'Einstellungen', settings_body(request, dict(form), error=_e(exc)))
        diff = ctx.store.update(changes, ctx.config)
        ctx.audit.record_diff(request['user'], diff)
        if any(f == 'log_level' for f, *_ in diff):
            logging.getLogger().setLevel(getattr(logging, level))
            _LOGGER.info("Protokoll-Detailgrad aus der Oberfläche: %s", level)
        if any(f != 'log_level' for f, *_ in diff):
            ctx.restart_reasons.add(_RESTART)
        return page(request, 'Einstellungen', settings_body(
            request, info='Gespeichert.' if diff else 'Keine Änderung.'))

    async def password_post(request):
        form = await request.post()
        error = password_ok(request, form)
        if not error:
            try:
                new = validate.admin_password(form.get('new'), form.get('new2'))
            except ValueError as exc:
                error = str(exc)
        if error:
            return page(request, 'Einstellungen', settings_body(request, error=_e(error)))
        ctx.accounts.change_password(new)
        ctx.audit.record(request['user'], 'admin_passwort', None, None, 'geändert')
        response = page(request, 'Einstellungen', settings_body(request, info='Passwort geändert.'))
        _login_cookie(response, ctx, request)   # diese Sitzung bleibt angemeldet
        return response

    async def backup(request):
        form = await request.post()
        error = password_ok(request, form)
        if error:
            return page(request, 'Einstellungen', settings_body(request, error=_e(error)))
        ctx.audit.record(request['user'], 'backup', None, None, 'heruntergeladen')
        name = f'expensecharge-backup-{datetime.now().strftime("%Y%m%d-%H%M")}.zip'
        return web.Response(body=make_backup(ctx.data_dir), content_type='application/zip',
                            headers={'Content-Disposition': f'attachment; filename="{name}"',
                                     'Cache-Control': 'no-store'})

    async def restore(request):
        form = await request.post()
        error = password_ok(request, form)
        upload = form.get('file')
        if not error and not hasattr(upload, 'file'):
            error = 'Keine Datei ausgewählt.'
        if not error:
            try:
                files = check_backup(upload.file.read(_MAX_RESTORE_BYTES + 1))
            except ValueError as exc:
                error = f'Backup nicht verwendbar: {exc}'
        if error:
            return page(request, 'Einstellungen', settings_body(request, error=_e(error)))
        keep = restore_files(ctx.data_dir, files)
        ctx.audit.record(request['user'], 'wiederherstellung', None, ', '.join(sorted(files)),
                         f'vorheriger Stand: {keep}')
        _LOGGER.warning("Backup wiederhergestellt (%s) — Neustart", ', '.join(sorted(files)))
        if ctx.restart:
            asyncio.get_running_loop().call_later(1.0, ctx.restart)
        body = ('<p>Wiederhergestellt. Neustart läuft … die Seite lädt in 15 Sekunden neu'
                + (' – danach mit dem Konto aus dem Backup anmelden.' if 'admin.json' in files else '.') +
                '</p><meta http-equiv="refresh" content="15;url=/">')
        return page(request, 'Wiederherstellung', body)

    # -- System-Log -------------------------------------------------------------------
    def log_lines(request):
        level = request.query.get('level', 'INFO')
        minimum = getattr(logging, level, logging.INFO) if level in VALID_LOG_LEVELS else logging.INFO
        query = request.query.get('q', '').strip().lower()
        return level, query, [(lv, text) for lv, text in buffer.lines
                              if lv >= minimum and (not query or query in text.lower())]

    async def logs_page(request):
        level, query, lines = log_lines(request)
        opts = ''.join(f'<option{" selected" if lv == level else ""}>{lv}</option>' for lv in VALID_LOG_LEVELS)
        shown = lines[-500:][::-1]
        text = '\n'.join(f'<span class="l{lv}">{_e(t)}</span>' for lv, t in shown)
        body = ('<div class="tabs"><a href="/audit">Änderungen</a><a class="on" href="/logs">System-Log</a></div>'
                '<form method="GET" action="/logs" class="row2">'
                f'<div><label class="flabel">ab Stufe</label><select name="level">{opts}</select></div>'
                f'<div><label class="flabel">Suche</label><input name="q" value="{_e(query)}"></div>'
                '<button class="btn-2nd" type="submit">Anzeigen</button>'
                f'<a class="btn-2nd" href="/logs.txt?level={_e(level)}&q={_e(query)}">Herunterladen</a></form>'
                f'<div class="hint">{len(lines)} Zeile(n) seit dem Start (höchstens {logs.MAX_LINES} im Speicher), '
                'neueste oben, angezeigt die letzten 500. Karten-IDs stehen nie im Log.</div>'
                f'<div class="logbox">{text or "Keine Zeilen."}</div>')
        return page(request, 'Protokoll', body, active='audit')

    async def logs_txt(request):
        _, _, lines = log_lines(request)
        return web.Response(text='\n'.join(t for _, t in lines) + '\n', content_type='text/plain',
                            charset='utf-8', headers={
                                'Content-Disposition': 'attachment; filename="expensecharge.log"'})

    r.add_get('/settings', settings_page)
    r.add_post('/settings', settings_post)
    r.add_post('/settings/password', password_post)
    r.add_post('/settings/backup', backup)
    r.add_post('/settings/restore', restore)
    r.add_get('/logs', logs_page)
    r.add_get('/logs.txt', logs_txt)
