#!/usr/bin/env python3
"""
Wallbox-Dolibarr Addon Hauptskript

Verbindet sich via Websocket API mit Home Assistant Core, abonniert die
konfigurierten Wallbox-Sensoren (RFID-Tag, Zählerstand/Leistung,
Wallbox-Status oder externe Aktiv-Entity) und schreibt jede abgeschlossene
Lade-Session direkt in die Dolibarr-Spesenabrechnung des zugeordneten
Mitarbeiters.

Herstellerunabhängig via `wallbox_profile.py`: `wallbox_profile: "alfen_eve"`
(Default) liefert das bewährte Alfen-Verhalten unverändert. Mit
`wallbox_profile: "custom"` sind Autorisierung (auth_mode) und
Zustand-Erkennung (state_mode) frei kombinierbar — u.a. für Wallboxen
anderer Hersteller, vorgeschaltete Zähler (z.B. Shelly EM) oder Wallboxen
ganz ohne eigenen Zähler. Details siehe wallbox_profile.py.

Session-Logik (Alfen-Standard, auth_mode='tag_hold' + state_mode='state_keywords'):
  • RFID-Wechsel auf bekannten Tag        → Session START
  • State-Wechsel auf "Available"/"Finishing"/"Stopped"/…  → Session ENDE
  • RFID-Wechsel auf "No Tag" (Karte ab)  → Session ENDE (Fallback)
  • Geladen = Zähler_END − Zähler_START
"""
import asyncio
import time
import aiohttp
import json
import logging
import os
import sys
from datetime import datetime
from typing import Dict, Any, Optional

# Hash-Utility importieren
sys.path.insert(0, '/usr/local/bin')
from utils.hash import hash_rfid

# Session Manager importieren
from session_manager import SessionManager

# Wallbox-Profil-Auflösung (herstellerunabhängige Auth-/Zustand-Erkennung)
import wallbox_profile

# API Client importieren (Phase 3)
from api_client import WallboxApiClient

# Ingress Web-Server für manuelle Sessions
from web_server import start_web_server
from ocpp_server.central_system import CentralSystemDeps
from ocpp_server.server import OCPP_PORT, OcppServer
from ocpp_server.settings import resolve_ocpp_settings

# Logging Setup (D-17, D-20)
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
_LOGGER = logging.getLogger(__name__)

# RFID-Werte die als "keine Karte" interpretiert werden
_RFID_NONE_VALUES = {'', 'no tag', 'no_tag', 'none', 'unknown', 'unavailable'}

# Pending-Auth-Fenster: wie lange (Sekunden) ein erkannter RFID-Tag
# "memoriert" wird, falls erst SPÄTER der Charging-Power-On-State kommt.
# Alfen brauchst manchmal Minuten zwischen NFC-Auth und tatsächlichem
# Charging-Start (Auto wird erst danach angesteckt).
_PENDING_AUTH_WINDOW = 600  # 10 Minuten

# Letzte erkannte autorisierte RFID — wird vom Charging-State-Trigger
# als Fallback verwendet, falls der RFID-Event verpasst wurde.
_pending_auth = None  # dict: {'rfid_hex': str, 'time': float}

# Zuletzt am RFID-Sensor anliegender Tag-Wert (ohne Zeitfenster). Bei Alfen
# bleibt der Tag stundenlang als Sensor-Wert "anliegend", ohne neues Event —
# startet das Laden erst viel später, ist _pending_auth längst abgelaufen.
# Dieser Cache dient dann als Fallback für den Charging-State-Trigger.
_latest_rfid = None  # str oder None

# Gecachter Energie-Zählerstand (kWh). Wird laufend aus den state_changed-
# Events des Energiesensors aktualisiert UND beim Start einmalig geseedet.
# Start/Ende einer Session lesen NUR diesen Cache — niemals get_state()
# während der Event-Schleife (das würde Frames stehlen, siehe websocket).
_latest_energy = None  # float oder None wenn (noch) kein gültiger Wert vorliegt

# Aufgelöstes Wallbox-Profil (Auth-/Zustand-Modus, Sensoren, Schwellenwerte) —
# wird einmal beim Start aus current_config berechnet (siehe wallbox_profile.py).
profile: Optional[wallbox_profile.WallboxProfile] = None

# Für state_mode='power_threshold': wann zuletzt Leistung > Schwelle gemessen wurde.
_last_power_high_time = None  # float (time.time()) oder None

# Für state_mode='energy_delta': wann sich der Zählerstand zuletzt geändert hat.
_last_energy_change_time = None  # float (time.time()) oder None

# Platzhalter-Identität für auth_mode='none' (keine Autorisierungspflicht).
_NO_AUTH_RFID = "NO_AUTH_REQUIRED"

# Sofort-Übertragung anstoßen (z.B. nach einer OCPP-StopTransaction), statt
# auf das nächste transmit_interval zu warten.
_transmit_requested = asyncio.Event()


def _parse_energy(value):
    """Sensor-Wert → float kWh oder None bei 'unavailable'/'unknown'/Müll."""
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in ('', 'unavailable', 'unknown', 'none', 'nan'):
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None

# Wallbox-Status: substring-Match (case-insensitive) gegen den echten Sensor-Wert,
# nur relevant für state_mode='state_keywords' (Alfen-Standard: "Available",
# "Preparing", "Charging Power On", "Charging Stopped", "Charging Power Off",
# "Suspended EV", "Suspended EVSE", "Finishing", "Reserved", "Unavailable", "Faulted").
#
# WICHTIG (Lastmanagement): Wir unterscheiden ZWEI Arten von "lädt gerade nicht":
#   - ENDE  = Fahrzeug abgesteckt / Sitzung abgeschlossen / Fehler → Session beenden
#   - PAUSE = Fahrzeug ANGESTECKT, aber gerade kein Strom (Lastmanagement drosselt
#             auf 0, EV pausiert, kurz gestoppt) → Session OFFEN halten, damit der
#             gesamte Ladevorgang über die Pause hinweg als EINE Session erfasst
#             wird (kumulativer Zähler liefert die korrekte Gesamt-kWh).
# Prüf-Vorrang: ENDE > PAUSE > CHARGING. Keyword-Listen und die Zuordnung zu
# anderen Erkennungsmodi (power_threshold/energy_delta/external_boolean) kommen
# jetzt aus dem aufgelösten WallboxProfile (siehe wallbox_profile.py).

