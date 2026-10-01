"""Nachgebaute Alfen-Wallbox (HTTPS-API) für Tests."""
from aiohttp import web


class FakeAlfen:
    """Spricht /api/login, /api/prop und /api/transactions wie die echte Box."""

    def __init__(self, username="admin", password="geheim", props=None, lines=None,
                 page_size=3, require_login=True):
        self.username = username
        self.password = password
        self.props = dict(props or {})
        self.lines = list(lines or [])
        self.page_size = page_size
        self.require_login = require_login
        self.logged_in = False
        self.login_attempts = 0
        self.prop_requests = []
        self.transaction_offsets = []
        self.fail_prop_with = None
        self._runner = None
        self.port = None

    async def start(self):
        app = web.Application()
        app.router.add_post('/api/login', self._login)
        app.router.add_post('/api/logout', self._logout)
        app.router.add_get('/api/prop', self._prop)
        app.router.add_get('/api/transactions', self._transactions)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, '127.0.0.1', 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        return self.port

    async def close(self):
        if self._runner:
            await self._runner.cleanup()

    @property
    def base_url(self):
        return f'http://127.0.0.1:{self.port}'

    async def _login(self, request):
        self.login_attempts += 1
        body = await request.json()
        if body.get('username') == self.username and body.get('password') == self.password:
            self.logged_in = True
            return web.Response(status=200)
        return web.Response(status=401, text='Unauthorized')

    async def _logout(self, request):
        self.logged_in = False
        return web.Response(status=200)

    async def _prop(self, request):
        if self.require_login and not self.logged_in:
            return web.Response(status=401)
        if self.fail_prop_with:
            return web.Response(status=self.fail_prop_with)
        ids = request.query.get('id', '')
        self.prop_requests.append(ids)
        props = [{'id': i, 'value': self.props.get(i)}
                 for i in ids.split(',') if i in self.props]
        return web.json_response({'properties': props})

    async def _transactions(self, request):
        if self.require_login and not self.logged_in:
            return web.Response(status=401)
        offset = int(request.query.get('offset', 0))
        self.transaction_offsets.append(offset)
        page = self.lines[offset:offset + self.page_size]
        return web.Response(text='\n'.join(page), content_type='text/plain')
