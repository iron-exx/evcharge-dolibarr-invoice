"""OCPP-1.6J-Zentralserver (Central System) für ExpenseCharge.

Aktiv nur bei `session_source: ocpp`. Die Wallbox verbindet sich per
WebSocket direkt mit dem Addon; Start/Ende/Zählerstände kommen aus den
OCPP-Transaktionsnachrichten statt aus Home-Assistant-Sensoren.
"""
