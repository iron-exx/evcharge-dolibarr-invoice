"""Ladenachweis je Karte/Mitarbeiter und Monat (BMF-Schreiben 11.11.2025)."""
import os
import sqlite3
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from session_manager import SessionManager  # noqa: E402
from utils.charge_report import build_report  # noqa: E402
from utils.hash import hash_rfid  # noqa: E402

MAX, EVA, PRIV = hash_rfid('04A1B2C3'), hash_rfid('EFCD083E'), hash_rfid('99887766')


def _ins(sm, h, start, end, e0, e1, status='completed', tx=None):
    conn = sqlite3.connect(sm.db_path)
    conn.execute("INSERT INTO sessions (rfid_hash, wallbox_id, start_time, end_time, start_energy_kwh, end_energy_kwh,"
                 " total_kwh, status, created_at, transmitted_at) VALUES (?, 'Wallbox_1', ?, ?, ?, ?, ?, ?, ?, ?)",
                 (h, start, end, e0, e1, None if e1 is None else round(e1 - e0, 3), status, start, tx))
    conn.commit()
    conn.close()


@pytest.fixture()
def sm(tmp_path):
    sm = SessionManager(db_path=str(tmp_path / 's.db'))
    sm.upsert_tag('04A1B2C3', 'Max Müller', 'business')
    sm.upsert_tag('EFCD083E', 'Eva Schmidt', 'business')
    sm.upsert_tag('99887766', 'Privatwagen', 'private')
    _ins(sm, MAX, '2026-10-01T18:00:00', '2026-10-01T21:00:00', 1000.0, 1012.5, tx='2026-10-01T21:05:00')
    _ins(sm, MAX, '2026-10-03T18:00:00', '2026-10-03T19:00:00', 1012.5, 1020.0)
    _ins(sm, MAX, '2026-10-04T18:00:00', '2026-10-04T19:00:00', 1020.0, None, status='incomplete')
    _ins(sm, EVA, '2026-10-02T08:00:00', '2026-10-02T09:00:00', 500.0, 507.25)
    _ins(sm, PRIV, '2026-10-02T10:00:00', '2026-10-02T11:00:00', 600.0, 640.0, status='private')
    _ins(sm, MAX, '2026-09-30T18:00:00', '2026-09-30T19:00:00', 990.0, 1000.0)     # anderer Monat
    sm.add_manual_session(kwh=4.0, wallbox_id='Wallbox_1', session_date='2026-10-05', rfid_hash=MAX)
    return sm


def test_groups_per_card_with_meter_readings_and_flat_price(sm):
    report = build_report(sm, '2026-10', flat_price=0.34)
    by_name = {g['name']: g for g in report}
    assert set(by_name) == {'Max Müller', 'Eva Schmidt'}, "privat gehört nicht in den Dienstwagen-Nachweis"
    mx = by_name['Max Müller']
    assert [r['kwh'] for r in mx['rows']] == [12.5, 7.5, 4.0]
    assert mx['rows'][0]['meter_start'] == 1000.0 and mx['rows'][0]['meter_end'] == 1012.5
    assert mx['rows'][2]['manual'] and mx['rows'][2]['meter_start'] is None, "manuell = kein Zählernachweis"
    assert mx['kwh'] == 24.0 and mx['amount'] == pytest.approx(8.16)
    assert mx['incomplete'] == 1, "unvollständige werden genannt, nicht mitgezählt"


def test_without_flat_price_no_amount(sm):
    assert all(g['amount'] is None for g in build_report(sm, '2026-10', flat_price=0))


async def test_report_page_and_csv(sm):
    from aiohttp.test_utils import TestClient, TestServer
    from app_settings import resolve_app_settings
    from web_server import create_app
    config = {'tax_flat_price': 0.34}
    app = create_app(sm, config, {'client': None, 'settings': resolve_app_settings(config)})
    async with TestClient(TestServer(app)) as c:
        page = await (await c.get('/report?month=2026-10')).text()
        assert 'Ladenachweis Dienstwagen · Oktober 2026' in page and 'Max Müller' in page and 'Eva Schmidt' in page
        for needle in ("1.000,000", "24,000", "8,16 €", "kein Zählernachweis"):
            assert needle in page, needle
        assert 'Privatwagen' not in page and 'BMF-Schreiben vom 11.11.2025' in page
        assert (await c.get('/report?month=../x')).status == 200, "ungültiger Monat → aktueller"
        csv_text = (await (await c.get('/report.csv?month=2026-10')).read()).decode('utf-8-sig')
        assert 'Max Müller;Summe;;;;;;24,000;Erstattung 8,16 €' in csv_text
        hist = await (await c.get('/history?year=2026&month=10')).text()
        assert 'href="report?month=2026-10"' in hist
