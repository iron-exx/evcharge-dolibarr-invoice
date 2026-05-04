# Requirements: Wallbox-Dolibarr Integration

**Defined:** 2026-05-04
**Core Value:** Automatisierte, RFID-basierte Abrechnung von Wallbox-Ladevorgängen ohne manuellen Aufwand

## v1 Requirements

### HA-Addon Grundfunktionen

- [ ] **HA-01**: Home-Assistant-Addon erfasst RFID-Karten über sensor.alfen_eve_tag_socket_1
- [ ] **HA-02**: Addon prüft erkannte RFID-IDs gegen konfigurierbare Whitelist (YAML)
- [ ] **HA-03**: Ladevorgänge (Sessions) werden mit Startzeit, Endzeit, kWh und Wallbox-ID erfasst
- [ ] **HA-04**: Session-Start erfolgt bei RFID-Erkennung + Whitelist-Validierung + kein aktiver Ladevorgang
- [ ] **HA-05**: Session-Ende erfolgt bei Zustandswechsel auf "Idle" oder "Stopped"
- [ ] **HA-06**: Verbrauchte Energie wird berechnet (End-Energie - Start-Energie aus sensor.alfen_energy_total)
- [ ] **HA-07**: RFID-Mehrfachauslösungen werden entprellt (5-10 Sekunden Unterdrückung)

### Persistenz & Stabilität

- [ ] **PER-01**: Aktive Sessions werden in SQLite-Datenbank persistiert
- [ ] **PER-02**: Sessions überleben Home-Assistant-Neustarts (Startup-Recovery-Logik)
- [ ] **PER-03**: Unvollständige Sessions werden erkannt und korrekt fortgesetzt oder beendet
- [ ] **PER-04**: SQLite verwendet WAL-Modus für bessere Concurrency
- [ ] **PER-05**: Energie-Zählerstand wird atomar vor Session-Start bestätigt

### API-Integration (HA → Dolibarr)

- [ ] **API-01**: Addon überträgt abgeschlossene Sessions per REST-API an Dolibarr
- [ ] **API-02**: Übertragung erfolgt mit JSON-Payload (rfid_hash, wallbox_id, start_time, end_time, kwh)
- [ ] **API-03**: Absicherung der API-Zugriffe per DOLAPIKEY Token
- [ ] **API-04**: Fehlerhandling mit Retry-Mechanismus (exponentieller Backoff)
- [ ] **API-05**: RFID wird nur als Hash (SHA-256) übertragen, nie im Klartext

### Dolibarr-Modul: Benutzerverwaltung

- [ ] **USR-01**: Bestehende Dolibarr-Benutzer können für Wallbox-Abrechnung aktiviert werden
- [ ] **USR-02**: Benutzer können mit einer oder mehreren RFID-Karten verknüpft werden
- [ ] **USR-03**: Pro Benutzer speicherbar: RFID-Hash(s), individueller Strompreis pro kWh
- [ ] **USR-04**: Optional: Kostenstelle / Projekt pro Benutzer zuweisbar
- [ ] **USR-05**: RFID-Hash-Speicherung in Dolibarr (SHA-256, keine Klartext-Logs)

### Dolibarr-Modul: Datenbank & Sessions

- [ ] **DB-01**: Tabelle llx_wallbox_sessions mit Pflichtfeldern (id, user_id, rfid_hash, wallbox_id, start_time, end_time, kwh, price_per_kwh, total_cost, created_at)
- [ ] **DB-02**: Indizes auf rfid_hash, user_id, start_time für performante Abfragen
- [ ] **DB-03**: Modul-Installation erstellt Datenbanktabellen automatisch

### Abrechnung & Auswertung

- [ ] **BIL-01**: Monatliche Abrechnung als Dolibarr-Cronjob (am letzten Tag des Monats)
- [ ] **BIL-02**: Abrechnung gruppiert Sessions nach Benutzer
- [ ] **BIL-03**: Preislogik: Kosten = kWh × nutzerspezifischer kWh-Preis
- [ ] **BIL-04**: Erstellt detaillierte Ladeliste pro Nutzer
- [ ] **BIL-05**: Erstellt Summenübersicht pro Nutzer und Gesamtkosten
- [ ] **BIL-06**: Generiert PDF-Dokument der Abrechnung (via TCPDF)
- [ ] **BIL-07**: Optional: Erstellt Rechnung / Gutschrift in Dolibarr

### Sicherheit & Datenschutz

