"""Auflösung der OCPP-Konfiguration aus /data/options.json."""
import logging
import re
from dataclasses import dataclass
from typing import Optional, Tuple

_LOGGER = logging.getLogger(__name__)

DEFAULT_HEARTBEAT_INTERVAL = 300
_MIN_HEARTBEAT, _MAX_HEARTBEAT = 30, 3600
_MIN_PASSWORD_LEN = 16          # OCPP-1.6-Security-Whitepaper: AuthorizationKey >= 16 Byte
_WALLBOX_ID_INVALID = re.compile(r'[^\w\-.]')   # receive.php: ^[\w\-\.]{1,50}$


@dataclass(frozen=True)
class ChargePointConfig:
    id: str            # Charge-Point-Identity = letztes Segment der Verbindungs-URL
    password: str      # '' = ohne Basic Auth (Security Profile 0)
    wallbox_id: str    # Wert, der als wallbox_id an Dolibarr geht


@dataclass(frozen=True)
class OcppSettings:
    enabled: bool
    charge_points: Tuple[ChargePointConfig, ...]
    heartbeat_interval: int
    apply_recommended_config: bool

    def find(self, cp_id: str) -> Optional[ChargePointConfig]:
        for cp in self.charge_points:
            if cp.id == cp_id:
                return cp
        return None


def sanitize_wallbox_id(raw) -> str:
    cleaned = _WALLBOX_ID_INVALID.sub('_', str(raw or '').strip())[:50]
    return cleaned or 'wallbox'


def _heartbeat(raw) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_HEARTBEAT_INTERVAL
    return min(max(value, _MIN_HEARTBEAT), _MAX_HEARTBEAT)


def resolve_ocpp_settings(config: dict) -> OcppSettings:
    charge_points = []
    seen = set()
    for entry in config.get('ocpp_charge_points') or []:
        if not isinstance(entry, dict):
            continue
        cp_id = str(entry.get('id') or '').strip()
        if not cp_id:
            _LOGGER.warning("ocpp_charge_points: Eintrag ohne 'id' wird ignoriert")
            continue
        if cp_id in seen:
            _LOGGER.warning("ocpp_charge_points: doppelte id '%s' — nur der erste Eintrag zählt", cp_id)
            continue
        seen.add(cp_id)
        password = str(entry.get('password') or '')
        if not password:
            _LOGGER.warning("Wallbox '%s' ohne Passwort (Security Profile 0) — nur in "
                            "vertrauenswürdigem LAN/VPN betreiben", cp_id)
        elif len(password) < _MIN_PASSWORD_LEN:
            _LOGGER.warning("Wallbox '%s': Passwort kürzer als %d Zeichen — manche Wallboxen "
                            "lehnen das ab", cp_id, _MIN_PASSWORD_LEN)
        charge_points.append(ChargePointConfig(
            id=cp_id,
            password=password,
            wallbox_id=sanitize_wallbox_id(entry.get('wallbox_id') or cp_id),
        ))
    return OcppSettings(
        enabled=config.get('session_source', 'ha_sensors') == 'ocpp',
        charge_points=tuple(charge_points),
        heartbeat_interval=_heartbeat(config.get('ocpp_heartbeat_interval', DEFAULT_HEARTBEAT_INTERVAL)),
        apply_recommended_config=bool(config.get('ocpp_apply_recommended_config', False)),
    )
