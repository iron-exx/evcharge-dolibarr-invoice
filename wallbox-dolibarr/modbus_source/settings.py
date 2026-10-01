"""Auflösung der Modbus-Konfiguration aus options.json.

Es gibt keine herstellerübergreifende Registerkarte — jede Wallbox legt ihre
Adressen, Typen und Skalierungen selbst fest. Deshalb ist hier alles explizit
konfigurierbar, statt Profile zu erraten: eine falsch geratene Adresse würde
falsche kWh abrechnen.

Eine ungültige Konfiguration bricht mit ModbusConfigError ab, statt mit
Standardwerten weiterzulaufen. Lieber ein Addon, das nicht startet, als eines,
das stillschweigend 0 kWh abrechnet.
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from modbus_source.registers import VALID_TYPES, VALID_WORD_ORDERS

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 502
DEFAULT_POLL_INTERVAL = 5.0
# Wie lange ein gelesener Tag als "liegt an" gilt. Viele Wallboxen halten im
# Tag-Register dauerhaft die LETZTE Karte; danach erzeugt der Poller selbst den
# Rückfall auf "kein Tag". Ohne das bliebe der Zustand für immer auf dem Tag
# stehen und jede Zustandslogik käme durcheinander.
DEFAULT_RFID_HOLD_SECONDS = 1.0
_MIN_POLL_INTERVAL = 1.0          # schneller verkraften viele Wallboxen nicht
_MAX_POLL_INTERVAL = 300.0
VALID_FUNCTION_CODES = (3, 4)


class ModbusConfigError(Exception):
    """Die Modbus-Konfiguration ist unbrauchbar — das Addon darf nicht starten."""


@dataclass(frozen=True)
class RegisterSpec:
    address: int
    type: str = 'uint16'
    word_order: str = 'big'
    scale: float = 1.0
    count: int = 1
    state_map: Dict[int, str] = field(default_factory=dict)

    def translate(self, value: Any) -> Any:
        """Zahlencode → Text für die bestehende Zustandslogik.

        Ein unbekannter Code wird als Zahl durchgereicht, nicht verworfen:
        so steht er im Log und in der Web-UI und kann ergänzt werden.
        """
        if not self.state_map:
            return value
        try:
            return self.state_map[int(value)]
        except (KeyError, TypeError, ValueError):
            return str(value)


@dataclass(frozen=True)
class ModbusSettings:
    enabled: bool
    host: str = ''
    port: int = DEFAULT_PORT
    unit_id: int = 1
    function_code: int = 3
    timeout: float = 5.0
    poll_interval: float = DEFAULT_POLL_INTERVAL
    rfid_hold_seconds: float = DEFAULT_RFID_HOLD_SECONDS
    fixed_login: str = ''
    energy: Optional[RegisterSpec] = None
    state: Optional[RegisterSpec] = None
    rfid: Optional[RegisterSpec] = None
    power: Optional[RegisterSpec] = None


def _int(raw, name: str, default: int) -> int:
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ModbusConfigError(f"modbus.{name}: {raw!r} ist keine ganze Zahl")


def _state_map(raw, name: str) -> Dict[int, str]:
    """Zahlencode → Text, in zwei Schreibweisen.

    Dict  ({3: "Charging"}) ist die natürliche Form für eine handgeschriebene
    options.json. Das Konfigurationsschema von Home Assistant kann ein Dict
    mit beliebigen Schlüsseln aber nicht validieren — dafür gibt es die
    Listenform ["3:Charging"]. Der Text darf selbst Doppelpunkte enthalten,
    getrennt wird nur am ersten.
    """
    if not raw:
        return {}

    pairs = []
    if isinstance(raw, dict):
        pairs = list(raw.items())
    elif isinstance(raw, (list, tuple)):
        for entry in raw:
            code, sep, text = str(entry).partition(':')
            if not sep:
                raise ModbusConfigError(
                    f"modbus.registers.{name}.state_map: {entry!r} hat kein "
                    f"'code:Text' — erwartet z.B. \"3:Charging\"")
            pairs.append((code, text))
    else:
        raise ModbusConfigError(
            f"modbus.registers.{name}.state_map muss eine Liste oder ein Objekt sein")

    out = {}
    for code, text in pairs:
        try:
            out[int(str(code).strip())] = str(text).strip()
        except (TypeError, ValueError):
            raise ModbusConfigError(
                f"modbus.registers.{name}.state_map: {code!r} ist kein Zahlencode")
    return out


def _register(raw, name: str, default_type: str, default_count: int) -> RegisterSpec:
    if not isinstance(raw, dict):
        raise ModbusConfigError(f"modbus.registers.{name} muss ein Objekt sein")
    if 'address' not in raw:
        raise ModbusConfigError(f"modbus.registers.{name}: 'address' fehlt")

    value_type = str(raw.get('type', default_type))
    if value_type != 'string' and value_type not in VALID_TYPES:
        raise ModbusConfigError(
            f"modbus.registers.{name}: type {value_type!r} unbekannt — "
            f"erlaubt: {', '.join(VALID_TYPES)}, string")

    word_order = str(raw.get('word_order', 'big'))
    if word_order not in VALID_WORD_ORDERS:
        raise ModbusConfigError(
            f"modbus.registers.{name}: word_order {word_order!r} unbekannt — "
            f"erlaubt: {', '.join(VALID_WORD_ORDERS)}")

    try:
        scale = float(raw.get('scale', 1.0))
    except (TypeError, ValueError):
        raise ModbusConfigError(f"modbus.registers.{name}: scale {raw.get('scale')!r} ist keine Zahl")

    # Wie viele Register gelesen werden müssen: bei 32-Bit-Typen zwei, bei
    # Text so viele wie konfiguriert.
    if value_type == 'string':
        count = _int(raw.get('count'), f"registers.{name}.count", default_count)
    else:
        count = 2 if value_type in ('uint32', 'int32', 'float32') else 1

    state_map = _state_map(raw.get('state_map'), name)

    return RegisterSpec(address=_int(raw['address'], f"registers.{name}.address", 0),
                        type=value_type, word_order=word_order, scale=scale,
                        count=count, state_map=state_map)


def resolve_modbus_settings(config: dict) -> ModbusSettings:
    if config.get('session_source', 'ha_sensors') != 'modbus':
        return ModbusSettings(enabled=False)

    raw = config.get('modbus') or {}
    if not isinstance(raw, dict):
        raise ModbusConfigError("modbus: muss ein Objekt sein")

    host = str(raw.get('host') or '').strip()
    if not host:
        raise ModbusConfigError("modbus.host fehlt — IP oder Hostname der Wallbox angeben")

    function_code = _int(raw.get('function_code'), 'function_code', 3)
    if function_code not in VALID_FUNCTION_CODES:
        raise ModbusConfigError(
            f"modbus.function_code muss 3 (Holding) oder 4 (Input) sein, war {function_code}")

    registers = raw.get('registers') or {}
    if not isinstance(registers, dict) or 'energy' not in registers:
        raise ModbusConfigError(
            "modbus.registers.energy fehlt — ohne Zählerstand kann nicht abgerechnet werden")

    try:
        poll_interval = float(raw.get('poll_interval', DEFAULT_POLL_INTERVAL))
    except (TypeError, ValueError):
        raise ModbusConfigError(f"modbus.poll_interval: {raw.get('poll_interval')!r} ist keine Zahl")
    clamped = min(max(poll_interval, _MIN_POLL_INTERVAL), _MAX_POLL_INTERVAL)
    if clamped != poll_interval:
        _LOGGER.warning("modbus.poll_interval %s auf %s s angepasst (erlaubt %s–%s)",
                        poll_interval, clamped, _MIN_POLL_INTERVAL, _MAX_POLL_INTERVAL)

    try:
        timeout = float(raw.get('timeout', 5.0))
    except (TypeError, ValueError):
        raise ModbusConfigError(f"modbus.timeout: {raw.get('timeout')!r} ist keine Zahl")

    try:
        rfid_hold = float(raw.get('rfid_hold_seconds', DEFAULT_RFID_HOLD_SECONDS))
    except (TypeError, ValueError):
        raise ModbusConfigError(
            f"modbus.rfid_hold_seconds: {raw.get('rfid_hold_seconds')!r} ist keine Zahl")
    if rfid_hold < 0:
        raise ModbusConfigError("modbus.rfid_hold_seconds darf nicht negativ sein")

    fixed_login = str(raw.get('fixed_login') or '').strip()
    if fixed_login and 'rfid' in registers:
        _LOGGER.warning("modbus: fixed_login UND ein rfid-Register gesetzt — die gelesene "
                        "Karte hat Vorrang, fixed_login greift nur als Rückfall")

    return ModbusSettings(
        enabled=True,
        host=host,
        port=_int(raw.get('port'), 'port', DEFAULT_PORT),
        unit_id=_int(raw.get('unit_id'), 'unit_id', 1),
        function_code=function_code,
        timeout=timeout,
        poll_interval=clamped,
        rfid_hold_seconds=rfid_hold,
        fixed_login=fixed_login,
        energy=_register(registers['energy'], 'energy', 'uint32', 2),
        state=_register(registers['state'], 'state', 'uint16', 1) if 'state' in registers else None,
        rfid=_register(registers['rfid'], 'rfid', 'string', 4) if 'rfid' in registers else None,
        power=_register(registers['power'], 'power', 'uint32', 2) if 'power' in registers else None,
    )
