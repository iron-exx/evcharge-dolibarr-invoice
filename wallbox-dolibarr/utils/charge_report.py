"""Ladenachweis je Karte bzw. Mitarbeiter und Monat.

Seit 01.01.2026 (BMF-Schreiben vom 11.11.2025) gibt es für das Laden eines
Dienstwagens beim Mitarbeiter keine Monatspauschale mehr: erstattet wird die
NACHGEWIESENE Strommenge — zum tatsächlichen Preis oder zur Strompreis-Pauschale
(2026: 34 ct/kWh). Dieser Nachweis listet jede geschäftliche Ladung mit den
Zählerständen der Wallbox. Private Karten gehören nicht hinein; von Hand
erfasste Ladungen werden genannt, aber als „ohne Zählernachweis“ markiert.
"""
import sqlite3
from datetime import datetime

COUNTED = ('pending', 'transmitted', 'rejected')


def _is_manual(row) -> bool:
    """add_manual_session: Zähler 0 → kWh, Dauer genau 1 Minute."""
    try:
        minutes = (datetime.fromisoformat(row['end_time']) - datetime.fromisoformat(row['start_time'])).total_seconds() / 60
    except (TypeError, ValueError):
        return False
    return bool(row.get('login')) or (not row.get('start_energy_kwh') and minutes == 1)


def build_report(session_manager, month: str, flat_price: float = 0.0) -> list:
    """→ [{key, name, rows, kwh, amount, incomplete}], nach Name sortiert."""
    from session_manager import session_state   # hier: session_manager importiert utils
    tags = {t['rfid_hash']: t for t in session_manager.list_tags()}
    conn = sqlite3.connect(session_manager.db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM sessions WHERE strftime('%Y-%m', start_time) = ? ORDER BY start_time, id", (month,))]
    conn.close()
    groups = {}
    for s in rows:
        tag = tags.get(s['rfid_hash']) or {}
        state = session_state(s)
        if state in ('private', 'discarded', 'active') or tag.get('mode') == 'private':
            continue
        key = f'login:{s["login"]}' if s.get('login') else s['rfid_hash'][:16]
        g = groups.setdefault(key, {'key': key, 'name': s.get('login') or tag.get('label') or
                                    f'Karte {s["rfid_hash"][:8]}…', 'rows': [], 'kwh': 0.0, 'incomplete': 0})
        if state == 'incomplete':
            g['incomplete'] += 1
            continue
        manual = _is_manual(s)
        g['rows'].append({'id': s['id'], 'start': s['start_time'], 'end': s['end_time'], 'wallbox': s['wallbox_id'],
                          'meter_start': None if manual else s.get('start_energy_kwh'),
                          'meter_end': None if manual else s.get('end_energy_kwh'),
                          'kwh': round(s.get('total_kwh') or 0.0, 3), 'manual': manual, 'state': state})
        g['kwh'] = round(g['kwh'] + (s.get('total_kwh') or 0.0), 3)
    for g in groups.values():
        g['amount'] = round(g['kwh'] * flat_price, 2) if flat_price else None
    return sorted((g for g in groups.values() if g['rows'] or g['incomplete']), key=lambda g: g['name'].lower())
