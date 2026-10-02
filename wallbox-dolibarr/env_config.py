"""Konfiguration über Umgebungsvariablen (Docker/.env/Portainer).

  EC_<OPTION>             → oberste Ebene   (EC_SESSION_SOURCE=alfen_http)
  EC_<BEREICH>__<OPTION>  → verschachtelt   (EC_API__API_TOKEN=..., EC_ALFEN__HOST=...)

Umgebung hat Vorrang vor options.json. Werte: true/false → bool, [..]/{..} →
JSON (Fehler = Abbruch), Zahlen → Zahl, sonst Text. Geheimnisse bleiben Text.
"""
import copy
import json
import logging
import os
import re

_LOGGER = logging.getLogger(__name__)

PREFIX = 'EC_'
_NUMBER = re.compile(r'-?(0|[1-9]\d*)(\.\d+)?')
_TEXT_ONLY = ('password', 'token', 'secret', 'user', 'wallbox_id', 'host', 'url')


def _parse(name: str, key: str, raw: str):
    low = raw.lower()
    if low in ('true', 'false'):
        return low == 'true'
    if raw[0] in '[{':
        try:
            return json.loads(raw)
        except ValueError as e:
            raise ValueError(f"{name}: ungültiges JSON ({e})") from None
    if not any(t in key for t in _TEXT_ONLY) and _NUMBER.fullmatch(raw):
        return float(raw) if '.' in raw else int(raw)
    return raw


def apply_env_overrides(config: dict, environ) -> dict:
    """Gibt eine Kopie von config mit allen EC_*-Werten aus environ zurück."""
    result = copy.deepcopy(config)
    for name in sorted(environ):
        raw = environ[name].strip()
        if not name.startswith(PREFIX) or not raw:
            continue
        path = [p.lower() for p in name[len(PREFIX):].split('__')]
        if not all(path):
            continue
        target = result
        for section in path[:-1]:
            if not isinstance(target.get(section), dict):
                target[section] = {}
            target = target[section]
        target[path[-1]] = _parse(name, path[-1], raw)
        _LOGGER.info("Option aus Umgebung gesetzt: %s", '.'.join(path))
    return result


def env_name(field: str) -> str:
    """'api.api_token' → 'EC_API__API_TOKEN'."""
    return PREFIX + '__'.join(p.upper() for p in field.split('.'))


def env_overrides(field: str, environ=None) -> bool:
    """True, wenn eine Umgebungsvariable diesen Wert überschreibt."""
    environ = os.environ if environ is None else environ
    return bool(environ.get(env_name(field), '').strip())
