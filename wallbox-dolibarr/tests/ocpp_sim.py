"""Simulierte Wallbox (OCPP-1.6J-Client) für Integrationstests."""
import asyncio
import base64
import logging
from contextlib import asynccontextmanager

import websockets
from ocpp.routing import on
from ocpp.v16 import ChargePoint, call_result
from ocpp.v16.enums import Action, ConfigurationStatus


class SimChargePoint(ChargePoint):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.config_changes = []

    @on(Action.change_configuration)
    async def on_change_configuration(self, key, value):
        self.config_changes.append((key, value))
        return call_result.ChangeConfiguration(status=ConfigurationStatus.accepted)


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
