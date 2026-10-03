"""WebSocket-Server: nimmt Wallbox-Verbindungen an (Pfad-ID, Basic Auth, Subprotocol)."""
import asyncio
import base64
import binascii
import hmac
import logging
import os
from datetime import datetime
from http import HTTPStatus
from typing import Optional, Tuple
from urllib.parse import unquote, urlsplit

import websockets
from websockets.asyncio.server import serve
from websockets.exceptions import NegotiationError

from ocpp_server.central_system import CentralSystemChargePoint, CentralSystemDeps
from ocpp_server.redact import install as _install_redaction
from ocpp_server.settings import OcppSettings
from placeholders import is_placeholder_password

_LOGGER = logging.getLogger(__name__)

# websockets loggt auf DEBUG jeden Frame roh (inkl. idTag-Klartext) → eigener
# Logger, fest auf INFO (Verbindungsfehler bleiben sichtbar), auch bei log_level DEBUG.
_WS_LOGGER = logging.getLogger('websockets.expensecharge')
_WS_LOGGER.setLevel(logging.INFO)
_install_redaction(_WS_LOGGER)

OCPP_PORT = 9000              # Container-Port; Host-Port wird in HA unter "Netzwerk" gesetzt
OCPP_SUBPROTOCOL = 'ocpp1.6'
_MAX_LOGGED_CP_ID = 64
_MAX_PENDING = 20             # unbekannte IDs, die die Oberfläche zum Übernehmen anbietet
COMMAND_TIMEOUT = 20


def charge_point_id_from_path(path: str) -> str:
    """Letztes Pfadsegment ohne Query: '/ocpp/CP001?x=1' → 'CP001'."""
    raw = urlsplit(path or '').path.rstrip('/')
    return unquote(raw.rsplit('/', 1)[-1]) if raw else ''


def safe_cp_id_for_log(cp_id: str) -> str:
    """Charge-Point-ID gefahrlos loggen.

    Der Wert stammt aus dem URL-Pfad eines noch NICHT authentifizierten Peers.
    Ohne Begrenzung könnte er das Addon-Log fluten, und mit einem Zeilenumbruch
    könnte er gefälschte Logzeilen einschleusen. repr() maskiert Steuerzeichen.
    """
    return repr((cp_id or '')[:_MAX_LOGGED_CP_ID])


def parse_basic_auth(header: Optional[str]) -> Optional[Tuple[str, str]]:
    if not header or not header.lower().startswith('basic '):
        return None
    try:
        decoded = base64.b64decode(header[6:].strip(), validate=True).decode('utf-8')
    except (binascii.Error, UnicodeDecodeError):
        return None
    user, sep, password = decoded.partition(':')
    return (user, password) if sep else None


def select_subprotocol(connection, offered):
    """'ocpp1.6' wählen. Kein Angebot → tolerieren (manche Wallboxen senden den
    Header nicht) und als 1.6 behandeln. Nur fremde Versionen → HTTP 400."""
    if not offered:
        return None
    if OCPP_SUBPROTOCOL in offered:
        return OCPP_SUBPROTOCOL
    raise NegotiationError(f"unsupported subprotocols: {', '.join(offered)}")


def _equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode('utf-8'), b.encode('utf-8'))


