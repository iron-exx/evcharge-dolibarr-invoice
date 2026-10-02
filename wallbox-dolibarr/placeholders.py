"""Erkennt nicht ersetzte Vorlagenwerte (options.standalone.example.json, config.yaml)."""

_PLACEHOLDER_TOKENS = {'your_dolapikey_here', 'gemeinsames-token-identisch-mit-dem-dolibarr-modul'}
_PLACEHOLDER_PASSWORDS = {'bitte-mindestens-16-zeichen'}


def is_placeholder_password(password: str) -> bool:
    return password in _PLACEHOLDER_PASSWORDS


def find_placeholders(config: dict) -> list:
    """Feldnamen, deren Wert noch aus der Vorlage stammt."""
    found = []
    api = config.get('api') or {}
    url = str(api.get('dolibarr_url') or '')
    if '.example.com' in url or url.rstrip('/').endswith('//example.com'):
        found.append('api.dolibarr_url')
    if api.get('api_token') in _PLACEHOLDER_TOKENS:
        found.append('api.api_token')
    for cp in config.get('ocpp_charge_points') or []:
        if isinstance(cp, dict) and is_placeholder_password(cp.get('password')):
            found.append(f"ocpp_charge_points[{cp.get('id')}].password")
    return found
