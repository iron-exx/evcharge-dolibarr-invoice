#!/usr/bin/env python3
"""
API Client für Dolibarr WallboxBilling Integration

Sendet abgeschlossene Lade-Sessions via REST API an Dolibarr.
"""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import logging
from typing import Optional, Dict, Any, Tuple
from datetime import datetime

_LOGGER = logging.getLogger(__name__)


def format_timestamp(dt: Any) -> str:
    """
    Formatiert ein datetime-Objekt zu ISO 8601 String mit Zeitzone

    Args:
        dt: datetime-Objekt oder String

    Returns:
        ISO 8601 formatierter String (z.B. "2026-05-05T14:30:00+02:00")

    Note:
        Wenn dt bereits ein String ist, wird er unverändert zurückgegeben
    """
    if isinstance(dt, str):
        return dt

    if isinstance(dt, datetime):
        # ISO 8601 mit Zeitzone formatieren
        return dt.strftime("%Y-%m-%dT%H:%M:%S%z")

    return str(dt)


class WallboxApiClient:
    """
    API-Client für Dolibarr WallboxBilling Endpoint

    Sendet abgeschlossene Lade-Sessions via REST API mit:
    - DOLAPIKEY Authentifizierung
    - Exponential backoff retry (D-02)
    - RFID nur als SHA-256 Hash (SEC-03, API-05)
    """

    def __init__(self, base_url: str, api_token: str, timeout: int = 30,
                 retries: int = 5, backoff: float = 0.5):
        """
        Initialisiert den API-Client

        Args:
            base_url: Dolibarr Basis-URL (z.B. https://dolibarr.example.com)
            api_token: DOLAPIKEY Token für Authentifizierung
            timeout: Timeout für API-Calls in Sekunden
            retries: Wiederholungen bei 429/5xx (0 = keine)
            backoff: Wartefaktor zwischen den Wiederholungen
        """
        self.base_url = base_url.rstrip('/')
        self.api_token = api_token
        self.timeout = timeout

        # HTTP Headers mit DOLAPIKEY (API-02, SEC-03)
        self.headers = {
            "DOLAPIKEY": api_token,
            "Content-Type": "application/json"
        }

        # Retry-Strategie (D-02: Konservativ)
        retry_strategy = Retry(
            total=retries,                      # einstellbar (Standard 5)
            backoff_factor=backoff,             # einstellbar (Standard 0.5s)
            status_forcelist=[429, 500, 502, 503, 504],  # Retryable Errors
            allowed_methods=["POST"],           # Nur POST retry (idempotent für unsere API)
            raise_on_status=False               # Nicht bei Status-Fehlern werfen
        )

        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session = requests.Session()
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        _LOGGER.info("API-Client initialisiert: %s", self.base_url)

    def transmit_session(self, session_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Überträgt eine abgeschlossene Session an Dolibarr

        Args:
            session_data: Dict mit Session-Daten (rfid_hash, wallbox_id, start_time, end_time, kwh)

        Returns:
            Tuple[bool, str]: (Erfolg, Fehlermeldung)
        """
        url = f"{self.base_url}/custom/wallboxbilling/receive.php"

        # JSON-Payload gemäß D-04 (API-01)
        # Entweder rfid_hash (physischer Tap) oder login (manuelle Admin-Erfassung
        # per Mitarbeiter-Auswahl) — nie beides gleichzeitig
        payload = {
            "wallbox_id": session_data["wallbox_id"],
            "start_time": format_timestamp(session_data["start_time"]),  # ISO 8601
            "end_time": format_timestamp(session_data["end_time"]),      # ISO 8601
            "kwh": round(float(session_data["kwh"]), 3)                # 3 Nachkommastellen
        }
        if session_data.get("login"):
            payload["login"] = session_data["login"]
        else:
            payload["rfid_hash"] = session_data["rfid_hash"]      # Immer Hash (API-05, SEC-03)

        identity_log = f"login={payload['login']}" if "login" in payload else f"rfid_hash={payload['rfid_hash'][:16]}..."
        try:
            _LOGGER.debug("Sende Session an %s: %s", url, identity_log)

            response = self.session.post(
                url,
                json=payload,
                headers=self.headers,
                timeout=self.timeout,
                allow_redirects=False  # Kein HTTP→HTTPS Redirect der POST→GET macht
            )

            # HTTP 4xx/5xx prüfen
            response.raise_for_status()

            # Content-Type prüfen — HTML-Antwort = Login-Page = Auth-Problem
            content_type = response.headers.get('Content-Type', '').lower()
            if 'text/html' in content_type:
                error_msg = (
                    "Server returned HTML statt JSON — wahrscheinlich Dolibarr Login-Page "
                    "(nologin-Fix in receive.php nicht aktiv oder API-Key falsch). "
                    f"Erste 200 Zeichen: {response.text[:200]}"
                )
                _LOGGER.error("API-Antwort ist HTML, nicht JSON: %s", error_msg)
                return (False, error_msg)

            # Response-Body prüfen (HTTP 200 ≠ immer Erfolg)
            try:
                body = response.json()
            except Exception as e:
                error_msg = f"Keine gültige JSON-Antwort: {e} — Body: {response.text[:200]}"
                _LOGGER.error("API-Antwort nicht parsbar: %s", error_msg)
                return (False, error_msg)

            if body.get('success') is False:
                msg = body.get('message', '')
                if msg == 'Session already exists':
                    # Idempotent: schon übertragen → als Erfolg markieren, aber loggen
                    _LOGGER.warning(
                        "Session bereits in Dolibarr (identisch) — als übertragen markiert. %s",
                        identity_log
                    )
                else:
                    error_msg = body.get('error', msg or 'API returned success=false')
                    _LOGGER.error("API abgelehnt: %s", error_msg)
                    return (False, error_msg)

            _LOGGER.info("Session erfolgreich übertragen: %s, kwh=%.3f",
                        identity_log, payload["kwh"])
            return (True, "")

        except requests.exceptions.Timeout:
            error_msg = f"Timeout nach {self.timeout}s"
            _LOGGER.error("API-Timeout: %s", error_msg)
            return (False, error_msg)

        except requests.exceptions.ConnectionError as e:
            error_msg = f"Verbindungsfehler: {e}"
            _LOGGER.error("API-Verbindungsfehler: %s", error_msg)
            return (False, error_msg)

        except requests.exceptions.HTTPError as e:
            error_msg = f"HTTP {response.status_code}: {response.text}"
            _LOGGER.error("API-Fehler: %s", error_msg)
            return (False, error_msg)

        except Exception as e:
            error_msg = f"Unerwarteter Fehler: {e}"
            _LOGGER.error("API-Fehler: %s", error_msg)
            return (False, error_msg)

    def check_connection(self) -> bool:
        """
        Prüft ob Dolibarr erreichbar ist (einfacher HTTP-Ping auf Basis-URL).

        Returns:
            True wenn Server antwortet (HTTP < 500 oder Redirect)
        """
        try:
            response = self.session.get(
                self.base_url,
                headers={"DOLAPIKEY": self.api_token},
                timeout=5,
                allow_redirects=True
            )
            _LOGGER.info("Dolibarr erreichbar: HTTP %d", response.status_code)
            return response.status_code < 500
        except Exception as e:
            _LOGGER.warning("Dolibarr nicht erreichbar: %s", e)
            return False

    def list_employees(self) -> list:
        """
        Holt die Liste der Mitarbeiter mit zugeordnetem RFID-Tag aus Dolibarr
        (für die Auswahl beim manuellen Erfassen — SEC-01: enthält nie den
        rfid_hash, nur login + Name).

        Returns:
            Liste von {"login": ..., "name": ...} — leer bei Fehler
        """
        url = f"{self.base_url}/custom/wallboxbilling/employees.php"
        try:
            response = self.session.get(
                url,
                headers={"DOLAPIKEY": self.api_token},
                timeout=self.timeout
            )
            response.raise_for_status()
            body = response.json()
            if body.get("success"):
                return body.get("employees", [])
            _LOGGER.warning("employees.php lieferte success=false: %s", body.get("error"))
            return []
        except Exception as e:
            _LOGGER.warning("Mitarbeiterliste konnte nicht geladen werden: %s", e)
            return []