class OcppServer:
    def __init__(self, settings: OcppSettings, deps: CentralSystemDeps):
        self._settings = settings
        self._deps = deps
        self._server = None
        self.connected = {}   # cp_id → aktuelle Verbindung
        self.charge_points = {}   # cp_id → CentralSystemChargePoint (für Fernbefehle)
        self.pending = {}     # unbekannte cp_id → {first_seen, last_seen, count, remote}

    @property
    def live(self) -> dict:
        return self._deps.live

    def update_settings(self, settings: OcppSettings) -> None:
        """Neue Wallbox-Liste ohne Neustart; bestehende Verbindungen bleiben."""
        self._settings = settings
        for cp in settings.charge_points:
            self.pending.pop(cp.id, None)

    def _note_pending(self, cp_id: str, remote) -> None:
        now = datetime.now().replace(microsecond=0).isoformat()
        entry = self.pending.get(cp_id)
        if entry is None:
            if len(self.pending) >= _MAX_PENDING:
                self.pending.pop(min(self.pending, key=lambda k: self.pending[k]['last_seen']))
            entry = self.pending[cp_id] = {'first_seen': now, 'count': 0}
        entry.update(last_seen=now, count=entry['count'] + 1,
                     remote=str(remote[0]) if remote else '')

    async def send(self, cp_id: str, payload, timeout: float = COMMAND_TIMEOUT):
        """Fernbefehl an eine verbundene Wallbox. CallError → OCPPError, keine Antwort → TimeoutError."""
        cp = self.charge_points.get(cp_id)
        if cp is None:
            raise LookupError('Wallbox ist nicht verbunden')
        return await asyncio.wait_for(cp.call(payload, suppress=False), timeout)

    async def disconnect(self, cp_id: str) -> None:
        connection = self.connected.get(cp_id)
        if connection is not None:
            await connection.close()

    async def _process_request(self, connection, request):
        cp_id = charge_point_id_from_path(request.path)
        cp_cfg = self._settings.find(cp_id)
        if cp_cfg is None:
            if cp_id:
                self._note_pending(cp_id[:_MAX_LOGGED_CP_ID], connection.remote_address)
            _LOGGER.warning("Unbekannte Charge-Point-ID %s – in ocpp_charge_points eintragen (Verbindung abgewiesen)",
                            safe_cp_id_for_log(cp_id))
            return connection.respond(HTTPStatus.NOT_FOUND, "Unknown charge point\n")
        if is_placeholder_password(cp_cfg.password):
            _LOGGER.error("Wallbox %s abgewiesen: Passwort ist noch der Platzhalter aus der Vorlage "
                          "— in ocpp_charge_points ein eigenes setzen", safe_cp_id_for_log(cp_id))
            return connection.respond(HTTPStatus.UNAUTHORIZED, "Unauthorized\n")
        if cp_cfg.password:
            creds = parse_basic_auth(request.headers.get('Authorization'))
            # Security Whitepaper A00.FR.204: Benutzername MUSS die Charge-Point-ID sein
            if creds is None or not (_equal(creds[0], cp_cfg.id) and _equal(creds[1], cp_cfg.password)):
                _LOGGER.warning("Wallbox %s: falsche oder fehlende Zugangsdaten — abgewiesen",
                                safe_cp_id_for_log(cp_id))
                response = connection.respond(HTTPStatus.UNAUTHORIZED, "Unauthorized\n")
                response.headers['WWW-Authenticate'] = 'Basic realm="ocpp", charset="UTF-8"'
                return response
        return None

    async def _handler(self, connection):
        cp_id = charge_point_id_from_path(connection.request.path)
        cp_cfg = self._settings.find(cp_id)
        if cp_cfg is None:   # durch _process_request eigentlich ausgeschlossen
            await connection.close()
            return
        if connection.subprotocol is None:
            _LOGGER.warning("Wallbox %s sendet kein Subprotocol — wird als OCPP 1.6 behandelt",
                            safe_cp_id_for_log(cp_id))
        previous = self.connected.get(cp_id)
        if previous is not None:
            _LOGGER.info("Wallbox %s verbindet sich neu — alte Verbindung wird geschlossen",
                         safe_cp_id_for_log(cp_id))
            await previous.close()
        self.connected[cp_id] = connection
        _LOGGER.info("Wallbox %s verbunden (%s)", safe_cp_id_for_log(cp_id), connection.remote_address)
        cp = CentralSystemChargePoint(cp_cfg, connection, self._deps)
        self.charge_points[cp_id] = cp
        try:
            await cp.start()
        except websockets.ConnectionClosed as exc:
            _LOGGER.info("Wallbox %s getrennt (%s)", safe_cp_id_for_log(cp_id), exc)
        finally:
            if self.connected.get(cp_id) is connection:
                del self.connected[cp_id]
                self.charge_points.pop(cp_id, None)
                self._deps.live.get(cp_id, {})['connected'] = False

    async def start(self, host: str = '0.0.0.0', port: int = OCPP_PORT) -> int:
        """Startet den Server; gibt den tatsächlich gebundenen Port zurück (port=0 in Tests)."""
        self._server = await serve(self._handler, host, port,
                                   select_subprotocol=select_subprotocol,
                                   process_request=self._process_request,
                                   logger=_WS_LOGGER)
        bound = self._server.sockets[0].getsockname()[1]
        if os.getenv('SUPERVISOR_TOKEN'):
            hint = "ws://<ha-host>:<port>/<charge-point-id>"
        else:
            hint = f"ws://<host-ip>:{bound}/<charge-point-id>"
        _LOGGER.info("OCPP-Zentralserver lauscht auf Port %d (%s)", bound, hint)
        return bound

    async def serve_forever(self) -> None:
        await self._server.serve_forever()

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
