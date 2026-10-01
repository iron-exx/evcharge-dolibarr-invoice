"""Weboberfläche der Tag-Verwaltung: Lernmodus, Benennen, Einordnen."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from session_manager import SessionManager  # noqa: E402
from tag_learning import LearnBuffer  # noqa: E402
from web_server import create_app  # noqa: E402


@pytest.fixture()
async def client(tmp_path):
    sm = SessionManager(db_path=str(tmp_path / "s.db"))
    learn = LearnBuffer()
    api_state = {"client": None, "current_energy": None, "wallbox_state": None,
                 "last_update": None, "learn": learn}
    async with TestClient(TestServer(create_app(sm, {"wallbox_id": "garage"}, api_state))) as c:
        c.sm, c.learn = sm, learn
        yield c


async def test_learn_mode_is_off_and_can_be_toggled(client):
    data = await (await client.get("/tags.json")).json()
    assert data["learn"]["enabled"] is False
    assert data["learn"]["detected"] == []

    assert (await client.post("/learn", json={"enabled": True})).status == 200
    assert client.learn.enabled is True
    data = await (await client.get("/tags.json")).json()
    assert data["learn"]["enabled"] is True

    await client.post("/learn", json={"enabled": False})
    assert client.learn.enabled is False


async def test_detected_card_shows_up_with_plaintext(client):
    client.learn.enabled = True
    client.learn.observe("c0ffee42")
    data = await (await client.get("/tags.json")).json()
    (entry,) = data["learn"]["detected"]
    assert entry["tag"] == "C0FFEE42", "Klartext nötig, damit der Admin die Karte wiedererkennt"
    assert entry["hash_prefix"]


async def test_name_and_classify_a_card(client):
    client.learn.enabled = True
    client.learn.observe("c0ffee42")

    r = await client.post("/tags", json={"tag": "C0FFEE42", "label": "Firmenwagen 1",
                                         "mode": "business"})
    assert r.status == 200
    assert client.sm.get_tag("C0FFEE42")["label"] == "Firmenwagen 1"
    assert client.sm.get_tag("C0FFEE42")["mode"] == "business"

    data = await (await client.get("/tags.json")).json()
    (tag,) = [t for t in data["tags"] if t["label"] == "Firmenwagen 1"]
    assert tag["mode"] == "business"
    assert "rfid_hash" not in tag, "der volle Hash gehört nicht in die Oberfläche"
    assert tag["hash_prefix"]


async def test_classify_as_private(client):
    await client.post("/tags", json={"tag": "AABBCCDD", "label": "Privat Meier",
                                     "mode": "private"})
    assert client.sm.get_tag("AABBCCDD")["mode"] == "private"


async def test_invalid_input_is_rejected(client):
    assert (await client.post("/tags", json={"tag": "", "mode": "business"})).status == 400
    assert (await client.post("/tags", json={"tag": "AA", "mode": "vielleicht"})).status == 400
    assert (await client.post("/tags", json={"label": "ohne tag"})).status == 400


async def test_delete_a_tag(client):
    await client.post("/tags", json={"tag": "AABBCCDD", "label": "weg", "mode": "business"})
    r = await client.post("/tags/delete", json={"tag": "AABBCCDD"})
    assert r.status == 200
    assert client.sm.get_tag("AABBCCDD") is None
    assert (await client.post("/tags/delete", json={"tag": "AABBCCDD"})).status == 404


async def test_tags_page_renders(client):
    await client.post("/tags", json={"tag": "C0FFEE42", "label": "Firmenwagen 1",
                                     "mode": "business"})
    body = await (await client.get("/tags")).text()
    assert "Firmenwagen 1" in body
    assert "Lernmodus" in body
    assert "C0FFEE42" not in body, "Klartext darf nicht in die gerenderte Seite"
