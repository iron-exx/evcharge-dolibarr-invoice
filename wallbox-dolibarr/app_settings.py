#!/usr/bin/env python3
"""Zentrale Auflösung aller Betriebsparameter aus options.json.

Warum zentral: die Werte hier waren über fünf Module fest verdrahtet. Wer
einen davon ändern wollte, musste den Quellcode anfassen — und niemand konnte
nachsehen, was gerade wirkt. Jetzt gibt es EINE Stelle, und die Oberfläche
zeigt die wirksame Konfiguration an (`as_rows()`).

Zwei Grundsätze:

1. **Die Vorgaben entsprechen genau dem bisherigen Verhalten.** Eine bestehende
   Anlage ändert sich durch ein Update nicht.
2. **Unsinnige Werte werden begrenzt, nicht abgelehnt.** Ein Tippfehler darf
   das Addon nicht lahmlegen — aber auch nicht wirken. Jede Begrenzung wird
   protokolliert, damit sie nicht stillschweigend passiert.

Die Ausnahme von Grundsatz 2 ist die Modbus-Registerkarte (siehe
modbus_source/settings.py): dort bricht eine unbrauchbare Konfiguration ab,
weil ein geratener Standardwert falsche kWh abrechnen würde.
"""
import logging
import os
import re
from dataclasses import dataclass, field, fields
from typing import Any, Optional

_LOGGER = logging.getLogger(__name__)

VALID_LOG_LEVELS = ('DEBUG', 'INFO', 'WARNING', 'ERROR')

# Nur Adressen, an die man sinnvoll binden kann. Kein Hostname: ein Tippfehler
# dort würde den Server stumm auf etwas anderem lauschen lassen.
_BIND_RE = re.compile(r'^(?:\d{1,3}\.){3}\d{1,3}$|^::$|^::1$')

# key → (Label für die Oberfläche, Vorgabe, Minimum, Maximum, Typ)
_SPEC = (
    ('web_port',           'Port der Web-UI',                    8099,  1, 65535, int),
    ('web_bind',           'Bind-Adresse der Web-UI',       '0.0.0.0', None, None, str),
    ('ocpp_port',          'Port des OCPP-Servers',              9000,  1, 65535, int),
    ('ocpp_bind',          'Bind-Adresse des OCPP-Servers', '0.0.0.0', None, None, str),
    ('debounce_seconds',   'RFID-Entprellung (s)',                  7,  1,   120, int),
    ('max_plausible_kw',   'Plausibilitätsgrenze Leistung (kW)',  50.0, 1.0, 400.0, float),
    ('max_discard_hours',  'Kurz-Session-Fenster (h)',            0.25, 0.0,  24.0, float),
    ('pending_auth_window', 'Gültigkeit vorgehaltener Karte (s)',  600, 10, 86400, int),
    ('api_timeout',        'Dolibarr-Zeitlimit (s)',               30,  5,   300, int),
    ('api_retries',        'Dolibarr-Wiederholungen',               5,  0,    10, int),
    ('api_backoff',        'Dolibarr-Wartefaktor',                 0.5, 0.0,  30.0, float),
    ('learn_ttl_seconds',  'Lernmodus: Klartext-Haltezeit (s)',  600.0, 30.0, 3600.0, float),
    ('learn_max_entries',  'Lernmodus: max. Karten in der Liste',   10,  1,   100, int),
    ('trend_days',         'Tagesstreifen: Fenster (Tage)',         14,  1,    90, int),
)


@dataclass(frozen=True)
class AppSettings:
    log_level: str = 'INFO'
    web_port: int = 8099
    web_bind: str = '0.0.0.0'
    ocpp_port: int = 9000
    ocpp_bind: str = '0.0.0.0'
    debounce_seconds: int = 7
    max_plausible_kw: float = 50.0
    max_discard_hours: float = 0.25
    pending_auth_window: int = 600
    api_timeout: int = 30
    api_retries: int = 5
    api_backoff: float = 0.5
    learn_ttl_seconds: float = 600.0
    learn_max_entries: int = 10
    trend_days: int = 14
    _changed: frozenset = field(default_factory=frozenset, repr=False, compare=False)

    def as_rows(self) -> list:
        """Für die Oberfläche: Label, wirksamer Wert, Vorgabe, abweichend?"""
        rows = [{'key': 'log_level', 'label': 'Protokoll-Detailgrad',
                 'value': self.log_level, 'default': 'INFO',
                 'changed': 'log_level' in self._changed}]
        for key, label, default, _lo, _hi, _t in _SPEC:
            rows.append({'key': key, 'label': label, 'value': getattr(self, key),
                         'default': default, 'changed': key in self._changed})
        return rows


def _number(raw: Any, key: str, default, lo, hi, cast):
    """Zahl lesen, begrenzen, Begrenzung protokollieren."""
    if raw is None:
        return default, False
    try:
        value = cast(raw)
    except (TypeError, ValueError):
        _LOGGER.warning("%s: %r ist keine Zahl — es gilt der Standard %s", key, raw, default)
        return default, False
    clamped = min(max(value, cast(lo)), cast(hi))
    if clamped != value:
        _LOGGER.warning("%s: %s liegt außerhalb von %s–%s und wurde auf %s gezogen",
                        key, value, lo, hi, clamped)
    return clamped, True


def _bind(raw: Any, key: str, default: str):
    if raw is None:
        return default, False
    text = str(raw).strip()
    if not _BIND_RE.match(text):
        _LOGGER.warning("%s: %r ist keine IP-Adresse — es gilt %s", key, text, default)
        return default, False
    return text, True


def _log_level(config: dict):
    """Umgebung hat Vorrang vor der Option — so lässt sich im Container
    debuggen, ohne die Konfiguration zu ändern."""
    from_env = os.getenv('LOG_LEVEL')
    raw = from_env if from_env else config.get('log_level')
    if raw is None:
        return 'INFO', False
    level = str(raw).strip().upper()
    if level not in VALID_LOG_LEVELS:
        _LOGGER.warning("log_level: %r unbekannt — erlaubt sind %s, es gilt INFO",
                        raw, ', '.join(VALID_LOG_LEVELS))
        return 'INFO', False
    return level, level != 'INFO'


def resolve_app_settings(config: Optional[dict] = None) -> AppSettings:
    config = config or {}
    values = {}
    changed = set()

    level, level_changed = _log_level(config)
    values['log_level'] = level
    if level_changed:
        changed.add('log_level')

    for key, _label, default, lo, hi, cast in _SPEC:
        raw = config.get(key)
        if cast is str:
            value, was_set = _bind(raw, key, default)
        else:
            value, was_set = _number(raw, key, default, lo, hi, cast)
        values[key] = value
        if was_set and value != default:
            changed.add(key)

    return AppSettings(_changed=frozenset(changed), **values)
