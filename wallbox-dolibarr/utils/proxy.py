"""TLS-Proxy (Caddy, docker-compose-Profil „tls“) vor ExpenseCharge.

Hinter dem Proxy kommt jede Verbindung von dessen Adresse. Die echte steht in
X-Forwarded-For — aber nur dem EIGENEN Proxy darf man das glauben, sonst setzt
sich jeder Absender eine beliebige Adresse und umgeht die Sperre nach
Fehlversuchen. Caddy ersetzt den Header eines Clients durch dessen echte
Adresse; ausgewertet wird der letzte Eintrag.
"""
import ipaddress
import os
import socket
import time

_CACHE = {'at': 0.0, 'ips': set()}
_TTL = 60


def domain() -> str:
    return (os.getenv('TLS_DOMAIN') or '').strip()


def trusted_proxies() -> set:
    """IP-Adressen des Proxy-Containers (Dienstname aus TRUSTED_PROXY, Vorgabe „tls“)."""
    if not domain():
        return set()
    if time.monotonic() - _CACHE['at'] > _TTL:
        host = os.getenv('TRUSTED_PROXY', 'tls')
        try:
            _CACHE['ips'] = {info[4][0] for info in socket.getaddrinfo(host, None)}
        except OSError:
            _CACHE['ips'] = set()
        _CACHE['at'] = time.monotonic()
    return _CACHE['ips']


def via_proxy(peer_ip: str) -> bool:
    return bool(peer_ip) and peer_ip in trusted_proxies()


def client_ip(peer_ip: str, forwarded_for) -> str:
    if forwarded_for and via_proxy(peer_ip):
        last = forwarded_for.split(',')[-1].strip()
        try:
            return str(ipaddress.ip_address(last))
        except ValueError:
            pass
    return peer_ip


def public_ws_url():
    """Adresse für Wallboxen über das Internet, oder None ohne TLS-Proxy."""
    return f'wss://{domain()}/ocpp/' if domain() else None
