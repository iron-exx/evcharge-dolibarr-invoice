"""Serverseitige Prüfung aller Eingaben der Verwaltungsoberfläche.

Jede Funktion gibt den bereinigten Wert zurück oder wirft ValueError mit einer
Meldung, die direkt in der Oberfläche steht.
"""
import re
from urllib.parse import urlsplit

_USERNAME = re.compile(r'^[A-Za-z0-9._-]{3,32}$')
# OCPP 1.6: Charge-Point-Identity, als letztes URL-Segment
_CP_ID = re.compile(r'^[A-Za-z0-9._:-]{1,48}$')
_WALLBOX_ID = re.compile(r'^[\w.-]{1,50}$')     # wie receive.php
_RFID = re.compile(r'^[A-Za-z0-9]{4,20}$')      # OCPP idTag: CiString20

MIN_ADMIN_PASSWORD = 10
MIN_OCPP_PASSWORD, MAX_OCPP_PASSWORD = 16, 40   # Security Whitepaper: 16–40 Zeichen


def username(value: str) -> str:
    value = (value or '').strip()
    if not _USERNAME.match(value):
        raise ValueError('Benutzername: 3–32 Zeichen, nur Buchstaben, Ziffern, . _ -')
    return value


def admin_password(value: str, repeat: str) -> str:
    if len(value or '') < MIN_ADMIN_PASSWORD:
        raise ValueError(f'Passwort: mindestens {MIN_ADMIN_PASSWORD} Zeichen')
    if value != repeat:
        raise ValueError('Die beiden Passwörter stimmen nicht überein')
    return value


def dolibarr_url(value: str) -> str:
    value = (value or '').strip().rstrip('/')
    parts = urlsplit(value)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise ValueError('Dolibarr-URL: vollständig mit http:// oder https:// angeben, '
                         'z.B. https://erp.firma.de')
    if parts.hostname == 'example.com' or parts.hostname.endswith('.example.com'):
        raise ValueError('Dolibarr-URL: das ist noch der Platzhalter aus der Vorlage')
    return value


def api_token(value: str) -> str:
    value = (value or '').strip()
    if len(value) < 8 or any(c.isspace() for c in value):
        raise ValueError('API-Token: mindestens 8 Zeichen, keine Leerzeichen — '
                         'identisch mit WALLBOXBILLING_API_TOKEN im Dolibarr-Modul')
    return value


def charge_point_id(value: str) -> str:
    value = (value or '').strip()
    if not _CP_ID.match(value):
        raise ValueError('Charge-Point-ID: 1–48 Zeichen, Buchstaben, Ziffern, . _ : - '
                         '(steht in der Wallbox-Konfiguration, bei Alfen die Seriennummer)')
    return value


def wallbox_id(value: str) -> str:
    value = (value or '').strip()
    if not _WALLBOX_ID.match(value):
        raise ValueError('wallbox_id: 1–50 Zeichen, Buchstaben, Ziffern, . _ -')
    return value


def ocpp_password(value: str) -> str:
    value = value or ''
    if not MIN_OCPP_PASSWORD <= len(value) <= MAX_OCPP_PASSWORD:
        raise ValueError(f'OCPP-Passwort: {MIN_OCPP_PASSWORD}–{MAX_OCPP_PASSWORD} Zeichen')
    if not value.isprintable():
        raise ValueError('OCPP-Passwort: keine Steuerzeichen')
    return value


def label(value: str, max_len: int = 60) -> str:
    value = (value or '').strip()
    if len(value) > max_len or not value.isprintable():
        raise ValueError(f'Bezeichnung: höchstens {max_len} druckbare Zeichen')
    return value


def rfid(value: str) -> str:
    value = (value or '').strip().upper()
    if not _RFID.match(value):
        raise ValueError(f'Karten-ID „{value}“: 4–20 Zeichen, nur Buchstaben und Ziffern')
    return value


def card_lines(text: str) -> list:
    """'EFCD083E; Firmenwagen' je Zeile → [(uid, bezeichnung)]. Leere Zeilen zählen nicht."""
    cards, seen = [], set()
    for line in (text or '').splitlines():
        if not line.strip():
            continue
        uid, _, name = line.replace(',', ';').partition(';')
        uid = rfid(uid)
        if uid in seen:
            continue
        seen.add(uid)
        cards.append((uid, label(name)))
    return cards
