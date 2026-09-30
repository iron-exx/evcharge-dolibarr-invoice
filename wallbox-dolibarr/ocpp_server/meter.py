"""Umrechnung von OCPP-Zählerständen und -Zeitstempeln.

OCPP 1.6: meterStart/meterStop sind Integer in Wh. MeterValues tragen eine
optionale Einheit (Default Wh) und einen optionalen Measurand (Default
Energy.Active.Import.Register). Zeitstempel SOLLEN UTC sein; das Addon
speichert — wie der bestehende Code — lokale, naive ISO-Zeitstempel.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

ENERGY_MEASURAND = 'Energy.Active.Import.Register'
_MIN_PLAUSIBLE_YEAR = 2020
_MAX_CLOCK_SKEW = timedelta(days=1)


def wh_to_kwh(value) -> Optional[float]:
    """Wh → kWh; None bei nicht-numerischem Wert."""
    try:
        return float(value) / 1000.0
    except (TypeError, ValueError):
        return None


def extract_energy_kwh(meter_values) -> Optional[float]:
    """Letzter Gesamt-Zählerstand in kWh aus einer MeterValue-Liste.

    Erwartet die snake_case-Keys, wie sie die ocpp-Bibliothek an die Handler
    übergibt (`sampled_value`). Berücksichtigt nur den Summenwert (ohne
    `phase`), ignoriert signierte Werte und unbekannte Einheiten.
    """
    result = None
    for meter_value in meter_values or []:
        for sample in meter_value.get('sampled_value') or []:
            if sample.get('measurand', ENERGY_MEASURAND) != ENERGY_MEASURAND:
                continue
            if sample.get('phase') or sample.get('format') == 'SignedData':
                continue
            try:
                value = float(sample.get('value'))
            except (TypeError, ValueError):
                continue
            unit = sample.get('unit', 'Wh')
            if unit == 'kWh':
                result = value
            elif unit == 'Wh':
                result = value / 1000.0
    return result


def to_local_naive_iso(ocpp_timestamp, now: Optional[datetime] = None) -> str:
    """OCPP-Zeitstempel → lokaler, naiver ISO-String (Sekundengenauigkeit).

    Zeitstempel ohne Zeitzone gelten als UTC (OCPP-Vorgabe). Unlesbare oder
    unplausible Werte (Uhr der Wallbox nicht gestellt: Jahr < 2020, oder mehr
    als 1 Tag in der Zukunft) → aktuelle Addon-Zeit.
    """
    now = now or datetime.now()
    fallback = now.replace(microsecond=0).isoformat()
    try:
        parsed = datetime.fromisoformat(str(ocpp_timestamp))
    except ValueError:
        return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    local = parsed.astimezone().replace(tzinfo=None)
    if local.year < _MIN_PLAUSIBLE_YEAR or local > now + _MAX_CLOCK_SKEW:
        return fallback
    return local.replace(microsecond=0).isoformat()
