"""Benachrichtigungen: was ist ein Problem, jedes nur einmal melden, E-Mail und Webhook."""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from admin import notify  # noqa: E402
from admin.web import AdminContext  # noqa: E402
from session_manager import SessionManager  # noqa: E402


class FakeServer:
    def __init__(self, live):
        self.live, self.charge_points = live, {}


def _session(sm, status, hours_ago, error=None):
    t = (datetime.now() - timedelta(hours=hours_ago)).isoformat(timespec='seconds')
    conn = sqlite3.connect(sm.db_path)
    cur = conn.execute("INSERT INTO sessions (rfid_hash, wallbox_id, start_time, end_time, total_kwh, status, created_at,"
                       " transmit_error) VALUES (?, 'garage', ?, ?, 5, ?, ?, ?)", ('a' * 64, t, t, status, t, error))
    conn.commit()
    conn.close()
    return cur.lastrowid


@pytest.fixture()
def ctx(tmp_path):
    sm = SessionManager(db_path=str(tmp_path / 'sessions.db'))
    old = (datetime.now() - timedelta(hours=5)).isoformat(timespec='seconds')
    live = {'ACE1': {'connected': False, 'last_seen': old}, 'ACE2': {'connected': True, 'last_seen': old}}
    config = {'ocpp_charge_points': [{'id': 'ACE1', 'name': 'Wallbox 1'}, {'id': 'ACE2'}, {'id': 'NEU'}],
              'notify': {'offline_hours': 2, 'pending_hours': 6}}
    return AdminContext(data_dir=str(tmp_path), config=config, session_manager=sm, ocpp=lambda: FakeServer(live))


def test_collect_alerts(ctx):
    sm = ctx.session_manager
    rejected = _session(sm, 'completed', 1, error='HTTP 404: RFID not registered in Dolibarr')
    incomplete = _session(sm, 'incomplete', 1)
    _session(sm, 'completed', 8)       # wartet seit 8 h
    alerts = notify.collect_alerts(ctx)
    assert f'rejected:{rejected}' in alerts and f'incomplete:{incomplete}' in alerts
    assert 'pending' in alerts and 'offline:ACE1' in alerts
    assert 'offline:ACE2' not in alerts, "verbunden"
    assert 'offline:NEU' not in alerts, "noch nie verbunden = noch nicht montiert, kein Alarm"
    assert 'backup' in alerts, "noch nie ein automatisches Backup"
    assert 'Wallbox 1' in alerts['offline:ACE1']
    ctx.latest_version = '99.0.0'
    assert 'update:99.0.0' in notify.collect_alerts(ctx)


def test_each_problem_reported_once_until_resolved(ctx):
    sent = []
    current = {'a': 'Problem A'}
    send = lambda subject, lines: sent.append(lines)
    notify.check_and_notify(ctx, send, alerts=current)
    notify.check_and_notify(ctx, send, alerts=current)
    assert sent == [['Problem A']], "nur einmal"
    notify.check_and_notify(ctx, send, alerts={})                       # behoben
    notify.check_and_notify(ctx, send, alerts={'a': 'Problem A'})       # wieder da
    assert len(sent) == 2
    state = json.loads((ctx.data_dir and open(os.path.join(ctx.data_dir, 'notify.json')).read()))
    assert state == ['a'], "übersteht Neustarts"


def test_email_and_webhook(monkeypatch):
    mails, posts = [], []

    class SMTP:
        def __init__(self, host, port, timeout): mails.append(('connect', host, port))
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def starttls(self, context=None): mails.append('tls')
        def login(self, u, p): mails.append(('login', u))
        def send_message(self, msg): mails.append(('to', msg['To'], msg['Subject'], msg.get_content()))

    monkeypatch.setattr(notify.smtplib, 'SMTP', SMTP)
    monkeypatch.setattr(notify.requests, 'post', lambda url, json, timeout: posts.append((url, json)) or
                        type('R', (), {'raise_for_status': lambda self: None})())
    cfg = {'email_to': 'admin@firma.de', 'smtp_host': 'mail.firma.de', 'smtp_port': 587, 'smtp_user': 'ec',
           'smtp_password': 'geheim', 'smtp_from': 'ec@firma.de', 'smtp_tls': 'starttls',
           'webhook_url': 'https://n8n.firma.de/hook'}
    errors = notify.send(cfg, 'ExpenseCharge: 1 Problem', ['Wallbox 1 offline'])
    assert errors == []
    assert ('connect', 'mail.firma.de', 587) in mails and 'tls' in mails and ('login', 'ec') in mails
    assert any(m[0] == 'to' and m[1] == 'admin@firma.de' and 'Wallbox 1 offline' in m[3] for m in mails if isinstance(m, tuple))
    assert posts[0][0] == 'https://n8n.firma.de/hook' and posts[0][1]['text'].endswith('Wallbox 1 offline')


