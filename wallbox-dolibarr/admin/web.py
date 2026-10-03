"""Anmeldung, CSRF, Ersteinrichtungs-Assistent und Änderungsprotokoll (Standalone).

Regeln:
- Ohne Admin-Konto: alles lesbar, schreibend nur der Assistent — und dessen
  erster Schritt verlangt den Einrichtungscode aus dem Container-Log, damit
  nicht irgendwer im Netz sich zuerst zum Admin macht.
- Mit Admin-Konto: jede Seite außer /login und /health nur angemeldet.
- Jeder POST braucht das CSRF-Token (Double-Submit: Cookie + Formularfeld
  bzw. Header X-CSRF-Token). Die Formulare bekommen es automatisch eingesetzt.
"""
import asyncio
import html
import re
import secrets
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import quote

from aiohttp import web

import env_config
from placeholders import find_placeholders

from . import validate
from .dolibarr_check import check_dolibarr
from .security import LoginLimiter, AccountStore, SESSION_SECONDS
from .store import AuditLog, ConfigStore, mask

SESSION_COOKIE = 'ec_session'
CSRF_COOKIE = 'ec_csrf'
_PUBLIC = ('/health', '/login')
_MAX_FORM = 1024 * 1024
MAX_UPLOAD = 256 * 1024 * 1024     # Wiederherstellung eines Backups
_FORM_POST = re.compile(r'(<form\b[^>]*\bmethod=["\']?post["\']?[^>]*>)', re.IGNORECASE)

_CSS = """
.steps{display:flex;gap:6px;margin-bottom:14px;flex-wrap:wrap}
.steps span{flex:1;min-width:52px;text-align:center;font-size:11px;padding:6px 4px;border-radius:6px;
  background:var(--surface2);color:var(--muted);border:1px solid var(--border)}
.steps span.on{background:var(--primary-d);color:var(--primary-i);border-color:var(--primary-d)}
.steps span.done{color:var(--success);border-color:rgba(0,135,86,.35)}
textarea{width:100%;min-height:110px;padding:10px 12px;background:var(--bg);border:1.5px solid var(--border);
  border-radius:7px;font:13px ui-monospace,monospace;color:var(--text)}
.btn-2nd{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:10px 14px;margin-top:12px;
  background:var(--surface);color:var(--primary-d);border:1.5px solid var(--primary-d);border-radius:8px;
  font-size:14px;font-weight:600;cursor:pointer;text-decoration:none}
.row2{display:flex;gap:10px;flex-wrap:wrap}.row2>*{flex:1;min-width:140px}
.checks{list-style:none;padding:0;margin:12px 0 0}
.checks li{padding:7px 0;border-bottom:1px solid var(--border);font-size:13px}
.checks b{display:inline-block;min-width:92px}
.copy{display:flex;gap:6px;margin:4px 0 10px}.copy input{font-family:ui-monospace,monospace}
.copy button{padding:0 12px;border-radius:7px;border:1.5px solid var(--border);background:var(--surface2);cursor:pointer}
.hint{font-size:12px;color:var(--muted);margin:6px 0 4px;line-height:1.5}
.audit td{font-size:12px;vertical-align:top}
.tabs{display:flex;gap:6px;margin-bottom:12px}.tabs a{padding:6px 12px;border-radius:7px;border:1px solid var(--border);
  text-decoration:none;color:var(--text);font-size:13px}.tabs a.on{background:var(--primary-d);color:#fff;border-color:var(--primary-d)}
"""

_COPY_JS = """<script>
function ecCopy(id){var el=document.getElementById(id);
 (navigator.clipboard?navigator.clipboard.writeText(el.value):Promise.reject())
 .catch(function(){el.select();document.execCommand&&document.execCommand('copy');});}
</script>"""


