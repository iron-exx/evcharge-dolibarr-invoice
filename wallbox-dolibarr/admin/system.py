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
import re
import secrets
import zipfile
from datetime import datetime

import requests
from aiohttp import web

from app_settings import VALID_LOG_LEVELS

from . import logs, validate
from .store import mask
from .web import _e, _env_warning, _login_cookie, _msg, _page, _restart_box

_LOGGER = logging.getLogger(__name__)

# options.json-Feld → (Bezeichnung, Vorgabe, min, max, Typ). Ports und
# Bind-Adressen fehlen bewusst: die hängen an der Portfreigabe in der
# docker-compose.yml/.env — hier geändert, wäre die Oberfläche weg.
FIELDS = (
    ('tax_flat_price', 'Strompreis-Pauschale Ladenachweis (€/kWh, 0 = tatsächl. Kosten)', 0.0, 0.0, 2.0, float),
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
ROLE_LABELS = {'buchhaltung': 'Buchhaltung', 'mitarbeiter': 'Mitarbeiter'}
_HOT = ('log_level', 'tax_flat_price')    # wirken ohne Neustart
# Was im Alltag jemand ändert — der Rest steht unter „Erweitert“
COMMON_FIELDS = ('tax_flat_price', 'api.transmit_interval', 'min_session_kwh', 'max_session_hours')
BACKUP_FILES = ('options.json', 'sessions.db', 'admin.json', 'users.json', 'secret.key', 'audit.log')
_MAX_RESTORE_BYTES = 200 * 1024 * 1024

_CSS = """
details.adv>summary{cursor:pointer;padding:10px 0;color:var(--muted);font-size:13px;font-weight:600}
input[type=checkbox]{width:16px;height:16px;padding:0;vertical-align:middle;margin-right:6px}
.inline{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.inline button{padding:5px 10px;border-radius:6px;border:1.5px solid var(--border);background:var(--surface2);
  cursor:pointer;font-size:12px}
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


DAILY_KEEP = 14
_CONFIG_YAML = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.yaml')
LATEST_URL = ('https://raw.githubusercontent.com/systemwerk-GmbH-Co-KG/ExpenseCharge/main/'
              'wallbox-dolibarr/config.yaml')
_VERSION = re.compile(r'^version:\s*"?([0-9][0-9A-Za-z.\-]*)"?', re.M)


def app_version() -> str:
    """Die Addon-Version aus config.yaml (liegt im Image neben dem Code); Build-Argument hat Vorrang."""
    env = os.getenv('EXPENSECHARGE_VERSION')
    if env and env != 'dev':
        return env
    try:
        with open(_CONFIG_YAML) as f:
            m = _VERSION.search(f.read())
        return m.group(1) if m else 'dev'
    except OSError:
        return 'dev'


def newer(candidate: str, current: str) -> bool:
    def parts(v):
        return tuple(int(x) for x in v.split('.')[:3])
    try:
        return parts(candidate) > parts(current)
    except (ValueError, AttributeError):
        return False


def fetch_latest_version():
    """Version im main-Branch auf GitHub, oder None."""
    try:
        r = requests.get(LATEST_URL, timeout=10)
        r.raise_for_status()
        m = _VERSION.search(r.text)
        return m.group(1) if m else None
    except Exception:
        return None


async def update_check_loop(ctx, interval: float = 24 * 3600) -> None:
    """Einmal am Tag nachsehen, ob es eine neuere Version gibt (abschaltbar: update_check: false)."""
    while True:
        if ctx.config.get('update_check', True):
            ctx.latest_version = await asyncio.to_thread(fetch_latest_version) or ctx.latest_version
        await asyncio.sleep(interval)




def _backup_dir(data_dir: str) -> str:
    return os.path.join(data_dir, 'backups')


def daily_backup(data_dir: str, today=None, keep: int = DAILY_KEEP):
    """Ein Backup pro Tag nach <data>/backups/, die ältesten über `keep` weg.
    → Pfad des neuen Backups, oder None, wenn es heute schon eins gibt."""
    today = today or datetime.now().date()
    folder = _backup_dir(data_dir)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    path = os.path.join(folder, f'expensecharge-{today:%Y%m%d}.zip')
    if os.path.exists(path):
        return None
    data = make_backup(data_dir)
    tmp = path + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # enthält Token und Passwörter
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)
    for old in sorted(n for n in os.listdir(folder) if n.startswith('expensecharge-') and n.endswith('.zip'))[:-keep]:
        os.remove(os.path.join(folder, old))
    return path


def backup_status(data_dir: str) -> dict:
    folder = _backup_dir(data_dir)
    try:
        names = sorted(n for n in os.listdir(folder) if n.startswith('expensecharge-') and n.endswith('.zip'))
    except FileNotFoundError:
        names = []
    return {'count': len(names), 'latest': names[-1] if names else None, 'folder': folder}


async def backup_loop(data_dir: str, interval: float = 3600) -> None:
    """Stündlich prüfen, ob das Backup von heute fehlt — übersteht Neustarts und Zeitsprünge."""
    while True:
        try:
            path = await asyncio.to_thread(daily_backup, data_dir)
            if path:
                _LOGGER.info("Automatisches Backup: %s", path)
        except Exception as exc:   # ein volles Laufwerk o.ä. darf den Betrieb nicht stören
            _LOGGER.error("Automatisches Backup fehlgeschlagen: %s", exc)
        await asyncio.sleep(interval)


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
    def settings_body(request, values=None, error='', info='', reveal=None):
        values = values or {}
        level = values.get('log_level') or ctx.config.get('log_level') or 'INFO'
        inputs = advanced = ''
        for field, label, default, lo, hi, _cast in FIELDS:
            current = values.get(field, _get(ctx.config, field))
            html_ = (f'<div><label class="flabel">{_e(label)}</label><input name="{_e(field)}" inputmode="decimal" '
                       f'value="{_e(current if current is not None else "")}" placeholder="{_e(default)}">'
                       f'<div class="hint">{lo}–{hi}, Vorgabe {default}</div></div>')
            if field in COMMON_FIELDS:
                inputs += html_
            else:
                advanced += html_
        recommended = ctx.config.get('ocpp_apply_recommended_config')
        api = ctx.config.get('api') or {}
        state = ctx.api_state or {}
        srv = ctx.ocpp() if ctx.ocpp else None
        counts = ctx.session_manager.session_counts()
        disk = shutil.disk_usage(ctx.data_dir)
        info_rows = [
            ('Version', app_version() + ('' if not ctx.latest_version else
                                         ' · aktuell' if not newer(ctx.latest_version, app_version()) else
                                         f' · Update verfügbar: {ctx.latest_version}')),
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
        notify_form = notify_body(values if values.get('_form') == 'notify' else {})
        users_html = users_body(reveal)
        bs = backup_status(ctx.data_dir)
        auto_backup = (f'<p class="hint"><b>Automatisch:</b> jede Nacht ein Backup nach <code>{_e(bs["folder"])}</code>, '
                       f'die letzten {DAILY_KEEP} bleiben. Zurzeit {bs["count"]}'
                       + (f', zuletzt <code>{_e(bs["latest"])}</code>' if bs['latest'] else '') +
                       '. Liegt auf demselben Laufwerk – zusätzlich ab und zu eins herunterladen und woanders '
                       'ablegen.</p>')
        info_html = ''.join(f'<li><b>{_e(k)}</b> {_e(v)}</li>' for k, v in info_rows)
        update = (_msg('warn', f'Update verfügbar: {_e(ctx.latest_version)} (installiert {_e(app_version())}). '
                               'Einspielen auf dem Server: <code>cd /opt/ExpenseCharge &amp;&amp; git pull &amp;&amp; '
                               'cd wallbox-dolibarr &amp;&amp; docker compose up -d --build --force-recreate</code> – '
                               'vorher ein Backup herunterladen.')
                  if ctx.latest_version and newer(ctx.latest_version, app_version()) else '')
        return (f"""{_msg('err', error)}{_msg('ok', info)}{update}{_restart_box(ctx)}
{_env_warning(['log_level'] + [f for f, *_ in FIELDS])}
<form method="POST" action="/settings">
  <div class="grid2">{inputs}</div>
  <details class="adv"><summary>Erweitert – Feineinstellungen, meist nicht nötig</summary><div class="grid2">
    <div><label class="flabel">Protokoll-Detailgrad</label><select name="log_level">{''.join(
        f'<option{" selected" if lv == level else ""}>{lv}</option>' for lv in VALID_LOG_LEVELS)}</select>
      <div class="hint">wirkt sofort</div></div>
    {advanced}
  </div></details>
  <label class="hint"><input type="checkbox" name="ocpp_apply_recommended_config" value="1"
    {'checked' if recommended else ''}> Empfohlene OCPP-Einstellungen nach jedem Wallbox-Start setzen</label>
  <div class="hint">Detailgrad und Strompreis-Pauschale wirken sofort, der Rest nach einem Neustart. Leere Felder = Vorgabe.
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
</div><div class="card"><div class="card-title">Benutzer</div>
{users_html}
</div><div class="card"><div class="card-title">Benachrichtigungen</div>
{notify_form}
</div><div class="card"><div class="card-title">Backup</div>
<p class="hint">Enthält Konfiguration (mit Dolibarr-Token und Wallbox-Passwörtern im Klartext), alle
Ladevorgänge, Admin-Konto und Änderungsprotokoll – sicher aufbewahren.</p>
{auto_backup}
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

    def notify_body(values):
        from . import notify   # hier: notify nutzt selbst backup_status aus diesem Modul
        cfg = notify.settings(ctx.config)
        v = lambda k: values.get(k, cfg.get(k, ''))
        pw_hint = (f'Gespeichert: {_e(mask(cfg.get("smtp_password")))} – leer lassen, um es zu behalten.'
                   if cfg.get('smtp_password') else '')
        tls = v('smtp_tls') or 'starttls'
        return f"""<p class="hint">Meldet einmal, wenn etwas liegen bleibt: Ladung von Dolibarr abgelehnt oder
unvollständig, Ladungen hängen in der Warteschlange, eine Wallbox ist offline, kein aktuelles Backup.
Behobene Probleme melden sich beim nächsten Auftreten wieder.</p>
{_env_warning(['notify.email_to', 'notify.smtp_password', 'notify.webhook_url'])}
<form method="POST" action="/settings/notify">
  <div class="grid2">
    <div><label class="flabel">E-Mail an</label><input name="email_to" value="{_e(v('email_to'))}" placeholder="fuhrpark@firma.de"></div>
    <div><label class="flabel">SMTP-Server</label><input name="smtp_host" value="{_e(v('smtp_host'))}" placeholder="mail.firma.de"></div>
    <div><label class="flabel">Port</label><input name="smtp_port" inputmode="numeric" value="{_e(v('smtp_port'))}"></div>
    <div><label class="flabel">Verschlüsselung</label><select name="smtp_tls">{''.join(
        f'<option value="{m}"{" selected" if m == tls else ""}>{n}</option>'
        for m, n in (('starttls', 'STARTTLS (587)'), ('ssl', 'SSL/TLS (465)'), ('none', 'keine')))}</select></div>
    <div><label class="flabel">SMTP-Benutzer</label><input name="smtp_user" value="{_e(v('smtp_user'))}" autocomplete="off"></div>
    <div><label class="flabel">SMTP-Passwort</label><input name="smtp_password" type="password" autocomplete="new-password">
      <div class="hint">{pw_hint}</div></div>
    <div><label class="flabel">Absender</label><input name="smtp_from" value="{_e(v('smtp_from'))}" placeholder="expensecharge@firma.de"></div>
    <div><label class="flabel">Webhook-URL (optional)</label><input name="webhook_url" value="{_e(v('webhook_url'))}" placeholder="https://n8n.firma.de/webhook/…">
      <div class="hint">POST mit JSON <code>{{"title", "text", "alerts"}}</code> – z.B. n8n, ntfy, Home Assistant</div></div>
    <div><label class="flabel">Wallbox offline melden nach (h)</label><input name="offline_hours" inputmode="numeric" value="{_e(v('offline_hours'))}"></div>
    <div><label class="flabel">Warteschlange melden nach (h)</label><input name="pending_hours" inputmode="numeric" value="{_e(v('pending_hours'))}"></div>
  </div>
  <div class="row2">
    <button class="btn-2nd" type="submit" name="action" value="test">Test senden</button>
    <button class="btn-save" type="submit" name="action" value="save">Speichern</button>
  </div>
</form>"""

    def users_body(reveal=None):
        tags = {t['rfid_hash'][:16]: t for t in ctx.session_manager.list_tags() if t.get('mode') != 'unknown'}
        names = lambda cards: ', '.join((tags.get(c) or {}).get('label') or f'Karte {c[:8]}…' for c in cards) or '–'
        rows = ''
        for u in ctx.accounts.list_users():
            name = _e(u['username'])
            rows += (f'<tr><td><b>{name}</b></td><td>{_e(ROLE_LABELS[u["role"]])}</td>'
                     f'<td>{_e(names(u.get("cards") or [])) if u["role"] == "mitarbeiter" else "alle"}</td>'
                     '<td class="inline"><form method="POST" action="/settings/users">'
                     f'<input type="hidden" name="username" value="{name}"><button name="action" value="reset" '
                     'type="submit">Neues Passwort</button></form><form method="POST" action="/settings/users" '
                     f'onsubmit="return confirm(\'Benutzer {name} löschen?\')"><input type="hidden" name="username" '
                     f'value="{name}"><button name="action" value="delete" type="submit">Löschen</button></form></td></tr>')
        shown = ''
        if reveal:
            shown = _msg('ok', f'Passwort für <b>{_e(reveal[0])}</b> – nur jetzt sichtbar, gleich weitergeben:'
                               f'<div class="copy"><input id="newpw" readonly value="{_e(reveal[1])}"><button '
                               'type="button" onclick="ecCopy(\'newpw\')">Kopieren</button></div>')
        boxes = ''.join(f'<label class="hint" style="display:block"><input type="checkbox" name="cards" value="{_e(k)}"> '
                        f'{_e(t.get("label") or "(ohne Namen)")} <span class="mono">{_e(k[:8])}…</span></label>'
                        for k, t in sorted(tags.items(), key=lambda kv: (kv[1].get('label') or '').lower()))
        return (shown + '<p class="hint"><b>Buchhaltung</b> sieht alle Ladevorgänge und Ladenachweise und kann '
                'exportieren, aber nichts ändern. <b>Mitarbeiter</b> sehen nur die Ladungen ihrer Karte(n) und '
                'ihren eigenen Ladenachweis.</p>' +
                (f'<div class="tbl-wrap"><table><tr><th>Benutzer</th><th>Rolle</th><th>Karten</th><th></th></tr>'
                 f'{rows}</table></div>' if rows else '') +
                '<form method="POST" action="/settings/users"><div class="grid2">'
                '<div><label class="flabel">Benutzername</label><input name="username" required '
                'placeholder="z.B. mmueller" autocomplete="off"></div>'
                '<div><label class="flabel">Rolle</label><select name="role"><option value="mitarbeiter">Mitarbeiter'
                '</option><option value="buchhaltung">Buchhaltung</option></select></div></div>'
                '<label class="flabel">Karten (nur für Mitarbeiter)</label>' +
                (boxes or '<p class="hint">Noch keine Karten eingetragen – erst unter „Karten“ anlegen.</p>') +
                '<div class="hint">Das Passwort wird erzeugt und einmal angezeigt.</div>'
                '<button class="btn-2nd" type="submit" name="action" value="add">Benutzer anlegen</button></form>')

    async def users_post(request):
        form = await request.post()
        action, name = form.get('action'), (form.get('username') or '').strip()
        try:
            if action == 'add':
                name = validate.username(name)
                role = form.get('role') if form.get('role') in ROLE_LABELS else 'mitarbeiter'
                cards = [c for c in form.getall('cards', []) if len(c) == 16] if role == 'mitarbeiter' else []
                pw = secrets.token_urlsafe(12)
                ctx.accounts.add_user(name, pw, role, cards)
                ctx.audit.record(request['user'], 'benutzer', None, f'{name} ({ROLE_LABELS[role]})', 'angelegt')
                return page(request, 'Einstellungen', settings_body(request, info='Benutzer angelegt.',
                                                                    reveal=(name, pw)))
            if ctx.accounts.role(name) not in ROLE_LABELS:
                raise ValueError('Benutzer nicht gefunden')
            if action == 'reset':
                pw = secrets.token_urlsafe(12)
                ctx.accounts.change_password(pw, name)
                ctx.audit.record(request['user'], 'benutzer', None, name, 'Passwort zurückgesetzt')
                return page(request, 'Einstellungen', settings_body(request, reveal=(name, pw)))
            if action == 'delete':
                ctx.accounts.remove_user(name)
                ctx.audit.record(request['user'], 'benutzer', name, None, 'gelöscht')
                return page(request, 'Einstellungen', settings_body(request, info=f'Benutzer {_e(name)} gelöscht.'))
        except ValueError as exc:
            return page(request, 'Einstellungen', settings_body(request, error=_e(exc)))
        raise web.HTTPBadRequest(text='Unbekannte Aktion')

    async def notify_post(request):
        from . import notify   # hier: notify nutzt selbst backup_status aus diesem Modul
        form = await request.post()
        stored = notify.settings(ctx.config)
        try:
            new = {}
            for key in ('email_to', 'smtp_host', 'smtp_user', 'smtp_from', 'webhook_url'):
                new[key] = (form.get(key) or '').strip()
                if len(new[key]) > 200 or not new[key].isprintable():
                    raise ValueError(f'{key}: höchstens 200 druckbare Zeichen')
            for key in ('email_to', 'smtp_from'):
                if new[key] and ('@' not in new[key] or ' ' in new[key]):
                    raise ValueError(f'{"E-Mail an" if key == "email_to" else "Absender"}: keine gültige Adresse')
            if new['email_to'] and not new['smtp_host']:
                raise ValueError('Für E-Mail wird ein SMTP-Server gebraucht')
            if new['webhook_url'] and not new['webhook_url'].startswith(('http://', 'https://')):
                raise ValueError('Webhook-URL: mit http:// oder https:// angeben')
            new['smtp_port'] = _parse(form.get('smtp_port') or 587, 'Port', 1, 65535, int)
            new['smtp_tls'] = form.get('smtp_tls') if form.get('smtp_tls') in notify.TLS_MODES else 'starttls'
            new['offline_hours'] = _parse(form.get('offline_hours') or 2, 'Wallbox offline', 1, 168, int)
            new['pending_hours'] = _parse(form.get('pending_hours') or 6, 'Warteschlange', 1, 168, int)
            new['smtp_password'] = form.get('smtp_password') or stored.get('smtp_password') or ''
        except ValueError as exc:
            return page(request, 'Einstellungen', settings_body(request, dict(form) | {'_form': 'notify'},
                                                                error=_e(exc)))
        if form.get('action') == 'test':
            errors = await asyncio.to_thread(notify.send, new, 'ExpenseCharge: Testnachricht',
                                             ['Benachrichtigungen funktionieren.'])
            msg = dict(error=_e(' · '.join(errors))) if errors else dict(info='Testnachricht verschickt.')
            return page(request, 'Einstellungen', settings_body(request, dict(form) | {'_form': 'notify'}, **msg))
        diff = ctx.store.update({f'notify.{k}': v for k, v in new.items()}, ctx.config)
        ctx.audit.record_diff(request['user'], diff)
        return page(request, 'Einstellungen', settings_body(
            request, info='Benachrichtigungen gespeichert – wirken sofort.' if diff else 'Keine Änderung.'))

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
            recommended = bool(form.get('ocpp_apply_recommended_config'))
            if recommended != bool(ctx.config.get('ocpp_apply_recommended_config')):   # fehlend = aus
                changes['ocpp_apply_recommended_config'] = recommended
        except ValueError as exc:
            return page(request, 'Einstellungen', settings_body(request, dict(form), error=_e(exc)))
        diff = ctx.store.update(changes, ctx.config)
        ctx.audit.record_diff(request['user'], diff)
        if any(f == 'log_level' for f, *_ in diff):
            logging.getLogger().setLevel(getattr(logging, level))
            _LOGGER.info("Protokoll-Detailgrad aus der Oberfläche: %s", level)
        if any(f not in _HOT for f, *_ in diff):
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
    r.add_post('/settings/notify', notify_post)
    r.add_post('/settings/users', users_post)
    r.add_post('/settings/backup', backup)
    r.add_post('/settings/restore', restore)
    r.add_get('/logs', logs_page)
    r.add_get('/logs.txt', logs_txt)
