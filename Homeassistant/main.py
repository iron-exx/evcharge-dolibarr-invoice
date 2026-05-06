#!/usr/bin/env python3
"""
Wallbox-Dolibarr Addon Hauptskript

Verbindet sich via Websocket API mit Home Assistant Core,
liest Alfen Wallbox Sensoren aus und implementiert Session-Tracking.
"""
import asyncio
import aiohttp
import json
import logging
import os
import sys
import yaml
from typing import Dict, Any, Optional

# Hash-Utility importieren
sys.path.insert(0, '/usr/local/bin')
from utils.hash import hash_rfid

# Session Manager importieren
from session_manager import SessionManager, CHARGING, IDLE, STOPPED

# API Client importieren (Phase 3)
from api_client import WallboxApiClient

# Logging Setup (D-17, D-20)
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
_LOGGER = logging.getLogger(__name__)

# Status-Konstanten (D-16)
CHARGING = "Charging"
IDLE = "Idle"
STOPPED = "Stopped"

# Alfen Sensoren (D-09)
SENSOR_RFID = "sensor.alfen_eve_tag_socket_1"
SENSOR_ENERGY = "sensor.alfen_energy_total"
SENSOR_STATE = None  # Wird dynamisch aus Alfen Integration ermittelt

# Globale Variablen für Session-Tracking
session_manager = None
current_config = {}
ha_ws = None


