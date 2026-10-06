"""Benachrichtigungen per E-Mail und/oder Webhook, wenn etwas liegen bleibt (Standalone).

Jedes Problem hat einen Schlüssel ('rejected:42', 'offline:ACE1', …). Gemeldet
wird nur, was neu ist; ein behobenes Problem darf beim nächsten Auftreten wieder
melden. Die gemeldeten Schlüssel liegen in <data>/notify.json — sonst käme nach
jedem Neustart alles noch einmal.
"""
import asyncio
import json
import logging
import os
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage

import requests

from .system import app_version, backup_status, newer

_LOGGER = logging.getLogger(__name__)

CHECK_SECONDS = 300
DEFAULTS = {'offline_hours': 2, 'pending_hours': 6, 'smtp_port': 587, 'smtp_tls': 'starttls'}
TLS_MODES = ('starttls', 'ssl', 'none')


_SUPERVISOR = 'http://supervisor/core/api/services'


def settings(config: dict) -> dict:
    cfg = {**DEFAULTS, **(config.get('notify') or {})}
    # Im HA-Addon: über Home Assistant melden (Token nur aus der Umgebung, nie gespeichert)
    if os.getenv('SUPERVISOR_TOKEN') and config.get('notify_ha', True):
        cfg['ha_token'] = os.getenv('SUPERVISOR_TOKEN')
        cfg['ha_service'] = (config.get('notify_ha_service') or '').strip().removeprefix('notify.')
    return cfg


def _older_than(iso: str, hours: float) -> bool:
    try:
        return datetime.fromisoformat(iso) < datetime.now() - timedelta(hours=hours)
    except (TypeError, ValueError):
        return False


def collect_alerts(ctx) -> dict:
    """Aktuelle Probleme → {schlüssel: meldung}."""
    cfg = settings(ctx.config)
    sm = ctx.session_manager
    alerts = {}
    for s in sm.list_sessions(status='rejected'):
        alerts[f'rejected:{s["id"]}'] = (f'Ladung #{s["id"]} ({(s.get("start_time") or "")[:16].replace("T", " ")}) '
                                         f'von Dolibarr abgelehnt: {s.get("transmit_error") or ""}'[:300])
    for s in sm.list_sessions(status='incomplete'):
        alerts[f'incomplete:{s["id"]}'] = (f'Ladung #{s["id"]} ({(s.get("start_time") or "")[:16].replace("T", " ")}) '
                                           'unvollständig – kWh unter „Ladevorgänge“ nachtragen')
    pending = sm.list_sessions(status='pending')
    if pending and _older_than(pending[-1].get('end_time') or pending[-1].get('start_time'), cfg['pending_hours']):
        alerts['pending'] = (f'{len(pending)} Ladung(en) warten seit über {cfg["pending_hours"]} h auf die '
                             'Übertragung an Dolibarr – Verbindung prüfen')
    server = ctx.ocpp() if ctx.ocpp else None
    if server is not None:
        for cp in ctx.config.get('ocpp_charge_points') or []:
            st = server.live.get(cp.get('id')) or {}
            # Noch nie verbunden = vermutlich noch nicht montiert → kein Alarm
            if st.get('last_seen') and not st.get('connected') and _older_than(st['last_seen'], cfg['offline_hours']):
                alerts[f'offline:{cp["id"]}'] = (f'Wallbox {cp.get("name") or cp["id"]} seit '
                                                 f'{st["last_seen"].replace("T", " ")} nicht verbunden')
    if ctx.latest_version and newer(ctx.latest_version, app_version()):
        alerts[f'update:{ctx.latest_version}'] = (f'Neue Version {ctx.latest_version} verfügbar (installiert '
                                                   f'{app_version()}) – Einstellungen → Systeminfo')
    latest = backup_status(ctx.data_dir)['latest']
    stale = f'expensecharge-{datetime.now() - timedelta(days=2):%Y%m%d}.zip'
    if getattr(ctx, 'standalone', True) and (not latest or latest < stale):
        alerts['backup'] = 'Kein automatisches Backup der letzten zwei Tage – Speicherplatz und System-Log prüfen'
    return alerts


