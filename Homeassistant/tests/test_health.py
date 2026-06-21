"""
Tests for /health GET and /session/stop POST endpoints.
Phase 6, MON-01 — Wave 0 stubs (RED phase).

Expected behavior after Wave 1 implementation:
  - GET /health -> 200 {"status": "ok", "addon": "wallbox-dolibarr"}
  - POST /session/stop missing session_id -> 400 {"error": "session_id required"}
  - POST /session/stop when api_client=None -> 503 {"error": "API client not configured"}
  - start_health_server() returns an AppRunner instance
  - AppRunner does not block asyncio event loop
"""
import pytest


@pytest.mark.xfail(reason="Wave 1 not yet implemented: main.py /health endpoint")
def test_health_returns_200_with_status_ok():
    """GET /health returns HTTP 200 with body {"status": "ok", "addon": "wallbox-dolibarr"}"""
    # Will be filled in Wave 1 using aiohttp.test_utils.TestClient
    from aiohttp.test_utils import TestClient, TestServer, loop_context
    from aiohttp import web
    from main import handle_health
    app = web.Application()
    app.router.add_get('/health', handle_health)
    with loop_context() as loop:
        client = TestClient(TestServer(app), loop=loop)
        loop.run_until_complete(client.start_server())
        resp = loop.run_until_complete(client.get('/health'))
        assert resp.status == 200
        data = loop.run_until_complete(resp.json())
        assert data['status'] == 'ok'
        assert data['addon'] == 'wallbox-dolibarr'
        loop.run_until_complete(client.close())


@pytest.mark.xfail(reason="Wave 1 not yet implemented: /session/stop validation")
def test_session_stop_missing_session_id_returns_400():
    """POST /session/stop with empty body returns HTTP 400 {"error": "session_id required"}"""
    from aiohttp.test_utils import TestClient, TestServer, loop_context
    from aiohttp import web
    from main import handle_session_stop
    app = web.Application()
    app.router.add_post('/session/stop', handle_session_stop)
    with loop_context() as loop:
        client = TestClient(TestServer(app), loop=loop)
        loop.run_until_complete(client.start_server())
        resp = loop.run_until_complete(
            client.post('/session/stop', json={"session_id": 0})
        )
        assert resp.status == 400
        data = loop.run_until_complete(resp.json())
        assert 'error' in data
        loop.run_until_complete(client.close())


@pytest.mark.xfail(reason="Wave 1 not yet implemented: /session/stop api_client=None guard")
def test_session_stop_no_api_client_returns_503():
    """POST /session/stop when api_client is None returns HTTP 503"""
    from aiohttp.test_utils import TestClient, TestServer, loop_context
    from aiohttp import web
    import main as main_module
    main_module.api_client = None
    from main import handle_session_stop
    app = web.Application()
    app.router.add_post('/session/stop', handle_session_stop)
    with loop_context() as loop:
        client = TestClient(TestServer(app), loop=loop)
        loop.run_until_complete(client.start_server())
        resp = loop.run_until_complete(
            client.post('/session/stop', json={"session_id": 1})
        )
        assert resp.status == 503
        loop.run_until_complete(client.close())


@pytest.mark.xfail(reason="Wave 1 not yet implemented: start_health_server returns AppRunner")
def test_start_health_server_returns_app_runner():
    """start_health_server() returns a non-None aiohttp AppRunner instance"""
    import asyncio
    from aiohttp.web_runner import AppRunner
    from main import start_health_server
    async def _run():
        runner = await start_health_server(port=18099)
        assert runner is not None
        assert isinstance(runner, AppRunner)
        await runner.cleanup()
    asyncio.get_event_loop().run_until_complete(_run())


@pytest.mark.xfail(reason="Wave 1 not yet implemented: AppRunner non-blocking")
def test_app_runner_does_not_block_event_loop():
    """AppRunner starts without blocking: asyncio.gather completes an immediate coroutine"""
    import asyncio
    from main import start_health_server
    completed = []
    async def _immediate():
        completed.append(True)
    async def _run():
        runner = await start_health_server(port=18098)
        await asyncio.gather(_immediate())
        await runner.cleanup()
    asyncio.get_event_loop().run_until_complete(_run())
    assert completed == [True]
