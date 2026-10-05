"""Wallbox-Name → Kennung, die Dolibarr annimmt.

receive.php prüft `^[\\w\\-\\.]{1,50}$` mit PHP-\\w ohne /u, also nur ASCII.
Ein Name mit Leerzeichen oder Umlaut würde dort mit HTTP 400 abgelehnt — und
weil die Übertragung beim ersten Fehler abbricht, blieben danach alle Ladungen
hängen. Deshalb wird JEDER Wert vor dem Senden hierüber geschickt.

Zeichenweise und deterministisch: derselbe Name ergibt immer dieselbe Kennung
(Dolibarr erkennt Duplikate über sie). Bestehende gültige Kennungen bleiben gleich.
"""
import re
import unicodedata

_MAX = 50
_GERMAN = str.maketrans({'ä': 'ae', 'ö': 'oe', 'ü': 'ue', 'Ä': 'Ae', 'Ö': 'Oe', 'Ü': 'Ue', 'ß': 'ss'})
_INVALID = re.compile(r'[^\w\-.]', re.ASCII)
FALLBACK = 'wallbox'


def to_dolibarr_id(raw) -> str:
    text = str(raw or '').strip().translate(_GERMAN)
    # é → e usw.; was danach nicht ASCII ist, fällt weg
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')
    cleaned = _INVALID.sub('_', text)[:_MAX]
    return cleaned if cleaned.strip('_') else FALLBACK
