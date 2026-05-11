# Wallbox-Dolibarr Integration

## What This Is

Eine vollständig integrierte Lösung aus einem Home-Assistant-Addon und einem Dolibarr-ERP-Modul zur automatischen Erfassung, Auswertung und monatlichen Abrechnung von Ladevorgängen einer Alfen Eve Wallbox. Die Identifikation erfolgt über RFID-Karten, die Lösung ist produktionsreif, ausfallsicher, datenschutzkonform und skalierbar.

## Core Value

Automatisierte, RFID-basierte Abrechnung von Wallbox-Ladevorgängen ohne manuellen Aufwand – Ladevorgänge werden erfasst, Nutzer identifiziert und monatlich korrekt abgerechnet.

## Current State (v1.0 — shipped 2026-05-11)

Das System ist vollständig einsatzbereit:

- **HA Addon** (Alpine 3.23, Python 3.13, SQLite) — erfasst RFID-Karten, authentifiziert gegen Whitelist, trackt Sessions mit Start/Ende/kWh/Wallbox-ID, 7s Debouncing, Neustart-Recovery, WAL-Modus
- **Dolibarr Modul** (PHP 8.1+) — Benutzerverwaltung mit RFID-Hash(es), individuellem kWh-Preis, Kostenstelle; API-Endpunkt für Session-Upload; monatlicher Cronjob für Abrechnung; PDF-Invoices via TCPDF; CSV/DATEV-Export
- **Integration** — REST-API mit DOLAPIKEY-Auth, exponentieller Backoff-Retry, SHA-256 Hash für RFID

Stacks: Alpine 3.23 + Python 3.13 + requests 2.32+ (HA-Addon) | PHP 8.1+ + Dolibarr 21.x-22.x + TCPDF (Dolibarr)
Code: ~4.100 LOC (Python + PHP + Shell + Config)

## Requirements

### Validated (v1.0)

- ✓ **HA-01**: RFID-Erfassung via sensor.alfen_eve_tag_socket_1
- ✓ **HA-02**: Whitelist-Prüfung per YAML
- ✓ **HA-03–07**: Session-Tracking, Start/Ende, Energieberechnung, Debouncing
- ✓ **PER-01–05**: SQLite-Persistenz, Neustart-Recovery, WAL-Modus, atomare Energie
- ✓ **API-01–05**: REST-Übertragung, JSON-Payload, DOLAPIKEY, Retry/Backoff, RFID-Hash
- ✓ **USR-01–05**: Benutzeraktivierung, RFID-Verknüpfung, kWh-Preis, Kostenstelle, SHA-256
- ✓ **DB-01–03**: llx_wallbox_sessions Tabelle, Indizes, Auto-Erstellung
- ✓ **BIL-01–06**: Monatlicher Cronjob, Gruppierung, Preislogik, PDF via TCPDF
- ✓ **BIL-07**: Optional — PDF-Invoices implementiert, Dolibarr-Invoice deferred
- ✓ **SEC-01–05**: Keine Klartext-Logs, SHA-256, API-Token, Rollen/Rechte, SQL-Injection-Prävention
- ✓ **EXT-01–03**: Multi-Wallbox, CSV-Export, DATEV-Export

### Active

(No active requirements — next milestone will define v1.1 scope)

### Out of Scope

- [Mobile App für Nutzer] — Web-Interface von Dolibarr ausreichend für v1
- [Echtzeit-Ladeanzeige im Dashboard] — Kernfokus liegt auf Abrechnung, nicht Monitoring
- [Lastmanagement / Load Balancing] — Nicht Teil der Abrechnungslösung
- [Zahlungsabwicklung (SEPA/Überweisung)] — Dolibarr-Standardfunktionen nutzen
- [Plug & Charge (ISO 15118)] — Erfordert PKI-Infrastruktur, RFID reicht für v1
- [Roaming (OCPI)] — Irrelevant für Single-Site Dolibarr-Setup
- [OCPP-Backend] — Lokale Modbus/API-Lösung bevorzugt für v1

## Context

- Wallbox: Alfen Eve mit Home-Assistant-Integration
- Sensoren: sensor.alfen_eve_tag_socket_1 (RFID), sensor.alfen_energy_total (Energie), Ladezustand (Charging/Idle/Stopped)
- RFID-Format: z.B. "EFCD083E"
- Addon-Verzeichnis: ~/projects/Wallbox-Dolibarr/Homeassistant
- Dolibarr-Verzeichnis: ~/projects/Wallbox-Dolibarr/Dolibarr
- Übertragung: REST-API von HA-Addon an Dolibarr mit JSON-Payload

## Constraints

- **Datenschutz**: RFID nur gehasht speichern, keine Klartext-Logs
- **Ausfallsicherheit**: Sessions in SQLite persistieren, HA-Neustarts überleben
- **Skalierbarkeit**: Mehrere Wallboxen müssen später unterstützbar sein
- **Sprache**: Deutsch (Code-Kommentare, Dokumentation, UI)

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| SQLite für Session-Persistenz | Leichtgewichtig, lokal, keine externe DB nötig | ✓ Good |
| REST-API mit DOLAPIKEY | Standard, in Dolibarr nativ unterstützt | ✓ Good |
| RFID-Whitelist per YAML | Einfach pflegbar, kein komplexes UI nötig für v1 | ✓ Good |
| Dolibarr Cronjob für Abrechnung | Native Dolibarr-Funktion, kein externer Scheduler | ✓ Good |
| SHA-256 für RFID-Hash | Sicher, deterministisch, kein Klartext | ✓ Good |
| WAL-Modus für SQLite | Bessere Concurrency bei gleichzeitigen Zugriffen | ✓ Good |
| TCPDF für PDF-Rechnungen | Dolibarr-eigene PDF-Bibliothek, kein zusätzliches Tool | ✓ Good |
| Mehrere Wallboxen per Konfiguration | Flexibel ohne Code-Änderungen | ✓ Good |

---

*Last updated: 2026-05-11 after v1.0 milestone*
