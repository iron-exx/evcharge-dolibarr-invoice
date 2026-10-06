"""Startseite für den Admin: Ampel („Alles in Ordnung“ bzw. was zu tun ist) und —
bis zur ersten übertragenen Ladung — eine Einrichtungs-Checkliste.

Was ein Problem ist, entscheidet notify.collect_alerts: dieselben Regeln wie bei
den Benachrichtigungen, damit Startseite und E-Mail nie verschieden urteilen.
"""
import sqlite3

from placeholders import find_placeholders

from .notify import collect_alerts
from .web import _e

# Schlüssel-Präfix der Meldung → (Sammeltext, Link, Knopf). None = Einzeltext behalten.
_GROUPS = {
    'rejected': ('{n} Ladung(en) von Dolibarr abgelehnt – meist ist die Karte dort keinem Mitarbeiter zugeordnet',
                 '/sessions?month=all&status=rejected', 'Ansehen'),
    'incomplete': ('{n} Ladung(en) unvollständig – kWh nachtragen, dann wird übertragen',
                   '/sessions?month=all&status=incomplete', 'Nachtragen'),
    'pending': (None, '/sessions?month=all&status=pending', 'Ansehen'),
    'offline': (None, None, 'Wallbox ansehen'),
    'backup': (None, '/settings', 'Einstellungen'),
    'update': (None, '/settings', 'Anleitung'),
}


def _scalar(db, sql) -> int:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


def status(ctx) -> dict:
    """→ {'problems': [{text, link, button}], 'checklist': [{label, done, link}] oder None}"""
    state = ctx.api_state or {}
    api = ctx.config.get('api') or {}
    dolibarr_ok = bool(state.get('client'))
    problems = []
    if not dolibarr_ok:
        problems.append({'text': 'Dolibarr nicht verbunden – Ladungen werden gesammelt, aber nicht abgerechnet'
                         if api.get('dolibarr_url') else 'Dolibarr ist noch nicht eingerichtet',
                         'link': '/setup/2', 'button': 'Verbindung prüfen'})
    groups = {}
    for key, text in sorted(collect_alerts(ctx).items()):
        prefix, _, ident = key.partition(':')
        groups.setdefault(prefix, []).append((ident, text))
    for prefix, items in groups.items():
        summary, link, button = _GROUPS.get(prefix, (None, None, None))
        if summary:
            problems.append({'text': summary.format(n=len(items)), 'link': link, 'button': button})
        else:
            for ident, text in items:
                problems.append({'text': text, 'link': link or f'/wallbox/{ident}', 'button': button})

    db = ctx.session_manager.db_path
    ocpp = ctx.config.get('session_source') == 'ocpp'
    steps = [{'label': 'Dolibarr verbunden', 'done': dolibarr_ok, 'link': '/setup/2'}]
    if ocpp:
        cps = [c for c in ctx.config.get('ocpp_charge_points') or []
               if not any(f.startswith('ocpp_') for f in find_placeholders({'ocpp_charge_points': [c]}))]
        server = ctx.ocpp() if ctx.ocpp else None
        seen = any((server.live.get(c.get('id')) or {}).get('last_seen') for c in cps) if server else False
        steps += [{'label': 'Wallbox eingetragen', 'done': bool(cps), 'link': '/wallboxes/new'},
                  {'label': 'Wallbox hat sich verbunden', 'done': seen, 'link': '/wallboxes'}]
    steps += [{'label': 'Karte angelernt', 'done': _scalar(db, "SELECT COUNT(*) FROM tags WHERE mode = 'business'") > 0,
               'link': '/tags'},
              {'label': 'Erste Ladung an Dolibarr übertragen',
               'done': _scalar(db, "SELECT COUNT(*) FROM sessions WHERE transmitted_at IS NOT NULL") > 0,
               'link': '/sessions'}]
    return {'problems': problems, 'checklist': None if all(s['done'] for s in steps) else steps}


def render(st: dict) -> str:
    out = ''
    if st['checklist']:
        done = sum(s['done'] for s in st['checklist'])
        items = ''.join(
            f'<li class="{"done" if s["done"] else ""}">{"✅" if s["done"] else "⬜"} {_e(s["label"])}'
            + ('' if s['done'] else f' <a class="ov-btn" href="{s["link"]}">Jetzt erledigen</a>') + '</li>'
            for s in st['checklist'])
        out += (f'<div class="card ov"><div class="card-title">Erste Schritte · {done} von {len(st["checklist"])} '
                f'erledigt</div><ul class="ov-list">{items}</ul></div>')
    if st['problems']:
        n = len(st['problems'])
        items = ''.join(f'<li>⚠️ {_e(p["text"])}' + (f' <a class="ov-btn" href="{p["link"]}">{_e(p["button"])}</a>'
                                                       if p.get('link') else '') + '</li>' for p in st['problems'])
        out += (f'<div class="card ov ov-warn"><div class="card-title">{n} {"Punkt braucht" if n == 1 else "Punkte brauchen"} '
                f'Ihre Aufmerksamkeit</div><ul class="ov-list">{items}</ul></div>')
    elif not st['checklist']:
        out += '<div class="card ov ov-ok">✅ <b>Alles in Ordnung</b> – Wallboxen verbunden, Ladungen werden übertragen.</div>'
    return out + """<style>
.ov-list{list-style:none;padding:0;margin:0}.ov-list li{padding:8px 0;border-bottom:1px solid var(--border);font-size:14px}
.ov-list li:last-child{border-bottom:none}.ov-list li.done{color:var(--muted)}
.ov-btn{display:inline-block;margin-left:8px;padding:3px 10px;border-radius:6px;border:1.5px solid var(--primary-d);
  color:var(--primary-d);font-size:12px;font-weight:600;text-decoration:none}
.ov-warn{border-left:4px solid var(--warn)}.ov-ok{border-left:4px solid var(--success);font-size:14px}
</style>"""
