"""Verwaltungsoberfläche für den Standalone-Betrieb (ohne Home Assistant).

Im HA-Addon wird nichts davon aktiv — dort gehört die Konfiguration HA.
"""
import os


def is_standalone() -> bool:
    return not os.getenv('SUPERVISOR_TOKEN')
