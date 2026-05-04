#!/usr/bin/env python3
"""
Session Manager für Wallbox-Ladevorgänge

Verwaltet SQLite-Datenbank für Lade-Sessions:
- Session-Start (RFID-Validierung, Whitelist-Check)
- Session-Ende (Energie-Berechnung, Zeitstempel)
- RFID-Debouncing (5-10 Sekunden Unterdrückung)
- Persistenz für Neustart-Überlebung
"""
import sqlite3
import time
import logging
from typing import Optional, Dict, Any
from datetime import datetime

# Hash-Utility importieren
import sys
sys.path.insert(0, '/usr/local/bin')
from utils.hash import hash_rfid, verify_rfid_hash

# Status-Konstanten (D-16)
CHARGING = "Charging"
IDLE = "Idle"
STOPPED = "Stopped"

# Debounce-Zeit in Sekunden (HA-07)
DEBOUNCE_SECONDS = 7

class SessionManager:
    """Verwaltet Lade-Sessions in SQLite"""

    def __init__(self, db_path: str = "/data/sessions.db"):
        self.db_path = db_path
        self._init_database()
        self._last_rfid_time: Dict[str, float] = {}  # Für Debouncing
        self._logger = logging.getLogger(__name__)

    def _init_database(self):
        """Initialisiert die SQLite-Datenbank mit Sessions-Tabelle (PER-01, DB-01 Vorbereitung)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Tabelle für Lade-Sessions (HA-03, Felder für Phase 2)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rfid_hash TEXT NOT NULL,
                wallbox_id TEXT NOT NULL DEFAULT 'alfen_eve',
                start_time TEXT NOT NULL,
                end_time TEXT,
                start_energy_kwh REAL NOT NULL DEFAULT 0.0,
                end_energy_kwh REAL,
                total_kwh REAL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL
            )
        ''')

        # Index für rfid_hash (DB-02 Vorbereitung)
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_rfid_hash ON sessions(rfid_hash)
        ''')

        # Index für status (für aktive Sessions)
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_status ON sessions(status)
        ''')

        conn.commit()
        conn.close()
        self._logger.info("SQLite Datenbank initialisiert: %s", self.db_path)

    def debounce_rfid(self, rfid_hex: str) -> bool:
        """
        Prüft ob RFID-Lesung innerhalb des Debounce-Intervalls liegt (HA-07)

        Args:
            rfid_hex: RFID als Hex-String

        Returns:
            True wenn RFID akzeptiert wird (nicht debounced)
        """
        current_time = time.time()
        rfid_hash = hash_rfid(rfid_hex)

        if rfid_hash in self._last_rfid_time:
            time_diff = current_time - self._last_rfid_time[rfid_hash]
            if time_diff < DEBOUNCE_SECONDS:
                self._logger.debug("RFID debounced: %s (%.1fs < %ds)",
                                rfid_hash[:16], time_diff, DEBOUNCE_SECONDS)
                return False

        self._last_rfid_time[rfid_hash] = current_time
        return True

    def is_rfid_authorized(self, rfid_hex: str, whitelist: list) -> bool:
        """
        Prüft ob RFID in der Whitelist ist (HA-02, HA-04)

        Args:
            rfid_hex: RFID als Hex-String
            whitelist: Liste der erlaubten RFID-Karten (aus config.yaml)

        Returns:
            True wenn RFID autorisiert ist
        """
        if not whitelist:
            self._logger.warning("Keine RFID-Whitelist konfiguriert")
            return False

        rfid_hash = hash_rfid(rfid_hex)

        # Whitelist enthält Hex-Strings, wir vergleichen Hashes
        for whitelisted_rfid in whitelist:
            if verify_rfid_hash(whitelisted_rfid, rfid_hash):
                self._logger.info("RFID autorisiert: %s...", rfid_hash[:16])
                return True

        self._logger.warning("RFID NICHT autorisiert: %s...", rfid_hash[:16])
        return False

    def get_active_session(self) -> Optional[Dict[str, Any]]:
        """
        Holt die aktuell aktive Session (für Neustart-Recovery, PER-01)

        Returns:
            Session-Dict oder None
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM sessions
            WHERE status = 'active'
            ORDER BY start_time DESC
            LIMIT 1
        ''')

        row = cursor.fetchone()
        conn.close()

        if row:
            return dict(row)
        return None

    def start_session(self, rfid_hex: str, start_energy_kwh: float, wallbox_id: str = "alfen_eve") -> Optional[int]:
        """
        Startet eine neue Lade-Session (HA-03, HA-04)

        Args:
            rfid_hex: RFID als Hex-String
            start_energy_kwh: Energie-Zählerstand bei Start
            wallbox_id: ID der Wallbox

        Returns:
            Session-ID oder None bei Fehler
        """
        # Prüfen ob bereits eine aktive Session läuft
        active = self.get_active_session()
        if active:
            self._logger.warning("Bestehende aktive Session: ID=%s", active['id'])
            return None

        rfid_hash = hash_rfid(rfid_hex)
        start_time = datetime.now().isoformat()
        created_at = datetime.now().isoformat()

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO sessions (rfid_hash, wallbox_id, start_time, start_energy_kwh, status, created_at)
            VALUES (?, ?, ?, ?, 'active', ?)
        ''', (rfid_hash, wallbox_id, start_time, start_energy_kwh, created_at))

        session_id = cursor.lastrowid
        conn.commit()
        conn.close()

        self._logger.info("Session gestartet: ID=%s, RFID=%s..., Energie=%.2f kWh",
                       session_id, rfid_hash[:16], start_energy_kwh)
        return session_id

    def end_session(self, end_energy_kwh: float) -> Optional[Dict[str, Any]]:
        """
        Beendet die aktive Lade-Session (HA-05, HA-06)

        Args:
            end_energy_kwh: Energie-Zählerstand bei Ende

        Returns:
            Session-Dict mit berechneten Werten oder None
        """
        active = self.get_active_session()
        if not active:
            self._logger.warning("Keine aktive Session zum Beenden")
            return None

        end_time = datetime.now().isoformat()
        total_kwh = end_energy_kwh - active['start_energy_kwh']
        total_kwh = max(0.0, total_kwh)  # Negativer Wert verhindern

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE sessions
            SET end_time = ?, end_energy_kwh = ?, total_kwh = ?, status = 'completed'
            WHERE id = ?
        ''', (end_time, end_energy_kwh, total_kwh, active['id']))

        conn.commit()
        conn.close()

        completed_session = {
            'id': active['id'],
            'rfid_hash': active['rfid_hash'],
            'wallbox_id': active['wallbox_id'],
            'start_time': active['start_time'],
            'end_time': end_time,
            'start_energy_kwh': active['start_energy_kwh'],
            'end_energy_kwh': end_energy_kwh,
            'total_kwh': total_kwh
        }

        self._logger.info("Session beendet: ID=%s, Verbrauch=%.2f kWh",
                       active['id'], total_kwh)
        return completed_session

    def get_completed_sessions(self, limit: int = 10) -> list:
        """
        Holt abgeschlossene Sessions (für API-Übertragung an Dolibarr, Phase 3)

        Args:
            limit: Maximale Anzahl an Sessions

        Returns:
            Liste von Session-Dicts
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM sessions
            WHERE status = 'completed'
            ORDER BY end_time DESC
            LIMIT ?
        ''', (limit,))

        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]
