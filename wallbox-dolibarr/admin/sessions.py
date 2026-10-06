"""Ladevorgänge: Liste mit Filter, CSV-Export, Übertragungsstatus und
Nachbearbeitung unvollständiger Ladungen (Standalone).

Schreibend nur angemeldet — das erzwingt die Middleware in admin.web.
"""
import csv
import io
import re
from datetime import datetime
from urllib.parse import urlsplit

from aiohttp import web

from session_manager import session_state

from .web import _e, _msg, _page

_MONTH = re.compile(r'^\d{4}-\d{2}$')
# Filterwert → (Bezeichnung, Badge-Klasse)
STATUS = {
    'pending': ('ausstehend', 'b-pend'),
    'rejected': ('abgelehnt', 'b-inc'),
    'transmitted': ('übertragen', 'b-ok'),
    'active': ('läuft', 'b-pend'),
    'incomplete': ('unvollständig', 'b-inc'),
    'discarded': ('verworfen', 'b-disc'),
    'private': ('privat', 'b-disc'),
}

_CSS = """
.filters{display:flex;gap:8px;flex-wrap:wrap;align-items:end;margin-bottom:12px}
.filters>*{flex:1;min-width:140px}
.sess td{font-size:12px;vertical-align:top}.sess .nowrap{white-space:nowrap}
.sess form{display:flex;gap:4px;margin:2px 0}.sess form input{width:70px;padding:4px 6px}
.sess form button{padding:4px 8px;border-radius:6px;border:1.5px solid var(--border);background:var(--surface2);cursor:pointer;font-size:12px}
"""


def status_key(s: dict) -> str:
    return session_state(s)


def _kwh(value) -> str:
    return '' if value is None else f'{value:.3f}'.replace('.', ',')


def _when(value) -> str:
    return (value or '')[:16].replace('T', ' ')


def _short(start, end=None) -> str:
    """'03.10. 07:00'; beim Ende nur die Uhrzeit, wenn es derselbe Tag ist."""
    value = end if end is not None else start
    if not value or len(value) < 16:
        return value or '–'
    if end is not None and start and start[:10] == end[:10]:
        return end[11:16]
    return f'{value[8:10]}.{value[5:7]}. {value[11:16]}'


def _dolibarr_reason(error: str) -> str:
    """'HTTP 404: {"success":false,"error":"RFID not registered in Dolibarr"}' → verständlicher Grund."""
    text = str(error or '')
    if 'RFID not registered' in text:
        return 'Karte in Dolibarr keinem Mitarbeiter zugeordnet'
    if 'User not found' in text:
        return 'Mitarbeiter in Dolibarr nicht gefunden oder deaktiviert'
    return text[:160]


def _filters(request):
    month = request.query.get('month', datetime.now().strftime('%Y-%m'))
    if month != 'all' and not _MONTH.match(month):
        month = datetime.now().strftime('%Y-%m')
    status = request.query.get('status', '')
    if status not in STATUS:
        status = ''
    return month, status


