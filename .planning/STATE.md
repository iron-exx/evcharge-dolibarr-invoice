# Project State: Wallbox-Dolibarr Integration

## Project Reference

**Core Value:** Automatisierte, RFID-basierte Abrechnung von Wallbox-Ladevorgängen ohne manuellen Aufwand
**Current Focus:** Milestone v1.0 shipped — planning next milestone
**Created:** 2026-05-04
**Last Updated:** 2026-05-11

## Current Position

**Status:** ✅ v1.0 MVP shipped
**Phases completed:** 5/5
**Plans completed:** 10/10
**Requirements covered:** 38/40 (2 partial: PER-04 doc gap, BIL-07 deferred)

## Performance Metrics

- Phases completed: 5/5
- Plans completed: 10/10
- Requirements covered: 38/40
- Sessions worked: All phases complete

## Accumulated Context

### Key Decisions
- SQLite für Session-Persistenz (leichtgewichtig, lokal)
- REST-API mit DOLAPIKEY (Standard in Dolibarr)
- RFID-Whitelist per YAML (einfach pflegbar für v1)
- Dolibarr Cronjob für Abrechnung (native Dolibarr-Funktion)
- HA-Addon: Dockerfile + build.json, Async loop mit aiohttp, HA Core Integration
- Dolibarr-Modul: wallboxbilling, noumainventoryapp Vorlage, 4 Frontend-Seiten
- SHA-256 Hash für RFID (kein Klartext, SEC-01/02)
- Session-Tracking: SQLite mit active/completed Sessions, RFID-Debouncing 7s
- User Management: RFID-Hash, Preis pro kWh, Kostenstelle
- API: DOLAPIKEY Auth, exponentieller Backoff (1s init, 60s max, 5 retries, 2x factor)
- Billing: Monatlicher Cronjob, TCPDF-PDF, CSV/DATEV-Export
- WAL-Modus für SQLite (PER-04), Neustart-Recovery (PER-02/03)
- Multi-Wallbox per YAML-Konfiguration (EXT-01)

### Completed Plans
- [x] **01-01**: HA Addon Foundation (Dockerfile, Websocket API, Hash Utility)
- [x] **01-02**: Dolibarr Module Skeleton (modWallboxbilling, SQL, Frontend)
- [x] **02-01**: HA Session Tracking (session_manager.py, SQLite, RFID Whitelist, Debouncing)
- [x] **02-02**: Dolibarr User Management (wallboxbilling DAO, llx_wallbox_sessions, RFID-Hash, Pricing)
- [x] **03-01**: HA Addon API Client (api_client.py, session_manager, config, main loop)
- [x] **03-02**: Dolibarr Custom API Endpoint (api_wallboxbilling.class.php, DB transmitted_at)
- [x] **04-01**: Dolibarr Billing Class + Cron Job
- [x] **04-02**: PDF Invoices + CSV/DATEV Export
- [x] **05-01**: HA Addon Restart Recovery + WAL Mode
- [x] **05-02**: Multi-Wallbox Support

### Deferred Items
Items acknowledged and deferred at v1.0 milestone close on 2026-05-11:

| Category | Item | Status |
|----------|------|--------|
| requirement | PER-04 (WAL mode) not tracked in SUMMARY frontmatter | documentation gap |
| requirement | BIL-07 (Dolibarr invoice creation) deferred as optional | optional, deferred |

---

*State updated: 2026-05-11 after v1.0 milestone completion*