@dataclass
class AdminContext:
    data_dir: str
    config: dict                       # wirksame Konfiguration (wird bei Änderungen nachgeführt)
    session_manager: object
    render: Callable = None            # (active, content, base_href) → HTML (setzt create_app)
    ocpp_port: int = 9000
    setup_code: Optional[str] = None   # nur solange kein Admin-Konto existiert
    reload_dolibarr: Callable = None   # (url, token) → None
    reload_ocpp: Callable = None       # () → bool (True = ohne Neustart übernommen)
    restart: Callable = None           # () → None, Prozess beendet sich, Docker startet neu
    ocpp: Callable = None              # () → laufender OcppServer oder None
    transmit_now: Callable = None      # () → None, stößt die Übertragung an Dolibarr an
    api_state: dict = None             # gemeinsamer Live-Zustand (setzt create_app)
    accounts: AccountStore = None
    store: ConfigStore = None
    audit: AuditLog = None
    limiter: LoginLimiter = field(default_factory=LoginLimiter)
    reveal: dict = field(default_factory=dict)          # cp_id → Passwort, einmalig anzeigen
    restart_reasons: set = field(default_factory=set)
    flash: dict = field(default_factory=dict)            # cp_id → Meldung für die nächste Detailseite
    cp_config: dict = field(default_factory=dict)        # cp_id → zuletzt gelesene Wallbox-Konfiguration

    def __post_init__(self):
        self.accounts = self.accounts or AccountStore(self.data_dir)
        self.store = self.store or ConfigStore(self.data_dir)
        self.audit = self.audit or AuditLog(self.data_dir)


def new_setup_code() -> str:
    n = secrets.randbelow(10 ** 8)
    return f'{n // 10000:04d}-{n % 10000:04d}'


def _e(value) -> str:
    return html.escape('' if value is None else str(value), quote=True)


def _client_key(request) -> str:
    return request.remote or 'unbekannt'


def _secure(request) -> bool:
    return request.secure or request.headers.get('X-Forwarded-Proto') == 'https'


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

def middleware(ctx: AdminContext):
    @web.middleware
    async def admin_guard(request, handler):
        path = request.path
        csrf = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(24)
        request['csrf'] = csrf
        has_account = ctx.accounts.exists()
        request['user'] = ctx.accounts.session_user(request.cookies.get(SESSION_COOKIE, ''))

        if request.method == 'POST':
            sent = request.headers.get('X-CSRF-Token', '')
            if not sent and request.content_type == 'application/x-www-form-urlencoded':
                # Vor der Anmeldeprüfung gelesen → klein halten (Uploads gehen als multipart)
                if (request.content_length or 0) > _MAX_FORM:
                    return _plain(413, 'Formular zu groß')
                sent = (await request.post()).get('_csrf', '')
            elif not sent and request.content_type == 'multipart/form-data':
                # Datei-Upload: Token in der Formular-URL, damit der Körper erst nach
                # der Anmeldeprüfung gelesen wird
                sent = request.query.get('_csrf', '')
            if not (request.cookies.get(CSRF_COOKIE) and secrets.compare_digest(str(sent).encode(), csrf.encode())):
                return _plain(403, 'Sicherheitsprüfung (CSRF) fehlgeschlagen – Seite neu laden und '
                                   'noch einmal absenden.')

        if path not in _PUBLIC:
            if not has_account:
                if request.method == 'POST' and not path.startswith('/setup/1'):
                    return _plain(403, 'Erst die Ersteinrichtung abschließen (Admin-Konto anlegen) – '
                                       'bis dahin ist die Oberfläche nur lesend.')
                if path == '/' and request.method == 'GET':
                    raise web.HTTPFound('/setup')
            elif not request['user']:
                if request.method == 'GET' and 'json' not in path:
                    raise web.HTTPFound('/login?next=' + quote(str(request.rel_url), safe=''))
                return _plain(401, 'Anmeldung erforderlich')
            elif path == '/' and request.method == 'GET' and _first_open_step(ctx) is not None:
                raise web.HTTPFound(f'/setup/{_first_open_step(ctx)}')

        response = await handler(request)
        if isinstance(response, web.Response) and response.content_type == 'text/html' and response.body:
            response.text = _decorate(response.text, request, ctx, has_account)
        if not request.cookies.get(CSRF_COOKIE) and isinstance(response, web.StreamResponse):
            response.set_cookie(CSRF_COOKIE, csrf, httponly=True, samesite='Strict',
                                secure=_secure(request), path='/')
        return response

    return admin_guard


def _plain(status, text):
    return web.Response(status=status, text=text + '\n', content_type='text/plain', charset='utf-8')


