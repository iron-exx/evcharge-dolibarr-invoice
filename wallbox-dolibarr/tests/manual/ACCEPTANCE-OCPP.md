# Abnahmeprotokoll — Betriebsart OCPP (Task 12)

Stand: 30.09.2026 · Branch `feat/ocpp-central-system` · Addon-Version 2.0.0

## Umgebung dieses Durchlaufs

| | |
|---|---|
| Python | 3.12.14 im Zielimage — **hier lokal 3.14.4** (kein 3.12 auf dem Rechner) |
| ocpp / websockets / jsonschema | 2.1.0 / 15.0.1 (zusätzlich gegen 17.1 geprüft) / 4.26.0 |
| Docker | **nicht verfügbar** (`docker: command not found`) |
| Home Assistant | **nicht verfügbar** |
| Echte Wallbox | **nicht verfügbar** |
| pytest | 75 passed |

## Step 1 — Addon im OCPP-Betrieb starten

Docker fehlt, daher **nicht als Container** gefahren. Ersatz: `tests/manual/e2e_ocpp.py`
startet `main.build_ocpp_server()` und `main.start_web_server()` direkt — also denselben
Code, den der Container ausführt, nur ohne Docker-Schicht und ohne `/data`.

    python tests/manual/e2e_ocpp.py

Offen für den Nutzer: der Containerlauf selbst (`docker build` + `docker run -p 9000:9000
-p 8099:8099`) inklusive der erwarteten Logzeilen
`Betriebsart: OCPP-Zentralserver (1 Wallbox(en) konfiguriert)` und
`OCPP-Zentralserver lauscht auf Port 9000`.

## Step 2 — Szenarien S1–S6

Gefahren mit der mitgelieferten Simulation (`tests/ocpp_sim.py`), **nicht** mit dem
unabhängigen SAP-Simulator — dafür fehlen Docker und `pnpm`. Ergebnis: **18/18 Prüfungen OK.**

| # | Szenario | Erwartung | Ergebnis |
|---|---|---|---|
| S1 | Boot, Heartbeat, StatusNotification | Wallbox „verbunden" in der UI | **OK** — BootNotification `Accepted`, `live.json` zeigt `connected: true`, Hersteller/Modell, `wallbox_id: garage` |
| S2 | Ladung mit `EFCD083E` (von der Wallbox als `efcd083e` gesendet) | Session aktiv, kWh steigt, nach Stop `completed` | **OK** — Kleinschreibung toleriert (D5), `current_kwh` 3,25 während der Ladung, danach genau **eine** Session mit **7,25 kWh**, Sofort-Übertragung angefordert |
| S3 | Ladung mit unbekanntem Tag | `Invalid`, keine Session, roter Hinweis in der UI | **OK** — `Authorize` → `Invalid`, `StartTransaction` → `transactionId 0`, Stop mit ID 0 bestätigt aber nicht abgerechnet, UI zeigt `DEADBEEF` |
| S4 | Addon während der Ladung stoppen und neu starten | Session bleibt `active`, nach dem Stop genau eine weitere `completed` | **OK** — Session überlebt als `active` (keine Recovery, D8), danach **5,0 kWh** abgerechnet |
| S5 | Verbindungsabriss: Start doppelt, Stop doppelt nachgeliefert | genau eine Session, Zeitstempel der Wallbox | **OK** — wiederholter Start bekommt dieselbe `transactionId` (D3), zweiter Stop ändert nichts, abgerechnet wird der **erste** Stop (6,0 kWh), Startzeit = Wallbox-Zeit (vor 3 h), nicht die Nachlieferzeit |
| S6 | Station ohne Eintrag in `ocpp_charge_points` | abgewiesen, Logzeile nennt die ID | **OK** — HTTP 404, Log: `Unbekannte Wallbox 'FREMD-00002' abgewiesen — in ocpp_charge_points eintragen` |

Ergänzend außerhalb der Szenarien geprüft:

- `test_id_tag_plaintext_never_logged`: bei Log-Level DEBUG steht **keine** Karten-ID im
  Klartext im Log (D15) — nur der Hash-Präfix.
- Web-UI-Rendering ohne Browser in Node gegen einen DOM-Stub: OCPP-Zeile mit grünem
  Charging-Chip, „getrennt" rot, roter Hinweis auf die abgelehnte Karte, HA-Betrieb
  unverändert (11/11).
- Schema-Migration: DB mit dem alten Code erzeugt, mit dem neuen geöffnet — 13 → 18 Spalten,
  Bestandszeile unverändert.

## Step 3 — Abnahme mit echter Wallbox (Alfen Eve)

**Nicht durchgeführt — keine Hardware verfügbar.** Vollständig offen:

- [ ] Vorher: bisherige Backend-Einstellungen der Wallbox dokumentieren (Rollback-Pfad)
- [ ] ACE Service Installer: Backend-URL `ws://<HA-IP>:9000/`, „OCPP 1.6 JSON",
      Autorisierung auf Central System/Backend
      — **zu klären: Ist dafür eine Alfen-Lizenz nötig?** (Recherche unklar, Risiko im Plan)
      — **zu klären: Welche Charge-Point-ID hängt die Wallbox an?**
