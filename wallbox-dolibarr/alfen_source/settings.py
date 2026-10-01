"""Auflösung der Alfen-HTTP-Konfiguration.

Wie bei Modbus bricht eine unbrauchbare Konfiguration ab, statt mit geratenen
Werten weiterzulaufen: ohne Host und Zugangsdaten gibt es keine Daten, und ein
falscher paramID würde den falschen Wert abrechnen.
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

_LOGGER = logging.getLogger(__name__)

# Alfen-paramIDs für Steckdose 1 (aus der HACS-Integration):
#   2221_22 = Zählerstand in kWh      2501_1 = Hauptzustand (Text)
DEFAULT_PARAM_ENERGY = '2221_22'
DEFAULT_PARAM_STATE = '2501_1'

_PARAM_RE = re.compile(r'^[0-9A-Fa-f]{1,8}_\d{1,3}$')
_HOST_RE = re.compile(r'^[A-Za-z0-9.\-]+(?::\d{1,5})?$')

_MIN_POLL, _MAX_POLL = 5.0, 3600.0
_MIN_TX, _MAX_TX = 30.0, 86400.0


class AlfenConfigError(Exception):
    """Die Alfen-Konfiguration ist unbrauchbar — das Addon darf nicht starten."""


@dataclass(frozen=True)
class AlfenSettings:
    enabled: bool
    base_url: str = ''
    username: str = ''
    # repr=False: die Einstellungen landen im Log und im System-Tab.
    password: str = field(default='', repr=False)
    display_name: str = 'ExpenseCharge'
    verify_ssl: bool = False
    timeout: float = 10.0
    poll_interval: float = 30.0
    transaction_interval: float = 300.0
    max_log_pages: int = 200
    param_energy: str = DEFAULT_PARAM_ENERGY
    param_state: str = DEFAULT_PARAM_STATE
    fixed_login: str = ''


def _required(raw: dict, key: str) -> str:
    value = str(raw.get(key) or '').strip()
    if not value:
        raise AlfenConfigError(f"alfen.{key} fehlt — ohne {key} ist kein Zugriff möglich")
    return value


def _clamped(raw, key, default, lo, hi):
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        _LOGGER.warning("alfen.%s: %r ist keine Zahl — es gilt %s", key, raw, default)
        return default
    clamped = min(max(value, lo), hi)
    if clamped != value:
        _LOGGER.warning("alfen.%s: %s außerhalb von %s–%s, auf %s gezogen",
                        key, value, lo, hi, clamped)
    return clamped


def _param(raw, key: str, default: str) -> str:
    value = str(raw if raw is not None else default).strip()
    if not _PARAM_RE.match(value):
        raise AlfenConfigError(
            f"alfen.{key}: {value!r} ist keine Alfen-paramID (erwartet z.B. 2221_22)")
    return value


def _base_url(host: str) -> str:
    """Host → vollständige URL. HTTPS ist die Vorgabe, http:// wird respektiert."""
    text = host.strip().rstrip('/')
    if text.startswith(('http://', 'https://')):
        return text
    if not _HOST_RE.match(text):
        raise AlfenConfigError(
            f"alfen.host: {text!r} ist keine IP, kein Hostname und keine URL")
    return f'https://{text}'


def resolve_alfen_settings(config: dict) -> AlfenSettings:
    if (config or {}).get('session_source', 'ha_sensors') != 'alfen_http':
        return AlfenSettings(enabled=False)

    raw = (config or {}).get('alfen') or {}
    if not isinstance(raw, dict):
        raise AlfenConfigError("alfen: muss ein Objekt sein")

    poll = _clamped(raw.get('poll_interval'), 'poll_interval', 30.0, _MIN_POLL, _MAX_POLL)
    tx = _clamped(raw.get('transaction_interval'), 'transaction_interval', 300.0,
                  _MIN_TX, _MAX_TX)
    if tx < poll:
        # Das Log zu lesen kostet die Wallbox deutlich mehr als eine
        # Eigenschaft — häufiger als der Normal-Takt ist nie sinnvoll.
        _LOGGER.warning("alfen.transaction_interval (%s s) war kürzer als poll_interval "
                        "(%s s) — auf %s s gesetzt", tx, poll, poll)
        tx = poll

    return AlfenSettings(
        enabled=True,
        base_url=_base_url(_required(raw, 'host')),
        username=_required(raw, 'username'),
        password=_required(raw, 'password'),
        display_name=str(raw.get('display_name') or 'ExpenseCharge').strip() or 'ExpenseCharge',
        verify_ssl=bool(raw.get('verify_ssl', False)),
        timeout=_clamped(raw.get('timeout'), 'timeout', 10.0, 1.0, 120.0),
        poll_interval=poll,
        transaction_interval=tx,
        max_log_pages=int(_clamped(raw.get('max_log_pages'), 'max_log_pages', 200, 1, 5000)),
        param_energy=_param(raw.get('param_energy'), 'param_energy', DEFAULT_PARAM_ENERGY),
        param_state=_param(raw.get('param_state'), 'param_state', DEFAULT_PARAM_STATE),
        fixed_login=str(raw.get('fixed_login') or '').strip(),
    )