def _state_path(ctx) -> str:
    return os.path.join(ctx.data_dir, 'notify.json')


def _load_state(ctx) -> set:
    try:
        with open(_state_path(ctx)) as f:
            return set(json.load(f))
    except (OSError, ValueError):
        return set()


def check_and_notify(ctx, send, alerts=None) -> list:
    """Meldet neue Probleme über send(subject, lines). → die gemeldeten Zeilen."""
    alerts = collect_alerts(ctx) if alerts is None else alerts
    known = _load_state(ctx)
    new = [alerts[k] for k in sorted(alerts) if k not in known]
    if new:
        n = len(new)
        send(f'ExpenseCharge: {n} neue{"s" if n == 1 else ""} Problem{"" if n == 1 else "e"}', new)
    if new or known != set(alerts):
        with open(_state_path(ctx), 'w') as f:
            json.dump(sorted(alerts), f)
    return new


def send(cfg: dict, subject: str, lines: list) -> list:
    """E-Mail und/oder Webhook. → Fehlermeldungen (leer = alles zugestellt)."""
    text = '\n'.join(f'• {line}' for line in lines)
    errors = []
    if not (cfg.get('email_to') or cfg.get('webhook_url') or cfg.get('ha_token')):
        return ['Keine Benachrichtigung eingerichtet (E-Mail oder Webhook).']
    if cfg.get('ha_token'):
        headers = {'Authorization': f'Bearer {cfg["ha_token"]}'}
        calls = [('persistent_notification/create',
                  {'title': subject, 'message': text, 'notification_id': 'expensecharge'})]
        if cfg.get('ha_service'):   # z.B. mobile_app_iphone → Push aufs Handy
            calls.append((f'notify/{cfg["ha_service"]}', {'title': subject, 'message': text}))
        for service, body in calls:
            try:
                requests.post(f'{_SUPERVISOR}/{service}', json=body, timeout=15, headers=headers).raise_for_status()
            except Exception as exc:
                errors.append(f'Home Assistant ({service}): {exc}')
    if cfg.get('email_to'):
        try:
            msg = EmailMessage()
            msg['Subject'], msg['To'] = subject, cfg['email_to']
            msg['From'] = cfg.get('smtp_from') or cfg.get('smtp_user') or cfg['email_to']
            msg.set_content(text + '\n\nDetails in der Oberfläche unter „Ladevorgänge“ bzw. „Wallboxen“.\n')
            tls = cfg.get('smtp_tls', 'starttls')
            factory = smtplib.SMTP_SSL if tls == 'ssl' else smtplib.SMTP
            kwargs = {'context': ssl.create_default_context()} if tls == 'ssl' else {}
            with factory(cfg['smtp_host'], int(cfg.get('smtp_port') or 587), timeout=20, **kwargs) as smtp:
                if tls == 'starttls':
                    smtp.starttls(context=ssl.create_default_context())
                if cfg.get('smtp_user'):
                    smtp.login(cfg['smtp_user'], cfg.get('smtp_password') or '')
                smtp.send_message(msg)
        except Exception as exc:
            errors.append(f'E-Mail: {exc}')
    if cfg.get('webhook_url'):
        try:
            # "text" verstehen Slack, Mattermost, ntfy (JSON), n8n und HA-Webhooks direkt
            requests.post(cfg['webhook_url'], json={'title': subject, 'text': f'{subject}\n{text}',
                                                     'alerts': lines}, timeout=15).raise_for_status()
        except Exception as exc:
            errors.append(f'Webhook: {exc}')
    return errors


async def notify_loop(ctx) -> None:
    while True:
        await asyncio.sleep(CHECK_SECONDS)
        try:
            cfg = settings(ctx.config)
            if cfg.get('email_to') or cfg.get('webhook_url') or cfg.get('ha_token'):
                def deliver(subject, lines):
                    for err in send(cfg, subject, lines):
                        _LOGGER.error("Benachrichtigung nicht zugestellt — %s", err)
                await asyncio.to_thread(check_and_notify, ctx, deliver)
        except Exception as exc:   # Benachrichtigen darf nie den Betrieb stören
            _LOGGER.warning("Benachrichtigungen: %s", exc)
