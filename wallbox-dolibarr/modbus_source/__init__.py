"""Modbus-TCP-Datenquelle für ExpenseCharge.

Aktiv nur bei `session_source: modbus`. Statt Home-Assistant-Sensoren (bzw.
statt OCPP) fragt das Addon die Wallbox direkt über Modbus TCP ab und speist
die gelesenen Werte in dieselbe Session-Logik ein wie der HA-Pfad.

Warum zusätzlich zu OCPP: eine Wallbox kennt nur EIN OCPP-Backend. Wer bereits
ein Cloud-Backend des Herstellers nutzt, kann den OCPP-Betrieb nicht verwenden —
Modbus TCP läuft parallel dazu.
"""