# Globale Variablen für Session-Tracking
session_manager = None
current_config = {}
ha_ws = None
api_client = None
api_state = None  # Live-Zustand für Web-Server (current_energy, wallbox_state)


def load_config():
    """Lädt Addon-Konfiguration aus /data/options.json (D-04)"""
    config_path = '/data/options.json'
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
            _LOGGER.info("Konfiguration geladen von %s", config_path)

            if isinstance(config.get('ha_token'), str):
                config['ha_token'] = config['ha_token'].strip()

            # API-Konfiguration validieren (Task 3)
            api_config = config.get('api', {})
            if api_config:
                for key in ('dolibarr_url', 'api_token'):
                    if isinstance(api_config.get(key), str):
                        api_config[key] = api_config[key].strip()

                dolibarr_url = api_config.get('dolibarr_url', '')
                if dolibarr_url and not (dolibarr_url.startswith('http://') or dolibarr_url.startswith('https://')):
                    _LOGGER.warning("API-Konfiguration: dolibarr_url muss mit http:// oder https:// beginnen")

                api_token = api_config.get('api_token', '')
                if not api_token or api_token == 'your_dolapikey_here':
                    _LOGGER.warning("API-Token nicht konfiguriert oder noch Default-Wert")

            return config
    except Exception as e:
        _LOGGER.error("Fehler beim Laden der Konfiguration: %s", e)
        return {}


