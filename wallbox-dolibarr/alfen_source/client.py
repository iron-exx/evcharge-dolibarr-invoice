"""HTTPS-Client für die Alfen-Wallbox.

Endpunkte (aus der HACS-Integration leeyuentuen/alfen_wallbox abgeleitet):

    POST /api/login        {"username", "password", "displayname"}
    POST /api/logout
    GET  /api/prop?id=<paramID>        → {"properties": [{"id", "value"}, …]}
    GET  /api/transactions?offset=<n>  → Klartext, eine Logzeile je Zeile

Drei Dinge, die hier bewusst so sind:

* **Anmeldeversuche werden begrenzt.** Die Wallbox sperrt nach mehreren
  Fehlversuchen. Ein falsches Passwort in der Konfiguration darf nicht dazu
  führen, dass das Addon die Box in die Sperre treibt.
* **Ein Fehler wird geworfen, nicht ersetzt.** Nie ein Standardwert statt
  eines nicht gelesenen Zählerstands — das würde falsch abrechnen.
* **Zugangsdaten stehen in keiner Fehlermeldung.** Sie landen sonst im
  Addon-Log.

Zur TLS-Prüfung: Alfen-Wallboxen liefern ein selbst ausgestelltes Zertifikat.
`verify_ssl: false` ist im LAN daher der Normalfall und die Vorgabe; im Log
steht dann ein Hinweis.
"""
import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import aiohttp

_LOGGER = logging.getLogger(__name__)

DEFAULT_DISPLAY_NAME = 'ExpenseCharge'
_MAX_LOG_PAGES = 200          # Deckel gegen ein endlos wachsendes Log


class AlfenError(Exception):
    """Zugriff auf die Wallbox fehlgeschlagen."""


class AlfenHttpClient:
    def __init__(self, base_url: str, username: str, password: str,
                 display_name: str = DEFAULT_DISPLAY_NAME, verify_ssl: bool = False,
                 timeout: float = 10.0, max_login_attempts: int = 5,
                 login_window: float = 60.0, max_log_pages: int = _MAX_LOG_PAGES):
        self.base_url = str(base_url or '').rstrip('/')
        self._user = username
        self._password = password
        self._display_name = display_name or DEFAULT_DISPLAY_NAME
        self.verify_ssl = bool(verify_ssl)
        self.timeout = float(timeout)
        self.max_login_attempts = int(max_login_attempts)
        self.login_window = float(login_window)
        self.max_log_pages = max(1, int(max_log_pages))
        self._session: Optional[aiohttp.ClientSession] = None
        self._logged_in = False
        self._attempts = []          # Zeitstempel der Anmeldeversuche
        self._lock = asyncio.Lock()  # eine Anfrage zur Zeit

    # -- Verbindung ---------------------------------------------------------

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout),
                connector=aiohttp.TCPConnector(ssl=False if not self.verify_ssl else None),
            )
        return self._session

    def _rate_limited(self) -> bool:
        now = time.monotonic()
        self._attempts = [t for t in self._attempts if now - t < self.login_window]
        return len(self._attempts) >= self.max_login_attempts

    async def _login(self) -> None:
        if self._rate_limited():
            raise AlfenError(
                f"Anmeldung an {self.base_url} vorläufig ausgesetzt: "
                f"{self.max_login_attempts} Fehlversuche in {self.login_window:.0f}s. "
                f"Benutzername und Passwort in der Konfiguration prüfen.")
        session = await self._ensure_session()
        self._attempts.append(time.monotonic())
        try:
            async with session.post(
                    f'{self.base_url}/api/login',
                    json={'username': self._user, 'password': self._password,
                          'displayname': self._display_name}) as response:
                if response.status in (401, 403):
                    raise AlfenError(
                        f"Anmeldung an {self.base_url} abgelehnt (HTTP {response.status}) — "
                        f"Benutzername und Passwort prüfen")
                if response.status >= 400:
                    raise AlfenError(f"Anmeldung an {self.base_url} fehlgeschlagen "
                                     f"(HTTP {response.status})")
        except aiohttp.ClientError as exc:
            raise AlfenError(f"Wallbox {self.base_url} nicht erreichbar: {exc}") from exc
        except asyncio.TimeoutError as exc:
            raise AlfenError(f"Zeitüberschreitung bei der Anmeldung an {self.base_url}") from exc
        self._logged_in = True
        if not self.verify_ssl and self.base_url.startswith('https'):
            _LOGGER.debug("TLS-Prüfung für %s abgeschaltet (selbst ausgestelltes "
                          "Zertifikat, im LAN üblich)", self.base_url)

    async def _request(self, path: str, *, as_text: bool = False, _retry: bool = True):
        """Anfrage mit genau einem Wiederholungsversuch nach erneuter Anmeldung."""
        if not self._logged_in:
            await self._login()
        session = await self._ensure_session()
        try:
            async with session.get(f'{self.base_url}{path}') as response:
                if response.status in (401, 403) and _retry:
                    # Sitzung abgelaufen — einmal neu anmelden.
                    self._logged_in = False
                    return await self._request(path, as_text=as_text, _retry=False)
                if response.status >= 400:
                    raise AlfenError(f"{path}: HTTP {response.status}")
                return await response.text() if as_text else await response.json()
        except aiohttp.ClientError as exc:
            self._logged_in = False
            raise AlfenError(f"Wallbox {self.base_url} nicht erreichbar: {exc}") from exc
        except asyncio.TimeoutError as exc:
            self._logged_in = False
            raise AlfenError(f"Zeitüberschreitung bei {path}") from exc

    # -- Abfragen -----------------------------------------------------------

    async def get_properties(self, param_ids) -> Dict[str, Any]:
        """paramID → Wert. Nicht gelieferte IDs fehlen im Ergebnis."""
        out: Dict[str, Any] = {}
        async with self._lock:
            for param_id in param_ids or []:
                data = await self._request(f'/api/prop?id={param_id}')
                for prop in (data or {}).get('properties') or []:
                    if 'id' in prop:
                        out[str(prop['id'])] = prop.get('value')
        return out

    async def get_transaction_lines(self) -> List[str]:
        """Das Transaktions-Log, seitenweise bis zum Ende oder bis zum Deckel."""
        lines: List[str] = []
        async with self._lock:
            offset = 0
            for _page in range(self.max_log_pages):
                text = await self._request(f'/api/transactions?offset={offset}', as_text=True)
                page = [ln for ln in str(text or '').splitlines() if ln.strip()]
                if not page:
                    break
                lines.extend(page)
                offset += len(page)
            else:
                _LOGGER.warning("Transaktions-Log nach %d Seiten abgebrochen — "
                                "max_log_pages erhöhen, falls Ladungen fehlen",
                                self.max_log_pages)
        return lines

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            if self._logged_in:
                try:
                    await self._session.post(f'{self.base_url}/api/logout')
                except Exception:
                    pass        # Abmelden ist Höflichkeit, kein Muss
            await self._session.close()
        self._session = None
        self._logged_in = False