def _decorate(page: str, request, ctx, has_account) -> str:
    """CSRF-Feld in jedes POST-Formular, Konto/Protokoll in Kopf und Navigation."""
    page = _FORM_POST.sub(
        lambda m: m.group(1) + f'<input type="hidden" name="_csrf" value="{_e(request["csrf"])}">', page)
    user = request.get('user')
    if user:
        acct = (f'<form method="POST" action="/logout" class="acct">{_e(user)} · '
                f'<input type="hidden" name="_csrf" value="{_e(request["csrf"])}">'
                '<button type="submit">Abmelden</button></form>')
    elif not has_account:
        acct = '<span class="acct"><a href="/setup">Ersteinrichtung</a></span>'
    else:
        acct = '<span class="acct"><a href="/login">Anmelden</a></span>'
    page = page.replace('<!--ec-account-->', acct)
    extra = ''.join(
        f'<a href="{href}" class="{"active" if request.path.startswith(prefixes) else ""}">{name}</a>'
        for href, prefixes, name in (('/wallboxes', '/wallbox', 'Wallboxen'), ('/sessions', '/sessions', 'Ladevorgänge'),
                                     ('/settings', ('/settings', '/setup'), 'Einstellungen'),
                                     ('/audit', ('/audit', '/logs'), 'Protokoll'))) if user else ''
    return page.replace('<!--ec-nav-extra-->', extra)


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------

def _first_open_step(ctx) -> Optional[int]:
    """Erster Assistenten-Schritt, der wegen fehlender/Platzhalter-Werte offen ist."""
    found = find_placeholders(ctx.config)
    api = ctx.config.get('api') or {}
    if any(f.startswith('api.') for f in found) or not (api.get('dolibarr_url') and api.get('api_token')):
        return 2
    if any(f.startswith('ocpp_') for f in found):
        return 3
    return None


def _env_warning(fields) -> str:
    hits = [env_config.env_name(f) for f in fields if env_config.env_overrides(f)]
    if not hits:
        return ''
    return ('<div class="msg warn">Achtung: ' + ', '.join(_e(h) for h in hits) +
            ' ist als Umgebungsvariable gesetzt und hat Vorrang – die Änderung wirkt erst, '
            'wenn die Variable aus der .env entfernt ist.</div>')


_STEP_NAMES = ('Konto', 'Dolibarr', 'Wallbox', 'Karten', 'Fertig')


def _steps(current: int) -> str:
    return '<div class="steps">' + ''.join(
        f'<span class="{"on" if i == current else "done" if i < current else ""}">{i}. {n}</span>'
        for i, n in enumerate(_STEP_NAMES, 1)) + '</div>'


def _page(ctx, request, title, body, step=None, active='setup'):
    content = (f'<style>{_CSS}</style>' + (_steps(step) if step else '') +
               f'<div class="card"><div class="card-title">{_e(title)}</div>{body}</div>{_COPY_JS}')
    return web.Response(text=ctx.render(active, content, ''), content_type='text/html')


def _msg(kind, text):
    return f'<div class="msg {kind}">{text}</div>' if text else ''


def _ws_url(ctx, request) -> str:
    host = request.url.host or '<host-ip>'
    return f'ws://{host}:{ctx.ocpp_port}/'


def _restart_box(ctx) -> str:
    if not ctx.restart_reasons:
        return ''
    reasons = ', '.join(sorted(ctx.restart_reasons))
    return (f'<div class="msg warn">Neustart nötig, damit das wirkt: {_e(reasons)}.'
            '<form method="POST" action="/restart"><button class="btn-2nd" type="submit">'
            'Übernehmen und neu starten</button></form>'
            '<div class="hint">Der Container startet neu (ca. 10 s, Docker-Neustartrichtlinie '
            '<code>unless-stopped</code>). Laufende Ladevorgänge gehen nicht verloren – '
            'die Wallbox überträgt sie nach dem Wiederverbinden.</div></div>')


def _redirect(location: str) -> web.Response:
    return web.Response(status=302, headers={'Location': location})


def _login_cookie(response, ctx, request):
    response.set_cookie(SESSION_COOKIE, ctx.accounts.issue(), httponly=True, samesite='Strict',
                        secure=_secure(request), path='/', max_age=SESSION_SECONDS)


# ---------------------------------------------------------------------------
# Routen
# ---------------------------------------------------------------------------

