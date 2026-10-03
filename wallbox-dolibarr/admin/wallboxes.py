"""Wallbox-Verwaltung (OCPP): Liste, Anlegen/Bearbeiten/Löschen, wartende IDs,
Fernbefehle, Konfiguration der Wallbox und Nachrichtenprotokoll (Standalone).

Schreibend nur angemeldet — das erzwingt die Middleware in admin.web.
"""
import asyncio
import secrets
from datetime import datetime
from urllib.parse import quote

from aiohttp import web
from ocpp.exceptions import OCPPError
from ocpp.v16 import call

from ocpp_server.central_system import RECOMMENDED_CONFIGURATION
from ocpp_server.settings import sanitize_wallbox_id

from . import validate
from .store import mask
from .web import _e, _env_warning, _msg, _page, _redirect, _restart_box, _ws_url

PLACEHOLDER_ID = 'ACE0123456'
_RESTART = 'OCPP-Wallboxen'
_SECRET_KEYS = ('authorizationkey',)

_CSS = """
.cmds{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px}
.cmds form{border:1px solid var(--border);border-radius:8px;padding:10px}
.cmds .btn-2nd{width:100%;margin-top:8px}
.log td{font:11px ui-monospace,monospace;vertical-align:top;word-break:break-all}
.log .in{color:var(--primary-d)}.log .out{color:var(--success)}
.inline{display:flex;gap:6px;align-items:center}.inline input{flex:1;min-width:90px}
.inline button{padding:6px 10px;border-radius:6px;border:1.5px solid var(--border);background:var(--surface2);cursor:pointer}
"""

_LIVE_JS = """<script>
setInterval(function(){fetch('/live.json').then(function(r){return r.json();}).then(function(d){
 (d.charge_points||[]).forEach(function(c){
  var el=document.querySelector('[data-cp="'+CSS.escape(c.id)+'"] .st'); if(!el) return;
  el.textContent=(c.connected?'verbunden':'getrennt')+(c.status?' · '+c.status:'')+(c.last_seen?' · '+c.last_seen.replace('T',' '):'');
 });}).catch(function(){});},5000);
</script>"""

_TRIGGERS = ('BootNotification', 'Heartbeat', 'StatusNotification', 'MeterValues',
             'DiagnosticsStatusNotification', 'FirmwareStatusNotification')


def _connector(form, required=True):
    raw = (form.get('connector_id') or '').strip()
    if not raw and not required:
        return None
    if not raw.isdigit() or not 0 <= int(raw) <= 10:
        raise ValueError('Connector: Zahl 0–10 (1 = erster Ladepunkt, 0 = ganze Wallbox)')
    return int(raw)


def _choice(value, allowed, what):
    if value not in allowed:
        raise ValueError(f'{what}: ungültige Auswahl')
    return value


def _positive_int(raw, what):
    raw = (raw or '').strip()
    if not raw.isdigit() or int(raw) <= 0:
        raise ValueError(f'{what}: positive Zahl')
    return int(raw)


# Befehl → (Bezeichnung, Bestätigungsfrage, Payload-Bauer, Protokolltext)
COMMANDS = {
    'remote_start': ('Laden starten', 'Ladevorgang mit dieser Karte starten?',
                     lambda f: call.RemoteStartTransaction(id_tag=validate.rfid(f.get('id_tag')),
                                                           connector_id=_connector(f, required=False)),
                     lambda f: f'Karte {mask(f.get("id_tag"))}'),
    'remote_stop': ('Laden beenden', 'Laufenden Ladevorgang beenden?',
                    lambda f: call.RemoteStopTransaction(
                        transaction_id=_positive_int(f.get('transaction_id'), 'Transaktions-Nr.')),
                    lambda f: f'Transaktion {f.get("transaction_id")}'),
    'unlock': ('Stecker entriegeln', 'Stecker entriegeln? Ein laufender Ladevorgang endet.',
               lambda f: call.UnlockConnector(connector_id=_connector(f)),
               lambda f: f'Connector {f.get("connector_id")}'),
    'availability': ('Verfügbarkeit', 'Verfügbarkeit ändern?',
                     lambda f: call.ChangeAvailability(
                         connector_id=_connector(f),
                         type=_choice(f.get('type'), ('Operative', 'Inoperative'), 'Verfügbarkeit')),
                     lambda f: f'Connector {f.get("connector_id")} → {f.get("type")}'),
    'reset': ('Neustart der Wallbox', 'Wallbox wirklich neu starten? Laufende Ladevorgänge werden beendet.',
              lambda f: call.Reset(type=_choice(f.get('type'), ('Soft', 'Hard'), 'Neustart-Art')),
              lambda f: f.get('type') or ''),
    'trigger': ('Nachricht anfordern', 'Nachricht von der Wallbox anfordern?',
                lambda f: call.TriggerMessage(
                    requested_message=_choice(f.get('message'), _TRIGGERS, 'Nachricht'),
                    connector_id=_connector(f, required=False)),
                lambda f: f.get('message') or ''),
    'clear_cache': ('Autorisierungs-Cache leeren', 'Gespeicherte Kartenfreigaben in der Wallbox löschen?',
                    lambda f: call.ClearCache(), lambda f: ''),
}


