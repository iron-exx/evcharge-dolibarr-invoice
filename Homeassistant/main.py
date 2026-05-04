#!/usr/bin/env python3
"""
Wallbox-Dolibarr Addon Hauptskript

Verbindet sich via Websocket API mit Home Assistant Core,
liest Alfen Wallbox Sensoren aus und bereitet Session-Tracking vor.
"""
import asyncio
import aiohttp
import json
import logging
import os
import sys
from typing import Dict, Any, Optional

# Hash-Utility importieren
sys.path.insert(0, '/usr/local/bin')
from utils.hash import hash_rfid

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
    """Callback für Sensor-Updates (D-09, D-10)"""
    state_value = state.get('state')
    
    # RFID Sensor
    if entity_id == SENSOR_RFID:
        if state_value and state_value != 'unknown':
            rfid_hash = hash_rfid(state_value)
            _LOGGER.info("RFID erkannt: %s (Hash: %s...)", state_value[:8], rfid_hash[:16])
        else:
            _LOGGER.debug("RFID Sensor unbekannt oder leer")
            
    # Energie Sensor
    elif entity_id == SENSOR_ENERGY:
        try:
            kwh = float(state_value) if state_value else 0.0
            _LOGGER.debug("Energie: %.2f kWh", kwh)
        except (ValueError, TypeError):
            _LOGGER.warning("Ungültiger Energie-Wert: %s", state_value)
            
    # Ladezustand (aus Alfen Integration)
    elif 'alfen' in entity_id and 'charging' in entity_id.lower():
        _LOGGER.info("Ladezustand: %s", state_value)

async def main():
    """Hauptschleife (D-03, D-10, D-11)"""
    _LOGGER.info("Wallbox-Dolibarr Addon startet...")
    
    ha_ws = HomeAssistantWebsocket()
    
    try:
        # Verbinden
        await ha_ws.connect()
        
        # Sensor-Updates abonnieren (event-basiert, D-10)
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