- [ ] Logzeile „Unbekannte Wallbox '…'" auswerten, ID in `ocpp_charge_points` eintragen
- [ ] Echte Ladung mit registrierter Karte:
      kWh in Dolibarr == Wallbox-Anzeige (Abweichung < 0,01 kWh);
      Zeitstempel korrekt (**Zeitzone!**); `wallbox_id` in der Spesenzeile stimmt
- [ ] Unbekannte Karte: Wallbox startet nicht bzw. bricht mit `DeAuthorized` ab;
      mit `ocpp_apply_recommended_config: true` wiederholen und
      `StopTransactionOnInvalidId` prüfen
- [ ] Lastmanagement-Pause → eine Session (`SuspendedEVSE`/`SuspendedEV` beendet nichts)
- [ ] Addon-Neustart während der Ladung → keine verlorene/doppelte Session
- [ ] WLAN/LAN der Wallbox 5 min trennen, während die Ladung endet → Stop wird nachgeliefert
- [ ] Parallelbetrieb mit bestehender HA-Alfen-Integration (nur Information)
- [ ] Rollback: `session_source: ha_sensors` + altes Wallbox-Backend → Verhalten wie vorher

## Nach dem OCPP-Teil ergänzt — ebenfalls abzunehmen

Seit diesem Protokoll sind drei weitere Datenquellen und die Kartenverwaltung
dazugekommen. Was davon nur mit Hardware prüfbar ist:

### Alfen HTTPS-API (`session_source: alfen_http`)

- [ ] Anmeldung an der echten Wallbox mit Installateur- bzw. Admin-Zugang
- [ ] **Format der Transaktions-Logzeilen gegen die echte Box prüfen.** Es ist aus
      dem Parser der HACS-Integration abgeleitet und nicht verifiziert. Einmal
      `curl -k "https://<ip>/api/transactions?offset=0"` nach der Anmeldung und
      eine `txstart`/`txstop`-Zeile mit `alfen_source/transactions.py` abgleichen
- [ ] Zählerstand aus `2221_22` gegen die Anzeige der Wallbox vergleichen
- [ ] Eine echte Ladung: wird sie genau einmal importiert, mit korrekten kWh,
      Zeitstempeln und Karte?
- [ ] Mehrere Durchläufe: bleibt es bei einer Abrechnung? (Idempotenz)
- [ ] Steckdose 2 einer Eve Double (`param_energy: 2221_32`, `param_state: 2502_1`)

### Modbus TCP (`session_source: modbus`)

- [ ] Registeradressen aus dem Alfen-Modbus-Handbuch übertragen und den
      Zählerstand gegen die Wallbox-Anzeige prüfen
- [ ] `word_order` verifizieren — bei falscher Reihenfolge steht ein unsinniger
      Zählerstand in der Oberfläche
- [ ] Parallelbetrieb mit einem Cloud-Backend des Herstellers (das ist der Zweck
      dieser Quelle)

### Kartenverwaltung und Lernmodus

- [ ] Lernmodus an der echten Wallbox: Karte vorhalten, erscheint sie mit ihrer ID?
- [ ] Als geschäftlich einordnen → Ladung erscheint in Dolibarr
- [ ] Als privat einordnen → Ladung bleibt lokal, erreicht Dolibarr **nie**
- [ ] Nicht eingeordnete Karte: kann sie wirklich nicht laden (nur OCPP-Betrieb)?
- [ ] Nach dem Beenden des Lernmodus ist der Klartext verschwunden

### Oberfläche

- [ ] Sichtprüfung aller vier Tabs im Browser (Erfassen, Verlauf, Karten, System)
- [ ] Tagesdiagramm mit echten Daten: Balkenhöhen plausibel, Tooltips lesbar
- [ ] Tab System: zeigt er die tatsächlich wirksame Konfiguration?

## Weitere offene Punkte

- [ ] `docker build` gegen `ghcr.io/home-assistant/amd64-base:3.23`; erwartet im Build-Log:
      `ocpp OK 15.0.1`. Danach pytest **im Image** (Python 3.12).
- [ ] Unabhängiger Simulator (SAP e-mobility-charging-stations-simulator, schemastreng) —
      deckt Fehler auf, die unsere eigene Simulation mit derselben Bibliothek nicht sieht.
- [ ] HA-Instanz: Konfigurationsseite ohne Schemafehler, Abschnitt „Netzwerk" zeigt
      `9000/tcp` leer, `session_source: ha_sensors` startet wie bisher,
      Parallelbetrieb mit `lbbrhzn/ocpp` ohne Portkonflikt.
- [ ] Sichtprüfung der Ingress-UI im Browser.
- [ ] GitHub-Actions-Lauf des neuen Workflows `tests.yaml` nach dem Push.

**Solange Step 3 und der Image-Build offen sind, ist 2.0.0 nicht freigabereif.**
