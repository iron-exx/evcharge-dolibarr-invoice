"""Alfen-HTTP-Datenquelle für ExpenseCharge.

Aktiv nur bei `session_source: alfen_http`. Spricht die HTTPS-API der Wallbox
direkt an (`/api/login`, `/api/prop`), ohne Home Assistant und ohne die
HACS-Integration.

Der entscheidende Unterschied zu den anderen Quellen: das Transaktions-Log der
Wallbox enthält FERTIGE Ladevorgänge mit Transaktions-ID, Zeitpunkt,
Zählerständen und Karte. Es wird deshalb nichts aus Sensorflanken erraten —
importiert wird wie bei OCPP eine abgeschlossene Transaktion.
"""