def register(app: web.Application, ctx) -> None:
    r = app.router
    sm = ctx.session_manager

    def labels():
        return {t['rfid_hash']: t.get('label') for t in sm.list_tags()}

    def card(s, names):
        if s.get('login'):
            return s['login']
        h = s.get('rfid_hash') or ''
        return names.get(h) or (h[:12] + '…' if h else '')

    def rows_for(month, status):
        return sm.list_sessions(None if month == 'all' else month, status or None)

    def transmit_box(counts):
        state = ctx.api_state or {}
        api = ctx.config.get('api') or {}
        last = state.get('last_transmit')
        if last:
            last_txt = (f'{_e(_when(last["time"]))}: {last["transmitted"]} übertragen'
                        + (f', {last["rejected"]} abgelehnt' if last.get('rejected') else '')
                        + (f', {last["failed"]} fehlgeschlagen – {_e(last.get("error"))}' if last['failed'] else ''))
        else:
            last_txt = 'seit dem Start noch keiner'
        reach = ('erreichbar' if state.get('client') else
                 'nicht eingerichtet' if not api.get('dolibarr_url') else 'nicht erreichbar oder noch nicht geprüft')
        warn = ''
        if counts.get('rejected'):
            warn += _msg('err', f'{counts["rejected"]} Ladung(en) von Dolibarr abgelehnt – meist ist die Karte in '
                                'Dolibarr keinem Mitarbeiter zugeordnet (ExpenseCharge → RFID-Verwaltung). Danach '
                                '„Erneut senden“. Die übrigen Ladungen werden weiter übertragen. '
                                '<a href="/sessions?month=all&status=rejected">Anzeigen</a>')
        if counts.get('incomplete'):
            warn += _msg('warn', f'{counts["incomplete"]} unvollständige Ladung(en) – sie werden erst übertragen, '
                                'wenn die Energiemenge unten korrigiert ist. <a href="/sessions?month=all&status='
                                'incomplete">Anzeigen</a>')
        return (f'{warn}<ul class="checks"><li><b>Dolibarr</b> {_e(api.get("dolibarr_url") or "–")} · {reach}</li>'
                f'<li><b>Ausstehend</b> {counts.get("pending", 0)} Ladung(en)</li>'
                f'<li><b>Letzter Lauf</b> {last_txt}</li></ul>'
                '<form method="POST" action="/sessions/transmit"><button class="btn-2nd" type="submit">'
                'Jetzt übertragen</button></form>')

    async def list_page(request):
        month, status = _filters(request)
        names = labels()
        rows = rows_for(month, status)
        body_rows = ''
        for s in rows:
            key = status_key(s)
            label, badge = STATUS.get(key, (key, 'b-pend'))
            action = ''
            if key == 'incomplete' and not s.get('transmitted_at'):
                action = (f'<form method="POST" action="/sessions/{s["id"]}/resolve" '
                          'onsubmit="return confirm(\'Mit dieser Energiemenge abschließen und übertragen?\')">'
                          f'<input name="kwh" inputmode="decimal" placeholder="kWh" value="{_e(_kwh(s.get("total_kwh")))}" '
                          'required><button type="submit">Abschließen</button></form>')
            if key == 'rejected':
                action = (f'<form method="POST" action="/sessions/{s["id"]}/retry">'
                          '<button type="submit">Erneut senden</button></form>')
            if key in ('incomplete', 'pending', 'rejected'):
                action += (f'<form method="POST" action="/sessions/{s["id"]}/discard" '
                           'onsubmit="return confirm(\'Ladung verwerfen? Sie wird nie übertragen.\')">'
                           '<button type="submit">Verwerfen</button></form>')
            body_rows += (f'<tr><td class="mono">{s["id"]}</td><td class="nowrap">{_e(_short(s.get("start_time")))}'
                          f'<div class="hint">bis {_e(_short(s.get("start_time"), s.get("end_time") or ""))}</div></td>'
                          f'<td class="td-bold">{_e(_kwh(s.get("total_kwh")))}</td>'
                          f'<td><span class="badge {badge}">{_e(label)}</span>'
                          f'{"<div class=hint>" + _e((s.get("stop_reason") or "").strip()) + "</div>" if s.get("stop_reason") else ""}'
                          f'{"<div class=hint>" + _e(_when(s["transmitted_at"])) + "</div>" if s.get("transmitted_at") else ""}'
                          f'{"<div class=hint>" + _e(_dolibarr_reason(s["transmit_error"])) + "</div>" if key == "rejected" else ""}'
                          f'</td><td>{_e(card(s, names))}</td><td class="mono">{_e(s.get("wallbox_id"))}</td>'
                          f'<td>{action}</td></tr>')
        total = sum(s.get('total_kwh') or 0 for s in rows if status_key(s) in ('pending', 'transmitted'))
        months = sorted({(s.get('start_time') or '')[:7] for s in sm.list_sessions(limit=100000)} - {''},
                        reverse=True)
        month_opts = ''.join(f'<option value="{m}"{" selected" if m == month else ""}>{m}</option>'
                             for m in [*months, *([month] if month not in months and month != 'all' else [])])
        status_opts = ''.join(f'<option value="{k}"{" selected" if k == status else ""}>{_e(v[0])}</option>'
                              for k, v in STATUS.items())
        query = f'month={month}&status={status}'
        table = (f'<div class="tbl-wrap"><table class="sess"><tr><th>Nr.</th><th>Beginn</th><th>kWh</th><th>Status</th>'
                 f'<th>Karte</th><th>Wallbox</th><th></th></tr>{body_rows}</table></div>' if body_rows else
                 '<p class="empty">Keine Ladevorgänge für diese Auswahl.</p>')
        body = (f'<style>{_CSS}</style>' + ctx.flash.pop('sessions', '') + transmit_box(sm.session_counts()) +
                '</div><div class="card"><div class="card-title">Ladevorgänge</div>'
                '<form method="GET" action="/sessions" class="filters">'
                f'<div><label class="flabel">Monat</label><select name="month"><option value="all"'
                f'{" selected" if month == "all" else ""}>alle</option>{month_opts}</select></div>'
                f'<div><label class="flabel">Status</label><select name="status"><option value="">alle</option>'
                f'{status_opts}</select></div>'
                '<button class="btn-2nd" type="submit">Anzeigen</button>'
                f'<a class="btn-2nd" href="/sessions.csv?{query}">CSV herunterladen</a>'
                f'<a class="btn-2nd" href="/report?month={month if month != "all" else datetime.now().strftime("%Y-%m")}">'
                'Ladenachweis</a></form>'
                f'<div class="hint">{len(rows)} Ladung(en) · abrechenbar {_kwh(total)} kWh</div>' + table)
        return _page(ctx, request, 'Übertragung an Dolibarr', body, active='sessions')

    async def export_csv(request):
        month, status = _filters(request)
        names = labels()
        out = io.StringIO()
        w = csv.writer(out, delimiter=';')
        w.writerow(['Nr', 'Beginn', 'Ende', 'kWh', 'Karte', 'Wallbox', 'Charge-Point', 'Status',
                    'Übertragen am', 'Hinweis'])
        for s in rows_for(month, status):
            w.writerow([s['id'], _when(s.get('start_time')), _when(s.get('end_time')), _kwh(s.get('total_kwh')),
                        card(s, names), s.get('wallbox_id') or '', s.get('charge_point_id') or '',
                        STATUS.get(status_key(s), (status_key(s),))[0], _when(s.get('transmitted_at')),
                        (s.get('stop_reason') or '').strip()])
        name = f'ladevorgaenge_{month}{"_" + status if status else ""}.csv'
        # BOM, damit Excel die Umlaute richtig liest
        return web.Response(body=('﻿' + out.getvalue()).encode('utf-8'), content_type='text/csv',
                            charset='utf-8', headers={'Content-Disposition': f'attachment; filename="{name}"'})

    def back(request):
        # nur Pfad und Filter der vorigen Seite übernehmen, nie einen fremden Host
        ref = urlsplit(request.headers.get('Referer') or '')
        return web.HTTPFound(f'/sessions?{ref.query}' if ref.path == '/sessions' else '/sessions')

    async def transmit(request):
        if ctx.transmit_now:
            ctx.transmit_now()
            ctx.flash['sessions'] = _msg('ok', 'Übertragung angestoßen – das Ergebnis steht in wenigen Sekunden '
                                               'unter „Letzter Lauf“ (Seite neu laden).')
        else:
            ctx.flash['sessions'] = _msg('err', 'Übertragung ist nicht verfügbar.')
        ctx.audit.record(request['user'], 'uebertragung', None, 'von Hand angestoßen')
        raise back(request)

    async def resolve(request):
        sid = int(request.match_info['sid'])
        form = await request.post()
        try:
            kwh = float((form.get('kwh') or '').replace(',', '.'))
            ok = sm.resolve_incomplete_session(sid, kwh)
        except ValueError as exc:
            ctx.flash['sessions'] = _msg('err', _e(str(exc) if 'kWh' in str(exc) else 'kWh: Zahl angeben, z.B. 12,5'))
            raise back(request)
        if ok:
            ctx.audit.record(request['user'], 'ladevorgang', None, f'#{sid}: {_kwh(kwh)} kWh', 'von Hand abgeschlossen')
            if ctx.transmit_now:
                ctx.transmit_now()
        ctx.flash['sessions'] = _msg('ok' if ok else 'err', f'Ladung #{sid} abgeschlossen und zur Übertragung '
                                                           'vorgemerkt.' if ok else
                                     f'Ladung #{sid} ist nicht (mehr) unvollständig.')
        raise back(request)

    async def retry(request):
        sid = int(request.match_info['sid'])
        ok = sm.retry_rejected_session(sid)
        if ok:
            ctx.audit.record(request['user'], 'ladevorgang', None, f'#{sid}', 'erneut senden')
            if ctx.transmit_now:
                ctx.transmit_now()
        ctx.flash['sessions'] = _msg('ok' if ok else 'err', f'Ladung #{sid} wird erneut gesendet.' if ok else
                                     f'Ladung #{sid} ist nicht abgelehnt.')
        raise back(request)

    async def discard(request):
        sid = int(request.match_info['sid'])
        ok = sm.discard_session(sid)
        if ok:
            ctx.audit.record(request['user'], 'ladevorgang', None, f'#{sid}', 'verworfen')
        ctx.flash['sessions'] = _msg('ok' if ok else 'err', f'Ladung #{sid} verworfen.' if ok else
                                     f'Ladung #{sid} lässt sich nicht verwerfen (schon übertragen?).')
        raise back(request)

    async def my_page(request):
        month, _ = _filters(request)
        mine = set(request.get('cards') or [])
        rows = [s for s in sm.list_sessions(None if month == 'all' else month)
                if (s.get('rfid_hash') or '')[:16] in mine and status_key(s) not in ('discarded',)]
        months = sorted({(s.get('start_time') or '')[:7] for s in sm.list_sessions(limit=100000)
                         if (s.get('rfid_hash') or '')[:16] in mine} - {''}, reverse=True) or [month]
        if month not in months and month != 'all':
            months.insert(0, month)
        body_rows = ''.join(
            f'<tr><td class="nowrap">{_e(_short(s.get("start_time")))}<div class="hint">bis '
            f'{_e(_short(s.get("start_time"), s.get("end_time") or ""))}</div></td>'
            f'<td class="td-bold">{_e(_kwh(s.get("total_kwh")))}</td>'
            f'<td><span class="badge {STATUS.get(status_key(s), ("", "b-pend"))[1]}">'
            f'{_e(STATUS.get(status_key(s), (status_key(s),))[0])}</span></td>'
            f'<td class="mono">{_e(s.get("wallbox_id"))}</td></tr>' for s in rows)
        total = sum(s.get('total_kwh') or 0 for s in rows if status_key(s) in ('pending', 'transmitted', 'rejected'))
        if not mine:
            body = _msg('warn', 'Ihrem Konto ist noch keine Karte zugeordnet – bitte beim Admin melden.')
        else:
            body = (f'<form method="GET" action="/me" class="filters"><div><label class="flabel">Monat</label>'
                    '<select name="month">' + ''.join(f'<option{" selected" if m == month else ""}>{m}</option>'
                                                      for m in months) +
                    '</select></div><button class="btn-2nd" type="submit">Anzeigen</button>'
                    f'<a class="btn-2nd" href="/report?month={month}">Ladenachweis (PDF)</a></form>'
                    f'<div class="hint">{len(rows)} Ladung(en) · abrechenbar {_kwh(total)} kWh. „Ausstehend“ wird '
                    'automatisch an die Spesenabrechnung übertragen; „privat“ erscheint dort nie.</div>' +
                    (f'<div class="tbl-wrap"><table class="sess"><tr><th>Beginn</th><th>kWh</th><th>Status</th>'
                     f'<th>Wallbox</th></tr>{body_rows}</table></div>' if body_rows else
                     '<p class="empty">Keine Ladungen in diesem Monat.</p>'))
        return _page(ctx, request, 'Meine Ladungen', f'<style>{_CSS}</style>' + body, active='me')

    r.add_get('/me', my_page)
    r.add_get('/sessions', list_page)
    r.add_get('/sessions.csv', export_csv)
    r.add_post('/sessions/transmit', transmit)
    r.add_post(r'/sessions/{sid:\d+}/resolve', resolve)
    r.add_post(r'/sessions/{sid:\d+}/discard', discard)
    r.add_post(r'/sessions/{sid:\d+}/retry', retry)