def load_config():
    """Lädt Addon-Konfiguration aus /data/options.json (D-04)"""
    config_path = '/data/options.json'
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
            _LOGGER.info("Konfiguration geladen von %s", config_path)

            # API-Konfiguration validieren (Task 3)
            api_config = config.get('api', {})
            if api_config:
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

    def __init__(self, host: str = "homeassistant", port: int = 8123):
        self.host = host
        self.port = port
        self.ws_url = f"ws://{host}:{port}/api/websocket"
        self.access_token = os.getenv('SUPERVISOR_TOKEN', '')
        self.session_id: Optional[str] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None

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
        """Abonniert Entitäts-Updates via Websocket (D-10, event-basiert)"""
        # Subscribe to state changes
        msg_id = 1
        await self._ws.send_json({
            'id': msg_id,
            'type': 'subscribe_events',
            'event_type': 'state_changed'
        })

        msg = await self._ws.receive_json()
        if msg.get('type') != 'result' or not msg.get('success'):
            raise RuntimeError(f"Subscribe fehlgeschlagen: {msg}")

        _LOGGER.info("Erfolgreich Entitäts-Updates abonniert")

        # Nachrichten verarbeiten
        while True:
            msg = await self._ws.receive_json()
            if msg.get('type') == 'event':
                event = msg.get('event', {})
                entity_id = event.get('data', {}).get('entity_id')
                new_state = event.get('data', {}).get('new_state', {})

                if entity_id and new_state:
                    await callback(entity_id, new_state)

    async def get_state(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """Holt den aktuellen State einer Entität"""
        msg_id = 2
        await self._ws.send_json({
            'id': msg_id,
            'type': 'get_states'
        })

        msg = await self._ws.receive_json()
        if msg.get('type') == 'result' and msg.get('success'):
            states = msg.get('result', [])
            for state in states:
                if state.get('entity_id') == entity_id:
                    return state
        return None

    async def disconnect(self):
        """Trennt die Verbindung"""
        if self._ws:
            await self._ws.close()
        if self._session:
            await self._session.close()
        _LOGGER.info("Verbindung getrennt")


async def sensor_callback(entity_id: str, state: Dict[str, Any]):
    """Callback für Sensor-Updates mit Session-Tracking (D-09, D-10, HA-03-HA-07)"""
    global session_manager, current_config, ha_ws

    state_value = state.get('state')

    # RFID Sensor (HA-03, HA-04, HA-07)
    if entity_id == SENSOR_RFID:
        if state_value and state_value != 'unknown':
            # Debouncing prüfen (HA-07)
            if not session_manager.debounce_rfid(state_value):
                return  # RFID debounced, ignorieren

            # RFID Whitelist prüfen (HA-02, HA-04)
            whitelist = current_config.get('rfid_whitelist', [])
            if not session_manager.is_rfid_authorized(state_value, whitelist):
                rfid_hash = hash_rfid(state_value)
                _LOGGER.warning("Nicht autorisierte RFID: %s...", rfid_hash[:16])
                return

            # Energie-Stand für Session-Start abfragen (PER-05: atomar)
            energy_state = await ha_ws.get_state(SENSOR_ENERGY)
            start_energy = float(energy_state.get('state', 0)) if energy_state else 0.0

            # Session starten (HA-03, HA-04)
            session_id = session_manager.start_session(state_value, start_energy)
            if session_id:
                _LOGGER.info("Ladevorgang gestartet: Session ID=%s", session_id)
        else:
            _LOGGER.debug("RFID Sensor unbekannt oder leer")

    # Energie Sensor (HA-06)
    elif entity_id == SENSOR_ENERGY:
        try:
            kwh = float(state_value) if state_value else 0.0

            # Wenn aktive Session, prüfen ob Wallbox noch lädt
            active_session = session_manager.get_active_session()
            if active_session:
                # Status der Wallbox abfragen (HA-05)
                # Annahme: Charging State wird über separaten Sensor ermittelt
                # oder über Energie-Änderung (vereinfacht)
                _LOGGER.debug("Aktive Session: ID=%s, Aktuelle Energie=%.2f kWh",
                             active_session['id'], kwh)
        except (ValueError, TypeError):
            _LOGGER.warning("Ungültiger Energie-Wert: %s", state_value)

    # Ladezustand (aus Alfen Integration) - HA-05
    elif 'charging' in entity_id.lower() or 'state' in entity_id.lower():
        active_session = session_manager.get_active_session()

        if state_value == IDLE or state_value == STOPPED:
            if active_session:
                # Session beenden (HA-05, HA-06)
                energy_state = await ha_ws.get_state(SENSOR_ENERGY)
                end_energy = float(energy_state.get('state', 0)) if energy_state else 0.0

                completed = session_manager.end_session(end_energy)
                if completed:
                    _LOGGER.info("Ladevorgang beendet: Session ID=%s, %.2f kWh",
                                completed['id'], completed['total_kwh'])

        elif state_value == CHARGING:
            _LOGGER.debug("Wallbox lädt (Charging)")


async def check_startup_session():
    """Prüft beim Start ob eine aktive Session existiert (PER-01)"""
    global session_manager
    active = session_manager.get_active_session()
    if active:
        _LOGGER.info("Aktive Session nach Neustart gefunden: ID=%s, RFID=%s..., Status: %s",
                    active['id'], active['rfid_hash'][:16], active['status'])
        # Hier könnte später geprüft werden ob Wallbox noch lädt (Phase 5)


async def main():
    """Hauptschleife (D-03, D-10, D-11) - erweitert für Session-Tracking und API-Transmission"""
    global session_manager, current_config, ha_ws

    _LOGGER.info("Wallbox-Dolibarr Addon startet...")

    # Session Manager initialisieren (PER-01)
    session_manager = SessionManager(db_path="/data/sessions.db")

    # Konfiguration laden (für Whitelist und API)
    current_config = load_config()

    # API Client initialisieren (nur wenn konfiguriert, Task 4)
    api_client = None
    api_config = current_config.get("api", {})
    if api_config.get("dolibarr_url"):
        try:
            api_client = WallboxApiClient(
                base_url=api_config["dolibarr_url"],
                api_token=api_config.get("api_token", ""),
                timeout=api_config.get("timeout", 30)
            )

            # Verbindung testen
            if api_client.check_connection():
                _LOGGER.info("Dolibarr API Verbindung erfolgreich")
            else:
                _LOGGER.warning("Dolibarr API Verbindung fehlgeschlagen - wird später erneut versucht")
                api_client = None
        except Exception as e:
            _LOGGER.error("Fehler beim Initialisieren des API-Clients: %s", e)
            api_client = None
    else:
        _LOGGER.info("Keine API-Konfiguration - Addon läuft ohne API-Transmission")

    ha_ws = HomeAssistantWebsocket()

    try:
        # Verbinden
        await ha_ws.connect()

        # Prüfen ob aktive Session nach Neustart existiert (PER-01)
        await check_startup_session()

        # Periodic API Transmission als Hintergrund-Task (Task 4 - Fix: subscribe_entities blockiert)
        async def periodic_transmission():
            """Periodische API-Übertragung als Hintergrund-Task"""
            import time
            last_transmit = 0
            transmit_interval = api_config.get("transmit_interval", 300)

            while True:
                if api_client:
                    current_time = time.time()
                    if (current_time - last_transmit) >= transmit_interval:
                        result = session_manager.transmit_completed_sessions(api_client)

                        if result["transmitted"] > 0:
                            _LOGGER.info("Sessions an Dolibarr übertragen: %s", result["transmitted"])

                        if result["failed"] > 0:
                            _LOGGER.error("Fehler bei API-Übertragung: %s Sessions fehlgeschlagen", result["failed"])
                            # Bei Fehlern: Verbindung neu testen
                            if not api_client.check_connection():
                                _LOGGER.warning("API-Verbindung verloren - deaktiviere temporär")
                                # api_client auf None setzen deaktiviert weitere Versuche
                                # TODO: Reconnect-Logik in Zukunft

                        last_transmit = current_time

                await asyncio.sleep(1)

        # Hintergrund-Task starten
        if api_client:
            transmission_task = asyncio.create_task(periodic_transmission())
            _LOGGER.info("API-Transmission Hintergrund-Task gestartet")

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
