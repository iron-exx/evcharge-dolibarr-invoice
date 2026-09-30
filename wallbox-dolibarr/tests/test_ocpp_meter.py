import os, sys, time
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from ocpp_server.meter import extract_energy_kwh, to_local_naive_iso, wh_to_kwh  # noqa: E402


@pytest.fixture()
def berlin_tz(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Berlin")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _mv(*samples):
    return [{"timestamp": "2026-09-30T10:00:00Z", "sampled_value": list(samples)}]


def test_wh_to_kwh():
    assert wh_to_kwh(1234567) == pytest.approx(1234.567)
    assert wh_to_kwh(None) is None
    assert wh_to_kwh("abc") is None


def test_energy_default_unit_is_wh():
    assert extract_energy_kwh(_mv({"value": "12500"})) == pytest.approx(12.5)


def test_energy_kwh_unit():
    assert extract_energy_kwh(_mv({"value": "12.5", "unit": "kWh",
                                   "measurand": "Energy.Active.Import.Register"})) == pytest.approx(12.5)


def test_energy_ignores_other_measurands_phases_and_signed():
    samples = _mv(
        {"value": "7400", "unit": "W", "measurand": "Power.Active.Import"},
        {"value": "4000", "phase": "L1"},
        {"value": "AB12", "format": "SignedData"},
        {"value": "3.2", "unit": "kWh"},
    )
    assert extract_energy_kwh(samples) == pytest.approx(3.2)


def test_energy_none_when_missing_or_garbage():
    assert extract_energy_kwh(None) is None
    assert extract_energy_kwh(_mv({"value": "n/a"})) is None
    assert extract_energy_kwh(_mv({"value": "5", "unit": "Percent"})) is None


def test_timestamp_utc_to_local(berlin_tz):
    now = datetime(2026, 9, 30, 13, 0, 0)
    assert to_local_naive_iso("2026-09-30T10:15:30.123Z", now=now) == "2026-09-30T12:15:30"
    assert to_local_naive_iso("2026-09-30T10:15:30+00:00", now=now) == "2026-09-30T12:15:30"


def test_timestamp_without_zone_is_utc(berlin_tz):
    now = datetime(2026, 9, 30, 13, 0, 0)
    assert to_local_naive_iso("2026-09-30T10:15:30", now=now) == "2026-09-30T12:15:30"


def test_timestamp_implausible_falls_back_to_now(berlin_tz):
    now = datetime(2026, 9, 30, 13, 0, 0)
    assert to_local_naive_iso("1970-01-01T00:00:00Z", now=now) == "2026-09-30T13:00:00"
    assert to_local_naive_iso("2027-01-01T00:00:00Z", now=now) == "2026-09-30T13:00:00"
    assert to_local_naive_iso("kaputt", now=now) == "2026-09-30T13:00:00"
    assert to_local_naive_iso(None, now=now) == "2026-09-30T13:00:00"