def test_nothing_configured_sends_nothing():
    assert notify.send({}, 's', ['x']) == ['Keine Benachrichtigung eingerichtet (E-Mail oder Webhook).']


def test_home_assistant_channel(monkeypatch):
    posts = []
    monkeypatch.setattr(notify.requests, 'post', lambda url, json, timeout, headers=None: posts.append((url, json, headers))
                        or type('R', (), {'raise_for_status': lambda self: None})())
    monkeypatch.setenv('SUPERVISOR_TOKEN', 'sv-token')
    cfg = notify.settings({'notify_ha_service': 'mobile_app_iphone'})
    assert notify.send(cfg, 'ExpenseCharge: 1 Problem', ['Wallbox 1 offline']) == []
    urls = [p[0] for p in posts]
    assert urls == ['http://supervisor/core/api/services/persistent_notification/create',
                    'http://supervisor/core/api/services/notify/mobile_app_iphone']
    assert posts[0][2] == {'Authorization': 'Bearer sv-token'} and 'Wallbox 1 offline' in posts[0][1]['message']
    assert 'sv-token' not in str(cfg.get('ha_service')), "Token nie in der Konfiguration"
    assert notify.settings({'notify_ha': False}).get('ha_token') is None, "abschaltbar"


def test_no_backup_alert_in_home_assistant(ctx):
    import types
    ha = types.SimpleNamespace(config=ctx.config, session_manager=ctx.session_manager, ocpp=None,
                               data_dir=ctx.data_dir, latest_version=None, standalone=False)
    assert 'backup' not in notify.collect_alerts(ha), "HA sichert selbst"


def test_monthly_report_mail(ctx, monkeypatch):
    from datetime import date
    from utils.hash import hash_rfid
    sm = ctx.session_manager
    for uid, name in (('04A1B2C3', 'Max Müller'), ('EFCD083E', 'Eva Schmidt')):
        sm.upsert_tag(uid, name, 'business')
        conn = sqlite3.connect(sm.db_path)
        conn.execute("INSERT INTO sessions (rfid_hash, wallbox_id, start_time, end_time, start_energy_kwh, end_energy_kwh,"
                     " total_kwh, status, created_at) VALUES (?, 'w', '2026-10-03T08:00:00', '2026-10-03T09:00:00', 1, 6,"
                     " 5, 'completed', 'x')", (hash_rfid(uid),))
        conn.commit()
        conn.close()
    ctx.accounts.create('admin', 'admin-passwort-1')
    ctx.accounts.add_user('mmueller', 'mm-passwort-12', 'mitarbeiter', [hash_rfid('04A1B2C3')[:16]], email='max@firma.de')
    ctx.config['notify'] = {'email_to': 'it@firma.de', 'report_to': 'buchhaltung@firma.de', 'smtp_host': 'mail.firma.de',
                            'smtp_tls': 'none'}
    sent = []
    monkeypatch.setattr(notify, '_deliver', lambda cfg, msg: sent.append(msg))
    assert notify.send_monthly_reports(ctx, today=date(2026, 10, 20)) == [], "nur am Monatsanfang für den Vormonat"
    to = notify.send_monthly_reports(ctx, today=date(2026, 11, 1))
    assert sorted(to) == ['buchhaltung@firma.de', 'max@firma.de']
    by_to = {m['To']: m for m in sent}
    bh = by_to['buchhaltung@firma.de']
    assert 'Oktober 2026' in bh['Subject']
    html_part = bh.get_body(('html',)).get_content()
    assert 'Max Müller' in html_part and 'Eva Schmidt' in html_part
    assert any(p.get_filename() == 'ladenachweis_2026-10.csv' for p in bh.iter_attachments())
    mine = by_to['max@firma.de'].get_body(('html',)).get_content()
    assert 'Max Müller' in mine and 'Eva Schmidt' not in mine, "Mitarbeiter bekommt nur den eigenen"
    assert notify.send_monthly_reports(ctx, today=date(2026, 11, 2)) == [], "jeder Monat nur einmal"
