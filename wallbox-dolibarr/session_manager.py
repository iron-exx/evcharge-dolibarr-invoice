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
from datetime import datetime, timedelta

# Hash-Utility importieren
import sys
sys.path.insert(0, '/usr/local/bin')
from utils.hash import hash_rfid, verify_rfid_hash

# Debounce-Zeit in Sekunden (HA-07)
# OCPP: Plausibilitätsgrenze für die mittlere Ladeleistung einer Session.
# AC-Wallboxen liefern max. 22 kW; alles deutlich darüber ist ein Einheiten-
# oder Zählerfehler (z.B. kWh statt Wh gemeldet) und darf nicht abgerechnet werden.
_MAX_PLAUSIBLE_KW = 50.0

# Ab dieser Dauer gilt eine Session unter min_session_kwh nicht mehr als
# "Karte gehalten, nie geladen", sondern als Zählerfehler → incomplete statt
# still verworfen.
_MAX_DISCARD_HOURS = 0.25

# Einordnung einer Karte in der Tag-Verwaltung des Addons:
#   business — Ladung wird an Dolibarr übertragen (Regelfall)
#   private  — Ladung bleibt LOKAL, erreicht Dolibarr nie
#   unknown  — nur erkannt, noch nicht eingeordnet: darf NICHT laden
TAG_MODE_BUSINESS = 'business'
TAG_MODE_PRIVATE = 'private'
TAG_MODE_UNKNOWN = 'unknown'
VALID_TAG_MODES = (TAG_MODE_BUSINESS, TAG_MODE_PRIVATE, TAG_MODE_UNKNOWN)
# Welche Einordnungen überhaupt laden dürfen.
_TAG_MODES_MAY_CHARGE = (TAG_MODE_BUSINESS, TAG_MODE_PRIVATE)

DEBOUNCE_SECONDS = 7


def format_iso8601(dt: Any) -> str:
    """
    Konvertiert datetime zu ISO 8601 String mit Zeitzone

    Args:
        dt: datetime-Objekt oder String

    Returns:
        ISO 8601 formatierter String

    Note:
        Wenn dt bereits ein String ist, wird er unverändert zurückgegeben
    """
    if isinstance(dt, str):
        return dt

    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%dT%H:%M:%S%z")

    return str(dt)

