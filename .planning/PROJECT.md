# Wallbox-Dolibarr Integration

## What This Is

Eine vollständig integrierte Lösung aus einem Home-Assistant-Addon und einem Dolibarr-ERP-Modul zur automatischen Erfassung, Auswertung und monatlichen Abrechnung von Ladevorgängen einer Alfen Eve Wallbox. Die Identifikation erfolgt über RFID-Karten, die Lösung ist produktionsreif, ausfallsicher, datenschutzkonform und skalierbar.

## Core Value

Automatisierte, RFID-basierte Abrechnung von Wallbox-Ladevorgängen ohne manuellen Aufwand – Ladevorgänge werden erfasst, Nutzer identifiziert und monatlich korrekt abgerechnet.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Home-Assistant-Addon erfasst RFID-Karten (sensor.alfen_eve_tag_socket_1) und prüft gegen Whitelist
- [ ] Ladevorgänge (Sessions) werden mit Startzeit, Endzeit, kWh und Wallbox-ID persistiert (SQLite)
- [ ] Sessions überleben Home-Assistant-Neustarts und werden korrekt fortgesetzt/beendet
- [ ] RFID-Mehrfachauslösungen werden entprellt
- [ ] Addon überträgt abgeschlossene Sessions per REST-API (mit API-Token & Retry) an Dolibarr
- [ ] Dolibarr-Modul erweitert Benutzer um RFID-IDs, individuellen kWh-Preis und Kostenstelle
- [ ] Datenbanktabelle llx_wallbox_sessions mit allen Pflichtfeldern
- [ ] Monatliche Abrechnung als Dolibarr-Cronjob (am letzten Tag des Monats)
- [ ] Abrechnung erstellt detaillierte Ladeliste, Summenübersicht, Gesamtkosten und PDF
- [ ] Preislogik: Kosten = kWh × nutzerspezifischer kWh-Preis
- [ ] RFID wird nicht im Klartext geloggt, interne Hash-Speicherung
- [ ] Erweiterbarkeit auf mehrere Wallboxen, CSV-/DATEV-Export

### Out of Scope

- [Mobile App für Nutzer] — Web-Interface von Dolibarr ausreichend für v1
- [Echtzeit-Ladeanzeige im Dashboard] — Kernfokus liegt auf Abrechnung, nicht Monitoring
- [Lastmanagement / Load Balancing] — Nicht Teil der Abrechnungslösung
- [Zahlungsabwicklung (SEPA/Überweisung)] — Dolibarr-Standardfunktionen nutzen

## Context

- Wallbox: Alfen Eve mit Home-Assistant-Integration
- Sensoren: sensor.alfen_eve_tag_socket_1 (RFID), sensor.alfen_energy_total (Energie), Ladezustand (Charging/Idle/Stopped)
- RFID-Format: z.B. "EFCD083E"
- Addon-Verzeichnis: ~/projects/Wallbox-Dolibarr/Homeassistant
- Dolibarr-Verzeichnis: ~/projects/Wallbox-Dolibarr/Dolibarr (Beispielpaket existiert bereits)
- Übertragung: REST-API von HA-Addon an Dolibarr mit JSON-Payload

## Constraints

- **Datenschutz**: RFID nur gehasht speichern, keine Klartext-Logs
- **Ausfallsicherheit**: Sessions in SQLite persistieren, HA-Neustarts überleben
- **Skalierbarkeit**: Mehrere Wallboxen müssen später unterstützbar sein
- **Sprache**: Deutsch (Code-Kommentare, Dokumentation, UI)

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| SQLite für Session-Persistenz | Leichtgewichtig, lokal, keine externe DB nötig | — Pending |
| REST-API mit API-Token | Standard, in Dolibarr nativ unterstützt | — Pending |
| RFID-Whitelist per YAML | Einfach pflegbar, kein komplexes UI nötig für v1 | — Pending |
| Dolibarr Cronjob für Abrechnung | Native Dolibarr-Funktion, keine externe Scheduler | — Pending |

---
*Last updated: 2026-05-04 after initialization*