- [ ] **SEC-01**: RFID-IDs werden nicht im Klartext in Logs gespeichert
- [ ] **SEC-02**: Interne Hash-Speicherung (SHA-256) in HA und Dolibarr
- [ ] **SEC-03**: API-Zugriffe sind abgesichert (Token-Validierung in Dolibarr)
- [ ] **SEC-04**: Dolibarr-Rollen & Rechte für Wallbox-Modul beachten
- [ ] **SEC-05**: SQL-Injection-Prävention in Dolibarr-Modul (GETPOST(), $db-Abstraktion)

### Erweiterbarkeit

- [ ] **EXT-01**: Erweiterbarkeit auf mehrere Wallboxen (wallbox_id in allen Sessions)
- [ ] **EXT-02**: CSV-Export für externe Auswertungen
- [ ] **EXT-03**: DATEV-Export für deutsche Buchhaltung

## v2 Requirements

### Features für zukünftige Versionen

- [ ] **MOB-01**: Mobile App für Endnutzer (Ladestatus, Abrechnungen) — v2
- [ ] **PLG-01**: Plug & Charge (ISO 15118) Unterstützung — v2
- [ ] **ROA-01**: Roaming (OCPI) Integration — v2
- [ ] **LDB-01**: Load Balancing / Smart Charging — v2
- [ ] **UI-01**: RFID-Karten Lifecycle Management UI in Dolibarr — v2
- [ ] **UI-02**: Echtzeit-Dashboard für Ladevorgänge — v2
- [ ] **BIL-08**: Konfigurierbare Abrechnungsperioden (nicht nur monatlich) — v2

## Out of Scope

| Feature | Reason |
|---------|--------|
| Mobile App für Nutzer | Dolibarr Web-Interface ist ausreichend für v1 |
| Echtzeit-Ladeanzeige im Dashboard | Kernfokus liegt auf Abrechnung, nicht Monitoring |
| Lastmanagement / Load Balancing | Nicht Teil der Abrechnungslösung, Alfen nativ via OCPP |
| Zahlungsabwicklung (SEPA/Überweisung) | Dolibarr-Standardfunktionen nutzen |
| Plug & Charge (ISO 15118) | Erfordert PKI-Infrastruktur, RFID reicht für v1 |
| Roaming (OCPI) | Irrelevant für Single-Site Dolibarr-Setup |
| OCPP-Backend | Lokale Modbus/API-Lösung bevorzugt für v1 |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| HA-01 | Phase 1 | Pending |
| HA-02 | Phase 2 | Pending |
| HA-03 | Phase 2 | Pending |
| HA-04 | Phase 2 | Pending |
| HA-05 | Phase 2 | Pending |
| HA-06 | Phase 2 | Pending |
| HA-07 | Phase 2 | Pending |
| PER-01 | Phase 2 | Pending |
| PER-02 | Phase 5 | Pending |
| PER-03 | Phase 5 | Pending |
| PER-04 | Phase 5 | Pending |
| PER-05 | Phase 2 | Pending |
| API-01 | Phase 3 | Pending |
| API-02 | Phase 3 | Pending |
| API-03 | Phase 3 | Pending |
| API-04 | Phase 3 | Pending |
| API-05 | Phase 3 | Pending |
| USR-01 | Phase 2 | Pending |
| USR-02 | Phase 2 | Pending |
| USR-03 | Phase 2 | Pending |
| USR-04 | Phase 2 | Pending |
| USR-05 | Phase 2 | Pending |
| DB-01 | Phase 2 | Pending |
| DB-02 | Phase 2 | Pending |
| DB-03 | Phase 1 | Pending |
| BIL-01 | Phase 4 | Pending |
| BIL-02 | Phase 4 | Pending |
| BIL-03 | Phase 4 | Pending |
| BIL-04 | Phase 4 | Pending |
| BIL-05 | Phase 4 | Pending |
| BIL-06 | Phase 4 | Pending |
| BIL-07 | Phase 4 | Pending |
| SEC-01 | Phase 2 | Pending |
| SEC-02 | Phase 2 | Pending |
| SEC-03 | Phase 3 | Pending |
| SEC-04 | Phase 1 | Pending |
| SEC-05 | Phase 2 | Pending |
| EXT-01 | Phase 5 | Pending |
| EXT-02 | Phase 4 | Pending |
| EXT-03 | Phase 4 | Pending |

**Coverage:**
- v1 requirements: 40 total
- Mapped to phases: 40
- Unmapped: 0 ✓

---
*Requirements defined: 2026-05-04*
*Last updated: 2026-05-04 after roadmap creation (40 v1 requirements mapped to 5 phases)*
