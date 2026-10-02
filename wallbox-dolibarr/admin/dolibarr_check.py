"""'Verbindung testen': Schritt für Schritt, mit konkretem nächsten Schritt je Fehler.

DNS → TCP/TLS + HTTP → Token (employees.php des Moduls) → Modul-Version.
Synchron (requests); aus dem Event-Loop per asyncio.to_thread aufrufen.
"""
import socket
from urllib.parse import urlsplit

import requests

TIMEOUT = 8
_WHERE = 'unter Einstellungen → Dolibarr prüfen'


def _step(name, ok, detail):
    return {'step': name, 'ok': ok, 'detail': detail}


def check_dolibarr(url: str, token: str, verify_tls: bool = True) -> list:
    steps = []
    parts = urlsplit(url)
    host = parts.hostname or ''
    port = parts.port or (443 if parts.scheme == 'https' else 80)

    try:
        addrs = sorted({a[4][0] for a in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)})
        steps.append(_step('DNS', True, f'{host} → {", ".join(addrs[:3])}'))
    except (socket.gaierror, UnicodeError) as exc:
        steps.append(_step('DNS', False, f'Name {host} nicht auflösbar ({exc.args[-1]}) – URL {_WHERE}'))
        return steps

    try:
        r = requests.get(url, timeout=TIMEOUT, allow_redirects=True, verify=verify_tls)
    except requests.exceptions.SSLError as exc:
        steps.append(_step('TLS', False, f'TLS-Fehler bei {host}: {_short(exc)} – Zertifikat prüfen '
                                         '(abgelaufen, selbst signiert, falscher Name?)'))
        return steps
    except requests.exceptions.ConnectTimeout:
        steps.append(_step('Verbindung', False, f'{host}:{port} antwortet nicht (Timeout) – '
                                                'Firewall/VPN-Route zum Dolibarr-Server prüfen'))
        return steps
    except requests.exceptions.ConnectionError as exc:
        steps.append(_step('Verbindung', False, f'{host}:{port} nicht erreichbar: {_short(exc)} – '
                                                'läuft der Webserver, stimmt der Port?'))
        return steps
    except requests.exceptions.RequestException as exc:
        steps.append(_step('Verbindung', False, f'{_short(exc)}'))
        return steps
    if parts.scheme == 'https':
        steps.append(_step('TLS', True, 'Zertifikat gültig'))
    steps.append(_step('HTTP', r.status_code < 500, f'HTTP {r.status_code}'
                       + ('' if r.status_code < 500 else ' – Dolibarr meldet einen Serverfehler')))

    endpoint = f"{url.rstrip('/')}/custom/wallboxbilling/employees.php"
    try:
        r = requests.get(endpoint, headers={'DOLAPIKEY': token}, timeout=TIMEOUT, verify=verify_tls)
    except requests.exceptions.RequestException as exc:
        steps.append(_step('Token', False, f'Modul-Endpunkt nicht erreichbar: {_short(exc)}'))
        return steps
    if r.status_code == 200:
        try:
            count = len(r.json().get('employees') or [])
            detail = f'Token gültig ({count} Mitarbeiter mit Karte in Dolibarr)'
        except (ValueError, AttributeError):
            detail = 'Token gültig'
        steps.append(_step('Token', True, detail))
        version = r.headers.get('X-Wallboxbilling-Version')
        steps.append(_step('Modul', True, f'wallboxbilling {version}' if version else
                           'wallboxbilling installiert (Version wird erst ab Modul 2.3.6 gemeldet)'))
    elif r.status_code in (401, 403):
        steps.append(_step('Token', False, 'Token abgelehnt – muss identisch sein mit '
                                           'WALLBOXBILLING_API_TOKEN im Dolibarr-Modul (Einrichtung des Moduls)'))
    elif r.status_code == 404:
        steps.append(_step('Modul', False, 'Modul wallboxbilling nicht gefunden – in Dolibarr '
                                           'installieren und aktivieren, URL ohne /index.php angeben'))
    else:
        steps.append(_step('Token', False, f'Unerwartete Antwort HTTP {r.status_code} vom Modul'))
    return steps


def _short(exc) -> str:
    text = str(exc)
    return text if len(text) <= 160 else text[:157] + '…'