class HomeAssistantWebsocket:
    """Verbindung zur Home Assistant Websocket API (D-02, D-10)"""

    def __init__(self, host: str = "homeassistant", port: int = 8123, token: str = '', ws_url: str = ''):
        self.host = host
        self.port = port
        self.ws_url = ws_url or f"ws://{host}:{port}/api/websocket"
        self.access_token = token or os.getenv('SUPERVISOR_TOKEN', '')
        self.session_id: Optional[str] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        # HA verlangt streng monoton steigende, eindeutige Message-IDs pro
        # Verbindung. Ein einziger Zähler für alle Kommandos (subscribe, get_states).
        self._msg_id = 0

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    async def connect(self):
        """Verbindet sich mit HA Websocket API"""
        self._session = aiohttp.ClientSession()
        try:
            self._ws = await self._session.ws_connect(self.ws_url)
            _LOGGER.info("Verbunden mit HA Websocket API: %s", self.ws_url)

            # Auth-Response empfangen
            msg = await self._ws.receive_json()
            if msg.get('type') != 'auth_required':
                raise ConnectionError("Unerwartete Antwort von HA")

            # Auth senden
            await self._ws.send_json({
                'type': 'auth',
                'access_token': self.access_token
            })

            # Auth-Bestätigung
            msg = await self._ws.receive_json()
            if msg.get('type') != 'auth_ok':
                raise PermissionError("Authentifizierung fehlgeschlagen")

            _LOGGER.info("Erfolgreich authentifiziert bei Home Assistant")
            return True

        except Exception as e:
            _LOGGER.error("Verbindungsfehler: %s", e)
            await self.disconnect()
            raise

    async def subscribe_entities(self, callback):
        """Abonniert Entitäts-Updates via Websocket (D-10, event-basiert).

        WICHTIG: Sobald diese Schleife läuft, ist sie der EINZIGE Leser des
        Sockets. get_state()/get_all_states() dürfen NICHT mehr aufgerufen
        werden (sie würden Frames stehlen) — stattdessen den gecachten Wert
        aus dem Event-Stream nutzen.
        """
        sub_id = self._next_id()
        await self._ws.send_json({
            'id': sub_id,
            'type': 'subscribe_events',
            'event_type': 'state_changed'
        })

        msg = await self._ws.receive_json()
        if msg.get('type') != 'result' or not msg.get('success'):
            raise RuntimeError(f"Subscribe fehlgeschlagen: {msg}")

        _LOGGER.info("Erfolgreich Entitäts-Updates abonniert")

        # Nachrichten verarbeiten — alle Frames gehören jetzt diesem Loop
        while True:
            msg = await self._ws.receive_json()
            if msg.get('type') == 'event':
                event = msg.get('event', {})
                entity_id = event.get('data', {}).get('entity_id')
                new_state = event.get('data', {}).get('new_state', {})

                if entity_id and new_state:
                    await callback(entity_id, new_state)

    async def get_all_states(self) -> Dict[str, Any]:
        """Holt EINMALIG alle Entitäts-States als dict {entity_id: state}.

        NUR vor dem Start der subscribe_entities-Schleife aufrufen — danach
        konkurriert das receive_json() mit dem Event-Loop. Demultiplext nach
        der eigenen Message-ID, damit zwischenzeitliche Frames korrekt
        übersprungen werden.
        """
        req_id = self._next_id()
        await self._ws.send_json({'id': req_id, 'type': 'get_states'})

        # Lese bis das 'result' mit UNSERER id kommt (andere Frames überspringen)
        for _ in range(50):
            msg = await self._ws.receive_json()
            if msg.get('id') == req_id and msg.get('type') == 'result':
                if not msg.get('success'):
                    _LOGGER.warning("get_states fehlgeschlagen: %s", msg.get('error'))
                    return {}
                return {s.get('entity_id'): s for s in msg.get('result', [])}
        _LOGGER.warning("get_states: kein passendes result nach 50 Frames")
        return {}

    async def get_state(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """Einzelnen State holen (nur vor dem subscribe-Loop verwenden)."""
        states = await self.get_all_states()
        return states.get(entity_id)

    async def disconnect(self):
        """Trennt die Verbindung"""
        if self._ws:
            await self._ws.close()
        if self._session:
            await self._session.close()
        _LOGGER.info("Verbindung getrennt")


async def _start_session_for(rfid_hex: str, source: str):
    """Startet eine neue Session falls noch keine aktiv ist. Der Start-
    Zählerstand kommt aus dem gecachten Energie-Wert (_latest_energy), der
    laufend aus dem Event-Stream aktualisiert wird — NICHT per get_state()."""
    if session_manager.get_active_session():
        return None  # bereits aktiv — kein Doppelstart

    global _last_power_high_time, _last_energy_change_time

    if _latest_energy is None:
        _LOGGER.warning(
            "Session-Start (%s) ohne gültigen Energie-Zählerstand — Sensor '%s' "
            "lieferte noch keinen Wert. Session wird gestartet, aber Start-Zähler=0; "
            "Ende markiert sie ggf. als unvollständig.",
            source, profile.sensor_energy
        )
    start_energy = _latest_energy if _latest_energy is not None else 0.0

    wallbox_id = current_config.get('wallbox_id', 'wallbox')
    session_id = session_manager.start_session(
        rfid_hex, start_energy, wallbox_id=wallbox_id,
        start_energy_valid=(_latest_energy is not None)
    )
    if session_id:
        _LOGGER.info("Ladevorgang gestartet (%s): Session #%s, Start-Zähler=%.3f kWh",
                     source, session_id, start_energy)
        # Idle-Zeitstempel auf JETZT zurücksetzen — sonst würde der
        # idle_energy_guard (power_threshold/energy_delta) eine frisch
        # gestartete Session sofort wieder beenden, falls Leistung/Zähler
        # vor dem Tag-Event schon länger als end_idle_minutes inaktiv war
        # (z.B. normale Stille zwischen Tag-Auth und tatsächlichem Ladestart).
        now = time.time()
        _last_power_high_time = now
        _last_energy_change_time = now
    return session_id


async def _end_active_session(reason: str):
    """Beendet die aktive Session mit dem gecachten Energie-Zählerstand.
    - kWh = end − start aus dem Event-Stream-Cache
    - Sessions ohne gültige Energie-Readings → 'incomplete' (sichtbar, nicht
      still verworfen), echte Mini-Ladungen < min_session_kwh → 'discarded'."""
    min_kwh = float(current_config.get('min_session_kwh', 0.05))
    energy_valid = _latest_energy is not None
    end_energy = _latest_energy if energy_valid else 0.0

    completed = session_manager.end_session(
        end_energy, min_kwh=min_kwh, end_energy_valid=energy_valid
    )
    if completed:
        _LOGGER.info("Ladevorgang beendet (%s): Session #%s, %.3f kWh",
                     reason, completed['id'], completed['total_kwh'])
    return completed


async def _try_start_from_signal(source: str):
    """Startet eine Session ausgehend von einem Charging-Signal (State-Keyword,
    Leistungsschwelle, Energie-Delta oder externe Aktiv-Entity — alles außer
    dem direkten RFID-Event). Löst die Autorisierung je nach auth_mode auf:

      - auth_mode == 'none': startet ohne RFID-Prüfung (reines Logging/Monitoring).
      - sonst: übliche Frisch-Event → anliegender Tag → persistierte
        Autorisierung-Kette (überlebt Neustart und beliebig lange
        Lastmanagement-Verzögerung zwischen Tag-Auth und Ladebeginn)."""
    global _pending_auth

    if profile.auth_mode == 'none':
        await _start_session_for(_NO_AUTH_RFID, source)
        return

    whitelist = current_config.get('rfid_whitelist', [])
    auth_hex = None
    auth_src = None
    if _pending_auth and (time.time() - _pending_auth['time']) < _PENDING_AUTH_WINDOW:
        auth_hex, auth_src = _pending_auth['rfid_hex'], 'frisches Auth-Event'
    elif _latest_rfid and _latest_rfid.lower() not in _RFID_NONE_VALUES \
            and session_manager.is_rfid_authorized(_latest_rfid, whitelist):
        auth_hex, auth_src = _latest_rfid, 'anliegender Tag'
    else:
        persisted = session_manager.get_pending_auth()
        if persisted and session_manager.is_rfid_authorized(persisted, whitelist):
            auth_hex, auth_src = persisted, 'persistierte Autorisierung (Lastmanagement-Verzögerung)'

    if auth_hex:
        _LOGGER.info("Session-Start via %s — Tag-Quelle: %s.", source, auth_src)
        await _start_session_for(auth_hex, source)
        _pending_auth = None
    else:
        _LOGGER.warning("Charging-Signal (%s) aber KEINE Autorisierung bekannt "
                        "(weder Event noch persistiert). Session wird NICHT erfasst.", source)


async def sensor_callback(entity_id: str, state: Dict[str, Any]):
    """
    Callback für Sensor-Updates mit Session-Tracking.

    Alfen-Wallbox-Datenfluss:
      1. RFID-Sensor wechselt auf eine Tag-ID (z.B. "A1B2C3D4")
         → ggf. Session starten (wenn whitelisted und keine aktive läuft).
      2. State-Sensor wechselt zu "Charging Power On" → Energie fließt,
         Live-Zustand wird aktualisiert.
      3. State-Sensor wechselt zu "Available" / "Finishing" / "Stopped"
         → Session beenden (Energie-Delta = end − start). Dies ist der
         EINZIGE Weg, eine Session zu beenden.
      Hinweis: "No Tag" beendet KEINE Session mehr — der Tag fällt bei der
      angepassten Alfen-Integration nach ~2 s automatisch auf "No Tag" zurück
      und ist damit der Ruhezustand, kein Abbruch-Signal.
    """
    global session_manager, current_config, ha_ws, api_state
    global _latest_energy, _latest_rfid, _last_power_high_time, _last_energy_change_time, _pending_auth

    sensor_rfid   = profile.sensor_rfid
    sensor_energy = profile.sensor_energy
    sensor_state  = profile.sensor_state

    state_value = state.get('state')

    # ----- Energie-Cache + Live-State für Web-Server pflegen ----------------
    if entity_id == sensor_energy:
        parsed = _parse_energy(state_value)
        if parsed is not None:
            prev_energy = _latest_energy
            energy_changed = (prev_energy is None) or (parsed != prev_energy)
            _latest_energy = parsed            # Cache für Session-Start/-Ende
            if api_state is not None:
                api_state['current_energy'] = parsed
                api_state['last_update'] = datetime.now().isoformat(timespec='seconds')

            if energy_changed:
                _last_energy_change_time = time.time()
                # state_mode='energy_delta': steigt der Zähler und läuft noch
                # keine Session → Ladebeginn wurde erkannt, Session starten.
                if profile.state_mode == 'energy_delta' and prev_energy is not None \
                        and parsed > prev_energy and not session_manager.get_active_session():
                    await _try_start_from_signal(f'energy_delta=+{parsed - prev_energy:.3f}kWh')

            active = session_manager.get_active_session()
            if active:
                delta = parsed - float(active.get('start_energy_kwh') or 0.0)
                _LOGGER.debug("Aktive Session #%s: Zähler=%.3f kWh, Geladen=%.3f kWh",
                              active['id'], parsed, delta)
        return

    if entity_id == sensor_state:
        if api_state is not None:
            api_state['wallbox_state'] = state_value
            api_state['last_update'] = datetime.now().isoformat(timespec='seconds')

    # ----- externe Aktiv-Entity (state_mode='external_boolean') -------------
    # Ersetzt die Status-Keyword-Erkennung komplett: on = Session läuft
    # (inkl. Lastmanagement-Pausen), off = Session vorbei. Der Nutzer kann
    # sich diese Entity selbst per HA-Template bauen (z.B. aus Leistung +
    # eigener Hysterese/Pause-Logik) — das Addon muss dann nichts mehr über
    # Schwellenwerte wissen.
    if profile.state_mode == 'external_boolean' and profile.active_entity \
            and entity_id == profile.active_entity:
        sv_low = str(state_value or '').strip().lower()
        active = session_manager.get_active_session()
        if sv_low in ('on', 'true', '1'):
            if not active:
                await _try_start_from_signal('external_boolean=on')
        elif sv_low in ('off', 'false', '0'):
            if active:
                await _end_active_session('external_boolean=off')
            _pending_auth = None
            session_manager.clear_pending_auth()
        return

    # ----- Leistungssensor (state_mode='power_threshold') -------------------
    # Kein Status-Sensor vorhanden → Ladezustand wird aus einem Leistungswert
    # abgeleitet. Ende erkennt der idle_energy_guard-Hintergrundtask (siehe
    # main()), sobald die Leistung end_idle_minutes lang unter der Schwelle war.
    if profile.state_mode == 'power_threshold' and profile.power_sensor \
            and entity_id == profile.power_sensor:
        power_val = _parse_energy(state_value)
        if power_val is None:
            return
        active = session_manager.get_active_session()
        if power_val >= profile.power_threshold_w:
            _last_power_high_time = time.time()
            if not active:
                await _try_start_from_signal(f'power_threshold={power_val:.0f}W')
        elif active:
            _LOGGER.debug("Leistung (%.0f W) unter Schwelle (%.0f W) — Session #%s "
                          "bleibt offen (Idle-Timer läuft).",
                          power_val, profile.power_threshold_w, active['id'])
        return

    # ----- RFID-Sensor (Session-Start / ggf. Session-Ende bei tag_toggle) ---
    if entity_id == sensor_rfid and sensor_rfid:
        sv = (state_value or '').strip()
        sv_low = sv.lower()

        # "No Tag" / unknown: Bei Auto-Reset-Wallboxen (Alfen-Integration setzt
        # den Tag nach ~2 s selbst auf "No Tag" zurück) ist das der NORMALE
        # Ruhezustand — KEIN "Karte abgezogen". Session-Ende läuft daher
        # (außer bei auth_mode='tag_toggle') über die Zustand-Erkennung, nicht
        # über "No Tag". _latest_rfid bleibt bewusst erhalten, damit ein etwas
        # später startender Ladevorgang noch dem letzten Tag zugeordnet wird.
        if sv_low in _RFID_NONE_VALUES:
            return

        # Anliegenden Tag cachen (auch vor Debounce/Whitelist — für Charging-Fallback)
        _latest_rfid = sv

        # Echter Tag erkannt — Debounce + Whitelist
        if not session_manager.debounce_rfid(sv):
            return
        whitelist = current_config.get('rfid_whitelist', [])
        if not session_manager.is_rfid_authorized(sv, whitelist):
            _LOGGER.warning("Nicht autorisierte RFID: %s... (Whitelist-Eintrag fehlt)",
                            hash_rfid(sv)[:16])
            return

        # tag_toggle: zweiter autorisierter Tap beendet die laufende Session
        # sofort — unabhängig vom Zustand-Modus (z.B. Alfen ohne Status-Sensor
        # oder generischer Leser+Relais ohne "Auto steckt"-Signal).
        if profile.auth_mode == 'tag_toggle' and session_manager.get_active_session():
            await _end_active_session('tag_toggle_stop')
            _pending_auth = None
            session_manager.clear_pending_auth()
            return

        # Tag merken — in-memory (schneller Pfad) UND persistent. Persistent,
        # weil das Lastmanagement den Ladebeginn um STUNDEN verzögern kann und
        # der In-Memory-Merker einen Addon-Neustart nicht überlebt. Gültig bis
        # Abstecken/neuer Tag — kein Zeitfenster mehr.
        _pending_auth = {'rfid_hex': sv, 'time': time.time()}
        session_manager.set_pending_auth(sv)

        # Session sofort starten (klassischer Fall: Karte → sofort Laden)
        await _start_session_for(sv, 'rfid_event')
        return

    # ----- Status-Sensor (state_mode='state_keywords', Session-Start/Ende) --
    if profile.state_mode == 'state_keywords' and entity_id == sensor_state:
        active = session_manager.get_active_session()
        category = wallbox_profile.classify_state(state_value, profile.end_keywords, profile.pause_keywords)

        # ENDE (Fahrzeug abgesteckt/abgeschlossen/Fehler) — beendet die Session.
        # Fahrzeug ist weg → Autorisierung (in-memory UND persistent) verfällt.
        if category == 'end':
            if active:
                await _end_active_session(f'state={state_value}')
            _pending_auth = None
            session_manager.clear_pending_auth()
            return

        # PAUSE (Lastmanagement/EV): Fahrzeug bleibt angesteckt → Session OFFEN
        # halten, NICHT beenden. Bei Wiederaufnahme läuft dieselbe Session weiter.
        if category == 'pause':
            if active:
                _LOGGER.info("Ladung pausiert (state='%s') — Session #%s bleibt offen "
                             "(Lastmanagement/EV-Pause, wird als EIN Vorgang erfasst).",
                             state_value, active['id'])
            return

        if category == 'charging':
            # Wallbox lädt jetzt aktiv. Wenn KEINE Session läuft, aber eine
            # RFID kürzlich autorisiert wurde → Session nachträglich starten.
            # Fängt RFID-Events ab die das Addon verpasst hat (z.B. während
            # Restart, Websocket-Reconnect, oder lange Auth→Charging-Delay).
            if active:
                _LOGGER.debug("Wallbox lädt (state='%s'), Session #%s läuft",
                              state_value, active['id'])
                return
            await _try_start_from_signal(f'charging_state={state_value}')
            return

        _LOGGER.debug("Wallbox-State Zwischenzustand: '%s'", state_value)
        return


async def check_startup_session():
    """Prüft beim Start ob eine aktive Session existiert UND ob die Wallbox
    aktuell lädt — fängt verlorene Sessions ab wenn das Addon während einer
    laufenden Ladung neugestartet wurde.

    WICHTIG (Datenverlust-Fix): Der Energiezähler ist kumulativ und läuft
    während eines Addon-/HA-Neustarts unbeeinflusst weiter. Eine beim Start
    vorgefundene 'active' Session darf deshalb NICHT pauschal als
    unvollständig verworfen werden — sonst gehen alle bis dahin geladenen
    kWh verloren, obwohl der Zähler sie korrekt mitgezählt hat. Stattdessen:
      - Zustand JETZT zeigt eindeutig "beendet" (Auto während des Ausfalls
        abgesteckt) → Session SOFORT mit dem frisch gelesenen Zählerstand
        korrekt abschließen. Das muss hier passieren, weil Home Assistant nur
        auf Zustands-WECHSEL Events schickt — ein bereits erreichter Endzustand
        löst nie mehr ein "state_changed"-Event aus, das ihn beenden könnte.
      - Zustand zeigt noch "lädt"/"pausiert" → Session unangetastet lassen,
        sie läuft in der DB als 'active' weiter und wird ganz normal per
        Zustands-Event/Idle-Wache beendet, sobald es soweit ist.
      - Zustand nicht bestimmbar (z.B. power_threshold/energy_delta ohne
        Zeitdauer-Info) → ebenfalls offen lassen; die Session-Wache
        (max_session_hours) greift notfalls später mit einem dann aktuellen
        Zählerstand.
    """
    global session_manager, api_client, _pending_auth, _latest_energy, _latest_rfid
    global _last_power_high_time, _last_energy_change_time

    # 1) EINMALIGER Snapshot ALLER States (vor dem subscribe-Loop, konkurrenzfrei) —
    #    VOR jeder Entscheidung über eine vorgefundene aktive Session. Nur mit
    #    einem frischen Zählerstand lässt sich eine während des Ausfalls beendete
    #    Ladung noch vollständig und korrekt abrechnen.
    sensor_rfid   = profile.sensor_rfid
    sensor_state  = profile.sensor_state
    sensor_energy = profile.sensor_energy
    try:
        snapshot = await ha_ws.get_all_states()
    except Exception as exc:
        _LOGGER.warning("Konnte Wallbox-Zustand beim Start nicht lesen: %s", exc)
        snapshot = {}

    rfid_val   = (snapshot.get(sensor_rfid)  or {}).get('state', '') or '' if sensor_rfid else ''
    state_val  = (snapshot.get(sensor_state) or {}).get('state', '') or ''
    energy_raw = (snapshot.get(sensor_energy) or {}).get('state')

    if rfid_val and rfid_val.lower() not in _RFID_NONE_VALUES:
        _latest_rfid = rfid_val

    # Energie-Cache seeden — ab jetzt hat Session-Start/-Ende einen gültigen Wert
    seeded = _parse_energy(energy_raw)
    if seeded is not None:
        _latest_energy = seeded
        _last_energy_change_time = time.time()
        if api_state is not None:
            api_state['current_energy'] = seeded
            api_state['last_update'] = datetime.now().isoformat(timespec='seconds')
    _LOGGER.info("Wallbox-Zustand beim Start: state='%s', rfid='%s', zaehler=%s kWh",
                 state_val, rfid_val, ('%.3f' % seeded) if seeded is not None else 'n/a')

    # 2) Vorgefundene aktive Session(en) anhand des JETZT gelesenen Zustands
    #    behandeln — siehe Docstring oben.
    recovered_sessions = session_manager.recover_active_sessions()
    if recovered_sessions:
        newest, *stale_extra = recovered_sessions
        _LOGGER.info("=== Startup Recovery: Session #%s war beim Neustart aktiv ===", newest['id'])

        definitely_ended = False
        if profile.state_mode == 'state_keywords':
            definitely_ended = wallbox_profile.classify_state(
                state_val, profile.end_keywords, profile.pause_keywords) == 'end'
        elif profile.state_mode == 'external_boolean' and profile.active_entity:
            active_val = str((snapshot.get(profile.active_entity) or {}).get('state', '')).strip().lower()
            definitely_ended = active_val in ('off', 'false', '0')
        # power_threshold/energy_delta: ein Einzel-Snapshot kann ein Ende nicht
        # zuverlässig belegen (fehlende Zeitdauer-Info) — Session bleibt offen,
        # idle_energy_guard erkennt das Ende danach ganz normal weiter unten.

        if definitely_ended and seeded is not None:
            _LOGGER.warning(
                "Session #%s wurde WÄHREND des Neustarts beendet (state='%s') — "
                "schließe sie jetzt mit dem aktuellen Zählerstand (%.3f kWh) ab, "
                "damit die geladene Energie nicht verloren geht.",
                newest['id'], state_val, seeded)
            await _end_active_session('restart_recovery_ended')
        elif definitely_ended:
            _LOGGER.warning(
                "Session #%s während des Ausfalls beendet, aber kein Zählerstand "
                "verfügbar — kWh nicht berechenbar, wird als unvollständig markiert.",
                newest['id'])
            session_manager.mark_session_incomplete(newest['id'], 'restart_recovery_no_energy')
        else:
            _LOGGER.info(
                "Session #%s bleibt nach dem Neustart aktiv (Ladung läuft/pausiert "
                "vermutlich weiter) — wird ganz normal per Zustands-Event/Idle-Wache "
                "beendet, sobald es soweit ist. Keine Energie geht dabei verloren.",
                newest['id'])

        # Mehr als eine 'active' Session ist eine Dateninkonsistenz (sollte durch
        # den Doppelstart-Schutz nie vorkommen) — ältere defensiv abschließen.
        for stale in stale_extra:
            session_manager.mark_session_incomplete(stale['id'], 'restart_recovery_duplicate')
            _LOGGER.error("Zusätzliche aktive Session #%s (Dateninkonsistenz) als "
                          "unvollständig markiert.", stale['id'])

    # 3) Nur falls jetzt WIRKLICH keine Session mehr aktiv ist (auch nach obiger
    #    Recovery), prüfen ob die Wallbox schon lädt und ggf. eine neue starten
    #    (z.B. Erstinstallation während einer laufenden Ladung).
    if session_manager.get_active_session() is None:
        already_charging = False
        if profile.state_mode == 'state_keywords':
            already_charging = wallbox_profile.classify_state(
                state_val, profile.end_keywords, profile.pause_keywords) == 'charging'
        elif profile.state_mode == 'power_threshold' and profile.power_sensor:
            power_val = _parse_energy((snapshot.get(profile.power_sensor) or {}).get('state'))
            if power_val is not None and power_val >= profile.power_threshold_w:
                already_charging = True
                _last_power_high_time = time.time()
        elif profile.state_mode == 'external_boolean' and profile.active_entity:
            active_val = str((snapshot.get(profile.active_entity) or {}).get('state', '')).strip().lower()
            already_charging = active_val in ('on', 'true', '1')
        # state_mode='energy_delta': ein Einzel-Snapshot kann keine Bewegung des
        # Zählers zeigen — Erkennung übernimmt der erste energy_sensor-Event danach.

        if already_charging:
            _LOGGER.warning(
                "Wallbox lädt bereits beim Addon-Start (state_mode=%s) — versuche "
                "Session nachträglich zu starten. Bereits geladene kWh vor diesem "
                "Start sind für DIESE Session verloren, ab jetzt wird erfasst.",
                profile.state_mode)
            await _try_start_from_signal('startup_charging_detected')

    # Falls gerade ein Tag anliegt, ihn als pending-auth merken (für Charging-Wechsel)
    if rfid_val and rfid_val.lower() not in _RFID_NONE_VALUES:
        whitelist = current_config.get('rfid_whitelist', [])
        if session_manager.is_rfid_authorized(rfid_val, whitelist):
            _pending_auth = {'rfid_hex': rfid_val, 'time': time.time()}


async def periodic_transmission():
    """Periodische (und auf Anforderung sofortige) Übertragung an Dolibarr."""
    last_transmit = 0.0
    transmit_interval = current_config.get("api", {}).get("transmit_interval", 300)
    while True:
        if api_client:
            now = time.time()
            if (now - last_transmit) >= transmit_interval or _transmit_requested.is_set():
                _transmit_requested.clear()
                last_transmit = now
                try:
                    # In einen Thread auslagern: transmit_completed_sessions ist
                    # synchrones requests mit Retry-Backoff und kann bei einem
                    # hängenden Dolibarr Minuten dauern. Im OCPP-Betrieb ist dieser
                    # Event-Loop derselbe, der die Wallbox bedient — er darf nicht
                    # stehenbleiben, sonst läuft die Wallbox in ihren Timeout.
                    result = await asyncio.to_thread(
                        session_manager.transmit_completed_sessions, api_client)
                    if result["transmitted"] > 0:
                        _LOGGER.info("Sessions an Dolibarr übertragen: %s", result["transmitted"])
                    if result["failed"] > 0:
                        _LOGGER.error("Fehler bei API-Übertragung: %s Sessions fehlgeschlagen",
                                      result["failed"])
                        if not await asyncio.to_thread(api_client.check_connection):
                            _LOGGER.warning("API-Verbindung verloren - deaktiviere temporär")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # Eine Ausnahme darf diesen Task nicht für den Rest der
                    # Prozesslaufzeit beenden — im OCPP-Betrieb gibt es keinen
                    # zweiten Auslöser für die Übertragung.
                    _LOGGER.exception("Übertragung an Dolibarr fehlgeschlagen: %s", exc)
        await asyncio.sleep(1)


def build_ocpp_server(settings) -> OcppServer:
    """Verdrahtet den OCPP-Server mit SessionManager, Whitelist und Live-Zustand."""
    api_state['charge_points'] = {}
    deps = CentralSystemDeps(
        session_manager=session_manager,
        whitelist=current_config.get('rfid_whitelist', []),
        live=api_state['charge_points'],
        min_kwh=float(current_config.get('min_session_kwh', 0.05)),
        heartbeat_interval=settings.heartbeat_interval,
        apply_recommended_config=settings.apply_recommended_config,
        on_session_completed=lambda _session: _transmit_requested.set(),
    )
    return OcppServer(settings, deps)


def ocpp_overdue_sessions(max_hours: float, now: Optional[datetime] = None) -> list:
    """Aktive OCPP-Sessions, die länger als max_hours laufen."""
    now = now or datetime.now()
    overdue = []
    for s in session_manager.get_active_ocpp_sessions():
        try:
            age_h = (now - datetime.fromisoformat(s['start_time'])).total_seconds() / 3600.0
        except (TypeError, ValueError):
            continue
        if age_h >= max_hours:
            overdue.append(s)
    return overdue


async def ocpp_stale_session_guard():
    """Im OCPP-Betrieb beendet NUR die Wallbox eine Session (StopTransaction,
    ggf. verspätet aus ihrer Offline-Queue). Die Wache warnt daher nur einmal
    je Session, statt sie mit einem geratenen Zählerstand zu schließen."""
    max_hours = float(current_config.get("max_session_hours", 24))
    warned = set()
    while True:
        await asyncio.sleep(300)
        try:
            for s in ocpp_overdue_sessions(max_hours):
                if s['id'] not in warned:
                    warned.add(s['id'])
                    _LOGGER.warning("OCPP-Session #%s (%s) läuft seit über %.0f h — Wallbox erreichbar? "
                                    "Wird erst mit ihrer StopTransaction abgeschlossen.",
                                    s['id'], s['charge_point_id'], max_hours)
        except Exception as exc:  # Wache darf nie den Loop killen
            _LOGGER.warning("ocpp_stale_session_guard Fehler: %s", exc)


async def run_ocpp_mode(settings) -> None:
    """Betriebsart session_source=ocpp: kein HA-Websocket, die Wallbox verbindet sich direkt."""
    if not settings.charge_points:
        _LOGGER.error("session_source=ocpp, aber keine ocpp_charge_points konfiguriert — "
                      "jede Wallbox wird abgewiesen")
    server = build_ocpp_server(settings)
    await server.start('0.0.0.0', OCPP_PORT)
    # KEINE Restart-Recovery wie im HA-Pfad: offene Sessions bleiben 'active' —
    # die Wallbox liefert StopTransaction aus ihrer Offline-Queue nach.
    open_sessions = session_manager.get_active_ocpp_sessions()
    if open_sessions:
        _LOGGER.info("%d laufende OCPP-Session(s) aus der Zeit vor dem Neustart — warte auf StopTransaction",
                     len(open_sessions))
    asyncio.create_task(ocpp_stale_session_guard())
    if api_client:
        asyncio.create_task(periodic_transmission())
    asyncio.create_task(start_web_server(session_manager, current_config, api_state, port=8099))
    await server.serve_forever()


async def main():
    """Hauptschleife (D-03, D-10, D-11) - erweitert für Session-Tracking und API-Transmission"""
    global session_manager, current_config, ha_ws, api_client, api_state, profile

    _LOGGER.info("Wallbox-Dolibarr Addon startet...")

    # Session Manager initialisieren (PER-01)
    session_manager = SessionManager(db_path="/data/sessions.db")

    # Konfiguration laden (für Whitelist und API)
    current_config = load_config()

    # Wallbox-Profil auflösen (Auth-/Zustand-Modus, Sensoren, Schwellenwerte).
    # 'alfen_eve' (Default) liefert exakt das bewährte, bisherige Verhalten.
    profile = wallbox_profile.resolve_profile(current_config)
    _LOGGER.info(
        "Wallbox-Profil: %s (auth_mode=%s, state_mode=%s, rfid=%s, energy=%s, state=%s)",
        current_config.get('wallbox_profile', 'alfen_eve'), profile.auth_mode, profile.state_mode,
        profile.sensor_rfid, profile.sensor_energy, profile.sensor_state,
    )

    # API Client initialisieren — flat config (dolibarr_url auf Top-Level)
    api_client = None
    # api_state: gemeinsamer Live-Zustand, wird vom Sensor-Callback aktualisiert
    # und vom Web-Server für die Live-Anzeige laufender Sessions gelesen.
    api_state  = {
        'client': None,
        'current_energy': None,    # aktueller Energiezähler-Stand in kWh
        'wallbox_state': None,     # 'Charging' / 'Idle' / 'Stopped' / None
        'last_update': None,       # ISO-Timestamp der letzten Sensor-Aktualisierung
    }
    api_config   = current_config.get("api", {})
    dolibarr_url = api_config.get("dolibarr_url", "")
    api_token    = api_config.get("api_token", "")
    if dolibarr_url and dolibarr_url != "https://dolibarr.example.com" and api_token:
        try:
            api_client = WallboxApiClient(
                base_url=dolibarr_url,
                api_token=api_token,
                timeout=30
            )
            if api_client.check_connection():
                api_state['client'] = api_client
                _LOGGER.info("Dolibarr API Verbindung erfolgreich: %s", dolibarr_url)
            else:
                _LOGGER.warning("Dolibarr API nicht erreichbar — wird später erneut versucht")
                api_client = None
        except Exception as e:
            _LOGGER.error("Fehler beim Initialisieren des API-Clients: %s", e)
            api_client = None
    else:
        _LOGGER.info("Keine Dolibarr API-Konfiguration — Addon läuft ohne API-Transmission")

    ocpp_settings = resolve_ocpp_settings(current_config)
    if ocpp_settings.enabled:
        _LOGGER.info("Betriebsart: OCPP-Zentralserver (%d Wallbox(en) konfiguriert)",
                     len(ocpp_settings.charge_points))
        await run_ocpp_mode(ocpp_settings)
        return

    # HA-Token ermitteln: SUPERVISOR_TOKEN hat Vorrang, Fallback auf ha_token aus Konfiguration
    supervisor_token = os.getenv('SUPERVISOR_TOKEN', '')
    config_ha_token  = current_config.get('ha_token', '')
    ha_token = supervisor_token or config_ha_token
    if not ha_token:
        _LOGGER.error(
            "Kein HA-Token verfügbar! Bitte Long-Lived Access Token unter "
            "Einstellungen → Profil → Langlebige Zugriffstoken erstellen "
            "und als 'ha_token' in der Addon-Konfiguration eintragen."
        )
    else:
        token_src = 'SUPERVISOR_TOKEN' if supervisor_token else 'ha_token (Konfiguration)'
        _LOGGER.info("HA-Authentifizierung via %s", token_src)

    ha_ws_url = "ws://supervisor/core/websocket" if supervisor_token else "ws://homeassistant:8123/api/websocket"
    ha_ws = HomeAssistantWebsocket(token=ha_token, ws_url=ha_ws_url)

    try:
        # Verbinden
        await ha_ws.connect()

        # Prüfen ob aktive Session nach Neustart existiert (PER-01)
        await check_startup_session()

        # Sicherung gegen hängende Sessions: Eine Session endet normalerweise
        # beim Abstecken (state=Available). Falls dieses Event ausbleibt (z.B.
        # Sensor-/Websocket-Aussetzer), würde eine "active" Session ewig offen
        # bleiben und JEDE weitere Ladung blockieren (kein Doppelstart). Diese
        # Wache schließt sie nach max_session_hours. Bewusst großzügig, damit
        # normale Lastmanagement-Pausen (Minuten–Stunden) NICHT betroffen sind.
        async def stale_session_guard():
            import time as _t
            max_hours = float(current_config.get("max_session_hours", 24))
            while True:
                await asyncio.sleep(300)  # alle 5 Minuten
                try:
                    active = session_manager.get_active_session()
                    if not active:
                        continue
                    started = active.get("start_time")
                    if not started:
                        continue
                    age_h = (datetime.now() - datetime.fromisoformat(started)).total_seconds() / 3600.0
                    if age_h >= max_hours:
                        _LOGGER.warning(
                            "Session #%s seit %.1f h aktiv (> %.0f h) ohne Absteck-Event "
                            "— wird als Sicherung beendet.", active["id"], age_h, max_hours)
                        await _end_active_session("max_duration_guard")
                except Exception as exc:  # Wache darf nie den Loop killen
                    _LOGGER.warning("stale_session_guard Fehler: %s", exc)

        asyncio.create_task(stale_session_guard())
        _LOGGER.info("Session-Wache gestartet (max_session_hours)")

        # Für state_mode='power_threshold'/'energy_delta' gibt es keinen
        # expliziten "Ende"-Event (im Gegensatz zu Status-Keywords oder der
        # externen Aktiv-Entity) — das Ende wird erst erkannt, wenn Leistung
        # bzw. Zählerstand end_idle_minutes lang unverändert/unter Schwelle
        # war. Dieser Task prüft das periodisch.
        async def idle_energy_guard():
            while True:
                await asyncio.sleep(30)
                try:
                    active = session_manager.get_active_session()
                    if not active:
                        continue
                    if profile.state_mode == 'power_threshold' and _last_power_high_time is not None:
                        idle_min = (time.time() - _last_power_high_time) / 60.0
                        if idle_min >= profile.end_idle_minutes:
                            _LOGGER.info(
                                "Keine Leistung > %.0f W seit %.1f min — Session #%s wird beendet.",
                                profile.power_threshold_w, idle_min, active["id"])
                            await _end_active_session("power_threshold_idle")
                    elif profile.state_mode == 'energy_delta' and _last_energy_change_time is not None:
                        idle_min = (time.time() - _last_energy_change_time) / 60.0
                        if idle_min >= profile.end_idle_minutes:
                            _LOGGER.info(
                                "Zählerstand seit %.1f min unverändert — Session #%s wird beendet.",
                                idle_min, active["id"])
                            await _end_active_session("energy_delta_idle")
                except Exception as exc:  # Wache darf nie den Loop killen
                    _LOGGER.warning("idle_energy_guard Fehler: %s", exc)

        if profile.state_mode in ('power_threshold', 'energy_delta'):
            asyncio.create_task(idle_energy_guard())
            _LOGGER.info("Idle-Wache gestartet (state_mode=%s, end_idle_minutes=%.1f)",
                         profile.state_mode, profile.end_idle_minutes)

        # Hintergrund-Task starten
        if api_client:
            transmission_task = asyncio.create_task(periodic_transmission())
            _LOGGER.info("API-Transmission Hintergrund-Task gestartet")

        # Ingress Web-Server für manuelle Ladevorgänge starten
        asyncio.create_task(start_web_server(session_manager, current_config, api_state, port=8099))
        _LOGGER.info("Ingress Web-Server Task gestartet (Port 8099)")

        # Sensor-Updates abonnieren (event-basiert, D-10) - blockiert bis zur Unterbrechung
        await ha_ws.subscribe_entities(sensor_callback)

    except KeyboardInterrupt:
        _LOGGER.info("Addon wird beendet...")
    except Exception as e:
        _LOGGER.error("Fehler: %s", e, exc_info=True)
        # Crash + Supervisor restart (D-11)
        raise
    finally:
        await ha_ws.disconnect()


if __name__ == '__main__':
    asyncio.run(main())
