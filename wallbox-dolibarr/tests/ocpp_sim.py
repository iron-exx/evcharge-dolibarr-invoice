"""Simulierte Wallbox (OCPP-1.6J-Client) für Integrationstests."""
import asyncio
import base64
import logging
from contextlib import asynccontextmanager

import websockets
from ocpp.routing import on
from ocpp.v16 import ChargePoint, call_result
from ocpp.v16.enums import Action, ConfigurationStatus, RemoteStartStopStatus, ResetStatus


class SimChargePoint(ChargePoint):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.config_changes = []

    @on(Action.change_configuration)
    async def on_change_configuration(self, key, value):
        self.config_changes.append((key, value))
        return call_result.ChangeConfiguration(status=ConfigurationStatus.accepted)

    @on(Action.get_configuration)
    async def on_get_configuration(self, **kwargs):
        return call_result.GetConfiguration(configuration_key=[
            {"key": "MeterValueSampleInterval", "readonly": False, "value": "60"},
            {"key": "AuthorizationKey", "readonly": False, "value": "geheim-geheim-123"},
            {"key": "ChargePointModel", "readonly": True, "value": "NG910"}])

    @on(Action.reset)
    async def on_reset(self, type):
        self.config_changes.append(("reset", type))
        return call_result.Reset(status=ResetStatus.accepted)

    @on(Action.remote_start_transaction)
    async def on_remote_start(self, id_tag, **kwargs):
        self.config_changes.append(("remote_start", id_tag))
        return call_result.RemoteStartTransaction(status=RemoteStartStopStatus.accepted)


def basic_auth_header(user: str, password: str) -> dict:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@asynccontextmanager
async def connect_sim(port: int, cp_id: str, password: str = None, subprotocols=("ocpp1.6",)):
    headers = basic_auth_header(cp_id, password) if password else {}
    async with websockets.connect(f"ws://127.0.0.1:{port}/ocpp/{cp_id}",
                                  subprotocols=list(subprotocols) or None,
                                  additional_headers=headers) as ws:
        cp = SimChargePoint(cp_id, ws, logger=logging.getLogger("ocpp_sim"))
        task = asyncio.create_task(cp.start())
        try:
            yield cp
        finally:
            task.cancel()
