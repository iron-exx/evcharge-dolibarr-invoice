"""OCPP-1.6J-Nachrichten-Handler je verbundener Wallbox."""
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Sequence

from ocpp.routing import after, on
from ocpp.v16 import ChargePoint, call, call_result, datatypes
from ocpp.v16.enums import Action, AuthorizationStatus, DataTransferStatus, RegistrationStatus

from ocpp_server.id_tags import is_whitelisted, normalize_id_tag
from ocpp_server.meter import extract_energy_kwh, to_local_naive_iso, wh_to_kwh
from ocpp_server.redact import install as _install_redaction, redact_id_tags
from ocpp_server.settings import ChargePointConfig
from utils.hash import hash_rfid

_LOGGER = logging.getLogger(__name__)

# Die ocpp-Bibliothek loggt jede Nachricht samt Payload auf INFO — inklusive
# idTag im Klartext. RFID-Klartext darf nie ins Log (DSGVO, Projektregel) →
# eigener Protokoll-Logger, fest auf WARNING (Fehler bleiben sichtbar).
_PROTOCOL_LOGGER = logging.getLogger('ocpp.expensecharge')
_PROTOCOL_LOGGER.setLevel(logging.WARNING)
# Der Level allein genügt nicht: Handler-Fehler und Schema-Verstöße loggt die
# Bibliothek auf ERROR, inklusive kompletter Nachricht. Deshalb zusätzlich
# inhaltsbasiert bereinigen.
_install_redaction(_PROTOCOL_LOGGER)

# Abgelehnte Starts bekommen transactionId 0 — eine spätere StopTransaction
# mit dieser ID wird bestätigt, aber nie abgerechnet.
REJECTED_TRANSACTION_ID = 0

LOG_SIZE = 200          # Nachrichten je Wallbox für die Verwaltungsoberfläche
_LOG_TEXT_MAX = 4000

# Optional nach dem Boot gesetzt (ocpp_apply_recommended_config: true).
RECOMMENDED_CONFIGURATION = (
    ('MeterValueSampleInterval', '60'),
    ('MeterValuesSampledData', 'Energy.Active.Import.Register'),
    ('StopTransactionOnInvalidId', 'true'),
)


@dataclass
class CentralSystemDeps:
    session_manager: Any
    whitelist: Sequence[str]
    live: dict                        # api_state['charge_points'] — Live-Zustand für die Web-UI
    min_kwh: float = 0.05
    heartbeat_interval: int = 300
    apply_recommended_config: bool = False
    on_session_completed: Optional[Callable[[dict], None]] = None
    # Lernmodus: wird mit JEDER vorgehaltenen Karte gerufen, auch mit einer
    # abgelehnten — sonst könnte der Admin sie nicht freischalten.
    on_tag_seen: Optional[Callable[[str], object]] = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _local_now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