def _status_text(st: dict) -> str:
    if not st:
        return 'noch nie verbunden'
    connectors = st.get('connectors') or {}
    main = connectors.get('1') or next(iter(connectors.values()), {})
    parts = ['verbunden' if st.get('connected') else 'getrennt']
    if main.get('status'):
        parts.append(main['status'])
    if st.get('last_seen'):
        parts.append(st['last_seen'].replace('T', ' '))
    return ' · '.join(parts)


def _confirm(text):
    return f'onsubmit="return confirm(\'{_e(text)}\')"'


def register(app: web.Application, ctx) -> None:
    r = app.router

    def server():
        return ctx.ocpp() if ctx.ocpp else None

    def live(cp_id):
        srv = server()
        return (srv.live.get(cp_id) if srv else None) or {}

    def entries():
        return [dict(c) for c in (ctx.config.get('ocpp_charge_points') or []) if isinstance(c, dict)]

    def find(cp_id):
        for c in entries():
            if c.get('id') == cp_id:
                return c
        raise web.HTTPNotFound(text='Wallbox nicht gefunden')

    def save(request, new_entries, note=''):
        stored = [dict(c) for c in (ctx.store.load().get('ocpp_charge_points') or []) if isinstance(c, dict)]
        diff = ctx.store.update({'ocpp_charge_points': new_entries}, ctx.config)
        if stored != new_entries:
            ctx.audit.record_diff(request['user'], diff, note)
        if diff and ctx.config.get('session_source') == 'ocpp' and not (ctx.reload_ocpp and ctx.reload_ocpp()):
            ctx.restart_reasons.add(_RESTART)

    def page(request, title, body):
        return _page(ctx, request, title, f'<style>{_CSS}</style>' + body, active='wallboxes')

    def flash(cp_id, kind, text):
        ctx.flash[cp_id] = _msg(kind, text)

    def mode_note():
        source = ctx.config.get('session_source', 'ha_sensors')
        if source == 'ocpp':
            return '' if server() else _msg('warn', 'OCPP-Server läuft noch nicht – nach dem Neustart aktiv.')
        return _msg('warn', f'Betriebsart ist <b>{_e(source)}</b> – OCPP-Wallboxen wirken erst mit '
                            '<b>ocpp</b> (umstellen in der <a href="/setup/3">Einrichtung, Schritt 3</a>).')

    # -- Liste -----------------------------------------------------------------------
    async def list_page(request):
        rows = ''
        for c in entries():
            cp_id = c.get('id', '')
            st = live(cp_id)
            placeholder = ' <span class="badge b-pend">Vorlage</span>' if cp_id == PLACEHOLDER_ID else ''
            rows += (f'<tr data-cp="{_e(cp_id)}"><td><a href="/wallbox/{quote(cp_id, safe="")}">'
                     f'<b>{_e(c.get("name") or cp_id)}</b></a>{placeholder}'
                     f'<div class="hint mono">{_e(cp_id)}</div></td>'
                     f'<td class="mono">{_e(c.get("wallbox_id") or cp_id)}</td>'
                     f'<td class="st">{_e(_status_text(st))}</td>'
                     f'<td>{_e(" ".join(x for x in (st.get("vendor"), st.get("model")) if x))}</td></tr>')
        table = (f'<div class="tbl-wrap"><table><tr><th>Wallbox</th><th>wallbox_id</th><th>Zustand</th>'
                 f'<th>Gerät</th></tr>{rows}</table></div>' if rows else
                 '<p class="empty">Noch keine Wallbox eingetragen.</p>')
        srv = server()
        pending = ''
        for cp_id, p in sorted((srv.pending if srv else {}).items(), key=lambda kv: kv[1]['last_seen'],
                               reverse=True):
            pending += (f'<tr><td class="mono">{_e(cp_id)}</td><td>{_e(p.get("remote"))}</td>'
                        f'<td>{_e(p["last_seen"].replace("T", " "))} ({p["count"]}×)</td>'
                        f'<td class="inline"><a class="btn-2nd" style="margin:0" '
                        f'href="/wallboxes/new?cp_id={quote(cp_id, safe="")}">Übernehmen</a>'
                        f'<form method="POST" action="/wallboxes/dismiss"><input type="hidden" name="cp_id" '
                        f'value="{_e(cp_id)}"><button type="submit">Ignorieren</button></form></td></tr>')
        pending_card = ('</div><div class="card"><div class="card-title">Wartende Wallboxen</div>'
                        '<p class="hint">Diese IDs haben sich gemeldet, sind aber nicht eingetragen und wurden '
                        f'abgewiesen.</p><div class="tbl-wrap"><table><tr><th>Charge-Point-ID</th><th>Adresse</th>'
                        f'<th>zuletzt</th><th></th></tr>{pending}</table></div>' if pending else '')
        body = (_restart_box(ctx) + mode_note() + table +
                '<a class="btn-save" style="text-decoration:none" href="/wallboxes/new">Wallbox hinzufügen</a>'
                f'<div class="hint">Backend-URL für alle Wallboxen: <code>{_e(_ws_url(ctx, request))}</code>'
                ' (die Charge-Point-ID hängt die Wallbox selbst an).</div>' + pending_card + _LIVE_JS)
        return page(request, 'Wallboxen', body)

    async def dismiss(request):
        form = await request.post()
        srv = server()
        if srv:
            srv.pending.pop(form.get('cp_id', ''), None)
        raise web.HTTPFound('/wallboxes')

    # -- Anlegen / Bearbeiten ---------------------------------------------------------
    def edit_form(values, action, is_new, error=''):
        cp_id_field = (f'<input name="cp_id" value="{_e(values.get("cp_id"))}" placeholder="ACE0123456" required>'
                       if is_new else
                       f'<input name="cp_id" value="{_e(values.get("cp_id"))}" readonly>'
                       '<div class="hint">Die ID lässt sich nicht ändern – Wallbox löschen und neu anlegen.</div>')
        if is_new:
            pw_hint = ('Leer lassen erzeugt ein sicheres Passwort, das danach einmal zum Kopieren angezeigt wird. '
                       'Bei einer wartenden Wallbox das Passwort eintragen, das in ihr schon steht.')
            gen = ''
        else:
            pw_hint = f'Gespeichert: {_e(mask(values.get("stored_password")))} – leer lassen, um es zu behalten.'
            gen = ('<label class="hint"><input type="checkbox" name="generate" value="1"> '
                   'neues Passwort erzeugen (danach in der Wallbox eintragen – bis dahin wird sie abgewiesen, '
                   'sobald sie sich neu verbindet)</label>')
        return f"""{_msg('err', error)}{_env_warning(['ocpp_charge_points'])}
<form method="POST" action="{_e(action)}">
  <label class="flabel">Charge-Point-ID</label>{cp_id_field}
  <div class="row2">
    <div><label class="flabel">Name</label><input name="name" value="{_e(values.get('name'))}" placeholder="Garage links"></div>
    <div><label class="flabel">wallbox_id (Dolibarr)</label><input name="wallbox_id" value="{_e(values.get('wallbox_id'))}" required></div>
  </div>
  <label class="flabel">OCPP-Passwort</label>
  <input name="password" type="password" autocomplete="new-password">
  <div class="hint">{validate.MIN_OCPP_PASSWORD}–{validate.MAX_OCPP_PASSWORD} Zeichen. {pw_hint}</div>{gen}
  <button class="btn-save" type="submit">Speichern</button>
</form>"""

    def parse(form, is_new):
        cp_id = validate.charge_point_id(form.get('cp_id'))
        name = validate.label(form.get('name'))
        wb = validate.wallbox_id(form.get('wallbox_id'))
        pw = form.get('password') or ''
        generated = (is_new and not pw) or (not pw and form.get('generate'))
        if generated:
            pw = secrets.token_hex(12)
        elif pw:
            pw = validate.ocpp_password(pw)
        return cp_id, name, wb, pw, generated

    async def new_page(request):
        cp_id = request.query.get('cp_id', '')
        values = {'cp_id': cp_id, 'wallbox_id': sanitize_wallbox_id(cp_id) if cp_id else ''}
        return page(request, 'Wallbox hinzufügen', edit_form(values, '/wallboxes/new', True))

    async def new_post(request):
        form = await request.post()
        try:
            cp_id, name, wb, pw, generated = parse(form, True)
            if any(c.get('id') == cp_id for c in entries()):
                raise ValueError(f'Charge-Point-ID {cp_id} ist schon eingetragen')
        except ValueError as exc:
            return page(request, 'Wallbox hinzufügen', edit_form(dict(form), '/wallboxes/new', True, _e(exc)))
        new_entries = [c for c in entries() if c.get('id') != PLACEHOLDER_ID]
        new_entries.append({'id': cp_id, 'name': name, 'wallbox_id': wb, 'password': pw})
        save(request, new_entries, f'Wallbox {cp_id} angelegt')
        srv = server()
        if srv:
            srv.pending.pop(cp_id, None)
        if generated:
            ctx.reveal[cp_id] = pw
        flash(cp_id, 'ok', 'Wallbox angelegt.')
        raise web.HTTPFound(f'/wallbox/{quote(cp_id, safe="")}')

    async def edit_page(request):
        c = find(request.match_info['cp_id'])
        values = {'cp_id': c.get('id'), 'name': c.get('name'), 'wallbox_id': c.get('wallbox_id') or c.get('id'),
                  'stored_password': c.get('password')}
        return page(request, 'Wallbox bearbeiten',
                    edit_form(values, f'/wallbox/{quote(c["id"], safe="")}/edit', False))

    async def edit_post(request):
        old = find(request.match_info['cp_id'])
        form = await request.post()
        action = f'/wallbox/{quote(old["id"], safe="")}/edit'
        try:
            _, name, wb, pw, generated = parse({**form, 'cp_id': old['id']}, False)
        except ValueError as exc:
            return page(request, 'Wallbox bearbeiten',
                        edit_form(dict(form) | {'cp_id': old['id'], 'stored_password': old.get('password')},
                                  action, False, _e(exc)))
        updated = dict(old, name=name, wallbox_id=wb, password=pw or old.get('password', ''))
        save(request, [updated if c.get('id') == old['id'] else c for c in entries()],
             f'Wallbox {old["id"]} bearbeitet')
        if generated:
            ctx.reveal[old['id']] = pw
        flash(old['id'], 'ok', 'Gespeichert.' + (' Neues Passwort unten – jetzt in die Wallbox eintragen.'
                                                 if generated else ''))
        raise web.HTTPFound(f'/wallbox/{quote(old["id"], safe="")}')

    async def delete(request):
        c = find(request.match_info['cp_id'])
        save(request, [x for x in entries() if x.get('id') != c['id']], f'Wallbox {c["id"]} gelöscht')
        srv = server()
        if srv:
            await srv.disconnect(c['id'])
            srv.live.pop(c['id'], None)
        ctx.cp_config.pop(c['id'], None)
        raise web.HTTPFound('/wallboxes')

    # -- Detail ------------------------------------------------------------------------
    def commands_html(cp_id, st, connected):
        if not connected:
            return _msg('warn', 'Fernbefehle und Konfiguration gehen nur, solange die Wallbox verbunden ist.')
        action = f'/wallbox/{quote(cp_id, safe="")}/command'
        active = [s for s in ctx.session_manager.get_active_ocpp_sessions() if s.get('charge_point_id') == cp_id]
        tx = active[0]['id'] if active else ''
        conns = sorted((st.get('connectors') or {}).keys() - {'0'}) or ['1']

        def box(cmd, fields='', button=None):
            title, question = COMMANDS[cmd][:2]
            return (f'<form method="POST" action="{action}" {_confirm(question)}>'
                    f'<input type="hidden" name="cmd" value="{cmd}"><b>{_e(title)}</b>{fields}'
                    f'<button class="btn-2nd" type="submit">{_e(button or title)}</button></form>')

        def conn_select(with_zero=False):
            opts = (['0'] if with_zero else []) + conns
            return ('<label class="flabel">Connector</label><select name="connector_id">' +
                    ''.join(f'<option>{_e(c)}</option>' for c in opts) + '</select>')

        return '<div class="cmds">' + ''.join([
            box('remote_start', '<label class="flabel">Karten-ID</label><input name="id_tag" required '
                                'autocomplete="off">' + conn_select()),
            box('remote_stop', '<label class="flabel">Transaktions-Nr.</label>'
                               f'<input name="transaction_id" value="{_e(tx)}" required>'),
            box('unlock', conn_select()),
            box('availability', conn_select(True) + '<select name="type"><option value="Operative">verfügbar'
                                '</option><option value="Inoperative">gesperrt</option></select>', 'Setzen'),
            box('reset', '<select name="type"><option value="Soft">sanft (Soft)</option>'
                         '<option value="Hard">hart (Hard)</option></select>', 'Neu starten'),
            box('trigger', '<select name="message">' + ''.join(f'<option>{m}</option>' for m in _TRIGGERS) +
                '</select>', 'Anfordern'),
            box('clear_cache', '', 'Leeren'),
        ]) + '</div>'

    def config_html(cp_id, connected):
        action = f'/wallbox/{quote(cp_id, safe="")}/config'
        cached = ctx.cp_config.get(cp_id)
        read_btn = (f'<form method="POST" action="{action}"><input type="hidden" name="action" value="get">'
                    '<button class="btn-2nd" type="submit">Konfiguration lesen</button></form>' if connected else '')
        current = {k['key']: k for k in (cached or {}).get('keys', [])}
        rec_rows, todo = '', 0
        for key, value in RECOMMENDED_CONFIGURATION:
            have = current.get(key, {}).get('value') if key in current else None
            same = have is not None and str(have).lower() == value.lower()
            todo += not same
            rec_rows += (f'<tr><td class="mono">{_e(key)}</td><td class="mono">'
                         f'{_e(have) if have is not None else "<span class=hint>unbekannt</span>"}</td>'
                         f'<td class="mono">{_e(value)}</td><td>{"✅" if same else "→ wird gesetzt"}</td></tr>')
        apply_btn = (f'<form method="POST" action="{action}" {_confirm("Empfohlene Einstellungen setzen?")}>'
                     '<input type="hidden" name="action" value="recommended"><button class="btn-2nd" type="submit">'
                     'Empfohlene übernehmen</button></form>' if connected and todo else '')
        rec = ('<label class="flabel">Empfohlene Einstellungen (Vorschau)</label><div class="hint">Zählerstand '
               'jede Minute, Energie-Register mitsenden, Laden bei ungültiger Karte beenden.</div>'
               '<div class="tbl-wrap"><table><tr><th>Schlüssel</th><th>aktuell</th><th>empfohlen</th><th></th></tr>'
               f'{rec_rows}</table></div>{apply_btn}')
        if not cached:
            return rec + read_btn
        rows = ''
        for k in cached['keys']:
            key, value = k.get('key', ''), k.get('value')
            shown = mask(value) if key.lower() in _SECRET_KEYS else value
            if k.get('readonly') or not connected or key.lower() in _SECRET_KEYS:
                cell = f'<span class="mono">{_e(shown)}</span>' + (' <span class="hint">nur lesbar</span>'
                                                                  if k.get('readonly') else '')
            else:
                cell = (f'<form method="POST" action="{action}" class="inline" '
                        f'{_confirm("Wert in der Wallbox ändern?")}><input type="hidden" name="action" value="set">'
                        f'<input type="hidden" name="key" value="{_e(key)}"><input name="value" value="{_e(value)}">'
                        '<button type="submit">Setzen</button></form>')
            rows += f'<tr><td class="mono">{_e(key)}</td><td>{cell}</td></tr>'
        unknown = cached.get('unknown') or []
        return (rec + f'<label class="flabel">Alle Schlüssel (gelesen {_e(cached["time"].replace("T", " "))})</label>'
                f'<div class="tbl-wrap"><table><tr><th>Schlüssel</th><th>Wert</th></tr>{rows}</table></div>' +
                (f'<div class="hint">Unbekannt: {_e(", ".join(unknown))}</div>' if unknown else '') + read_btn)

    def log_html(st):
        entries_ = list(st.get('log') or [])[::-1]
        if not entries_:
            return '<p class="empty">Noch keine Nachrichten seit dem Start.</p>'
        rows = ''.join(f'<tr><td>{_e(e["time"][11:])}</td><td class="{e["dir"]}">'
                       f'{"←" if e["dir"] == "in" else "→"}</td><td>{_e(e["text"])}</td></tr>' for e in entries_)
        return (f'<div class="hint">Letzte {len(entries_)} Nachrichten, neueste oben. ← von der Wallbox, '
                f'→ an die Wallbox. Karten-IDs sind ausgeblendet.</div>'
                f'<div class="tbl-wrap"><table class="log">{rows}</table></div>')

    async def detail(request):
        c = find(request.match_info['cp_id'])
        cp_id = c['id']
        st = live(cp_id)
        srv = server()
        connected = bool(srv and cp_id in srv.charge_points)
        enc = quote(cp_id, safe='')
        pw = ctx.reveal.pop(cp_id, None)
        pw_html = (f'<label class="flabel">Passwort</label><div class="copy"><input id="pw" readonly value="{_e(pw)}">'
                   '<button type="button" onclick="ecCopy(\'pw\')">Kopieren</button></div>'
                   '<div class="hint">Wird nur jetzt angezeigt – gleich in die Wallbox eintragen.</div>' if pw else
                   f'<div class="hint">Passwort: {_e(mask(c.get("password")))}</div>')
        conn_rows = ''.join(
            f'<tr><td>{_e(n)}</td><td>{_e(x.get("status"))}</td><td>{_e(x.get("error_code"))}</td>'
            f'<td>{_e(x.get("energy_kwh"))}</td><td>{_e(x.get("transaction_id") or "")}</td></tr>'
            for n, x in sorted((st.get('connectors') or {}).items()))
        device = ' '.join(x for x in (st.get('vendor'), st.get('model')) if x)
        body = (ctx.flash.pop(cp_id, '') + _restart_box(ctx) + mode_note() +
                f'<ul class="checks"><li><b>Zustand</b> {_e(_status_text(st))}</li>'
                f'<li><b>Gerät</b> {_e(device or "–")} {"· FW " + _e(st["firmware"]) if st.get("firmware") else ""}</li>'
                f'<li><b>wallbox_id</b> <span class="mono">{_e(c.get("wallbox_id") or cp_id)}</span></li></ul>'
                f'<label class="flabel">Charge-Point-ID</label><div class="copy"><input id="cpid" readonly '
                f'value="{_e(cp_id)}"><button type="button" onclick="ecCopy(\'cpid\')">Kopieren</button></div>'
                f'<label class="flabel">Backend-URL</label><div class="copy"><input id="ws" readonly '
                f'value="{_e(_ws_url(ctx, request))}"><button type="button" onclick="ecCopy(\'ws\')">Kopieren'
                f'</button></div>{pw_html}' +
                (f'<div class="tbl-wrap"><table><tr><th>Connector</th><th>Status</th><th>Fehler</th><th>kWh</th>'
                 f'<th>Transaktion</th></tr>{conn_rows}</table></div>' if conn_rows else '') +
                f'<div class="row2"><a class="btn-2nd" href="/wallbox/{enc}/edit">Bearbeiten</a>'
                f'<a class="btn-2nd" href="/wallbox/{enc}">Aktualisieren</a>'
                f'<form method="POST" action="/wallbox/{enc}/delete" '
                f'{_confirm("Wallbox löschen? Sie wird sofort getrennt und abgewiesen.")}>'
                '<button class="btn-2nd" type="submit" style="width:100%">Löschen</button></form></div>'
                '</div><div class="card"><div class="card-title">Fernbefehle</div>' + commands_html(cp_id, st, connected) +
                '</div><div class="card"><div class="card-title">Konfiguration der Wallbox</div>' +
                config_html(cp_id, connected) +
                '</div><div class="card"><div class="card-title">OCPP-Protokoll</div>' + log_html(st))
        return page(request, c.get('name') or cp_id, body)

    # -- Fernbefehle / Konfiguration -----------------------------------------------------
    async def send(request, cp_id, payload):
        """→ (Antwort, None) oder (None, Fehlermeldung)."""
        srv = server()
        if not srv:
            return None, 'OCPP-Server läuft nicht.'
        try:
            return await srv.send(cp_id, payload), None
        except LookupError as exc:
            return None, str(exc)
        except asyncio.TimeoutError:
            return None, 'Keine Antwort der Wallbox (Zeitüberschreitung).'
        except OCPPError as exc:
            return None, f'Wallbox meldet Fehler: {exc.__class__.__name__} {exc.description or ""}'.strip()

    async def command(request):
        cp_id = find(request.match_info['cp_id'])['id']
        form = await request.post()
        spec = COMMANDS.get(form.get('cmd', ''))
        back = f'/wallbox/{quote(cp_id, safe="")}'
        if spec is None:
            raise web.HTTPBadRequest(text='Unbekannter Befehl')
        title, _, build, describe = spec
        try:
            payload = build(form)
        except ValueError as exc:
            flash(cp_id, 'err', _e(exc))
            raise web.HTTPFound(back)
        result, error = await send(request, cp_id, payload)
        status = getattr(result, 'status', None) if result is not None else None
        outcome = error or status or 'gesendet'
        ctx.audit.record(request['user'], 'wallbox_befehl', None, f'{cp_id}: {title} {describe(form)}'.strip(),
                         outcome)
        ok = not error and status in (None, 'Accepted', 'Unlocked', 'Scheduled')
        flash(cp_id, 'ok' if ok else 'err', f'{_e(title)}: {_e(outcome)}')
        raise web.HTTPFound(back)

    async def configure(request):
        cp_id = find(request.match_info['cp_id'])['id']
        form = await request.post()
        back = f'/wallbox/{quote(cp_id, safe="")}'
        action = form.get('action')
        if action == 'get':
            result, error = await send(request, cp_id, call.GetConfiguration())
            if error:
                flash(cp_id, 'err', _e(error))
            else:
                ctx.cp_config[cp_id] = {
                    'keys': sorted(result.configuration_key or [], key=lambda k: k.get('key', '')),
                    'unknown': result.unknown_key or [],
                    'time': datetime.now().replace(microsecond=0).isoformat()}
                flash(cp_id, 'ok', f'{len(ctx.cp_config[cp_id]["keys"])} Schlüssel gelesen.')
            raise web.HTTPFound(back)
        if action == 'set':
            changes = [(form.get('key', ''), form.get('value', ''))]
        elif action == 'recommended':
            current = {k['key']: str(k.get('value')).lower()
                       for k in (ctx.cp_config.get(cp_id) or {}).get('keys', [])}
            changes = [(k, v) for k, v in RECOMMENDED_CONFIGURATION if current.get(k) != v.lower()]
        else:
            raise web.HTTPBadRequest(text='Unbekannte Aktion')
        results = []
        for key, value in changes:
            if not key or len(key) > 50 or len(value) > 500 or not (key + value).isprintable():
                results.append((key, 'ungültig (Schlüssel bis 50, Wert bis 500 Zeichen)'))
                continue
            if key.lower() in _SECRET_KEYS:
                results.append((key, 'hier nicht änderbar – Passwort unter „Bearbeiten“ setzen'))
                continue
            result, error = await send(request, cp_id, call.ChangeConfiguration(key=key, value=value))
            status = error or result.status
            results.append((key, status))
            ctx.audit.record(request['user'], 'wallbox_konfiguration', None, f'{cp_id}: {key}={value}', status)
            if status in ('Accepted', 'RebootRequired'):
                for k in (ctx.cp_config.get(cp_id) or {}).get('keys', []):
                    if k.get('key') == key:
                        k['value'] = value
        ok = results and all(s in ('Accepted', 'RebootRequired') for _, s in results)
        text = ' · '.join(f'{_e(k)}: {_e(s)}' for k, s in results) or 'Nichts zu ändern.'
        if any(s == 'RebootRequired' for _, s in results):
            text += ' – wirkt nach einem Neustart der Wallbox.'
        flash(cp_id, 'ok' if ok or not results else 'err', text)
        raise web.HTTPFound(back)

    r.add_get('/wallboxes', list_page)
    r.add_get('/wallboxes/new', new_page)
    r.add_post('/wallboxes/new', new_post)
    r.add_post('/wallboxes/dismiss', dismiss)
    r.add_get('/wallbox/{cp_id}', detail)
    r.add_get('/wallbox/{cp_id}/edit', edit_page)
    r.add_post('/wallbox/{cp_id}/edit', edit_post)
    r.add_post('/wallbox/{cp_id}/delete', delete)
    r.add_post('/wallbox/{cp_id}/command', command)
    r.add_post('/wallbox/{cp_id}/config', configure)
