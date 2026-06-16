# Project State: Wallbox-Dolibarr Integration

## Project Reference

**Core Value:** Automatisierte, RFID-basierte Abrechnung von Wallbox-Ladevorgängen ohne manuellen Aufwand
**Current Focus:** Milestone v1.1 — Robustheit & Monitoring
**Created:** 2026-05-04
**Last Updated:** 2026-06-16

## Current Position

Phase: 6 — Monitoring & Status
Plan: —
Status: Roadmap defined, ready for planning
Last activity: 2026-06-16 — v1.1 roadmap created (Phases 6-8)

```
Progress: v1.1 [          ] 0% (0/3 phases)
```

## Performance Metrics

| Metric | v1.0 | v1.1 |
|--------|------|------|
| Phases | 5 | 3 planned |
| Plans | 10 | TBD |
| Requirements | 40/40 | 0/11 |
| Status | Shipped | In progress |

## Accumulated Context

### Key Decisions (carried over from v1.0)
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

### Deferred Items (from v1.0)

| Category | Item | Status |
|----------|------|--------|
| requirement | PER-04 (WAL mode) not tracked in SUMMARY frontmatter | documentation gap |
| requirement | BIL-07 (Dolibarr invoice creation) deferred as optional | optional, deferred to v1.2+ |

### v1.1 Todos

- [ ] Plan Phase 6: Monitoring & Status (MON-01, MON-02, MON-03)
- [ ] Plan Phase 7: Alerts & Logging (ALT-01, ALT-02, LOG-01, LOG-02, LOG-03)
- [ ] Plan Phase 8: Retry & Dead-letter (RET-01, RET-02, RET-03)

---

*State updated: 2026-06-16 — v1.1 roadmap defined, Phase 6 next*
