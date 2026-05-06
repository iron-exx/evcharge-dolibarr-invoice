# Roadmap: Wallbox-Dolibarr Integration

**Project:** Wallbox-Dolibarr Integration
**Granularity:** Fine (from config.json)
**Created:** 2026-05-04
**Total v1 Requirements:** 40

## Phases

- [ ] **Phase 1: Foundation (HA Integration + Dolibarr Skeleton)** - Technical foundation for both systems
- [ ] **Phase 2: Session Tracking + User Management** - HA Addon tracks sessions; Dolibarr manages users
- [ ] **Phase 3: API Integration (HA Addon → Dolibarr)** - Sessions transmitted via REST API
- [ ] **Phase 4: Billing + Invoicing + Export** - Monthly billing, PDF invoices, CSV/DATEV export
- [ ] **Phase 5: Hardening + Multi-Wallbox** - Restart recovery, WAL mode, multiple wallboxes

## Phase Details

### Phase 1: Foundation (HA Integration + Dolibarr Skeleton)
**Goal**: Establish technical foundation for both HA Addon and Dolibarr Module
**Depends on**: Nothing (first phase)
**Requirements**: HA-01, DB-03, SEC-04
**Success Criteria** (what must be TRUE):
  1. Home Assistant shows Alfen Wallbox sensors (RFID tag, energy total, charging status) in entity list
  2. Dolibarr admin can activate Wallbox module from module list
  3. Module activation creates llx_wallbox_sessions table and sets up permissions
  4. Sensor data updates in real-time in HA dashboard when wallbox state changes
**Plans**: 2 plans

Plans:
- [ ] 01-01-PLAN.md — HA Addon Foundation (Dockerfile, Websocket API, Hash Utility)
- [ ] 01-02-PLAN.md — Dolibarr Module Skeleton (modWallboxbilling, SQL table, Permissions, Frontend pages)

### Phase 2: Session Tracking + User Management
**Goal**: HA Addon tracks charging sessions with RFID authentication; Dolibarr manages users with RFID-hash and pricing
**Depends on**: Phase 1 (need sensors and module)
**Requirements**: HA-02, HA-03, HA-04, HA-05, HA-06, HA-07, PER-01, PER-05, USR-01, USR-02, USR-03, USR-04, USR-05, DB-01, DB-02, SEC-01, SEC-02, SEC-05
**Success Criteria** (what must be TRUE):
  1. HA Addon starts a charging session when authorized RFID is detected (session recorded in SQLite with start time, kWh, RFID hash)
  2. HA Addon ends session when wallbox state changes to Idle/Stopped (end time and total kWh recorded)
  3. HA Addon rejects unauthorized RFIDs (whitelist check via YAML)
  4. HA Addon suppresses duplicate RFID reads within 5-10 seconds (debouncing)
  5. Dolibarr admin can create users with RFID-hash(es), individual kWh price, and cost center
  6. RFID hashes stored (not plaintext) in both HA SQLite and Dolibarr; SHA-256 hashing used
  7. Active sessions persist in SQLite and survive Addon restarts
  8. Dolibarr has llx_wallbox_sessions table with all required fields (id, user_id, rfid_hash, wallbox_id, start_time, end_time, kwh, price_per_kwh, total_cost, created_at)
**Plans**: 2 plans

Plans:
- [ ] 02-01-PLAN.md — HA Addon Session Tracking (session_manager.py, SQLite, RFID Whitelist, Debouncing)
- [ ] 02-02-PLAN.md — Dolibarr User Management (wallboxbilling.class.php, llx_wallbox_sessions SQL, RFID-Hash, Pricing)
**UI hint**: yes

### Phase 3: API Integration (HA Addon → Dolibarr)
**Goal**: Completed sessions transmitted from HA Addon to Dolibarr via REST API
**Depends on**: Phase 2 (need sessions in SQLite before transmitting)
**Requirements**: API-01, API-02, API-03, API-04, API-05, SEC-03
**Success Criteria** (what must be TRUE):
  1. HA Addon transmits completed session to Dolibarr via REST API (JSON with rfid_hash, wallbox_id, start_time, end_time, kwh)
  2. API calls authenticated with DOLAPIKEY token
  3. Failed transmissions retried with exponential backoff
  4. RFID transmitted as SHA-256 hash only (no plaintext)
  5. Dolibarr API endpoint validates token and rejects unauthorized requests
**Plans**: 2 plans

Plans:
- [x] 03-01-PLAN.md — HA Addon API Client (api_client.py, session_manager, config, main loop)
- [x] 03-02-PLAN.md — Dolibarr Custom API Endpoint (api_wallboxbilling.class.php, DB transmitted_at)

### Phase 4: Billing + Invoicing + Export
**Goal**: Monthly billing generates invoices and exports for German accounting
**Depends on**: Phase 3 (need sessions in Dolibarr before billing)
**Requirements**: BIL-01, BIL-02, BIL-03, BIL-04, BIL-05, BIL-06, BIL-07, EXT-02, EXT-03
**Success Criteria** (what must be TRUE):
  1. Dolibarr runs monthly billing cron job on last day of month (or 1st for previous month)
  2. Billing groups sessions by user and calculates cost (kWh × user-specific price)
  3. Detailed charging list per user and summary totals generated
  4. PDF invoice generated via TCPDF for each user
  5. Optional: Dolibarr invoice/credit note created for each user
  6. Admin can export sessions to CSV for external analysis
  7. Admin can export sessions to DATEV format for German accounting
**Plans**: 2 plans

Plans:
- [ ] 01-01-PLAN.md — HA Addon Foundation (Dockerfile, Websocket API, Hash Utility)
- [ ] 01-02-PLAN.md — Dolibarr Module Skeleton (modWallboxbilling, SQL table, Permissions, Frontend pages)
**UI hint**: yes

### Phase 5: Hardening + Multi-Wallbox
**Goal**: System survives restarts, supports multiple wallboxes, and handles edge cases
**Depends on**: Phase 4 (core value delivered, now harden)
**Requirements**: PER-02, PER-03, PER-04, EXT-01
**Success Criteria** (what must be TRUE):
  1. HA Addon recovers active sessions on restart (startup recovery queries SQLite for status='active')
  2. Incomplete sessions from crash detected and correctly continued (if still charging) or terminated (if not)
  3. SQLite uses WAL mode for better concurrency
  4. System supports multiple wallboxes (wallbox_id field populated in all session records)
**Plans**: 2 plans

Plans:
- [ ] 01-01-PLAN.md — HA Addon Foundation (Dockerfile, Websocket API, Hash Utility)
- [ ] 01-02-PLAN.md — Dolibarr Module Skeleton (modWallboxbilling, SQL table, Permissions, Frontend pages)

## Progress

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation | 2/2 | Complete ✓ | 2026-05-04 |
| 2. Session Tracking + User Management | 2/2 | Complete ✓ | 2026-05-05 |
| 3. API Integration | 2/2 | Complete ✓ | 2026-05-06 |
| 4. Billing + Invoicing + Export | 0/2 | Not started | - |
| 5. Hardening + Multi-Wallbox | 0/2 | Not started | - |

---

*Roadmap created: 2026-05-04*
*Total requirements: 40*
*Coverage: 40/40 ✓*