def register(app: web.Application, ctx: AdminContext) -> None:
    r = app.router

    async def login_page(request, error=''):
        if not ctx.accounts.exists():
            raise web.HTTPFound('/setup')
        nxt = request.query.get('next', '/')
        body = f"""{_msg('err', error)}
<form method="POST" action="/login">
  <input type="hidden" name="next" value="{_e(nxt)}">
  <label class="flabel">Benutzer</label><input name="username" autocomplete="username" required autofocus>
  <label class="flabel">Passwort</label><input name="password" type="password" autocomplete="current-password" required>
  <button class="btn-save" type="submit">Anmelden</button>
</form>"""
        return _page(ctx, request, 'Anmelden', body, active='login')

    async def login(request):
        form = await request.post()
        key = _client_key(request)
        wait = ctx.limiter.locked_for(key)
        if wait:
            return await login_page(request, f'Zu viele Fehlversuche – bitte {wait // 60 + 1} Minute(n) warten.')
        if not ctx.accounts.check(form.get('username', ''), form.get('password', '')):
            ctx.limiter.failure(key)
            return await login_page(request, 'Benutzer oder Passwort falsch.')
        ctx.limiter.success(key)
        nxt = form.get('next') or '/'
        if not nxt.startswith('/') or nxt.startswith('//'):
            nxt = '/'   # kein Weiterleiten auf fremde Seiten
        response = _redirect(nxt)
        _login_cookie(response, ctx, request)
        return response

    async def logout(request):
        ctx.accounts.revoke_all()
        response = _redirect('/login')
        response.del_cookie(SESSION_COOKIE, path='/')
        return response

    async def setup_index(request):
        if not ctx.accounts.exists():
            raise web.HTTPFound('/setup/1')
        raise web.HTTPFound(f'/setup/{_first_open_step(ctx) or 2}')

    # -- 1: Admin-Konto ------------------------------------------------------------
    async def step1(request, error=''):
        if ctx.accounts.exists():
            raise web.HTTPFound('/setup/2')
        body = f"""{_msg('err', error)}
<p class="hint">Willkommen. Zuerst ein Admin-Konto anlegen – danach sind alle Änderungen nur noch
angemeldet möglich. Den <b>Einrichtungscode</b> zeigt das Container-Log:<br>
<code>docker compose logs expensecharge | grep Einrichtungscode</code></p>
<form method="POST" action="/setup/1">
  <label class="flabel">Einrichtungscode</label><input name="code" inputmode="numeric" placeholder="1234-5678" required>
  <label class="flabel">Benutzername</label><input name="username" value="admin" autocomplete="username" required>
  <label class="flabel">Passwort (mind. {validate.MIN_ADMIN_PASSWORD} Zeichen)</label>
  <input name="password" type="password" autocomplete="new-password" required>
  <label class="flabel">Passwort wiederholen</label><input name="password2" type="password" autocomplete="new-password" required>
  <button class="btn-save" type="submit">Konto anlegen</button>
</form>"""
        return _page(ctx, request, 'Ersteinrichtung – Admin-Konto', body, step=1)

    async def step1_post(request):
        if ctx.accounts.exists():
            raise web.HTTPFound('/setup/2')
        form = await request.post()
        key = _client_key(request)
        wait = ctx.limiter.locked_for(key)
        if wait:
            return await step1(request, f'Zu viele Fehlversuche – bitte {wait // 60 + 1} Minute(n) warten.')
        code = (form.get('code') or '').strip()
        if not ctx.setup_code or not secrets.compare_digest(code.encode(), ctx.setup_code.encode()):
            ctx.limiter.failure(key)
            return await step1(request, 'Einrichtungscode falsch – er steht im Container-Log.')
        try:
            user = validate.username(form.get('username'))
            pw = validate.admin_password(form.get('password'), form.get('password2'))
        except ValueError as exc:
            return await step1(request, _e(exc))
        ctx.limiter.success(key)
        ctx.accounts.create(user, pw)
        ctx.setup_code = None
        ctx.audit.record(user, 'admin_konto', None, user, 'Ersteinrichtung')
        response = _redirect('/setup/2')
        _login_cookie(response, ctx, request)
        return response

    # -- 2: Dolibarr ---------------------------------------------------------------
    def step2_form(url, token_set, error='', info='', checks=''):
        token_hint = (f'Gespeichert: {mask(token_set)} – leer lassen, um es zu behalten.'
                      if token_set else 'Identisch mit WALLBOXBILLING_API_TOKEN im Dolibarr-Modul.')
        return f"""{_msg('err', error)}{_msg('ok', info)}{_env_warning(['api.dolibarr_url', 'api.api_token'])}
<form method="POST" action="/setup/2">
  <label class="flabel">Dolibarr-URL</label>
  <input name="dolibarr_url" value="{_e(url)}" placeholder="https://erp.firma.de" required>
  <label class="flabel">API-Token</label>
  <input name="api_token" type="password" autocomplete="off" placeholder="{'••••••••' if token_set else ''}">
  <div class="hint">{token_hint}</div>
  <div class="row2">
    <button class="btn-2nd" type="submit" name="action" value="test">Verbindung testen</button>
    <button class="btn-save" type="submit" name="action" value="save">Speichern und weiter</button>
  </div>
</form>{checks}
<a class="hint" href="/setup/3">Überspringen</a>"""

    async def step2(request):
        api = ctx.config.get('api') or {}
        url = api.get('dolibarr_url', '')
        if 'example.com' in url:
            url = ''
        token = api.get('api_token') if 'api.api_token' not in find_placeholders(ctx.config) else ''
        return _page(ctx, request, 'Dolibarr-Verbindung', step2_form(url, token), step=2)

    async def step2_post(request):
        form = await request.post()
        api = ctx.config.get('api') or {}
        stored_token = api.get('api_token') if 'api.api_token' not in find_placeholders(ctx.config) else ''
        raw_url, raw_token = form.get('dolibarr_url', ''), form.get('api_token', '') or stored_token or ''
        try:
            url = validate.dolibarr_url(raw_url)
            token = validate.api_token(raw_token)
        except ValueError as exc:
            return _page(ctx, request, 'Dolibarr-Verbindung',
                         step2_form(raw_url, stored_token, error=_e(exc)), step=2)
        if form.get('action') == 'test':
            steps = await asyncio.to_thread(check_dolibarr, url, token)
            ok = all(s['ok'] for s in steps)
            items = ''.join(f'<li>{"✅" if s["ok"] else "❌"} <b>{_e(s["step"])}</b> {_e(s["detail"])}</li>'
                            for s in steps)
            checks = (_msg('ok' if ok else 'err', 'Verbindung in Ordnung.' if ok else
                           'Verbindung fehlgeschlagen – Details unten.') + f'<ul class="checks">{items}</ul>')
            # Eingetipptes Token nicht zurück ins Formular schreiben (nie im Klartext ausliefern).
            return _page(ctx, request, 'Dolibarr-Verbindung',
                         step2_form(url, stored_token, checks=checks), step=2)
        diff = ctx.store.update({'api.dolibarr_url': url, 'api.api_token': token}, ctx.config)
        ctx.audit.record_diff(request['user'], diff)
        if diff and ctx.reload_dolibarr:
            ctx.reload_dolibarr(url, token)
        raise web.HTTPFound('/setup/3')

    # -- 3: Wallbox ----------------------------------------------------------------
    def step3_form(values, error=''):
        source = ctx.config.get('session_source', 'ha_sensors')
        note = '' if source == 'ocpp' else _msg(
            'warn', f'Aktuelle Betriebsart: <b>{_e(source)}</b>. Mit einer OCPP-Wallbox wird auf '
                    '<b>ocpp</b> umgestellt (Neustart nötig). Wer per Alfen-HTTP oder Modbus '
                    'arbeitet, überspringt diesen Schritt.')
        return f"""{_msg('err', error)}{note}{_env_warning(['session_source', 'ocpp_charge_points'])}
<form method="POST" action="/setup/3">
  <label class="flabel">Charge-Point-ID</label>
  <input name="cp_id" value="{_e(values.get('cp_id'))}" placeholder="ACE0123456" required>
  <div class="hint">Steht in der Wallbox-Konfiguration (Alfen: Seriennummer). Unbekannte IDs meldet das Log.</div>
  <div class="row2">
    <div><label class="flabel">Name</label><input name="name" value="{_e(values.get('name'))}" placeholder="Garage links"></div>
    <div><label class="flabel">wallbox_id (Dolibarr)</label><input name="wallbox_id" value="{_e(values.get('wallbox_id') or 'garage')}" required></div>
  </div>
  <label class="flabel">OCPP-Passwort</label>
  <input name="password" type="password" autocomplete="new-password" placeholder="leer = zufällig erzeugen">
  <div class="hint">{validate.MIN_OCPP_PASSWORD}–{validate.MAX_OCPP_PASSWORD} Zeichen. Leer lassen erzeugt ein
  sicheres Passwort, das im letzten Schritt einmal zum Kopieren angezeigt wird.</div>
  <label class="flabel">Backend-URL für die Wallbox</label>
  <div class="copy"><input id="ws" readonly value="{_e(values.get('ws'))}"><button type="button" onclick="ecCopy('ws')">Kopieren</button></div>
  <button class="btn-save" type="submit">Speichern und weiter</button>
</form>
<a class="hint" href="/setup/4">Überspringen</a>"""

    async def step3(request):
        cps = ctx.config.get('ocpp_charge_points') or []
        first = cps[0] if cps else {}
        values = {'cp_id': first.get('id', ''), 'name': first.get('name', ''),
                  'wallbox_id': first.get('wallbox_id', ''), 'ws': _ws_url(ctx, request)}
        if values['cp_id'] == 'ACE0123456':   # Vorlage
            values['cp_id'] = ''
        return _page(ctx, request, 'Erste Wallbox (OCPP)', step3_form(values), step=3)

    async def step3_post(request):
        form = await request.post()
        values = dict(form) | {'ws': _ws_url(ctx, request)}
        try:
            cp_id = validate.charge_point_id(form.get('cp_id'))
            name = validate.label(form.get('name'))
            wb = validate.wallbox_id(form.get('wallbox_id'))
            pw = form.get('password') or ''
            generated = not pw
            pw = secrets.token_hex(12) if generated else validate.ocpp_password(pw)
        except ValueError as exc:
            return _page(ctx, request, 'Erste Wallbox (OCPP)', step3_form(values, _e(exc)), step=3)
        cps = [dict(c) for c in (ctx.store.load().get('ocpp_charge_points') or [])
               if isinstance(c, dict) and c.get('id') not in (cp_id, 'ACE0123456')]
        cps.insert(0, {'id': cp_id, 'name': name, 'wallbox_id': wb, 'password': pw})
        changes = {'ocpp_charge_points': cps}
        if ctx.config.get('session_source') != 'ocpp':
            changes['session_source'] = 'ocpp'
        diff = ctx.store.update(changes, ctx.config)
        ctx.audit.record_diff(request['user'], diff)
        ctx.reveal[cp_id] = pw
        if 'session_source' in changes or not (ctx.reload_ocpp and ctx.reload_ocpp()):
            ctx.restart_reasons.add('OCPP-Wallboxen / Betriebsart')
        raise web.HTTPFound('/setup/4')

    # -- 4: Karten -----------------------------------------------------------------
    def step4_form(text='', error='', info=''):
        known = ctx.session_manager.list_tags()
        listing = ''.join(f'<li><b>{_e(t.get("label") or "(ohne Namen)")}</b> '
                          f'<span class="hint">{_e(t.get("mode"))}</span></li>' for t in known)
        return f"""{_msg('err', error)}{_msg('ok', info)}
<form method="POST" action="/setup/4">
  <label class="flabel">Karten-IDs, eine je Zeile, optional mit Bezeichnung</label>
  <textarea name="cards" placeholder="EFCD083E; Firmenwagen Müller&#10;04A1B2C3D4E5F6; Poolfahrzeug">{_e(text)}</textarea>
  <div class="hint">Diese Karten gelten als <b>geschäftlich</b> und werden an Dolibarr übertragen. Karten lassen
  sich auch später unter „Karten“ per Lernmodus an der Wallbox erfassen und als privat einordnen.</div>
  <button class="btn-save" type="submit">Speichern und weiter</button>
</form>
{f'<ul class="checks">{listing}</ul>' if listing else ''}
<a class="hint" href="/setup/5">Überspringen</a>"""

    async def step4(request):
        return _page(ctx, request, 'RFID-Karten', step4_form(), step=4)

    async def step4_post(request):
        form = await request.post()
        text = form.get('cards', '')
        try:
            cards = validate.card_lines(text)
        except ValueError as exc:
            return _page(ctx, request, 'RFID-Karten', step4_form(text, _e(exc)), step=4)
        for uid, name in cards:
            ctx.session_manager.upsert_tag(uid, name or None, 'business')
            # Klartext-UID nie ins Protokoll — nur maskiert wie alle Kennungen
            ctx.audit.record(request['user'], 'karte', None, f'{mask(uid)} {name}'.strip(),
                             'als geschäftlich angelegt')
        raise web.HTTPFound('/setup/5')

    # -- 5: Zusammenfassung --------------------------------------------------------
    async def step5(request):
        api = ctx.config.get('api') or {}
        rows = [f'<li><b>Dolibarr</b> {_e(api.get("dolibarr_url") or "nicht eingerichtet")} · '
                f'Token {_e(mask(api.get("api_token")))}</li>']
        wallboxes = ''
        for i, cp in enumerate(ctx.config.get('ocpp_charge_points') or []):
            pw = ctx.reveal.pop(cp.get('id'), None)
            pw_html = (f'<div class="copy"><input id="pw{i}" readonly value="{_e(pw)}"><button type="button" '
                       f'onclick="ecCopy(\'pw{i}\')">Kopieren</button></div><div class="hint">Wird nur jetzt '
                       'angezeigt – gleich in die Wallbox eintragen.</div>' if pw else
                       f'<div class="hint">Passwort: {_e(mask(cp.get("password")))} (neu setzen in Schritt 3)</div>')
            wallboxes += f"""<label class="flabel">{_e(cp.get('name') or cp.get('id'))} – Charge-Point-ID</label>
<div class="copy"><input id="id{i}" readonly value="{_e(cp.get('id'))}"><button type="button" onclick="ecCopy('id{i}')">Kopieren</button></div>
<label class="flabel">Backend-URL</label>
<div class="copy"><input id="ws{i}" readonly value="{_e(_ws_url(ctx, request))}"><button type="button" onclick="ecCopy('ws{i}')">Kopieren</button></div>
<label class="flabel">Passwort</label>{pw_html}"""
        rows.append(f'<li><b>Karten</b> {len(ctx.session_manager.list_tags())} bekannt</li>')
        body = (_restart_box(ctx) + f'<ul class="checks">{"".join(rows)}</ul>' + wallboxes +
                '<div class="hint">In der Wallbox (Alfen: ACE Service Installer → OCPP) Backend-URL, '
                'Charge-Point-ID und Passwort eintragen, Security Profile 1 (Basic Auth).</div>'
                '<a class="btn-save" style="text-decoration:none" href="/">Zur Übersicht</a>')
        return _page(ctx, request, 'Fertig', body, step=5)

    async def restart(request):
        ctx.audit.record(request['user'], 'neustart', None, ', '.join(sorted(ctx.restart_reasons)))
        if ctx.restart:
            asyncio.get_running_loop().call_later(1.0, ctx.restart)
        body = ('<p>Neustart läuft … die Seite lädt in 15 Sekunden neu.</p>'
                '<meta http-equiv="refresh" content="15;url=/">')
        return _page(ctx, request, 'Neustart', body)

    async def audit_page(request):
        rows = ''.join(
            f'<tr><td class="mono">{_e(a.get("time"))}</td><td>{_e(a.get("user"))}</td>'
            f'<td class="mono">{_e(a.get("field"))}</td><td>{_e(a.get("old"))} → {_e(a.get("new"))}'
            f'{"<div class=hint>" + _e(a["note"]) + "</div>" if a.get("note") else ""}</td></tr>'
            for a in ctx.audit.entries())
        body = (f'<div class="tbl-wrap"><table class="audit"><tr><th>Zeit</th><th>Benutzer</th><th>Feld</th>'
                f'<th>alt → neu</th></tr>{rows}</table></div>' if rows else
                '<p class="empty">Noch keine Änderungen.</p>')
        tabs = '<div class="tabs"><a class="on" href="/audit">Änderungen</a><a href="/logs">System-Log</a></div>'
        return _page(ctx, request, 'Protokoll', tabs + body, active='audit')

    r.add_get('/login', login_page)
    r.add_post('/login', login)
    r.add_post('/logout', logout)
    r.add_get('/setup', setup_index)
    r.add_get('/setup/1', step1)
    r.add_post('/setup/1', step1_post)
    r.add_get('/setup/2', step2)
    r.add_post('/setup/2', step2_post)
    r.add_get('/setup/3', step3)
    r.add_post('/setup/3', step3_post)
    r.add_get('/setup/4', step4)
    r.add_post('/setup/4', step4_post)
    r.add_get('/setup/5', step5)
    r.add_post('/restart', restart)
    r.add_get('/audit', audit_page)

    from . import sessions, system, wallboxes   # hier, weil sie die Hilfen dieses Moduls nutzen
    wallboxes.register(app, ctx)
    sessions.register(app, ctx)
    system.register(app, ctx)
