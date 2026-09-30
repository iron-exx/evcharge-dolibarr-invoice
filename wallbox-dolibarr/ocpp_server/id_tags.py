"""OCPP-idTag-Normalisierung und Whitelist-Abgleich.

OCPP 1.6 definiert idTag als CiString20Type (max. 20 Zeichen, Vergleich
OHNE Groß-/Kleinschreibung). Wallboxen liefern dieselbe Karte je nach
Hersteller mal "efcd083e", mal "EFCD083E". Damit der SHA-256-Hash — und
damit die Dolibarr-Zuordnung — stabil bleibt, wird VOR dem Hashen
einheitlich auf Großbuchstaben normalisiert.
"""


def normalize_id_tag(raw) -> str:
    """Trimmt und wandelt in Großbuchstaben; Nicht-Strings → ''."""
    if not isinstance(raw, str):
        return ''
    return raw.strip().upper()


def is_whitelisted(id_tag, whitelist) -> bool:
    """True, wenn der Tag (ohne Groß-/Kleinschreibung) in der Whitelist steht."""
    tag = normalize_id_tag(id_tag)
    if not tag:
        return False
    return any(normalize_id_tag(entry) == tag for entry in (whitelist or []))