class SessionManager:
    """Verwaltet Lade-Sessions in SQLite"""

    def __init__(self, db_path: str = "/data/sessions.db",
                 debounce_seconds: float = DEBOUNCE_SECONDS,
                 max_plausible_kw: float = _MAX_PLAUSIBLE_KW,
                 max_discard_hours: float = _MAX_DISCARD_HOURS):
        """Die drei Grenzwerte sind einstellbar, weil sie je Anlage abweichen:
        eine DC-Säule überschreitet 50 kW, ein träger Leser braucht eine
        andere Entprellung, und wie lange eine Session unter min_kwh noch als
        "Karte gehalten" statt als Zählerfehler gilt, hängt vom Standort ab.
        Die Vorgaben entsprechen genau dem bisherigen Verhalten.
        """
        self.db_path = db_path
        self.debounce_seconds = float(debounce_seconds)
        self.max_plausible_kw = float(max_plausible_kw)
        self.max_discard_hours = float(max_discard_hours)
        self._logger = logging.getLogger(__name__)  # muss vor _init_database() stehen
        self._last_rfid_time: Dict[str, float] = {}  # Für Debouncing
        self._init_database()

    def _init_database(self):
        """Initialisiert die SQLite-Datenbank mit Sessions-Tabelle (PER-01, DB-01 Vorbereitung)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # WAL Mode aktivieren für bessere Concurrenty (PER-02)
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")

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
                created_at TEXT NOT NULL,
                transmitted_at TEXT,
                start_energy_valid INTEGER NOT NULL DEFAULT 1
            )
        ''')

        # Migrationen für bereits existierende Tabellen (idempotent)
        for col_ddl, col_name in [
            ('ALTER TABLE sessions ADD COLUMN transmitted_at TEXT', 'transmitted_at'),
            ('ALTER TABLE sessions ADD COLUMN start_energy_valid INTEGER NOT NULL DEFAULT 1', 'start_energy_valid'),
            ('ALTER TABLE sessions ADD COLUMN login TEXT', 'login'),
            # OCPP-Betrieb (session_source: ocpp) — bei HA-Sensor-Sessions NULL
            ('ALTER TABLE sessions ADD COLUMN charge_point_id TEXT', 'charge_point_id'),
            ('ALTER TABLE sessions ADD COLUMN connector_id INTEGER', 'connector_id'),
            ('ALTER TABLE sessions ADD COLUMN ocpp_start_timestamp TEXT', 'ocpp_start_timestamp'),
            ('ALTER TABLE sessions ADD COLUMN last_meter_kwh REAL', 'last_meter_kwh'),
            ('ALTER TABLE sessions ADD COLUMN stop_reason TEXT', 'stop_reason'),
        ]:
            try:
                cursor.execute(col_ddl)
                self._logger.info("Datenbank-Schema erweitert: %s hinzugefügt", col_name)
            except sqlite3.OperationalError:
                pass  # Spalte existiert bereits

        # Persistente Autorisierung: hält den zuletzt vorgehaltenen, gültigen
        # Tag über Addon-Neustarts UND beliebig lange Lastmanagement-
        # Verzögerungen hinweg. Die Wallbox lädt nie ohne Tag — aber das
        # Lastmanagement kann den Ladebeginn um Stunden verschieben. Ohne
        # Persistenz ginge die Zuordnung dann verloren. Einzeiler-Tabelle.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pending_auth (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                rfid_hex TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        ''')

        # Tag-Verwaltung des Addons: benannte Karten mit Einordnung.
        # Bewusst NUR der Hash — der RFID-Klartext wird nie persistiert
        # (DSGVO, Datensparsamkeit). Der vom Admin vergebene Name ersetzt ihn
        # für die Wiedererkennung in der Oberfläche.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tags (
                rfid_hash  TEXT PRIMARY KEY,
                label      TEXT,
                mode       TEXT NOT NULL DEFAULT 'unknown',
                first_seen TEXT NOT NULL,
                last_seen  TEXT NOT NULL,
                seen_count INTEGER NOT NULL DEFAULT 1
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

        # OCPP: Duplikaterkennung wiederholter StartTransaction-Nachrichten
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_ocpp_start
            ON sessions(charge_point_id, connector_id, ocpp_start_timestamp)
        ''')

        conn.commit()
        conn.close()
        self._logger.info("SQLite Datenbank initialisiert mit WAL Mode: %s", self.db_path)

    def set_pending_auth(self, rfid_hex: str) -> None:
        """Merkt den zuletzt vorgehaltenen gültigen Tag dauerhaft (überlebt
        Neustart + lange Lastmanagement-Verzögerung). Ersetzt einen evtl.
        vorhandenen älteren Eintrag (neue Autorisierung hat Vorrang)."""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO pending_auth (id, rfid_hex, created_at) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET rfid_hex=excluded.rfid_hex, created_at=excluded.created_at",
            (rfid_hex, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()

    def get_pending_auth(self) -> Optional[str]:
        """Gibt den persistierten, zuletzt autorisierten Tag zurück (rfid_hex)
        oder None. Kein Zeitfenster — gültig bis Abstecken/neuer Tag."""
        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT rfid_hex FROM pending_auth WHERE id = 1").fetchone()
        conn.close()
        return row[0] if row else None

    def clear_pending_auth(self) -> None:
        """Löscht die persistierte Autorisierung (nach Abstecken/Session-Ende)."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM pending_auth WHERE id = 1")
        conn.commit()
        conn.close()

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
            if time_diff < self.debounce_seconds:
                self._logger.debug("RFID debounced: %s (%.1fs < %ds)",
                                rfid_hash[:16], time_diff, self.debounce_seconds)
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
        rfid_hash = hash_rfid(rfid_hex)

        # Zuerst die Tag-Verwaltung: wer dort eingeordnet ist, darf laden —
        # auch ohne Eintrag in der Konfigurations-Whitelist. Das ist der Zweck
        # des Lernmodus: Karten freischalten, ohne die Konfiguration anzufassen.
        tag = self._get_tag_by_hash(rfid_hash)
        if tag is not None:
            if tag['mode'] in _TAG_MODES_MAY_CHARGE:
                self._logger.info("RFID autorisiert über Tag-Verwaltung (%s): %s...",
                                  tag['mode'], rfid_hash[:16])
                return True
            self._logger.warning("RFID erkannt, aber noch nicht eingeordnet: %s... "
                                 "— in der Oberfläche benennen und einordnen", rfid_hash[:16])
            return False

        if not whitelist:
            # Ohne übergebene Whitelist IST die Tag-Verwaltung die einzige
            # Quelle — und die hat oben schon nichts gefunden. Nicht behaupten,
            # es sei keine Whitelist konfiguriert: der Aufrufer (OCPP) fragt
            # absichtlich mit leerer Liste, und die Meldung würde bei der
            # Fehlersuche in die falsche Richtung schicken.
            self._logger.warning("RFID nicht autorisiert: %s... — Karte ist nicht "
                                 "freigeschaltet (weder in der Tag-Verwaltung noch "
                                 "in rfid_whitelist)", rfid_hash[:16])
            return False

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

    def recover_active_sessions(self) -> list:
        """
        Findet aktive Sessions beim Neustart (PER-02)
        Prüft ob Session noch aktiv oder abgeschlossen

        Returns:
            Liste der wiederhergestellten Sessions
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM sessions
            WHERE status = 'active'
            ORDER BY start_time DESC
        ''')

        active_sessions = [dict(row) for row in cursor.fetchall()]
        conn.close()

        if active_sessions:
            self._logger.info("Gefundene aktive Sessions beim Start: %d", len(active_sessions))

        return active_sessions

    def mark_session_incomplete(self, session_id: int, reason: str = 'crash_recovery'):
        """
        Markiert eine Session als unvollständig (PER-03)

        Args:
            session_id: ID der Session
            reason: Grund für die Markierung
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE sessions
            SET status = 'incomplete', end_time = ?
            WHERE id = ?
        ''', (datetime.now().isoformat(), session_id))
        conn.commit()
        conn.close()
        self._logger.warning("Session %d als unvollständig markiert: %s", session_id, reason)

    def get_sessions_by_wallbox(self, wallbox_id: str) -> list:
        """
        Holt alle Sessions für eine spezifische Wallbox (EXT-01)

        Args:
            wallbox_id: ID der Wallbox

        Returns:
            Liste von Session-Dicts
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM sessions
            WHERE wallbox_id = ?
            ORDER BY start_time DESC
        ''', (wallbox_id,))

        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    def start_session(self, rfid_hex: str, start_energy_kwh: float,
                      wallbox_id: str = "alfen_eve",
                      start_energy_valid: bool = True) -> Optional[int]:
        """
        Startet eine neue Lade-Session (HA-03, HA-04)

        Args:
            rfid_hex: RFID als Hex-String
            start_energy_kwh: Energie-Zählerstand bei Start
            wallbox_id: ID der Wallbox
            start_energy_valid: False wenn der Zählerstand beim Start unbekannt
                                war (Sensor lieferte keinen Wert) — Ende markiert
                                die Session dann als unvollständig statt zu rechnen.

        Returns:
            Session-ID oder None bei Fehler
        """
        # Prüfen ob bereits eine aktive Session läuft
        active = self.get_active_session()
        if active:
            self._logger.warning("Bestehende aktive Session: ID=%s", active['id'])
            return None

        rfid_hash = hash_rfid(rfid_hex)
        start_time = datetime.now().replace(microsecond=0).isoformat()
        created_at = datetime.now().replace(microsecond=0).isoformat()

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO sessions (rfid_hash, wallbox_id, start_time, start_energy_kwh,
                                  status, created_at, start_energy_valid)
            VALUES (?, ?, ?, ?, 'active', ?, ?)
        ''', (rfid_hash, wallbox_id, start_time, start_energy_kwh, created_at,
              1 if start_energy_valid else 0))

        session_id = cursor.lastrowid
        conn.commit()
        conn.close()

        self._logger.info("Session gestartet: ID=%s, RFID=%s..., Energie=%.3f kWh (gültig=%s)",
                       session_id, rfid_hash[:16], start_energy_kwh, start_energy_valid)
        return session_id

    def end_session(self, end_energy_kwh: float, min_kwh: float = 0.05,
                    end_energy_valid: bool = True) -> Optional[Dict[str, Any]]:
        """
        Beendet die aktive Lade-Session.

        Args:
            end_energy_kwh:   Energie-Zählerstand bei Ende
            min_kwh:          Mindest-Verbrauch (Default 0.05 kWh) ab dem die
                              Session als echte Ladung gewertet wird. Sessions
                              unterhalb → 'discarded' (z.B. Karte gehalten ohne
                              Ladung).
            end_energy_valid: False wenn der End-Zählerstand unbekannt war.

        Status-Logik:
          - Start- ODER End-Zähler ungültig → 'incomplete' (SICHTBAR, nicht
            still verworfen — Admin kann manuell nachtragen).
          - Gültige Reads, aber < min_kwh → 'discarded' (echte Ghost-Session).
          - Sonst → 'completed' (wird übertragen).

        Returns:
            Session-Dict NUR bei 'completed', sonst None.
        """
        active = self.get_active_session()
        if not active:
            self._logger.warning("Keine aktive Session zum Beenden")
            return None

        end_time = datetime.now().replace(microsecond=0).isoformat()
        start_valid = int(active.get('start_energy_valid', 1)) == 1
        energy_trustworthy = start_valid and end_energy_valid
        total_kwh = max(0.0, end_energy_kwh - active['start_energy_kwh'])

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Fall 1: Energie-Readings nicht vertrauenswürdig → incomplete (sichtbar)
        if not energy_trustworthy:
            cursor.execute('''
                UPDATE sessions
                SET end_time = ?, end_energy_kwh = ?, total_kwh = ?, status = 'incomplete'
                WHERE id = ?
            ''', (end_time, end_energy_kwh if end_energy_valid else None,
                  total_kwh if energy_trustworthy else None, active['id']))
            conn.commit()
            conn.close()
            self._logger.warning(
                "Session %s unvollständig: Zählerstand ungültig (start_valid=%s, "
                "end_valid=%s) — kWh nicht berechenbar, bitte manuell prüfen/nachtragen",
                active['id'], start_valid, end_energy_valid
            )
            return None

        # Fall 2: gültige Reads aber zu wenig geladen → discarded (echte Ghost-Session)
        if total_kwh < min_kwh:
            cursor.execute('''
                UPDATE sessions
                SET end_time = ?, end_energy_kwh = ?, total_kwh = ?,
                    status = 'discarded', transmitted_at = ?
                WHERE id = ?
            ''', (end_time, end_energy_kwh, total_kwh, end_time, active['id']))
            conn.commit()
            conn.close()
            self._logger.info(
                "Session %s verworfen: nur %.3f kWh (< %.3f kWh) — keine echte Ladung",
                active['id'], total_kwh, min_kwh
            )
            return None

        # Fall 3: echte Ladung → completed
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

    def add_manual_session(self, kwh: float, wallbox_id: str,
                           session_date: str, rfid_hash: Optional[str] = None,
                           login: Optional[str] = None) -> Optional[int]:
        """
        Legt eine manuelle Session direkt als 'completed' an (für UI-Eingaben).

        Args:
            kwh:          Verbrauchte Energie in kWh
            wallbox_id:   Wallbox-ID aus der Konfiguration
            session_date: Datum als ISO-String (YYYY-MM-DD)
            rfid_hash:    SHA-256 Hash der RFID-Karte (physischer Tap-Ersatz)
            login:        Dolibarr-Login des Mitarbeiters (Auswahl per Name) —
                          genau eins von rfid_hash/login muss gesetzt sein

        Returns:
            Session-ID oder None bei Fehler
        """
        if not login and not rfid_hash:
            self._logger.error("add_manual_session: weder rfid_hash noch login angegeben")
            return None

        try:
            date_obj = datetime.fromisoformat(session_date)
        except (ValueError, TypeError):
            date_obj = datetime.now()

        # Aktuelle Uhrzeit verwenden — verhindert Duplikat-Kollisionen bei
        # mehreren manuellen Sessions am selben Tag (Dolibarr lehnt sonst
        # mit "Session already exists" ab, da Identität+start+end identisch)
        now_dt   = datetime.now().replace(microsecond=0)
        start_dt = date_obj.replace(
            hour=now_dt.hour,
            minute=now_dt.minute,
            second=now_dt.second,
            microsecond=0,
        )
        end_dt   = start_dt + timedelta(minutes=1)
        start_time = start_dt.isoformat()
        end_time   = end_dt.isoformat()
        now        = now_dt.isoformat()

        # rfid_hash-Spalte ist NOT NULL — bei Login-Auswahl einen klar erkennbaren
        # Platzhalter speichern, der NIE als echter Kartenwert an Dolibarr geht
        # (transmit_completed_sessions bevorzugt login, wenn gesetzt)
        stored_hash = rfid_hash or hash_rfid(f"__manual_login__:{login}")

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO sessions
                (rfid_hash, login, wallbox_id, start_time, end_time,
                 start_energy_kwh, end_energy_kwh, total_kwh, status, created_at)
            VALUES (?, ?, ?, ?, ?, 0.0, ?, ?, 'completed', ?)
        ''', (stored_hash, login, wallbox_id, start_time, end_time, kwh, kwh, now))

        session_id = cursor.lastrowid
        conn.commit()
        conn.close()

        self._logger.info("Manuelle Session erstellt: ID=%s, %.3f kWh, Datum=%s",
                          session_id, kwh, session_date)
        return session_id

    def transmit_completed_sessions(self, api_client: Any) -> Dict[str, Any]:
        """
        Überträgt abgeschlossene (noch nicht übertragene) Sessions an Dolibarr

        Args:
            api_client: WallboxApiClient Instanz für API-Calls

        Returns:
            Dict mit transmitted, failed, errors
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Sessions finden: abgeschlossen (NICHT discarded/incomplete) und noch nicht übertragen
        cursor.execute('''
            SELECT id, rfid_hash, wallbox_id, start_time, end_time, total_kwh, login
            FROM sessions
            WHERE status = 'completed'
              AND end_time IS NOT NULL
              AND transmitted_at IS NULL
        ''')

        rows = cursor.fetchall()

        result = {
            "transmitted": 0,
            "failed": 0,
            "private": 0,
            "errors": []
        }

        for row in rows:
            session_id = row[0]
            login = row[6] if len(row) > 6 else None

            # ABRECHNUNGSSCHRANKE: Eine als privat eingeordnete Karte darf
            # Dolibarr nie erreichen. Geprüft wird die AKTUELLE Einordnung,
            # damit ein nachträgliches Umstellen auf privat eine noch nicht
            # übertragene Ladung auch noch stoppt.
            tag = self._get_tag_by_hash(row[1])
            if tag is not None and tag['mode'] == TAG_MODE_PRIVATE:
                cursor.execute("UPDATE sessions SET status = 'private' WHERE id = ?",
                               (session_id,))
                result["private"] += 1
                self._logger.info("Session %s ist privat — bleibt lokal, keine Übertragung",
                                  session_id)
                continue
            session_data = {
                "wallbox_id": row[2],
                "start_time": format_iso8601(row[3]),
                "end_time": format_iso8601(row[4]),
                "kwh": row[5] if row[5] else 0.0
            }
            if login:
                session_data["login"] = login
            else:
                session_data["rfid_hash"] = row[1]

            # Session an Dolibarr übertragen
            success, error = api_client.transmit_session(session_data)

            if success:
                # transmitted_at setzen
                cursor.execute('''
                    UPDATE sessions SET transmitted_at = ? WHERE id = ?
                ''', (datetime.now().isoformat(), session_id))
                result["transmitted"] += 1
                self._logger.info("Session %s erfolgreich übertragen", session_id)
            else:
                error_msg = f"Session {session_id}: {error}"
                result["errors"].append(error_msg)
                result["failed"] += 1
                self._logger.error("Fehler bei Session %s: %s", session_id, error)

                # Bei Fehler: Schleife abbrechen (keine weiteren Transmissions)
                break

        conn.commit()
        conn.close()

        self._logger.info("API-Übertragung abgeschlossen: %s übertragen, %s fehlgeschlagen",
                         result["transmitted"], result["failed"])

        return result

    # ------------------------------------------------------------------
    # Nachbearbeitung aus der Verwaltungsoberfläche
    # ------------------------------------------------------------------

    def list_sessions(self, month: Optional[str] = None, status: Optional[str] = None,
                      limit: int = 1000) -> list:
        """Sessions, neueste zuerst. month 'YYYY-MM'; status wie in der DB oder
        'pending' (abgeschlossen, noch nicht übertragen) / 'transmitted'."""
        where, args = [], []
        if month:
            where.append("strftime('%Y-%m', start_time) = ?")
            args.append(month)
        if status == 'pending':
            where.append("status = 'completed' AND transmitted_at IS NULL")
        elif status == 'transmitted':
            where.append("status = 'completed' AND transmitted_at IS NOT NULL")
        elif status:
            where.append("status = ?")
            args.append(status)
        sql = ("SELECT * FROM sessions" + (" WHERE " + " AND ".join(where) if where else "") +
               " ORDER BY start_time DESC, id DESC LIMIT ?")
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql, (*args, limit)).fetchall()]
        finally:
            conn.close()

    def session_counts(self) -> Dict[str, int]:
        """Anzahl je Status, dazu 'pending' (abgeschlossen, noch nicht übertragen)."""
        conn = sqlite3.connect(self.db_path)
        try:
            counts = dict(conn.execute("SELECT status, COUNT(*) FROM sessions GROUP BY status").fetchall())
            counts['pending'] = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE status = 'completed' AND transmitted_at IS NULL"
            ).fetchone()[0]
            return counts
        finally:
            conn.close()

    def resolve_incomplete_session(self, session_id: int, total_kwh: float) -> bool:
        """Unvollständige Session mit von Hand ermittelter Energiemenge abschließen —
        danach wird sie wie jede andere übertragen."""
        if not 0 < total_kwh < 1000:
            raise ValueError("kWh: mehr als 0 und unter 1000")
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute('''
                UPDATE sessions SET total_kwh = ?, status = 'completed',
                       end_time = COALESCE(end_time, start_time),
                       stop_reason = TRIM(COALESCE(stop_reason, '') || ' manuell_korrigiert')
                WHERE id = ? AND status = 'incomplete' AND transmitted_at IS NULL
            ''', (round(total_kwh, 3), session_id))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def discard_session(self, session_id: int) -> bool:
        """Noch nicht übertragene Session verwerfen — sie erreicht Dolibarr nie."""
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute('''
                UPDATE sessions SET status = 'discarded',
                       stop_reason = TRIM(COALESCE(stop_reason, '') || ' manuell_verworfen')
                WHERE id = ? AND status IN ('incomplete', 'completed') AND transmitted_at IS NULL
            ''', (session_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # OCPP-Transaktionen (session_source: ocpp)
    #
    # Unterschiede zum HA-Sensor-Pfad:
    #   - Mehrere Sessions gleichzeitig aktiv (je Wallbox/Connector eine).
    #   - Die OCPP-transactionId IST die sessions.id (AUTOINCREMENT → eindeutig
    #     über Neustarts hinweg). Zugriffe prüfen zusätzlich charge_point_id,
    #     damit eine alte, gepufferte Nachricht nie eine fremde Session trifft.
    #   - Zeitstempel und Zählerstände kommen von der Wallbox.
    # ------------------------------------------------------------------

    def start_ocpp_transaction(self, rfid_hex: str, wallbox_id: str, charge_point_id: str,
                               connector_id: int, meter_start_kwh: float, start_time: str,
                               ocpp_start_timestamp: str) -> int:
        """Legt eine aktive OCPP-Session an und gibt ihre ID (= transactionId) zurück.

        Idempotent: Wiederholt die Wallbox dieselbe StartTransaction (gleiche
        Wallbox, gleicher Connector, gleicher Original-Zeitstempel), kommt die
        bereits vergebene ID zurück. Läuft auf dem Connector noch eine ältere
        aktive Session, hat die Wallbox deren Stop verloren → 'incomplete'.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            # Ein echter Wiederholungsversuch trägt denselben Zeitstempel UND
            # denselben Startzählerstand und kommt zeitnah. Eine Wallbox mit nie
            # gestellter Uhr meldet dagegen für JEDE Ladung denselben Zeitstempel —
            # ohne diese beiden Zusatzbedingungen bekäme jede weitere Ladung die ID
            # der ersten und wäre nirgends erfasst.
            cur.execute('''
                SELECT id FROM sessions
                WHERE charge_point_id = ? AND connector_id = ? AND ocpp_start_timestamp = ?
                  AND start_energy_kwh = ?
                  AND created_at >= ?
                ORDER BY id DESC
                LIMIT 1
            ''', (charge_point_id, connector_id, ocpp_start_timestamp, meter_start_kwh,
                  (datetime.now() - timedelta(days=1)).replace(microsecond=0).isoformat()))
            existing = cur.fetchone()
            if existing:
                self._logger.info("StartTransaction wiederholt (%s/%s) — bestehende Session #%s",
                                  charge_point_id, connector_id, existing['id'])
                return int(existing['id'])

            cur.execute('''
                UPDATE sessions SET status = 'incomplete', stop_reason = 'ocpp_superseded', end_time = ?
                WHERE status = 'active' AND charge_point_id = ? AND connector_id = ?
            ''', (start_time, charge_point_id, connector_id))
            if cur.rowcount:
                self._logger.warning("%s/%s: %d ältere aktive Session(s) ohne Stop → incomplete",
                                     charge_point_id, connector_id, cur.rowcount)

            created_at = datetime.now().replace(microsecond=0).isoformat()
            cur.execute('''
                INSERT INTO sessions (rfid_hash, wallbox_id, start_time, start_energy_kwh, status,
                                      created_at, start_energy_valid, charge_point_id, connector_id,
                                      ocpp_start_timestamp)
                VALUES (?, ?, ?, ?, 'active', ?, 1, ?, ?, ?)
            ''', (hash_rfid(rfid_hex), wallbox_id, start_time, meter_start_kwh, created_at,
                  charge_point_id, connector_id, ocpp_start_timestamp))
            conn.commit()
            session_id = int(cur.lastrowid)
        finally:
            conn.close()
        self._logger.info("OCPP-Session gestartet: #%s (%s/%s, Zähler %.3f kWh)",
                          session_id, charge_point_id, connector_id, meter_start_kwh)
        return session_id

    def update_ocpp_meter(self, transaction_id: int, charge_point_id: str, meter_kwh: float) -> bool:
        """Merkt den letzten Zählerstand einer aktiven OCPP-Session (Fallback fürs Ende).

        Nur monoton steigende Werte >= Startzählerstand werden übernommen.
        """
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute('''
                UPDATE sessions SET last_meter_kwh = ?
                WHERE id = ? AND charge_point_id = ? AND status = 'active'
                  AND ? >= start_energy_kwh
                  AND (last_meter_kwh IS NULL OR ? >= last_meter_kwh)
            ''', (meter_kwh, transaction_id, charge_point_id, meter_kwh, meter_kwh))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def stop_ocpp_transaction(self, transaction_id: int, charge_point_id: str,
                              meter_stop_kwh: Optional[float], end_time: str, reason: str,
                              min_kwh: float = 0.05) -> Optional[Dict[str, Any]]:
        """Schließt eine OCPP-Session ab. Gibt das Session-Dict NUR bei 'completed' zurück.

        - Unbekannte ID / fremde Wallbox → None (Aufrufer bestätigt trotzdem).
        - Bereits abgeschlossen (wiederholte StopTransaction) → None, keine Änderung.
        - meterStop fehlt, ist 0 oder kleiner als der Start → letzter MeterValue.
        - Kein brauchbarer Endstand oder unplausible Leistung → 'incomplete'.
        - < min_kwh → 'discarded'.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM sessions WHERE id = ? AND charge_point_id = ?",
                        (transaction_id, charge_point_id))
            row = cur.fetchone()
            if row is None:
                self._logger.warning("StopTransaction für unbekannte Transaktion %s (%s) — ignoriert",
                                     transaction_id, charge_point_id)
                return None
            if row['status'] != 'active':
                self._logger.info("StopTransaction für bereits abgeschlossene Session #%s — ignoriert",
                                  transaction_id)
                return None

            start_kwh = float(row['start_energy_kwh'])
            end_kwh = meter_stop_kwh
            if end_kwh is None or end_kwh < start_kwh:
                last = row['last_meter_kwh']
                end_kwh = float(last) if last is not None and float(last) >= start_kwh else None
            status, total_kwh = _classify_ocpp_energy(
                start_kwh, end_kwh, row['start_time'], end_time, min_kwh,
                max_plausible_kw=self.max_plausible_kw,
                max_discard_hours=self.max_discard_hours)
            cur.execute('''
                UPDATE sessions
                SET end_time = ?, end_energy_kwh = ?, total_kwh = ?, status = ?, stop_reason = ?,
                    transmitted_at = CASE WHEN ? = 'discarded' THEN ? ELSE transmitted_at END
                WHERE id = ?
            ''', (end_time, end_kwh, total_kwh, status, reason, status, end_time, transaction_id))
            conn.commit()
        finally:
            conn.close()

        if status != 'completed':
            self._logger.warning("OCPP-Session #%s: %s (Ende %s kWh, Grund %s)",
                                 transaction_id, status, end_kwh, reason)
            return None
        self._logger.info("OCPP-Session #%s beendet: %.3f kWh (%s)", transaction_id, total_kwh, reason)
        return {
            'id': int(row['id']),
            'rfid_hash': row['rfid_hash'],
            'wallbox_id': row['wallbox_id'],
            'start_time': row['start_time'],
            'end_time': end_time,
            'start_energy_kwh': start_kwh,
            'end_energy_kwh': end_kwh,
            'total_kwh': total_kwh,
        }

    def get_active_ocpp_sessions(self) -> list:
        """Alle aktiven OCPP-Sessions (älteste zuerst)."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute('''
                SELECT * FROM sessions
                WHERE status = 'active' AND charge_point_id IS NOT NULL
                ORDER BY start_time ASC
            ''')
            return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Tag-Verwaltung des Addons
    #
    # Zweck: Karten in der Oberfläche benennen und einordnen, ohne die
    # Konfiguration anzufassen — und entscheiden, ob eine Ladung abgerechnet
    # (business) oder nur lokal protokolliert wird (private).
    #
    # Gespeichert wird ausschließlich der SHA-256-Hash. Der RFID-Klartext
    # erscheint nur flüchtig im Lernmodus der Oberfläche, nie in der Datenbank.
    #
    # Abgrenzung zu Dolibarr: dort steht, WER der Mitarbeiter ist
    # (llx_wallbox_rfid). Hier steht, OB überhaupt übertragen wird.
    # ------------------------------------------------------------------

    def _get_tag_by_hash(self, rfid_hash: str) -> Optional[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM tags WHERE rfid_hash = ?", (rfid_hash,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_tag(self, rfid_hex: str) -> Optional[Dict[str, Any]]:
        """Tag-Eintrag zu einer Karte (Klartext-ID) oder None."""
        return self._get_tag_by_hash(hash_rfid(rfid_hex))

    def upsert_tag(self, rfid_hex: str, label: Optional[str] = None,
                   mode: str = TAG_MODE_UNKNOWN) -> Dict[str, Any]:
        """Legt einen Tag an oder aktualisiert Name und Einordnung.

        Ein bestehender Eintrag behält first_seen und seen_count; nur Name,
        Einordnung und last_seen werden überschrieben.
        """
        if mode not in VALID_TAG_MODES:
            raise ValueError(f"mode muss {VALID_TAG_MODES} sein, war {mode!r}")
        rfid_hash = hash_rfid(rfid_hex)
        now = datetime.now().replace(microsecond=0).isoformat()
        clean_label = (label or '').strip() or None

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute('''
                INSERT INTO tags (rfid_hash, label, mode, first_seen, last_seen, seen_count)
                VALUES (?, ?, ?, ?, ?, 1)
                ON CONFLICT(rfid_hash) DO UPDATE SET
                    label = excluded.label,
                    mode = excluded.mode,
                    last_seen = excluded.last_seen
            ''', (rfid_hash, clean_label, mode, now, now))
            conn.commit()
        finally:
            conn.close()
        self._logger.info("Tag %s... gespeichert: %s (%s)", rfid_hash[:16], clean_label, mode)
        return self._get_tag_by_hash(rfid_hash)

    def note_tag_seen(self, rfid_hex: str) -> Dict[str, Any]:
        """Hält fest, dass eine Karte vorgehalten wurde — für den Lernmodus.

        Eine unbekannte Karte landet als 'unknown' in der Liste (darf damit
        NICHT laden) und wartet darauf, benannt und eingeordnet zu werden.
        Eine bekannte Karte behält Name und Einordnung.
        """
        rfid_hash = hash_rfid(rfid_hex)
        now = datetime.now().replace(microsecond=0).isoformat()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute('''
                INSERT INTO tags (rfid_hash, label, mode, first_seen, last_seen, seen_count)
                VALUES (?, NULL, ?, ?, ?, 1)
                ON CONFLICT(rfid_hash) DO UPDATE SET
                    last_seen = excluded.last_seen,
                    seen_count = tags.seen_count + 1
            ''', (rfid_hash, TAG_MODE_UNKNOWN, now, now))
            conn.commit()
        finally:
            conn.close()
        return self._get_tag_by_hash(rfid_hash)

    def list_tags(self) -> list:
        """Alle bekannten Tags: noch nicht eingeordnete zuerst, dann nach Name."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute('''
                SELECT * FROM tags
                ORDER BY CASE mode WHEN 'unknown' THEN 0 ELSE 1 END,
                         label IS NULL, label, last_seen DESC
            ''').fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def delete_tag(self, rfid_hex: str) -> bool:
        """Entfernt einen Tag. True, wenn es ihn gab.

        Danach darf die Karte nur noch laden, wenn sie in der
        Konfigurations-Whitelist steht.
        """
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute("DELETE FROM tags WHERE rfid_hash = ?", (hash_rfid(rfid_hex),))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def delete_tag_by_hash(self, rfid_hash: str) -> bool:
        """Entfernt einen Tag anhand seines Hashes.

        Die Oberfläche kennt den RFID-Klartext bewusst nicht mehr, sobald eine
        Karte eingetragen ist — zum Löschen genügt daher der Hash.
        """
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute("DELETE FROM tags WHERE rfid_hash = ?", (rfid_hash,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def update_tag_by_hash(self, rfid_hash: str, label: Optional[str], mode: str) -> bool:
        """Name und Einordnung einer eingetragenen Karte ändern (ohne Klartext). True, wenn es sie gab."""
        if mode not in VALID_TAG_MODES:
            raise ValueError(f"mode muss {VALID_TAG_MODES} sein, war {mode!r}")
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute("UPDATE tags SET label = ?, mode = ? WHERE rfid_hash = ?",
                               ((label or '').strip() or None, mode, rfid_hash))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def is_tag_billable(self, rfid_hash: str) -> bool:
        """Darf eine Ladung mit diesem Hash abgerechnet werden?

        Ohne Eintrag: ja — bestehende Installationen pflegen nur die
        Konfigurations-Whitelist und müssen weiter abrechnen.
        """
        tag = self._get_tag_by_hash(rfid_hash)
        return tag is None or tag['mode'] != TAG_MODE_PRIVATE

def _classify_ocpp_energy(start_kwh: float, end_kwh: Optional[float], start_time: str,
                          end_time: str, min_kwh: float,
                          max_plausible_kw: float = _MAX_PLAUSIBLE_KW,
                          max_discard_hours: float = _MAX_DISCARD_HOURS):
    """(status, total_kwh) für eine abgeschlossene OCPP-Session."""
    if end_kwh is None:
        return 'incomplete', None
    total_kwh = round(end_kwh - start_kwh, 3)
    try:
        hours = (datetime.fromisoformat(end_time) - datetime.fromisoformat(start_time)).total_seconds() / 3600.0
    except (TypeError, ValueError):
        hours = None
    if total_kwh < min_kwh:
        # 'discarded' meint: Karte kurz gehalten, nie wirklich geladen. Zog sich die
        # Session dagegen über Stunden, ist fast nichts gezählt worden — typisch für
        # eine Wallbox, die ihren Zähler in kWh statt in Wh meldet (Faktor 1000 zu
        # klein). Das darf nicht still verworfen werden.
        if hours is not None and hours >= max_discard_hours:
            return 'incomplete', total_kwh
        return 'discarded', max(0.0, total_kwh)
    if hours is not None and total_kwh > max_plausible_kw * max(hours, 0.0) + 1.0:
        return 'incomplete', total_kwh
    return 'completed', total_kwh