class CentralSystemChargePoint(ChargePoint):
    def __init__(self, cp_config: ChargePointConfig, connection, deps: CentralSystemDeps):
        super().__init__(cp_config.id, connection, logger=_PROTOCOL_LOGGER)
        self._cp = cp_config
        self._deps = deps
        self._state = deps.live.setdefault(cp_config.id, {'connectors': {}, 'last_rejected_id_tag': None})
        self._state.update({'connected': True, 'wallbox_id': cp_config.wallbox_id})
        self._state.setdefault('log', deque(maxlen=LOG_SIZE))
        self._touch()

    def _log(self, direction: str, raw) -> None:
        """Nachricht fürs Oberflächen-Protokoll — idTag und AuthorizationKey nie im Klartext."""
        text = redact_id_tags(str(raw))
        if 'authorizationkey' in text.lower():
            text = ','.join(text.split(',', 2)[:2]) + ', … (enthält AuthorizationKey – ausgeblendet)]'
        self._state['log'].append({'time': _local_now_iso(), 'dir': direction,
                                   'text': text[:_LOG_TEXT_MAX]})

    async def route_message(self, raw_msg):
        self._log('in', raw_msg)
        await super().route_message(raw_msg)

    async def _send(self, message):
        self._log('out', message)
        await super()._send(message)

    def _touch(self) -> None:
        self._state['last_seen'] = _local_now_iso()

    def _connector(self, connector_id) -> dict:
        return self._state['connectors'].setdefault(str(connector_id), {})

    def _authorize(self, id_tag) -> AuthorizationStatus:
        tag = normalize_id_tag(id_tag)
        if tag and self._deps.on_tag_seen is not None:
            try:
                self._deps.on_tag_seen(tag)
            except Exception as exc:        # Lernmodus darf nie das Laden stören
                _LOGGER.warning("[%s] Lernmodus-Hook fehlgeschlagen: %s", self.id, exc)
        # Autorisierung über BEIDE Quellen: die Konfigurations-Whitelist und die
        # Tag-Verwaltung des Addons. Letztere ist der Zweck des Lernmodus —
        # eine dort eingeordnete Karte muss auch hier laden dürfen. 'private'
        # darf laden (nur nicht abgerechnet werden), 'unknown' nicht.
        if self._authorized(tag):
            return AuthorizationStatus.accepted
        # Klartext NUR im flüchtigen Live-Zustand (Ingress-UI, Admin), damit die
        # Karte eingetragen werden kann — im Log nur der Hash-Präfix.
        self._state['last_rejected_id_tag'] = tag
        _LOGGER.warning("[%s] Karte abgelehnt (nicht in rfid_whitelist): %s...",
                        self.id, hash_rfid(tag)[:16])
        return AuthorizationStatus.invalid

    def _authorized(self, tag: str) -> bool:
        if is_whitelisted(tag, self._deps.whitelist):
            return True
        checker = getattr(self._deps.session_manager, 'is_rfid_authorized', None)
        if checker is None or not tag:
            return False
        try:
            return bool(checker(tag, []))
        except Exception as exc:    # Datenbankfehler darf nicht autorisieren
            _LOGGER.error("[%s] Tag-Verwaltung nicht lesbar — Karte abgelehnt: %s", self.id, exc)
            return False

    @on(Action.boot_notification)
    async def on_boot_notification(self, charge_point_vendor, charge_point_model, **kwargs):
        self._touch()
        self._state.update({'vendor': charge_point_vendor, 'model': charge_point_model,
                            'firmware': kwargs.get('firmware_version')})
        _LOGGER.info("[%s] BootNotification: %s %s (FW %s)", self.id, charge_point_vendor,
                     charge_point_model, kwargs.get('firmware_version'))
        return call_result.BootNotification(current_time=_utc_now_iso(),
                                            interval=self._deps.heartbeat_interval,
                                            status=RegistrationStatus.accepted)

    @after(Action.boot_notification)
    async def after_boot_notification(self, **kwargs):
        if not self._deps.apply_recommended_config:
            return
        for key, value in RECOMMENDED_CONFIGURATION:
            try:
                result = await self.call(call.ChangeConfiguration(key=key, value=value))
            except Exception as exc:  # Timeout o.ä. — Laden funktioniert trotzdem
                _LOGGER.warning("[%s] ChangeConfiguration %s fehlgeschlagen: %s", self.id, key, exc)
                continue
            _LOGGER.info("[%s] ChangeConfiguration %s=%s → %s", self.id, key, value,
                         getattr(result, 'status', 'CallError'))

    @on(Action.heartbeat)
    async def on_heartbeat(self, **kwargs):
        self._touch()
        return call_result.Heartbeat(current_time=_utc_now_iso())

    @on(Action.status_notification)
    async def on_status_notification(self, connector_id, error_code, status, **kwargs):
        self._touch()
        self._connector(connector_id).update({'status': status, 'error_code': error_code})
        return call_result.StatusNotification()

    @on(Action.authorize)
    async def on_authorize(self, id_tag, **kwargs):
        self._touch()
        return call_result.Authorize(id_tag_info=datatypes.IdTagInfo(status=self._authorize(id_tag)))

    @on(Action.start_transaction)
    async def on_start_transaction(self, connector_id, id_tag, meter_start, timestamp, **kwargs):
        self._touch()
        # OCPP: Der Server MUSS den Tag hier erneut prüfen — die Wallbox kann
        # lokal (Cache/Offline) mit veralteten Daten autorisiert haben.
        status = self._authorize(id_tag)
        if status != AuthorizationStatus.accepted:
            return call_result.StartTransaction(transaction_id=REJECTED_TRANSACTION_ID,
                                                id_tag_info=datatypes.IdTagInfo(status=status))
        tx_id = self._deps.session_manager.start_ocpp_transaction(
            rfid_hex=normalize_id_tag(id_tag),
            wallbox_id=self._cp.wallbox_id,
            charge_point_id=self.id,
            connector_id=connector_id,
            meter_start_kwh=wh_to_kwh(meter_start) or 0.0,
            start_time=to_local_naive_iso(timestamp),
            ocpp_start_timestamp=str(timestamp),
        )
        self._connector(connector_id)['transaction_id'] = tx_id
        return call_result.StartTransaction(transaction_id=tx_id,
                                            id_tag_info=datatypes.IdTagInfo(status=AuthorizationStatus.accepted))

    @on(Action.stop_transaction)
    async def on_stop_transaction(self, transaction_id, meter_stop, timestamp, **kwargs):
        self._touch()
        # OCPP: Ein Stop MUSS immer bestätigt werden — sonst wiederholt die Wallbox ihn endlos.
        if transaction_id == REJECTED_TRANSACTION_ID:
            _LOGGER.warning("[%s] Stop eines abgelehnten Ladevorgangs (Zähler %s Wh) — nicht abgerechnet",
                            self.id, meter_stop)
            return call_result.StopTransaction()
        final_kwh = extract_energy_kwh(kwargs.get('transaction_data'))
        if final_kwh is not None:
            self._deps.session_manager.update_ocpp_meter(transaction_id, self.id, final_kwh)
        completed = self._deps.session_manager.stop_ocpp_transaction(
            transaction_id=transaction_id,
            charge_point_id=self.id,
            meter_stop_kwh=wh_to_kwh(meter_stop),
            end_time=to_local_naive_iso(timestamp),
            reason=kwargs.get('reason') or 'Local',
            min_kwh=self._deps.min_kwh,
        )
        for connector in self._state['connectors'].values():
            if connector.get('transaction_id') == transaction_id:
                connector['transaction_id'] = None
        if completed and self._deps.on_session_completed:
            self._deps.on_session_completed(completed)
        return call_result.StopTransaction()

    @on(Action.meter_values)
    async def on_meter_values(self, connector_id, meter_value, **kwargs):
        self._touch()
        kwh = extract_energy_kwh(meter_value)
        if kwh is not None:
            self._connector(connector_id)['energy_kwh'] = kwh
            tx_id = kwargs.get('transaction_id')
            if tx_id:
                self._deps.session_manager.update_ocpp_meter(tx_id, self.id, kwh)
        return call_result.MeterValues()

    @on(Action.data_transfer)
    async def on_data_transfer(self, vendor_id, **kwargs):
        self._touch()
        return call_result.DataTransfer(status=DataTransferStatus.unknown_vendor_id)

    @on(Action.firmware_status_notification)
    async def on_firmware_status_notification(self, status, **kwargs):
        self._touch()
        return call_result.FirmwareStatusNotification()

    @on(Action.diagnostics_status_notification)
    async def on_diagnostics_status_notification(self, status, **kwargs):
        self._touch()
        return call_result.DiagnosticsStatusNotification()
