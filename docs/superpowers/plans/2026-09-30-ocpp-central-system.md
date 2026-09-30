# ExpenseCharge als OCPP-Zentralserver: Implementierungsplan (Option B)

For agentic workers: REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Das HA-Addon ExpenseCharge kann wahlweise (`session_source: ocpp`) selbst als OCPP-1.6J-Zentralserver arbeiten. Wallboxen beliebiger Hersteller verbinden sich dann direkt per WebSocket, unbekannte Karten werden abgelehnt, und die Ladevorgänge werden mit den Zählerständen der Wallbox exakt nach Dolibarr übertragen.

**Architecture:** Das neue Paket `wallbox-dolibarr/ocpp_server/` kapselt Protokoll, Verbindungsannahme und Umrechnungen. `SessionManager` bekommt eigene OCPP-Methoden, die mehrere gleichzeitige Transaktionen erlauben; die OCPP-`transactionId` ist die `sessions.id`. `main.py` verzweigt beim Start: im OCPP-Betrieb gibt es keinen HA-Websocket, stattdessen läuft der OCPP-Server. Übertragung an Dolibarr (`receive.php`), Web-UI und SQLite-Puffer bleiben unverändert und werden nur erweitert.

**Tech Stack:** Python 3.12, `ocpp==2.1.0` (MIT, The Mobility House), `websockets` >= 14 (neue asyncio-API; im Image 15.0.1), `jsonschema` 4.x, `aiohttp` (bestehend), SQLite (bestehend), pytest + pytest-asyncio.

**Spec:** Dieses Dokument (Abschnitte "Ausgangslage", "Designentscheidungen", "Verifizierte Fakten").

## 0. Kurzfassung für Entscheider

| Frage | Antwort |
|---|---|
| Was ändert sich für bestehende Nutzer? | Nichts. Default bleibt `session_source: ha_sensors`, das heutige Verhalten. Die 22 bestehenden Tests laufen unverändert grün. |
| Was bringt der OCPP-Betrieb? | Echte Zugriffskontrolle: Die Wallbox fragt das Addon, und unbekannte Karten laden nicht. Start- und Endzählerstand kommen direkt von der Wallbox. Mehrere Wallboxen sind gleichzeitig möglich. Die HACS-Integration entfällt. |
| Welche Wallboxen? | Alle mit lokal konfigurierbarem OCPP 1.6J, z.B. Alfen, ABL eMH2/3, Keba P30 x-series, go-e (ab FW 59.4), Mennekes AMTRON, Compleo eBOX, Easee (native OCPP), Zaptec (Einrichtung im Portal), Wallbox Pulsar. Tabelle in Abschnitt 3. |
| Größte Einschränkung | Eine Wallbox kennt nur ein OCPP-Backend. Wer bereits ein Cloud-Backend nutzt, müsste darauf verzichten. |
| Aufwand (Schätzung) | Kern und Integration 3–4 Personentage, Paketierung und Doku 1–2 PT, Hardware-Abnahme 1–3 PT. Optionale Dolibarr-Autorisierung +1–2 PT. |
| Stand des Prototyps | Alle Kernmodule in einer Wegwerf-Kopie umgesetzt. 75 Tests grün: 22 bestehende und 53 neue, lokal sowie im echten Addon-Image (Python 3.12.14, websockets 15.0.1). Getestet mit websockets 15.0.1 und 17.1. Zusätzlich eine Ende-zu-Ende-Ladung gegen den laufenden Addon-Container (7,25 kWh → completed). |

## 1. Ausgangslage im Code (verifiziert, HEAD c951a30)

- Das Addon läuft heute als **eine** Wallbox pro Instanz. Zustände wie Energie, Tag und Pending-Auth sind Modul-Globals in `main.py:69-91`. `SessionManager.get_active_session()` arbeitet global mit `LIMIT 1` (`session_manager.py:200`), `start_session()` lehnt ab, sobald irgendeine Session aktiv ist (`:313`), und `end_session()` hat keinen Session-Parameter (`:340`). → Der OCPP-Pfad bekommt **eigene** SessionManager-Methoden. Der HA-Pfad bleibt unangetastet.
- `main()` (`main.py:691`) blockiert am Ende in `ha_ws.subscribe_entities()`. Hintergrund-Tasks sind Closures in `main()`, darunter `periodic_transmission` (`:769`) und `stale_session_guard` (`:802`).
- Die Übertragung an Dolibarr erledigt `transmit_completed_sessions()`: Payload `{wallbox_id, start_time, end_time, kwh, rfid_hash|login}` an `receive.php`. `receive.php` prüft `wallbox_id` gegen `^[\w\-\.]{1,50}$` und parst Zeiten mit `new DateTime()`. Das bestehende Format (lokal, naiv, ISO) bleibt erhalten.
- Der RFID-Hash ist `sha256(tag)` ohne Normalisierung (`utils/hash.py:6`). Dolibarr hasht die Admin-Eingabe `trim()`-bereinigt, aber ebenfalls ohne Groß-/Kleinschreibungs-Normalisierung (`admin/admin.php:126,137`).
- Dolibarr hat **keinen** Endpunkt "ist diese Karte registriert?". `receive.php` ist die einzige schreibende Aktion; nur optional in Task 13 relevant.
- Build: Base-Image `ghcr.io/home-assistant/{arch}-base:3.23` (Alpine). Abhängigkeiten kommen ausschließlich per `apk`; `requirements.txt` wird im Image nicht genutzt.
- CI (`.github/workflows/builder.yaml`) baut nur Images und führt **keine** Tests aus.
- Ingress läuft auf Port 8099. `config.yaml` gibt sonst keine Ports frei.
- Die Option `log_level` wird bisher nicht ausgewertet: `main.py:52` setzt fest INFO. Bestehender Befund außerhalb dieses Plans; der Schutz vor Klartext im Log (D15) gilt unabhängig davon.

## 2. Verifizierte Fakten (Prototyp und Quellen)

| Fakt | Wie verifiziert |
|---|---|
| `ocpp` 2.1.0 (16.07.2025) ist das aktuelle PyPI-Release, MIT, python >=3.11, einzige Abhängigkeit `jsonschema >=4.23,<5`. Aktiv gepflegt. | PyPI-Metadaten, Installation im venv |
| Action-Namen in snake_case (`Action.start_transaction`), Payloads ohne `…Payload`-Suffix (`call_result.StartTransaction(transaction_id, id_tag_info)`), verschachtelte Keys kommen snake_case an (`sampled_value`) | Prototyp-Aufrufe, `inspect.signature` |
| Unbehandelte Actions beantwortet die Bibliothek mit `CallError NotImplemented` → DataTransfer, Firmware- und Diagnostics-Status brauchen eigene Handler | Spike (DataTransfer ohne Handler) |
| `extract_charge_point_id` gibt es **nicht** in 2.1.0 → eigene Funktion `charge_point_id_from_path` | Wheel geprüft |
| websockets (neue API): Pfad über `connection.request.path` (enthält den Query-String!), `process_request(connection, request)` kann eine Response mit eigenem Header (`WWW-Authenticate`) liefern | Spike 3 |
| Fehlt der Subprotocol-Header, lehnt websockets den Handshake standardmäßig ab (`NegotiationError: missing subprotocol`). Manche Wallboxen senden keinen Header → eigenes `select_subprotocol`, das das toleriert | Prototyp-Test schlug zuerst fehl |
| `ChargePoint.start()` wirft beim normalen Trennen `ConnectionClosedOK` → muss im Handler abgefangen werden | Spike 1 |
| `@after(Action.boot_notification)` kann nach der Boot-Antwort `ChangeConfiguration` an die Wallbox senden | Spike 3 |
| Im Base-Image 3.23: python3 3.12.14, py3-websockets 15.0.1, py3-jsonschema 4.25.1, py3-aiohttp 3.13.5, py3-pip. `ocpp` braucht `pip install --break-system-packages --no-deps` (PEP 668) | `docker run` im echten Base-Image; Prototyp-Image gebaut |
| OCPP 1.6: `meterStart`/`meterStop` sind Integer in **Wh**. MeterValues haben die Default-Einheit Wh. `idTag` ist CiString20 (ohne Groß-/Kleinschreibung). Die Wallbox muss Transaktionsnachrichten offline puffern und später nachliefern. Der Server muss jede StopTransaction beantworten und den Tag bei StartTransaction erneut prüfen. | OCPP-1.6-Spezifikation (OASIS-PDF), JSON-Schemas der Bibliothek |
| Security Profile 1 = Basic Auth ohne TLS, Benutzername MUSS die Charge-Point-ID sein (A00.FR.204), Passwort >= 16 Byte. Profil 2/3 = TLS | OCA Security Whitepaper Ed. 3 |
| Die `ocpp`-Bibliothek loggt jede Nachricht samt Payload auf INFO (Logger `ocpp`), websockets auf DEBUG jeden Frame — jeweils mit idTag im Klartext → eigene Logger mit festem Level (D15) | Log des Prototyp-Containers; danach 0 Treffer, auch bei DEBUG (Unit-Test) |
| HA-Addon: TCP-Port über `ports: {"9000/tcp": <host-port>}`. Ingress ist für Wallboxen ungeeignet (HA-Login und Quell-IP 172.30.32.2) | developers.home-assistant.io |

## 3. Herstellerübersicht (Recherche, Stand 30.09.2026)

| Hersteller / Modell | OCPP lokal | Einrichtung | Hinweis |
|---|---|---|---|
| Alfen Eve Single/Double (Pro-line, S-line), NG9xx | 1.6J (NG9xx auch 2.0.1) | ACE Service Installer (Windows, Installateur-Zugang): Backend-URL, "OCPP 1.6 JSON" | Die Charge-Box-ID wird angehängt. Ob eine Lizenz nötig ist: unklar → Hardware-Abnahme |
| ABL eMH2/eMH3/eMC2/eMC3, eM4 | 1.6J | Web-UI `http://169.254.1.1:8300/` | eMH1: nur Modbus, kein OCPP |
| Keba P30 x-series, P40 | 1.6J | Web-UI "Central System Address/Path" | P30 c-series: kein OCPP |
| go-e V3/V4/V5 (FW >= 59.4), Pro (>= 59.3) | 1.6J | App → Internet → OCPP bzw. API-Key `ocppu` | |
| Mennekes AMTRON / AMEDIO | 1.6J | Web-UI → Backend | |
| Compleo eBOX smart/professional | 1.6J | eCONFIG-App (Bluetooth) | |
| Easee | 1.6J "native OCPP" (FW >= 328) | Aktivierung über Easee-Cloud-API, danach lokal `ws://` | |
| Zaptec Go/Go2/Pro | 1.6J | Zaptec-Portal ("Allow OCPP 1.6J") | Die Cloud bleibt aktiv |
| Wallbox Pulsar Plus / Copper SB / Commander 2 | 1.6J | myWallbox-App/Portal, Passwort leer | Laut evcc fehlen bei FW 5.x teils MeterValues → Fallback greift |
| Heidelberg Energy Control | – | nur mit zusätzlicher Heidelberg Combox | |

## 4. Designentscheidungen

| # | Entscheidung | Begründung |
|---|---|---|
| D1 | Neue Option `session_source: ha_sensors \| ocpp`, Default `ha_sensors` | Keine Verhaltensänderung für bestehende Installationen. Der OCPP-Pfad ist komplett separat. |
| D2 | OCPP-`transactionId` = `sessions.id` (AUTOINCREMENT). Jeder Zugriff prüft zusätzlich `charge_point_id` | Die ID ist über Neustarts eindeutig. Eine alte, gepufferte Nachricht trifft nie die Session einer anderen Wallbox. |
| D3 | Idempotenz: Eine wiederholte StartTransaction (gleiche Wallbox, gleicher Connector, gleicher Original-Zeitstempel) bekommt dieselbe ID. Eine wiederholte StopTransaction ändert nichts. | Die Wallbox wiederholt Nachrichten, deren Antwort verloren ging (OCPP §3.6). So wird jede Ladung genau einmal abgerechnet. |
| D4 | Abgelehnte Karte → `transactionId=0` + Status `Invalid`. Ein Stop mit ID 0 wird bestätigt, aber nie abgerechnet. | Spezifikationskonform. Mit `StopTransactionOnInvalidId=true` bricht die Wallbox dann ab (reason `DeAuthorized`). |
| D5 | idTag-Normalisierung auf GROSSBUCHSTABEN, bevor gehasht wird | CiString20 ist case-insensitive, Hersteller liefern unterschiedlich. Ohne Normalisierung entstünden je nach Wallbox zwei verschiedene Hashes derselben Karte. → Karten in Dolibarr und in der Whitelist in Großbuchstaben eintragen. Siehe E1. |
| D6 | Zählerstand-Fallbacks: Ist `meterStop` fehlend, 0 oder kleiner als der Start, wird der letzte MeterValue bzw. `transactionData` genutzt. Gibt es keinen brauchbaren Wert → `incomplete` (sichtbar, nie still verworfen). Eine mittlere Leistung über 50 kW → `incomplete`. | Bekanntes Herstellerverhalten. Lieber eine Session zur manuellen Prüfung als eine falsche Abrechnung. Der Prototyp fand hier einen Fehler: Die erste Fassung belegte `last_meter_kwh` mit dem Startwert vor und verwarf dadurch eine Ladung ohne Endwert still als 0 kWh. |
| D7 | Zeitstempel: UTC der Wallbox → lokal-naiv, wie im bestehenden Code. Ohne Zeitzone gilt UTC. Bei unplausiblen Werten (Jahr < 2020 oder > 1 Tag in der Zukunft) gilt die Addon-Zeit. | Bei offline nachgereichten Ladungen stimmt so der Abrechnungsmonat. Eine nicht gestellte Wallbox-Uhr erzeugt keinen Eintrag für 1970. |
| D8 | Keine Restart-Recovery im OCPP-Betrieb. Offene Sessions bleiben `active`. Die Session-Wache warnt nur und beendet nichts. | Nur die Wallbox kennt das Ende und liefert StopTransaction aus ihrer Queue nach. Den Zählerstand zu raten wäre genau der Datenverlust-Fehler, der mit c951a30 behoben wurde. |
| D9 | Verbindungsannahme: Nur Wallboxen aus `ocpp_charge_points` werden angenommen (sonst HTTP 404). Basic Auth pro Wallbox optional (sonst HTTP 401). Ein fehlendes Subprotocol wird toleriert, eine fremde Version abgelehnt (400). Verbindet sich eine Wallbox neu, wird die alte Verbindung geschlossen. | Fremdgeräte im LAN können keine Sessions erzeugen. Kompatibel mit Wallboxen ohne Passwortfeld. |
| D10 | Nach dem Boot optional die empfohlene Konfiguration setzen (`ocpp_apply_recommended_config`, Default `false`): `MeterValueSampleInterval=60`, `MeterValuesSampledData=Energy.Active.Import.Register`, `StopTransactionOnInvalidId=true` | Ohne ausdrückliche Zustimmung wird nichts an der Wallbox verändert. |
| D11 | Sofortige Übertragung nach dem Ende einer Ladung (`asyncio.Event`) zusätzlich zum `transmit_interval` | Das Ende ist bei OCPP exakt bekannt. Das bestehende Retry-Verhalten bleibt. |
| D12 | Nur OCPP 1.6J in v1. Kein TLS im Addon (Profil 2/3); dafür bei Bedarf ein Reverse-Proxy oder VPN | 1.6J deckt alle genannten Hersteller ab. 2.0.1 ist ein eigenes Folgeprojekt. |
| D13 | Container-Port fest 9000, Host-Port-Default `null` (vom Nutzer in HA unter "Netzwerk" zu setzen) | Ein Default von 9000 kollidiert mit der HACS-Integration lbbrhzn/ocpp (Port 9000) → das Addon würde auch im `ha_sensors`-Betrieb nicht mehr starten. Siehe E2. |
| D14 | Klartext einer abgelehnten Karte nur im flüchtigen Live-Zustand (Ingress-UI). Im Log steht nur der Hash-Präfix. | So kann der Admin die Karten-ID sehen und eintragen, ohne dass der Klartext persistiert wird (DSGVO, Datensparsamkeit). |
| D15 | Protokoll-Logs ohne Klartext: `CentralSystemChargePoint` nutzt den Logger `ocpp.expensecharge` (fest WARNING), der Server `websockets.expensecharge` (fest INFO) | Sonst stünde jede Karten-ID im Addon-Log. Test: `test_id_tag_plaintext_never_logged`. |

## 5. Offene Entscheidungen — vom Nutzer am 30.09.2026 bestätigt (Empfehlungen übernommen)

| # | Frage | Entscheidung |
|---|---|---|
| E1 | Karten-IDs einheitlich in Großbuchstaben (D5)? | **Ja.** Bestehende Einträge in Kleinbuchstaben einmal neu anlegen. Dolibarr-`strtoupper` optional als separater Commit, nicht Teil dieses Plans. |
| E2 | Host-Port-Default `null` oder 9000? | **`null`** (D13) mit deutlichem Hinweis in Doku und Log. |
| E3 | Basic-Auth-Passwort Pflicht oder optional? | **Optional** mit Warnung im Log (D9). |
| E4 | Autorisierung zusätzlich über Dolibarr (Task 13)? | **Später.** v1 nutzt die Whitelist. Task 13 wird in diesem Durchlauf NICHT gebaut. |
| E5 | Dieselbe Karte gleichzeitig an zwei Wallboxen? | **Ja** (kein Sonderfall, YAGNI). |

## Global Constraints

- Python-Mindestversion: 3.12 (Base-Image liefert 3.12.14); die Bibliothek verlangt >=3.11.
- `ocpp==2.1.0` gepinnt; `websockets` >= 14 (neue asyncio-API `websockets.asyncio.server.serve`), im Image py3-websockets 15.0.1; `jsonschema >=4.23,<5`.
- Keine neuen Laufzeitabhängigkeiten außer `ocpp` (per pip `--no-deps`). Alles andere kommt per apk.
- `session_source` Default `ha_sensors`. Kein bestehender Test darf sich ändern oder rot werden.
- Container-Port OCPP: 9000/tcp. Ingress bleibt auf 8099.
- `wallbox_id` an Dolibarr muss `^[\w\-\.]{1,50}$` erfüllen.
- Zeitstempel in `sessions`: lokal, naiv, ISO, Sekundengenauigkeit (wie bisher).
- RFID-Klartext wird nie persistiert oder geloggt; im Log nur `hash_rfid(tag)[:16]`.
- Code-Kommentare und Logmeldungen auf Deutsch (Projektkonvention), Bezeichner auf Englisch.
- Commits im Conventional-Commits-Format (`feat:`, `fix:`, `test:`, `docs:`, `ci:`), Beschreibung auf Deutsch.
- Addon-Version nach Abschluss: `2.0.0`. Das Dolibarr-Modul bleibt unverändert.

## Review Focus

1. **Wallbox war offline oder das Addon wurde neu gestartet**, Start und Stop kommen verspätet oder doppelt. Erwartung: jede Ladung genau einmal abgerechnet, mit dem Monat der echten Ladung. → `test_start_is_idempotent_for_repeated_message`, `test_stop_is_idempotent`, `test_repeated_start_after_reconnect_gets_same_id`, `test_main_ocpp_mode_skips_home_assistant_and_recovery` (Tasks 3, 5, 7).
2. **Die Wallbox meldet Zählerstände "kreativ"** (kWh statt Wh, `meterStop=0`, Zähler zurückgesetzt, keine MeterValues). Erwartung: nie falsche kWh abrechnen, lieber `incomplete`. → `test_stop_without_usable_meter_uses_last_meter_value`, `test_stop_without_any_meter_is_incomplete`, `test_implausible_energy_is_incomplete`, `test_stop_uses_transaction_data_when_meter_stop_zero` (Tasks 3, 5).
3. **Die Uhr der Wallbox ist nicht gestellt** (1970 bzw. Zukunft). Erwartung: Die Ladung landet im aktuellen Monat. → `test_timestamp_implausible_falls_back_to_now` (Task 2). Bekannte Folge: Stehen Start und Ende beide auf dem Fallback "jetzt", greift die Leistungsgrenze → `incomplete`. Gewollt und sichtbar.
4. **Fremde oder falsch konfigurierte Geräte im LAN** (unbekannte ID, falsches oder fehlendes Passwort, fremde OCPP-Version). Erwartung: abgewiesen, keine Session, verständliche Logzeile. → `test_unknown_charge_point_rejected`, `test_wrong_password_rejected`, `test_missing_password_rejected`, `test_foreign_subprotocol_rejected` (Task 6).
5. **Update einer bestehenden Installation** (`ha_sensors`, evtl. mit lbbrhzn/ocpp auf Port 9000). Erwartung: identisches Verhalten, das Addon startet weiter. → `test_legacy_ha_path_unaffected` (Task 3), `test_live_json_ha_mode_has_empty_charge_points` (Task 8), alle 22 Bestandstests, Host-Port-Default `null` (Task 9, D13).

## Dateistruktur

| Datei | Neu/Ändern | Verantwortung |
|---|---|---|
| `wallbox-dolibarr/ocpp_server/__init__.py` | neu | Paket-Docstring |
| `wallbox-dolibarr/ocpp_server/id_tags.py` | neu | idTag normalisieren, Abgleich mit der Whitelist |
| `wallbox-dolibarr/ocpp_server/meter.py` | neu | Wh→kWh, Energie aus MeterValues, Zeitstempel |
| `wallbox-dolibarr/ocpp_server/settings.py` | neu | `OcppSettings` aus options.json |
| `wallbox-dolibarr/ocpp_server/central_system.py` | neu | OCPP-Handler je Wallbox (`CentralSystemChargePoint`) |
| `wallbox-dolibarr/ocpp_server/server.py` | neu | WebSocket-Server: Pfad-ID, Basic Auth, Subprotocol, Registry |
| `wallbox-dolibarr/session_manager.py` | ändern | Schema-Migration und OCPP-Transaktionsmethoden |
| `wallbox-dolibarr/main.py` | ändern | Betriebsart `ocpp`, Übertragung auf Modulebene, OCPP-Wache |
| `wallbox-dolibarr/web_server.py` | ändern | `live.json` mit `charge_points` und kWh je Session, JS-Anzeige |
| `wallbox-dolibarr/config.yaml`, `translations/*.yaml` | ändern | Optionen, Schema, Port, Texte |
| `wallbox-dolibarr/Dockerfile`, `requirements*.txt` | ändern | Abhängigkeiten |
| `wallbox-dolibarr/tests/__init__.py`, `tests/ocpp_sim.py` | neu | Paket für Test-Helfer, simulierte Wallbox |
| `wallbox-dolibarr/tests/test_ocpp_*.py`, `test_session_manager_ocpp.py`, `test_main_ocpp.py`, `test_web_live_ocpp.py` | neu | Tests |
| `.github/workflows/tests.yaml` | neu | pytest in CI |
| `README.md`, `wallbox-dolibarr/README.md`, `INSTALL.md` | ändern | Doku |

## Phasen im Überblick

```
Phase 1 Kern ........... Task 1–6   (reine Python-Module + Tests, keine Wirkung auf das Addon)
Phase 2 Integration .... Task 7–8   (main.py, Web-UI)
Phase 3 Auslieferung ... Task 9–11  (config.yaml/Dockerfile, Doku, CI)
Phase 4 Abnahme ........ Task 12    (Simulator-E2E + echte Wallbox)
Phase 5 optional ....... Task 13    (Autorisierung über Dolibarr) — per E4 NICHT in diesem Durchlauf
```

Alle Befehle laufen aus `wallbox-dolibarr/`, sofern nicht anders angegeben. Vorher einmalig:

```bash
git checkout -b feat/ocpp-central-system
cd wallbox-dolibarr
python3 -m venv .venv && . .venv/bin/activate
```

# Phase 1: Kern

## Task 1: Abhängigkeiten, Paketgerüst und idTag-Normalisierung

**Files:**
- Modify: `wallbox-dolibarr/requirements.txt`, `.gitignore` (`.venv/`)
- Create: `wallbox-dolibarr/ocpp_server/__init__.py`, `wallbox-dolibarr/ocpp_server/id_tags.py`, `wallbox-dolibarr/tests/__init__.py` (leer)
- Test: `wallbox-dolibarr/tests/test_ocpp_id_tags.py`

**Interfaces:**
- Produces: `normalize_id_tag(raw) -> str`, `is_whitelisted(id_tag, whitelist) -> bool`

- [ ] **Step 1: Abhängigkeiten ergänzen.** `requirements.txt` am Ende ergänzen:

```
ocpp==2.1.0
websockets==15.0.1
jsonschema>=4.23,<5
```

Dann `pip install -r requirements-dev.txt` ausführen und `.venv/` in `.gitignore` aufnehmen, falls noch nicht vorhanden.

- [ ] **Step 2: Den fehlschlagenden Test schreiben** (`tests/test_ocpp_id_tags.py`):

```python
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ocpp_server.id_tags import is_whitelisted, normalize_id_tag  # noqa: E402


def test_normalize_trims_and_uppercases():
    assert normalize_id_tag("  efcd083e ") == "EFCD083E"


def test_normalize_non_string_is_empty():
    assert normalize_id_tag(None) == ""
    assert normalize_id_tag(1234) == ""


def test_whitelist_match_ignores_case():
    assert is_whitelisted("efcd083e", ["EFCD083E"])
    assert is_whitelisted("EFCD083E", ["efcd083e "])


def test_whitelist_rejects_unknown_empty_and_missing_list():
    assert not is_whitelisted("DEADBEEF", ["EFCD083E"])
    assert not is_whitelisted("", ["", "EFCD083E"])
    assert not is_whitelisted("EFCD083E", None)
```

- [ ] **Step 3: Test ausführen, er muss fehlschlagen.** `python -m pytest tests/test_ocpp_id_tags.py -q`
  Expected: FAIL mit `ModuleNotFoundError: No module named 'ocpp_server'`.

- [ ] **Step 4: Implementieren.** Leere Datei `tests/__init__.py` anlegen (für `from tests.ocpp_sim import …` ab Task 5). Dazu diese beiden Dateien:

`ocpp_server/__init__.py`:

```python
"""OCPP-1.6J-Zentralserver (Central System) für ExpenseCharge.

Aktiv nur bei `session_source: ocpp`. Die Wallbox verbindet sich per
WebSocket direkt mit dem Addon; Start/Ende/Zählerstände kommen aus den
OCPP-Transaktionsnachrichten statt aus Home-Assistant-Sensoren.
"""
```

`ocpp_server/id_tags.py`:

```python
"""OCPP-idTag-Normalisierung und Whitelist-Abgleich.

OCPP 1.6 definiert idTag als CiString20Type (max. 20 Zeichen, Vergleich
OHNE Groß-/Kleinschreibung). Wallboxen liefern dieselbe Karte je nach
Hersteller mal "efcd083e", mal "EFCD083E". Damit der SHA-256-Hash — und
damit die Dolibarr-Zuordnung — stabil bleibt, wird VOR dem Hashen
einheitlich auf Großbuchstaben normalisiert.
"""


def normalize_id_tag(raw) -> str:
    """Trimmt und wandelt in Großbuchstaben; Nicht-Strings → ''."""
    if not isinstance(raw, str):
        return ''
    return raw.strip().upper()


def is_whitelisted(id_tag, whitelist) -> bool:
    """True, wenn der Tag (ohne Groß-/Kleinschreibung) in der Whitelist steht."""
    tag = normalize_id_tag(id_tag)
    if not tag:
        return False
    return any(normalize_id_tag(entry) == tag for entry in (whitelist or []))
```

- [ ] **Step 5: Tests ausführen.** `python -m pytest -q`
  Expected: alle grün (22 bestehende + 4 neue).

- [ ] **Step 6: Commit**

```bash
git add requirements.txt ../.gitignore ocpp_server/__init__.py ocpp_server/id_tags.py tests/__init__.py tests/test_ocpp_id_tags.py
git commit -m "feat: OCPP-Paketgerüst und idTag-Normalisierung"
```

## Task 2: Zählerstände und Zeitstempel

**Files:**
- Create: `wallbox-dolibarr/ocpp_server/meter.py`
- Test: `wallbox-dolibarr/tests/test_ocpp_meter.py`

**Interfaces:**
- Produces: `wh_to_kwh(value) -> Optional[float]`, `extract_energy_kwh(meter_values: list[dict] | None) -> Optional[float]` (erwartet snake_case-Keys `sampled_value`), `to_local_naive_iso(ocpp_timestamp, now: datetime | None = None) -> str`, Konstante `ENERGY_MEASURAND`

- [ ] **Step 1: Den fehlschlagenden Test schreiben** (`tests/test_ocpp_meter.py`). Der Test setzt `TZ=Europe/Berlin`, damit die Umrechnung auf jedem Rechner gleich ist:

```python
import os, sys, time
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from ocpp_server.meter import extract_energy_kwh, to_local_naive_iso, wh_to_kwh  # noqa: E402


@pytest.fixture()
def berlin_tz(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Berlin")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _mv(*samples):
    return [{"timestamp": "2026-09-30T10:00:00Z", "sampled_value": list(samples)}]


def test_wh_to_kwh():
    assert wh_to_kwh(1234567) == pytest.approx(1234.567)
    assert wh_to_kwh(None) is None
    assert wh_to_kwh("abc") is None


def test_energy_default_unit_is_wh():
    assert extract_energy_kwh(_mv({"value": "12500"})) == pytest.approx(12.5)


def test_energy_kwh_unit():
    assert extract_energy_kwh(_mv({"value": "12.5", "unit": "kWh",
                                   "measurand": "Energy.Active.Import.Register"})) == pytest.approx(12.5)


def test_energy_ignores_other_measurands_phases_and_signed():
    samples = _mv(
        {"value": "7400", "unit": "W", "measurand": "Power.Active.Import"},
        {"value": "4000", "phase": "L1"},
        {"value": "AB12", "format": "SignedData"},
        {"value": "3.2", "unit": "kWh"},
    )
    assert extract_energy_kwh(samples) == pytest.approx(3.2)


def test_energy_none_when_missing_or_garbage():
    assert extract_energy_kwh(None) is None
    assert extract_energy_kwh(_mv({"value": "n/a"})) is None
    assert extract_energy_kwh(_mv({"value": "5", "unit": "Percent"})) is None


def test_timestamp_utc_to_local(berlin_tz):
    now = datetime(2026, 9, 30, 13, 0, 0)
    assert to_local_naive_iso("2026-09-30T10:15:30.123Z", now=now) == "2026-09-30T12:15:30"
    assert to_local_naive_iso("2026-09-30T10:15:30+00:00", now=now) == "2026-09-30T12:15:30"


def test_timestamp_without_zone_is_utc(berlin_tz):
    now = datetime(2026, 9, 30, 13, 0, 0)
    assert to_local_naive_iso("2026-09-30T10:15:30", now=now) == "2026-09-30T12:15:30"


def test_timestamp_implausible_falls_back_to_now(berlin_tz):
    now = datetime(2026, 9, 30, 13, 0, 0)
    assert to_local_naive_iso("1970-01-01T00:00:00Z", now=now) == "2026-09-30T13:00:00"
    assert to_local_naive_iso("2027-01-01T00:00:00Z", now=now) == "2026-09-30T13:00:00"
    assert to_local_naive_iso("kaputt", now=now) == "2026-09-30T13:00:00"
    assert to_local_naive_iso(None, now=now) == "2026-09-30T13:00:00"
```

- [ ] **Step 2: Test ausführen, er muss fehlschlagen.** `python -m pytest tests/test_ocpp_meter.py -q`
  Expected: FAIL (`No module named 'ocpp_server.meter'`).

- [ ] **Step 3: Implementieren** (`ocpp_server/meter.py`):

```python
"""Umrechnung von OCPP-Zählerständen und -Zeitstempeln.

OCPP 1.6: meterStart/meterStop sind Integer in Wh. MeterValues tragen eine
optionale Einheit (Default Wh) und einen optionalen Measurand (Default
Energy.Active.Import.Register). Zeitstempel SOLLEN UTC sein; das Addon
speichert — wie der bestehende Code — lokale, naive ISO-Zeitstempel.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

ENERGY_MEASURAND = 'Energy.Active.Import.Register'
_MIN_PLAUSIBLE_YEAR = 2020
_MAX_CLOCK_SKEW = timedelta(days=1)


def wh_to_kwh(value) -> Optional[float]:
    """Wh → kWh; None bei nicht-numerischem Wert."""
    try:
        return float(value) / 1000.0
    except (TypeError, ValueError):
        return None


def extract_energy_kwh(meter_values) -> Optional[float]:
    """Letzter Gesamt-Zählerstand in kWh aus einer MeterValue-Liste.

    Erwartet die snake_case-Keys, wie sie die ocpp-Bibliothek an die Handler
    übergibt (`sampled_value`). Berücksichtigt nur den Summenwert (ohne
    `phase`), ignoriert signierte Werte und unbekannte Einheiten.
    """
    result = None
    for meter_value in meter_values or []:
        for sample in meter_value.get('sampled_value') or []:
            if sample.get('measurand', ENERGY_MEASURAND) != ENERGY_MEASURAND:
                continue
            if sample.get('phase') or sample.get('format') == 'SignedData':
                continue
            try:
                value = float(sample.get('value'))
            except (TypeError, ValueError):
                continue
            unit = sample.get('unit', 'Wh')
            if unit == 'kWh':
                result = value
            elif unit == 'Wh':
                result = value / 1000.0
    return result


def to_local_naive_iso(ocpp_timestamp, now: Optional[datetime] = None) -> str:
    """OCPP-Zeitstempel → lokaler, naiver ISO-String (Sekundengenauigkeit).

    Zeitstempel ohne Zeitzone gelten als UTC (OCPP-Vorgabe). Unlesbare oder
    unplausible Werte (Uhr der Wallbox nicht gestellt: Jahr < 2020, oder mehr
    als 1 Tag in der Zukunft) → aktuelle Addon-Zeit.
    """
    now = now or datetime.now()
    fallback = now.replace(microsecond=0).isoformat()
    try:
        parsed = datetime.fromisoformat(str(ocpp_timestamp))
    except ValueError:
        return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    local = parsed.astimezone().replace(tzinfo=None)
    if local.year < _MIN_PLAUSIBLE_YEAR or local > now + _MAX_CLOCK_SKEW:
        return fallback
    return local.replace(microsecond=0).isoformat()
```

- [ ] **Step 4: Tests ausführen.** `python -m pytest -q`
  Expected: alle grün.

- [ ] **Step 5: Commit**

```bash
git add ocpp_server/meter.py tests/test_ocpp_meter.py
git commit -m "feat: OCPP-Zählerstände (Wh/kWh) und Zeitstempel umrechnen"
```

## Task 3: OCPP-Transaktionen im SessionManager

**Files:**
- Modify: `wallbox-dolibarr/session_manager.py`: Konstante oben, Migrationsliste in `_init_database()` (`:83-93`), neuer Index, neue Methoden am Klassenende, Modulfunktion `_classify_ocpp_energy` am Dateiende
- Test: `wallbox-dolibarr/tests/test_session_manager_ocpp.py`

**Interfaces:**
- Produces (auf `SessionManager`):
  - `start_ocpp_transaction(rfid_hex: str, wallbox_id: str, charge_point_id: str, connector_id: int, meter_start_kwh: float, start_time: str, ocpp_start_timestamp: str) -> int`
  - `update_ocpp_meter(transaction_id: int, charge_point_id: str, meter_kwh: float) -> bool`
  - `stop_ocpp_transaction(transaction_id: int, charge_point_id: str, meter_stop_kwh: Optional[float], end_time: str, reason: str, min_kwh: float = 0.05) -> Optional[dict]` (Dict nur bei `completed`, mit den Keys `id`, `rfid_hash`, `wallbox_id`, `start_time`, `end_time`, `start_energy_kwh`, `end_energy_kwh`, `total_kwh`)
  - `get_active_ocpp_sessions() -> list[dict]`
- Neue Spalten in `sessions`: `charge_point_id TEXT`, `connector_id INTEGER`, `ocpp_start_timestamp TEXT`, `last_meter_kwh REAL`, `stop_reason TEXT` (bei HA-Sessions NULL)

- [ ] **Step 1: Den fehlschlagenden Test schreiben** (`tests/test_session_manager_ocpp.py`):

```python
"""OCPP-Transaktionen im SessionManager (session_source: ocpp)."""
import os, sqlite3, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from session_manager import SessionManager  # noqa: E402
from utils.hash import hash_rfid  # noqa: E402


@pytest.fixture()
def sm(tmp_path):
    return SessionManager(db_path=str(tmp_path / "sessions.db"))


def _start(sm, cp="CP1", connector=1, meter=1000.0, ts="2026-09-30T08:00:00Z",
           start_time="2026-09-30T10:00:00"):
    return sm.start_ocpp_transaction(rfid_hex="EFCD083E", wallbox_id="garage", charge_point_id=cp,
                                     connector_id=connector, meter_start_kwh=meter,
                                     start_time=start_time, ocpp_start_timestamp=ts)


def _row(sm, session_id):
    conn = sqlite3.connect(sm.db_path)
    conn.row_factory = sqlite3.Row
    row = dict(conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone())
    conn.close()
    return row


def test_start_and_stop_completed(sm):
    tx = _start(sm)
    done = sm.stop_ocpp_transaction(tx, "CP1", 1012.5, "2026-09-30T12:00:00", "EVDisconnected")
    assert done["total_kwh"] == pytest.approx(12.5)
    assert done["wallbox_id"] == "garage"
    assert done["rfid_hash"] == hash_rfid("EFCD083E")
    row = _row(sm, tx)
    assert row["status"] == "completed"
    assert row["stop_reason"] == "EVDisconnected"
    assert row["transmitted_at"] is None


def test_start_is_idempotent_for_repeated_message(sm):
    assert _start(sm) == _start(sm)
    assert len(sm.get_active_ocpp_sessions()) == 1


def test_parallel_sessions_on_two_charge_points(sm):
    a = _start(sm, cp="CP1")
    b = _start(sm, cp="CP2")
    assert a != b
    assert {s["charge_point_id"] for s in sm.get_active_ocpp_sessions()} == {"CP1", "CP2"}


def test_new_start_on_same_connector_supersedes_lost_stop(sm):
    old = _start(sm, ts="2026-09-30T08:00:00Z")
    new = _start(sm, ts="2026-09-30T09:00:00Z")
    assert _row(sm, old)["status"] == "incomplete"
    assert _row(sm, old)["stop_reason"] == "ocpp_superseded"
    assert _row(sm, new)["status"] == "active"


def test_stop_is_idempotent(sm):
    tx = _start(sm)
    assert sm.stop_ocpp_transaction(tx, "CP1", 1010.0, "2026-09-30T12:00:00", "Local") is not None
    assert sm.stop_ocpp_transaction(tx, "CP1", 1020.0, "2026-09-30T13:00:00", "Local") is None
    assert _row(sm, tx)["total_kwh"] == pytest.approx(10.0)


def test_stop_unknown_or_foreign_transaction_is_ignored(sm):
    tx = _start(sm, cp="CP1")
    assert sm.stop_ocpp_transaction(999, "CP1", 1010.0, "2026-09-30T12:00:00", "Local") is None
    assert sm.stop_ocpp_transaction(tx, "CP2", 1010.0, "2026-09-30T12:00:00", "Local") is None
    assert _row(sm, tx)["status"] == "active"


def test_stop_without_usable_meter_uses_last_meter_value(sm):
    tx = _start(sm)
    assert sm.update_ocpp_meter(tx, "CP1", 1007.25)
    done = sm.stop_ocpp_transaction(tx, "CP1", 0.0, "2026-09-30T12:00:00", "Local")
    assert done["total_kwh"] == pytest.approx(7.25)


def test_stop_without_any_meter_is_incomplete(sm):
    tx = _start(sm)
    assert sm.stop_ocpp_transaction(tx, "CP1", None, "2026-09-30T12:00:00", "PowerLoss") is None
    assert _row(sm, tx)["status"] == "incomplete"


def test_meter_updates_only_monotonic_and_active(sm):
    tx = _start(sm)
    assert sm.update_ocpp_meter(tx, "CP1", 1005.0)
    assert not sm.update_ocpp_meter(tx, "CP1", 1004.0)
    assert not sm.update_ocpp_meter(tx, "CP1", 999.0)
    assert not sm.update_ocpp_meter(tx, "CP2", 1006.0)


def test_small_session_discarded(sm):
    tx = _start(sm)
    assert sm.stop_ocpp_transaction(tx, "CP1", 1000.01, "2026-09-30T10:05:00", "Local") is None
    row = _row(sm, tx)
    assert row["status"] == "discarded"
    assert row["transmitted_at"] is not None


def test_implausible_energy_is_incomplete(sm):
    # 500 kWh in 1 h = 500 kW → Einheiten-/Zählerfehler, nicht abrechnen
    tx = _start(sm, start_time="2026-09-30T10:00:00")
    assert sm.stop_ocpp_transaction(tx, "CP1", 1500.0, "2026-09-30T11:00:00", "Local") is None
    assert _row(sm, tx)["status"] == "incomplete"


def test_ocpp_session_is_transmitted(sm):
    class _Api:
        def __init__(self):
            self.sent = []

        def transmit_session(self, data):
            self.sent.append(data)
            return True, "ok"

    tx = _start(sm)
    sm.stop_ocpp_transaction(tx, "CP1", 1012.5, "2026-09-30T12:00:00", "Local")
    api = _Api()
    result = sm.transmit_completed_sessions(api)
    assert result["transmitted"] == 1
    assert api.sent[0]["wallbox_id"] == "garage"
    assert api.sent[0]["rfid_hash"] == hash_rfid("EFCD083E")


def test_legacy_ha_path_unaffected(sm):
    sid = sm.start_session("EFCD083E", 100.0, wallbox_id="alfen_eve")
    assert sm.get_active_ocpp_sessions() == []
    assert sm.end_session(110.0)["total_kwh"] == pytest.approx(10.0)
    assert _row(sm, sid)["charge_point_id"] is None
```

- [ ] **Step 2: Test ausführen, er muss fehlschlagen.** `python -m pytest tests/test_session_manager_ocpp.py -q`
  Expected: FAIL (`AttributeError: 'SessionManager' object has no attribute 'start_ocpp_transaction'`).

- [ ] **Step 3: Implementieren.** Die Änderung als exakter Diff gegen c951a30. Bestehende Methoden bleiben unverändert:

```diff
--- orig/session_manager.py
+++ val/session_manager.py
@@ -20,6 +20,11 @@
 from utils.hash import hash_rfid, verify_rfid_hash
 
 # Debounce-Zeit in Sekunden (HA-07)
+# OCPP: Plausibilitätsgrenze für die mittlere Ladeleistung einer Session.
+# AC-Wallboxen liefern max. 22 kW; alles deutlich darüber ist ein Einheiten-
+# oder Zählerfehler (z.B. kWh statt Wh gemeldet) und darf nicht abgerechnet werden.
+_MAX_PLAUSIBLE_KW = 50.0
+
 DEBOUNCE_SECONDS = 7
 
 
@@ -86,6 +91,12 @@
             ('ALTER TABLE sessions ADD COLUMN transmitted_at TEXT', 'transmitted_at'),
             ('ALTER TABLE sessions ADD COLUMN start_energy_valid INTEGER NOT NULL DEFAULT 1', 'start_energy_valid'),
             ('ALTER TABLE sessions ADD COLUMN login TEXT', 'login'),
+            # OCPP-Betrieb (session_source: ocpp) — bei HA-Sensor-Sessions NULL
+            ('ALTER TABLE sessions ADD COLUMN charge_point_id TEXT', 'charge_point_id'),
+            ('ALTER TABLE sessions ADD COLUMN connector_id INTEGER', 'connector_id'),
+            ('ALTER TABLE sessions ADD COLUMN ocpp_start_timestamp TEXT', 'ocpp_start_timestamp'),
+            ('ALTER TABLE sessions ADD COLUMN last_meter_kwh REAL', 'last_meter_kwh'),
+            ('ALTER TABLE sessions ADD COLUMN stop_reason TEXT', 'stop_reason'),
         ]:
             try:
                 cursor.execute(col_ddl)
@@ -116,6 +127,12 @@
             CREATE INDEX IF NOT EXISTS idx_status ON sessions(status)
         ''')
 
+        # OCPP: Duplikaterkennung wiederholter StartTransaction-Nachrichten
+        cursor.execute('''
+            CREATE INDEX IF NOT EXISTS idx_ocpp_start
+            ON sessions(charge_point_id, connector_id, ocpp_start_timestamp)
+        ''')
+
         conn.commit()
         conn.close()
         self._logger.info("SQLite Datenbank initialisiert mit WAL Mode: %s", self.db_path)
@@ -589,3 +606,174 @@
                          result["transmitted"], result["failed"])
 
         return result
+
+    # ------------------------------------------------------------------
+    # OCPP-Transaktionen (session_source: ocpp)
+    #
+    # Unterschiede zum HA-Sensor-Pfad:
+    #   - Mehrere Sessions gleichzeitig aktiv (je Wallbox/Connector eine).
+    #   - Die OCPP-transactionId IST die sessions.id (AUTOINCREMENT → eindeutig
+    #     über Neustarts hinweg). Zugriffe prüfen zusätzlich charge_point_id,
+    #     damit eine alte, gepufferte Nachricht nie eine fremde Session trifft.
+    #   - Zeitstempel und Zählerstände kommen von der Wallbox.
+    # ------------------------------------------------------------------
+
+    def start_ocpp_transaction(self, rfid_hex: str, wallbox_id: str, charge_point_id: str,
+                               connector_id: int, meter_start_kwh: float, start_time: str,
+                               ocpp_start_timestamp: str) -> int:
+        """Legt eine aktive OCPP-Session an und gibt ihre ID (= transactionId) zurück.
+
+        Idempotent: Wiederholt die Wallbox dieselbe StartTransaction (gleiche
+        Wallbox, gleicher Connector, gleicher Original-Zeitstempel), kommt die
+        bereits vergebene ID zurück. Läuft auf dem Connector noch eine ältere
+        aktive Session, hat die Wallbox deren Stop verloren → 'incomplete'.
+        """
+        conn = sqlite3.connect(self.db_path)
+        conn.row_factory = sqlite3.Row
+        try:
+            cur = conn.cursor()
+            cur.execute('''
+                SELECT id FROM sessions
+                WHERE charge_point_id = ? AND connector_id = ? AND ocpp_start_timestamp = ?
+                LIMIT 1
+            ''', (charge_point_id, connector_id, ocpp_start_timestamp))
+            existing = cur.fetchone()
+            if existing:
+                self._logger.info("StartTransaction wiederholt (%s/%s) — bestehende Session #%s",
+                                  charge_point_id, connector_id, existing['id'])
+                return int(existing['id'])
+
+            cur.execute('''
+                UPDATE sessions SET status = 'incomplete', stop_reason = 'ocpp_superseded', end_time = ?
+                WHERE status = 'active' AND charge_point_id = ? AND connector_id = ?
+            ''', (start_time, charge_point_id, connector_id))
+            if cur.rowcount:
+                self._logger.warning("%s/%s: %d ältere aktive Session(s) ohne Stop → incomplete",
+                                     charge_point_id, connector_id, cur.rowcount)
+
+            created_at = datetime.now().replace(microsecond=0).isoformat()
+            cur.execute('''
+                INSERT INTO sessions (rfid_hash, wallbox_id, start_time, start_energy_kwh, status,
+                                      created_at, start_energy_valid, charge_point_id, connector_id,
+                                      ocpp_start_timestamp)
+                VALUES (?, ?, ?, ?, 'active', ?, 1, ?, ?, ?)
+            ''', (hash_rfid(rfid_hex), wallbox_id, start_time, meter_start_kwh, created_at,
+                  charge_point_id, connector_id, ocpp_start_timestamp))
+            conn.commit()
+            session_id = int(cur.lastrowid)
+        finally:
+            conn.close()
+        self._logger.info("OCPP-Session gestartet: #%s (%s/%s, Zähler %.3f kWh)",
+                          session_id, charge_point_id, connector_id, meter_start_kwh)
+        return session_id
+
+    def update_ocpp_meter(self, transaction_id: int, charge_point_id: str, meter_kwh: float) -> bool:
+        """Merkt den letzten Zählerstand einer aktiven OCPP-Session (Fallback fürs Ende).
+
+        Nur monoton steigende Werte >= Startzählerstand werden übernommen.
+        """
+        conn = sqlite3.connect(self.db_path)
+        try:
+            cur = conn.cursor()
+            cur.execute('''
+                UPDATE sessions SET last_meter_kwh = ?
+                WHERE id = ? AND charge_point_id = ? AND status = 'active'
+                  AND ? >= start_energy_kwh
+                  AND (last_meter_kwh IS NULL OR ? >= last_meter_kwh)
+            ''', (meter_kwh, transaction_id, charge_point_id, meter_kwh, meter_kwh))
+            conn.commit()
+            return cur.rowcount > 0
+        finally:
+            conn.close()
+
+    def stop_ocpp_transaction(self, transaction_id: int, charge_point_id: str,
+                              meter_stop_kwh: Optional[float], end_time: str, reason: str,
+                              min_kwh: float = 0.05) -> Optional[Dict[str, Any]]:
+        """Schließt eine OCPP-Session ab. Gibt das Session-Dict NUR bei 'completed' zurück.
+
+        - Unbekannte ID / fremde Wallbox → None (Aufrufer bestätigt trotzdem).
+        - Bereits abgeschlossen (wiederholte StopTransaction) → None, keine Änderung.
+        - meterStop fehlt, ist 0 oder kleiner als der Start → letzter MeterValue.
+        - Kein brauchbarer Endstand oder unplausible Leistung → 'incomplete'.
+        - < min_kwh → 'discarded'.
+        """
+        conn = sqlite3.connect(self.db_path)
+        conn.row_factory = sqlite3.Row
+        try:
+            cur = conn.cursor()
+            cur.execute("SELECT * FROM sessions WHERE id = ? AND charge_point_id = ?",
+                        (transaction_id, charge_point_id))
+            row = cur.fetchone()
+            if row is None:
+                self._logger.warning("StopTransaction für unbekannte Transaktion %s (%s) — ignoriert",
+                                     transaction_id, charge_point_id)
+                return None
+            if row['status'] != 'active':
+                self._logger.info("StopTransaction für bereits abgeschlossene Session #%s — ignoriert",
+                                  transaction_id)
+                return None
+
+            start_kwh = float(row['start_energy_kwh'])
+            end_kwh = meter_stop_kwh
+            if end_kwh is None or end_kwh < start_kwh:
+                last = row['last_meter_kwh']
+                end_kwh = float(last) if last is not None and float(last) >= start_kwh else None
+            status, total_kwh = _classify_ocpp_energy(start_kwh, end_kwh, row['start_time'],
+                                                      end_time, min_kwh)
+            cur.execute('''
+                UPDATE sessions
+                SET end_time = ?, end_energy_kwh = ?, total_kwh = ?, status = ?, stop_reason = ?,
+                    transmitted_at = CASE WHEN ? = 'discarded' THEN ? ELSE transmitted_at END
+                WHERE id = ?
+            ''', (end_time, end_kwh, total_kwh, status, reason, status, end_time, transaction_id))
+            conn.commit()
+        finally:
+            conn.close()
+
+        if status != 'completed':
+            self._logger.warning("OCPP-Session #%s: %s (Ende %s kWh, Grund %s)",
+                                 transaction_id, status, end_kwh, reason)
+            return None
+        self._logger.info("OCPP-Session #%s beendet: %.3f kWh (%s)", transaction_id, total_kwh, reason)
+        return {
+            'id': int(row['id']),
+            'rfid_hash': row['rfid_hash'],
+            'wallbox_id': row['wallbox_id'],
+            'start_time': row['start_time'],
+            'end_time': end_time,
+            'start_energy_kwh': start_kwh,
+            'end_energy_kwh': end_kwh,
+            'total_kwh': total_kwh,
+        }
+
+    def get_active_ocpp_sessions(self) -> list:
+        """Alle aktiven OCPP-Sessions (älteste zuerst)."""
+        conn = sqlite3.connect(self.db_path)
+        conn.row_factory = sqlite3.Row
+        try:
+            cur = conn.cursor()
+            cur.execute('''
+                SELECT * FROM sessions
+                WHERE status = 'active' AND charge_point_id IS NOT NULL
+                ORDER BY start_time ASC
+            ''')
+            return [dict(r) for r in cur.fetchall()]
+        finally:
+            conn.close()
+
+
+def _classify_ocpp_energy(start_kwh: float, end_kwh: Optional[float], start_time: str,
+                          end_time: str, min_kwh: float):
+    """(status, total_kwh) für eine abgeschlossene OCPP-Session."""
+    if end_kwh is None:
+        return 'incomplete', None
+    total_kwh = round(end_kwh - start_kwh, 3)
+    if total_kwh < min_kwh:
+        return 'discarded', max(0.0, total_kwh)
+    try:
+        hours = (datetime.fromisoformat(end_time) - datetime.fromisoformat(start_time)).total_seconds() / 3600.0
+    except (TypeError, ValueError):
+        hours = None
+    if hours is not None and total_kwh > _MAX_PLAUSIBLE_KW * max(hours, 0.0) + 1.0:
+        return 'incomplete', total_kwh
+    return 'completed', total_kwh
```

**Wichtig:** In INSERT wird `last_meter_kwh` **nicht** mit dem Startwert vorbelegt, siehe D6. Andernfalls würde eine Ladung ohne Endwert still als 0 kWh verworfen statt als `incomplete` markiert. `test_stop_without_any_meter_is_incomplete` sichert das ab.

- [ ] **Step 4: Tests ausführen.** `python -m pytest -q`
  Expected: alle grün, insbesondere `test_legacy_ha_path_unaffected` und die 6 bestehenden SessionManager-Tests.

- [ ] **Step 5: Migration auf einer echten Bestands-DB prüfen.** Eine mit dem alten Code erzeugte DB öffnen:

```bash
python -c "from session_manager import SessionManager; SessionManager('/tmp/copy-of-sessions.db')"
sqlite3 /tmp/copy-of-sessions.db "PRAGMA table_info(sessions);" | grep -E "charge_point_id|last_meter_kwh"
```

Expected: beide Spalten vorhanden, alte Zeilen unverändert (`SELECT count(*)` vorher und nachher gleich).

- [ ] **Step 6: Commit**

```bash
git add session_manager.py tests/test_session_manager_ocpp.py
git commit -m "feat: OCPP-Transaktionen im SessionManager (idempotent, parallel, mit Zähler-Fallback)"
```

## Task 4: OCPP-Konfiguration auflösen

**Files:**
- Create: `wallbox-dolibarr/ocpp_server/settings.py`
- Test: `wallbox-dolibarr/tests/test_ocpp_settings.py`

**Interfaces:**
- Produces: `ChargePointConfig(id: str, password: str, wallbox_id: str)` (frozen), `OcppSettings(enabled: bool, charge_points: tuple[ChargePointConfig, ...], heartbeat_interval: int, apply_recommended_config: bool)` mit `.find(cp_id) -> Optional[ChargePointConfig]`, `resolve_ocpp_settings(config: dict) -> OcppSettings`, `sanitize_wallbox_id(raw) -> str`, `DEFAULT_HEARTBEAT_INTERVAL = 300`
- Konfigurationsschlüssel (flach, wie bestehende Optionen): `session_source`, `ocpp_charge_points: [{id, password?, wallbox_id?}]`, `ocpp_heartbeat_interval` (30–3600), `ocpp_apply_recommended_config`

- [ ] **Step 1: Den fehlschlagenden Test schreiben** (`tests/test_ocpp_settings.py`):

```python
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ocpp_server.settings import resolve_ocpp_settings, sanitize_wallbox_id  # noqa: E402


def test_defaults_disabled():
    s = resolve_ocpp_settings({})
    assert s.enabled is False
    assert s.charge_points == ()
    assert s.heartbeat_interval == 300
    assert s.apply_recommended_config is False


def test_enabled_with_charge_points():
    s = resolve_ocpp_settings({
        "session_source": "ocpp",
        "ocpp_charge_points": [
            {"id": "ACE0123456", "password": "0123456789abcdef", "wallbox_id": "garage"},
            {"id": "CP 2/links"},
        ],
    })
    assert s.enabled is True
    assert s.find("ACE0123456").wallbox_id == "garage"
    assert s.find("ACE0123456").password == "0123456789abcdef"
    assert s.find("CP 2/links").wallbox_id == "CP_2_links"
    assert s.find("CP 2/links").password == ""
    assert s.find("unbekannt") is None


def test_invalid_entries_skipped_and_duplicates_first_wins():
    s = resolve_ocpp_settings({"ocpp_charge_points": [
        {"id": ""}, "kaputt", {"id": "A", "wallbox_id": "erste"}, {"id": "A", "wallbox_id": "zweite"}]})
    assert [c.id for c in s.charge_points] == ["A"]
    assert s.find("A").wallbox_id == "erste"


def test_heartbeat_clamped():
    assert resolve_ocpp_settings({"ocpp_heartbeat_interval": 5}).heartbeat_interval == 30
    assert resolve_ocpp_settings({"ocpp_heartbeat_interval": 99999}).heartbeat_interval == 3600
    assert resolve_ocpp_settings({"ocpp_heartbeat_interval": "x"}).heartbeat_interval == 300


def test_sanitize_wallbox_id():
    assert sanitize_wallbox_id("Alfen Eve #1") == "Alfen_Eve__1"
    assert sanitize_wallbox_id("x" * 80) == "x" * 50
    assert sanitize_wallbox_id("") == "wallbox"
```

- [ ] **Step 2: Test ausführen, er muss fehlschlagen.** `python -m pytest tests/test_ocpp_settings.py -q`
  Expected: FAIL (Modul fehlt).

- [ ] **Step 3: Implementieren** (`ocpp_server/settings.py`):

```python
"""Auflösung der OCPP-Konfiguration aus /data/options.json."""
import logging
import re
from dataclasses import dataclass
from typing import Optional, Tuple

_LOGGER = logging.getLogger(__name__)

DEFAULT_HEARTBEAT_INTERVAL = 300
_MIN_HEARTBEAT, _MAX_HEARTBEAT = 30, 3600
_MIN_PASSWORD_LEN = 16          # OCPP-1.6-Security-Whitepaper: AuthorizationKey >= 16 Byte
_WALLBOX_ID_INVALID = re.compile(r'[^\w\-.]')   # receive.php: ^[\w\-\.]{1,50}$


@dataclass(frozen=True)
class ChargePointConfig:
    id: str            # Charge-Point-Identity = letztes Segment der Verbindungs-URL
    password: str      # '' = ohne Basic Auth (Security Profile 0)
    wallbox_id: str    # Wert, der als wallbox_id an Dolibarr geht


@dataclass(frozen=True)
class OcppSettings:
    enabled: bool
    charge_points: Tuple[ChargePointConfig, ...]
    heartbeat_interval: int
    apply_recommended_config: bool

    def find(self, cp_id: str) -> Optional[ChargePointConfig]:
        for cp in self.charge_points:
            if cp.id == cp_id:
                return cp
        return None


def sanitize_wallbox_id(raw) -> str:
    cleaned = _WALLBOX_ID_INVALID.sub('_', str(raw or '').strip())[:50]
    return cleaned or 'wallbox'


def _heartbeat(raw) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_HEARTBEAT_INTERVAL
    return min(max(value, _MIN_HEARTBEAT), _MAX_HEARTBEAT)


def resolve_ocpp_settings(config: dict) -> OcppSettings:
    charge_points = []
    seen = set()
    for entry in config.get('ocpp_charge_points') or []:
        if not isinstance(entry, dict):
            continue
        cp_id = str(entry.get('id') or '').strip()
        if not cp_id:
            _LOGGER.warning("ocpp_charge_points: Eintrag ohne 'id' wird ignoriert")
            continue
        if cp_id in seen:
            _LOGGER.warning("ocpp_charge_points: doppelte id '%s' — nur der erste Eintrag zählt", cp_id)
            continue
        seen.add(cp_id)
        password = str(entry.get('password') or '')
        if not password:
            _LOGGER.warning("Wallbox '%s' ohne Passwort (Security Profile 0) — nur in "
                            "vertrauenswürdigem LAN/VPN betreiben", cp_id)
        elif len(password) < _MIN_PASSWORD_LEN:
            _LOGGER.warning("Wallbox '%s': Passwort kürzer als %d Zeichen — manche Wallboxen "
                            "lehnen das ab", cp_id, _MIN_PASSWORD_LEN)
        charge_points.append(ChargePointConfig(
            id=cp_id,
            password=password,
            wallbox_id=sanitize_wallbox_id(entry.get('wallbox_id') or cp_id),
        ))
    return OcppSettings(
        enabled=config.get('session_source', 'ha_sensors') == 'ocpp',
        charge_points=tuple(charge_points),
        heartbeat_interval=_heartbeat(config.get('ocpp_heartbeat_interval', DEFAULT_HEARTBEAT_INTERVAL)),
        apply_recommended_config=bool(config.get('ocpp_apply_recommended_config', False)),
    )
```

- [ ] **Step 4: Tests ausführen.** `python -m pytest -q`
  Expected: alle grün.

- [ ] **Step 5: Commit**

```bash
git add ocpp_server/settings.py tests/test_ocpp_settings.py
git commit -m "feat: OCPP-Konfiguration (Wallbox-Liste, Heartbeat, empfohlene Einstellungen)"
```

## Task 5: OCPP-Nachrichten-Handler (CentralSystemChargePoint)

**Files:**
- Create: `wallbox-dolibarr/ocpp_server/central_system.py`, `wallbox-dolibarr/tests/ocpp_sim.py`
- Test: `wallbox-dolibarr/tests/test_ocpp_central_system.py` (Teil "Nachrichten"; die Tests unter `# ---- Verbindungsannahme` werden erst in Task 6 grün)

**Interfaces:**
- Consumes: `normalize_id_tag`, `is_whitelisted` (Task 1); `extract_energy_kwh`, `to_local_naive_iso`, `wh_to_kwh` (Task 2); SessionManager-OCPP-Methoden (Task 3); `ChargePointConfig` (Task 4); `utils.hash.hash_rfid`
- Produces:
  - `CentralSystemDeps(session_manager, whitelist, live: dict, min_kwh=0.05, heartbeat_interval=300, apply_recommended_config=False, on_session_completed: Callable[[dict], None] | None = None)` (Dataclass, veränderbar)
  - `CentralSystemChargePoint(cp_config: ChargePointConfig, connection, deps: CentralSystemDeps)`
  - Konstanten `REJECTED_TRANSACTION_ID = 0`, `RECOMMENDED_CONFIGURATION`
  - Live-Zustand je Wallbox in `deps.live[cp_id]`: `{connected, wallbox_id, vendor, model, firmware, last_seen, last_rejected_id_tag, connectors: {"<connectorId>": {status, error_code, energy_kwh, transaction_id}}}`
  - Behandelte Actions: BootNotification (+ `@after` für D10), Heartbeat, StatusNotification, Authorize, StartTransaction, StopTransaction, MeterValues, DataTransfer (→ UnknownVendorId), FirmwareStatusNotification, DiagnosticsStatusNotification

- [ ] **Step 1: Simulator und fehlschlagende Tests schreiben.** Die Testdatei deckt Task 5 und Task 6 ab; beide brauchen denselben Fixture-Server.

`tests/ocpp_sim.py`:

```python
"""Simulierte Wallbox (OCPP-1.6J-Client) für Integrationstests."""
import asyncio
import base64
import logging
from contextlib import asynccontextmanager

import websockets
from ocpp.routing import on
from ocpp.v16 import ChargePoint, call_result
from ocpp.v16.enums import Action, ConfigurationStatus


class SimChargePoint(ChargePoint):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.config_changes = []

    @on(Action.change_configuration)
    async def on_change_configuration(self, key, value):
        self.config_changes.append((key, value))
        return call_result.ChangeConfiguration(status=ConfigurationStatus.accepted)


def basic_auth_header(user: str, password: str) -> dict:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@asynccontextmanager
async def connect_sim(port: int, cp_id: str, password: str = None, subprotocols=("ocpp1.6",)):
    headers = basic_auth_header(cp_id, password) if password else {}
    async with websockets.connect(f"ws://127.0.0.1:{port}/ocpp/{cp_id}",
                                  subprotocols=list(subprotocols) or None,
                                  additional_headers=headers) as ws:
        cp = SimChargePoint(cp_id, ws, logger=logging.getLogger("ocpp_sim"))
        task = asyncio.create_task(cp.start())
        try:
            yield cp
        finally:
            task.cancel()
```

`tests/test_ocpp_central_system.py`:

```python
"""Integrationstests: simulierte Wallbox ↔ echter OCPP-Server (WebSocket auf Port 0)."""
import asyncio, os, sqlite3, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
import websockets  # noqa: E402
from ocpp.v16 import call  # noqa: E402

from ocpp_server.central_system import CentralSystemDeps  # noqa: E402
from ocpp_server.server import OcppServer, charge_point_id_from_path, parse_basic_auth  # noqa: E402
from ocpp_server.settings import resolve_ocpp_settings  # noqa: E402
from session_manager import SessionManager  # noqa: E402
from tests.ocpp_sim import connect_sim  # noqa: E402

PASSWORD = "0123456789abcdef"


def _ts(hours_ago: float = 0.0):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


@pytest.fixture()
async def env(tmp_path):
    settings = resolve_ocpp_settings({
        "session_source": "ocpp",
        "ocpp_charge_points": [{"id": "CP1", "password": PASSWORD, "wallbox_id": "garage"},
                               {"id": "OPEN"}],
    })
    completed = []
    deps = CentralSystemDeps(session_manager=SessionManager(db_path=str(tmp_path / "s.db")),
                             whitelist=["EFCD083E"], live={}, on_session_completed=completed.append)
    server = OcppServer(settings, deps)
    port = await server.start("127.0.0.1", 0)
    yield {"port": port, "deps": deps, "completed": completed, "server": server}
    await server.close()


def _status(result):
    return result.id_tag_info["status"]


async def test_full_charging_session(env):
    async with connect_sim(env["port"], "CP1", PASSWORD) as cp:
        boot = await cp.call(call.BootNotification(charge_point_vendor="Alfen BV", charge_point_model="NG910"))
        assert boot.status == "Accepted"
        assert _status(await cp.call(call.Authorize(id_tag="efcd083e"))) == "Accepted"
        start = await cp.call(call.StartTransaction(connector_id=1, id_tag="efcd083e",
                                                    meter_start=1_000_000, timestamp=_ts(hours_ago=2)))
        assert _status(start) == "Accepted" and start.transaction_id > 0
        await cp.call(call.MeterValues(connector_id=1, transaction_id=start.transaction_id, meter_value=[
            {"timestamp": _ts(), "sampledValue": [{"value": "1005000"}]}]))
        await cp.call(call.StopTransaction(transaction_id=start.transaction_id, meter_stop=1_012_500,
                                           timestamp=_ts(), reason="EVDisconnected"))
    assert len(env["completed"]) == 1
    assert env["completed"][0]["total_kwh"] == pytest.approx(12.5)
    assert env["completed"][0]["wallbox_id"] == "garage"
    live = env["deps"].live["CP1"]
    assert live["vendor"] == "Alfen BV"
    assert live["connectors"]["1"]["energy_kwh"] == pytest.approx(1005.0)
    await asyncio.sleep(0.05)
    assert live["connected"] is False


async def test_unknown_tag_rejected_and_never_billed(env):
    async with connect_sim(env["port"], "CP1", PASSWORD) as cp:
        assert _status(await cp.call(call.Authorize(id_tag="DEADBEEF"))) == "Invalid"
        start = await cp.call(call.StartTransaction(connector_id=1, id_tag="DEADBEEF",
                                                    meter_start=0, timestamp=_ts()))
        assert start.transaction_id == 0 and _status(start) == "Invalid"
        stop = await cp.call(call.StopTransaction(transaction_id=0, meter_stop=5000, timestamp=_ts(),
                                                  reason="DeAuthorized"))
        assert stop is not None
    assert env["completed"] == []
    assert env["deps"].live["CP1"]["last_rejected_id_tag"] == "DEADBEEF"
    assert env["deps"].session_manager.get_active_ocpp_sessions() == []


async def test_start_without_prior_authorize_still_checked(env):
    async with connect_sim(env["port"], "CP1", PASSWORD) as cp:
        start = await cp.call(call.StartTransaction(connector_id=1, id_tag="EFCD083E",
                                                    meter_start=0, timestamp=_ts()))
        assert start.transaction_id > 0


async def test_repeated_start_after_reconnect_gets_same_id(env):
    ts = _ts()
    async with connect_sim(env["port"], "CP1", PASSWORD) as cp:
        first = await cp.call(call.StartTransaction(connector_id=1, id_tag="EFCD083E", meter_start=0, timestamp=ts))
    async with connect_sim(env["port"], "CP1", PASSWORD) as cp:
        again = await cp.call(call.StartTransaction(connector_id=1, id_tag="EFCD083E", meter_start=0, timestamp=ts))
    assert first.transaction_id == again.transaction_id


async def test_stop_for_unknown_transaction_is_acknowledged(env):
    async with connect_sim(env["port"], "CP1", PASSWORD) as cp:
        stop = await cp.call(call.StopTransaction(transaction_id=4711, meter_stop=1, timestamp=_ts()))
        assert stop is not None
    assert env["completed"] == []


async def test_stop_uses_transaction_data_when_meter_stop_zero(env):
    async with connect_sim(env["port"], "CP1", PASSWORD) as cp:
        start = await cp.call(call.StartTransaction(connector_id=1, id_tag="EFCD083E",
                                                    meter_start=2_000_000, timestamp=_ts(hours_ago=2)))
        await cp.call(call.StopTransaction(
            transaction_id=start.transaction_id, meter_stop=0, timestamp=_ts(),
            transaction_data=[{"timestamp": _ts(), "sampledValue": [
                {"value": "2008.4", "unit": "kWh", "context": "Transaction.End"}]}]))
    assert env["completed"][0]["total_kwh"] == pytest.approx(8.4)


async def test_status_heartbeat_datatransfer_answered(env):
    async with connect_sim(env["port"], "CP1", PASSWORD) as cp:
        assert (await cp.call(call.Heartbeat())).current_time
        await cp.call(call.StatusNotification(connector_id=1, error_code="NoError", status="Charging"))
        dt = await cp.call(call.DataTransfer(vendor_id="com.alfen", message_id="x", data="y"))
        assert dt.status == "UnknownVendorId"
    assert env["deps"].live["CP1"]["connectors"]["1"]["status"] == "Charging"


async def test_recommended_config_pushed_after_boot_when_enabled(env):
    env["deps"].apply_recommended_config = True
    async with connect_sim(env["port"], "CP1", PASSWORD) as cp:
        await cp.call(call.BootNotification(charge_point_vendor="v", charge_point_model="m"))
        await asyncio.sleep(0.2)
        assert ("StopTransactionOnInvalidId", "true") in cp.config_changes


async def test_recommended_config_not_pushed_by_default(env):
    async with connect_sim(env["port"], "CP1", PASSWORD) as cp:
        await cp.call(call.BootNotification(charge_point_vendor="v", charge_point_model="m"))
        await asyncio.sleep(0.2)
        assert cp.config_changes == []


async def test_id_tag_plaintext_never_logged(env, caplog):
    caplog.set_level("DEBUG")
    async with connect_sim(env["port"], "CP1", PASSWORD) as cp:
        await cp.call(call.Authorize(id_tag="c0ffee42"))
        await cp.call(call.StartTransaction(connector_id=1, id_tag="efcd083e", meter_start=0, timestamp=_ts(1)))
    # Alles außer der simulierten Wallbox selbst (ihr ocpp- und websockets-Client-Log)
    server_side = [r for r in caplog.records if r.name not in ("ocpp_sim", "websockets.client")]
    text = "\n".join(r.getMessage() for r in server_side).upper()
    assert "EFCD083E" not in text and "C0FFEE42" not in text


# ---- Verbindungsannahme --------------------------------------------------

async def test_unknown_charge_point_rejected(env):
    with pytest.raises(websockets.InvalidStatus) as exc:
        async with connect_sim(env["port"], "FREMD"):
            pass
    assert exc.value.response.status_code == 404


async def test_wrong_password_rejected(env):
    with pytest.raises(websockets.InvalidStatus) as exc:
        async with connect_sim(env["port"], "CP1", "falsch-falsch-falsch"):
            pass
    assert exc.value.response.status_code == 401


async def test_missing_password_rejected(env):
    with pytest.raises(websockets.InvalidStatus) as exc:
        async with connect_sim(env["port"], "CP1"):
            pass
    assert exc.value.response.status_code == 401


async def test_open_charge_point_without_password(env):
    async with connect_sim(env["port"], "OPEN") as cp:
        assert (await cp.call(call.Heartbeat())).current_time


async def test_missing_subprotocol_tolerated(env):
    # Manche Wallboxen senden keinen Sec-WebSocket-Protocol-Header → als 1.6 behandeln
    async with connect_sim(env["port"], "OPEN", subprotocols=()) as cp:
        assert (await cp.call(call.Heartbeat())).current_time


async def test_foreign_subprotocol_rejected(env):
    with pytest.raises(websockets.InvalidStatus) as exc:
        async with connect_sim(env["port"], "OPEN", subprotocols=("ocpp2.0.1",)):
            pass
    assert exc.value.response.status_code == 400


async def test_reconnect_replaces_old_connection(env):
    async with connect_sim(env["port"], "OPEN"):
        async with connect_sim(env["port"], "OPEN") as second:
            assert (await second.call(call.Heartbeat())).current_time
            assert len(env["server"].connected) == 1


def test_path_and_auth_parsing():
    assert charge_point_id_from_path("/ocpp/CP001") == "CP001"
    assert charge_point_id_from_path("/CP001/?a=1") == "CP001"
    assert charge_point_id_from_path("/steve/websocket/CentralSystemService/ACE%200001") == "ACE 0001"
    assert charge_point_id_from_path("/") == ""
    assert parse_basic_auth("Basic Q1AxOnNlY3JldA==") == ("CP1", "secret")
    assert parse_basic_auth("Basic !!!") is None
    assert parse_basic_auth("Bearer x") is None
    assert parse_basic_auth(None) is None
```

**Hinweis zu den Zeitstempeln:** Die Tests starten Ladungen "vor 2 h" (`_ts(hours_ago=2)`), weil 12,5 kWh in 0 Sekunden zu Recht an der Leistungsgrenze (D6) scheitern.

- [ ] **Step 2: Tests ausführen, sie müssen fehlschlagen.** `python -m pytest tests/test_ocpp_central_system.py -q`
  Expected: FAIL (`No module named 'ocpp_server.central_system'`).

- [ ] **Step 3: Handler implementieren** (`ocpp_server/central_system.py`):

```python
"""OCPP-1.6J-Nachrichten-Handler je verbundener Wallbox."""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Sequence

from ocpp.routing import after, on
from ocpp.v16 import ChargePoint, call, call_result, datatypes
from ocpp.v16.enums import Action, AuthorizationStatus, DataTransferStatus, RegistrationStatus

from ocpp_server.id_tags import is_whitelisted, normalize_id_tag
from ocpp_server.meter import extract_energy_kwh, to_local_naive_iso, wh_to_kwh
from ocpp_server.settings import ChargePointConfig
from utils.hash import hash_rfid

_LOGGER = logging.getLogger(__name__)

# Die ocpp-Bibliothek loggt jede Nachricht samt Payload auf INFO — inklusive
# idTag im Klartext. RFID-Klartext darf nie ins Log (DSGVO, Projektregel) →
# eigener Protokoll-Logger, fest auf WARNING (Fehler bleiben sichtbar).
_PROTOCOL_LOGGER = logging.getLogger('ocpp.expensecharge')
_PROTOCOL_LOGGER.setLevel(logging.WARNING)

# Abgelehnte Starts bekommen transactionId 0 — eine spätere StopTransaction
# mit dieser ID wird bestätigt, aber nie abgerechnet.
REJECTED_TRANSACTION_ID = 0

# Optional nach dem Boot gesetzt (ocpp_apply_recommended_config: true).
RECOMMENDED_CONFIGURATION = (
    ('MeterValueSampleInterval', '60'),
    ('MeterValuesSampledData', 'Energy.Active.Import.Register'),
    ('StopTransactionOnInvalidId', 'true'),
)


@dataclass
class CentralSystemDeps:
    session_manager: Any
    whitelist: Sequence[str]
    live: dict                        # api_state['charge_points'] — Live-Zustand für die Web-UI
    min_kwh: float = 0.05
    heartbeat_interval: int = 300
    apply_recommended_config: bool = False
    on_session_completed: Optional[Callable[[dict], None]] = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _local_now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


class CentralSystemChargePoint(ChargePoint):
    def __init__(self, cp_config: ChargePointConfig, connection, deps: CentralSystemDeps):
        super().__init__(cp_config.id, connection, logger=_PROTOCOL_LOGGER)
        self._cp = cp_config
        self._deps = deps
        self._state = deps.live.setdefault(cp_config.id, {'connectors': {}, 'last_rejected_id_tag': None})
        self._state.update({'connected': True, 'wallbox_id': cp_config.wallbox_id})
        self._touch()

    def _touch(self) -> None:
        self._state['last_seen'] = _local_now_iso()

    def _connector(self, connector_id) -> dict:
        return self._state['connectors'].setdefault(str(connector_id), {})

    def _authorize(self, id_tag) -> AuthorizationStatus:
        tag = normalize_id_tag(id_tag)
        if is_whitelisted(tag, self._deps.whitelist):
            return AuthorizationStatus.accepted
        # Klartext NUR im flüchtigen Live-Zustand (Ingress-UI, Admin), damit die
        # Karte eingetragen werden kann — im Log nur der Hash-Präfix.
        self._state['last_rejected_id_tag'] = tag
        _LOGGER.warning("[%s] Karte abgelehnt (nicht in rfid_whitelist): %s...",
                        self.id, hash_rfid(tag)[:16])
        return AuthorizationStatus.invalid

    @on(Action.boot_notification)
    async def on_boot_notification(self, charge_point_vendor, charge_point_model, **kwargs):
        self._touch()
        self._state.update({'vendor': charge_point_vendor, 'model': charge_point_model,
                            'firmware': kwargs.get('firmware_version')})
        _LOGGER.info("[%s] BootNotification: %s %s (FW %s)", self.id, charge_point_vendor,
                     charge_point_model, kwargs.get('firmware_version'))
        return call_result.BootNotification(current_time=_utc_now_iso(),
                                            interval=self._deps.heartbeat_interval,
                                            status=RegistrationStatus.accepted)

    @after(Action.boot_notification)
    async def after_boot_notification(self, **kwargs):
        if not self._deps.apply_recommended_config:
            return
        for key, value in RECOMMENDED_CONFIGURATION:
            try:
                result = await self.call(call.ChangeConfiguration(key=key, value=value))
            except Exception as exc:  # Timeout o.ä. — Laden funktioniert trotzdem
                _LOGGER.warning("[%s] ChangeConfiguration %s fehlgeschlagen: %s", self.id, key, exc)
                continue
            _LOGGER.info("[%s] ChangeConfiguration %s=%s → %s", self.id, key, value,
                         getattr(result, 'status', 'CallError'))

    @on(Action.heartbeat)
    async def on_heartbeat(self, **kwargs):
        self._touch()
        return call_result.Heartbeat(current_time=_utc_now_iso())

    @on(Action.status_notification)
    async def on_status_notification(self, connector_id, error_code, status, **kwargs):
        self._touch()
        self._connector(connector_id).update({'status': status, 'error_code': error_code})
        return call_result.StatusNotification()

    @on(Action.authorize)
    async def on_authorize(self, id_tag, **kwargs):
        self._touch()
        return call_result.Authorize(id_tag_info=datatypes.IdTagInfo(status=self._authorize(id_tag)))

    @on(Action.start_transaction)
    async def on_start_transaction(self, connector_id, id_tag, meter_start, timestamp, **kwargs):
        self._touch()
        # OCPP: Der Server MUSS den Tag hier erneut prüfen — die Wallbox kann
        # lokal (Cache/Offline) mit veralteten Daten autorisiert haben.
        status = self._authorize(id_tag)
        if status != AuthorizationStatus.accepted:
            return call_result.StartTransaction(transaction_id=REJECTED_TRANSACTION_ID,
                                                id_tag_info=datatypes.IdTagInfo(status=status))
        tx_id = self._deps.session_manager.start_ocpp_transaction(
            rfid_hex=normalize_id_tag(id_tag),
            wallbox_id=self._cp.wallbox_id,
            charge_point_id=self.id,
            connector_id=connector_id,
            meter_start_kwh=wh_to_kwh(meter_start) or 0.0,
            start_time=to_local_naive_iso(timestamp),
            ocpp_start_timestamp=str(timestamp),
        )
        self._connector(connector_id)['transaction_id'] = tx_id
        return call_result.StartTransaction(transaction_id=tx_id,
                                            id_tag_info=datatypes.IdTagInfo(status=AuthorizationStatus.accepted))

    @on(Action.stop_transaction)
    async def on_stop_transaction(self, transaction_id, meter_stop, timestamp, **kwargs):
        self._touch()
        # OCPP: Ein Stop MUSS immer bestätigt werden — sonst wiederholt die Wallbox ihn endlos.
        if transaction_id == REJECTED_TRANSACTION_ID:
            _LOGGER.warning("[%s] Stop eines abgelehnten Ladevorgangs (Zähler %s Wh) — nicht abgerechnet",
                            self.id, meter_stop)
            return call_result.StopTransaction()
        final_kwh = extract_energy_kwh(kwargs.get('transaction_data'))
        if final_kwh is not None:
            self._deps.session_manager.update_ocpp_meter(transaction_id, self.id, final_kwh)
        completed = self._deps.session_manager.stop_ocpp_transaction(
            transaction_id=transaction_id,
            charge_point_id=self.id,
            meter_stop_kwh=wh_to_kwh(meter_stop),
            end_time=to_local_naive_iso(timestamp),
            reason=kwargs.get('reason') or 'Local',
            min_kwh=self._deps.min_kwh,
        )
        for connector in self._state['connectors'].values():
            if connector.get('transaction_id') == transaction_id:
                connector['transaction_id'] = None
        if completed and self._deps.on_session_completed:
            self._deps.on_session_completed(completed)
        return call_result.StopTransaction()

    @on(Action.meter_values)
    async def on_meter_values(self, connector_id, meter_value, **kwargs):
        self._touch()
        kwh = extract_energy_kwh(meter_value)
        if kwh is not None:
            self._connector(connector_id)['energy_kwh'] = kwh
            tx_id = kwargs.get('transaction_id')
            if tx_id:
                self._deps.session_manager.update_ocpp_meter(tx_id, self.id, kwh)
        return call_result.MeterValues()

    @on(Action.data_transfer)
    async def on_data_transfer(self, vendor_id, **kwargs):
        self._touch()
        return call_result.DataTransfer(status=DataTransferStatus.unknown_vendor_id)

    @on(Action.firmware_status_notification)
    async def on_firmware_status_notification(self, status, **kwargs):
        self._touch()
        return call_result.FirmwareStatusNotification()

    @on(Action.diagnostics_status_notification)
    async def on_diagnostics_status_notification(self, status, **kwargs):
        self._touch()
        return call_result.DiagnosticsStatusNotification()
```

- [ ] **Step 4: Weiter mit Task 6.** Die Fixture braucht `OcppServer`, deshalb werden die Tests erst nach Task 6 ausgeführt. Task 5 und 6 bekommen einen gemeinsamen Commit am Ende von Task 6.

## Task 6: WebSocket-Server (Verbindungsannahme, Basic Auth, Subprotocol)

**Files:**
- Create: `wallbox-dolibarr/ocpp_server/server.py`
- Test: `wallbox-dolibarr/tests/test_ocpp_central_system.py` (aus Task 5)

**Interfaces:**
- Consumes: `CentralSystemChargePoint`, `CentralSystemDeps` (Task 5); `OcppSettings` (Task 4)
- Produces: `OCPP_PORT = 9000`, `charge_point_id_from_path(path) -> str`, `parse_basic_auth(header) -> Optional[tuple[str, str]]`, `select_subprotocol(connection, offered)`, `OcppServer(settings, deps)` mit `async start(host='0.0.0.0', port=OCPP_PORT) -> int` (gebundener Port; `port=0` in Tests), `async serve_forever()`, `async close()`, Attribut `connected: dict[str, connection]`

- [ ] **Step 1: Implementieren** (`ocpp_server/server.py`):

```python
"""WebSocket-Server: nimmt Wallbox-Verbindungen an (Pfad-ID, Basic Auth, Subprotocol)."""
import base64
import binascii
import hmac
import logging
from http import HTTPStatus
from typing import Optional, Tuple
from urllib.parse import unquote, urlsplit

import websockets
from websockets.asyncio.server import serve
from websockets.exceptions import NegotiationError

from ocpp_server.central_system import CentralSystemChargePoint, CentralSystemDeps
from ocpp_server.settings import OcppSettings

_LOGGER = logging.getLogger(__name__)

# websockets loggt auf DEBUG jeden Frame roh (inkl. idTag-Klartext) → eigener
# Logger, fest auf INFO (Verbindungsfehler bleiben sichtbar), auch bei log_level DEBUG.
_WS_LOGGER = logging.getLogger('websockets.expensecharge')
_WS_LOGGER.setLevel(logging.INFO)

OCPP_PORT = 9000              # Container-Port; Host-Port wird in HA unter "Netzwerk" gesetzt
SUBPROTOCOLS = ['ocpp1.6']


def charge_point_id_from_path(path: str) -> str:
    """Letztes Pfadsegment ohne Query: '/ocpp/CP001?x=1' → 'CP001'."""
    raw = urlsplit(path or '').path.rstrip('/')
    return unquote(raw.rsplit('/', 1)[-1]) if raw else ''


def parse_basic_auth(header: Optional[str]) -> Optional[Tuple[str, str]]:
    if not header or not header.lower().startswith('basic '):
        return None
    try:
        decoded = base64.b64decode(header[6:].strip(), validate=True).decode('utf-8')
    except (binascii.Error, UnicodeDecodeError):
        return None
    user, sep, password = decoded.partition(':')
    return (user, password) if sep else None


def select_subprotocol(connection, offered):
    """'ocpp1.6' wählen. Kein Angebot → tolerieren (manche Wallboxen senden den
    Header nicht) und als 1.6 behandeln. Nur fremde Versionen → HTTP 400."""
    if not offered:
        return None
    if 'ocpp1.6' in offered:
        return 'ocpp1.6'
    raise NegotiationError(f"unsupported subprotocols: {', '.join(offered)}")


def _equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode('utf-8'), b.encode('utf-8'))


class OcppServer:
    def __init__(self, settings: OcppSettings, deps: CentralSystemDeps):
        self._settings = settings
        self._deps = deps
        self._server = None
        self.connected = {}   # cp_id → aktuelle Verbindung

    async def _process_request(self, connection, request):
        cp_id = charge_point_id_from_path(request.path)
        cp_cfg = self._settings.find(cp_id)
        if cp_cfg is None:
            _LOGGER.warning("Unbekannte Wallbox '%s' abgewiesen — in ocpp_charge_points eintragen", cp_id)
            return connection.respond(HTTPStatus.NOT_FOUND, "Unknown charge point\n")
        if cp_cfg.password:
            creds = parse_basic_auth(request.headers.get('Authorization'))
            # Security Whitepaper A00.FR.204: Benutzername MUSS die Charge-Point-ID sein
            if creds is None or not (_equal(creds[0], cp_cfg.id) and _equal(creds[1], cp_cfg.password)):
                _LOGGER.warning("Wallbox '%s': falsche oder fehlende Zugangsdaten — abgewiesen", cp_id)
                response = connection.respond(HTTPStatus.UNAUTHORIZED, "Unauthorized\n")
                response.headers['WWW-Authenticate'] = 'Basic realm="ocpp", charset="UTF-8"'
                return response
        return None

    async def _handler(self, connection):
        cp_id = charge_point_id_from_path(connection.request.path)
        cp_cfg = self._settings.find(cp_id)
        if cp_cfg is None:   # durch _process_request eigentlich ausgeschlossen
            await connection.close()
            return
        if connection.subprotocol is None:
            _LOGGER.warning("Wallbox '%s' sendet kein Subprotocol — wird als OCPP 1.6 behandelt", cp_id)
        previous = self.connected.get(cp_id)
        if previous is not None:
            _LOGGER.info("Wallbox '%s' verbindet sich neu — alte Verbindung wird geschlossen", cp_id)
            await previous.close()
        self.connected[cp_id] = connection
        _LOGGER.info("Wallbox '%s' verbunden (%s)", cp_id, connection.remote_address)
        try:
            await CentralSystemChargePoint(cp_cfg, connection, self._deps).start()
        except websockets.ConnectionClosed as exc:
            _LOGGER.info("Wallbox '%s' getrennt (%s)", cp_id, exc)
        finally:
            if self.connected.get(cp_id) is connection:
                del self.connected[cp_id]
                self._deps.live.get(cp_id, {})['connected'] = False

    async def start(self, host: str = '0.0.0.0', port: int = OCPP_PORT) -> int:
        """Startet den Server; gibt den tatsächlich gebundenen Port zurück (port=0 in Tests)."""
        self._server = await serve(self._handler, host, port,
                                   select_subprotocol=select_subprotocol,
                                   process_request=self._process_request,
                                   logger=_WS_LOGGER)
        bound = self._server.sockets[0].getsockname()[1]
        _LOGGER.info("OCPP-Zentralserver lauscht auf Port %d (ws://<ha-host>:<port>/<charge-point-id>)", bound)
        return bound

    async def serve_forever(self) -> None:
        await self._server.serve_forever()

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
```

- [ ] **Step 2: Tests ausführen.** `python -m pytest -q`
  Expected: alle grün (Prototyp: 70 an dieser Stelle).

- [ ] **Step 3: Gegen die neueste websockets-Version prüfen** (im Image ist 15.0.1):

```bash
pip install "websockets==17.1" && python -m pytest -q tests/test_ocpp_central_system.py; pip install "websockets==15.0.1"
```

Expected: grün mit beiden Versionen.

- [ ] **Step 4: Commit**

```bash
git add ocpp_server/central_system.py ocpp_server/server.py tests/ocpp_sim.py tests/test_ocpp_central_system.py
git commit -m "feat: OCPP-1.6J-Zentralserver (Handler, Basic Auth, Subprotocol-Toleranz)"
```

# Phase 2: Integration

## Task 7: Betriebsart session_source: ocpp in main.py

**Files:**
- Modify: `wallbox-dolibarr/main.py`: Imports (`:48`), neues Global `_transmit_requested` (nach `:94`), `periodic_transmission` von der Closure in `main()` (`:769-798`) auf Modulebene verschieben und um das Sofort-Event erweitern, neue Funktionen vor `main()`, Verzweigung in `main()` vor "HA-Token ermitteln" (`:743`)
- Test: `wallbox-dolibarr/tests/test_main_ocpp.py`

**Interfaces:**
- Consumes: `resolve_ocpp_settings` (Task 4), `CentralSystemDeps` (Task 5), `OcppServer`, `OCPP_PORT` (Task 6), `SessionManager.get_active_ocpp_sessions` (Task 3)
- Produces: `main._transmit_requested: asyncio.Event`, `main.periodic_transmission()`, `main.build_ocpp_server(settings) -> OcppServer` (setzt `api_state['charge_points']`), `main.ocpp_overdue_sessions(max_hours, now=None) -> list[dict]`, `main.ocpp_stale_session_guard()`, `main.run_ocpp_mode(settings)`

- [ ] **Step 1: Den fehlschlagenden Test schreiben** (`tests/test_main_ocpp.py`):

```python
"""Verdrahtung der Betriebsart session_source=ocpp in main.py."""
import asyncio, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from ocpp.v16 import call  # noqa: E402

import main  # noqa: E402
from ocpp_server.settings import resolve_ocpp_settings  # noqa: E402
from session_manager import SessionManager  # noqa: E402
from tests.ocpp_sim import connect_sim  # noqa: E402

CONFIG = {"session_source": "ocpp", "rfid_whitelist": ["EFCD083E"],
          "ocpp_charge_points": [{"id": "CP1", "wallbox_id": "garage"}]}


@pytest.fixture()
def ocpp_env(tmp_path):
    main.session_manager = SessionManager(db_path=str(tmp_path / "sessions.db"))
    main.current_config = dict(CONFIG)
    main.api_client = None
    main.api_state = {"client": None, "current_energy": None, "wallbox_state": None, "last_update": None}
    main._transmit_requested.clear()
    yield main.session_manager


async def test_completed_session_requests_immediate_transmit(ocpp_env):
    server = main.build_ocpp_server(resolve_ocpp_settings(main.current_config))
    port = await server.start("127.0.0.1", 0)
    try:
        start_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        async with connect_sim(port, "CP1") as cp:
            start = await cp.call(call.StartTransaction(connector_id=1, id_tag="EFCD083E",
                                                        meter_start=0, timestamp=start_ts))
            await cp.call(call.StopTransaction(transaction_id=start.transaction_id, meter_stop=7000,
                                               timestamp=datetime.now(timezone.utc).isoformat()))
    finally:
        await server.close()
    assert main._transmit_requested.is_set()
    assert "CP1" in main.api_state["charge_points"]
    assert ocpp_env.get_completed_sessions()[0]["wallbox_id"] == "garage"


def test_overdue_sessions(ocpp_env):
    ocpp_env.start_ocpp_transaction("EFCD083E", "garage", "CP1", 1, 0.0,
                                    "2026-09-28T08:00:00", "2026-09-28T06:00:00Z")
    assert len(main.ocpp_overdue_sessions(24, now=datetime(2026, 9, 30, 8, 0))) == 1
    assert main.ocpp_overdue_sessions(72, now=datetime(2026, 9, 30, 8, 0)) == []


async def test_main_ocpp_mode_skips_home_assistant_and_recovery(ocpp_env, monkeypatch):
    # Laufende OCPP-Session von vor dem "Neustart" muss aktiv bleiben
    ocpp_env.start_ocpp_transaction("EFCD083E", "garage", "CP1", 1, 0.0,
                                    "2026-09-30T08:00:00", "2026-09-30T06:00:00Z")
    calls = []

    async def fake_run(settings):
        calls.append(settings)

    def forbidden(*a, **kw):
        raise AssertionError("HA-Websocket darf im OCPP-Betrieb nicht genutzt werden")

    monkeypatch.setattr(main, "SessionManager", lambda db_path: ocpp_env)
    monkeypatch.setattr(main, "load_config", lambda: dict(CONFIG))
    monkeypatch.setattr(main, "run_ocpp_mode", fake_run)
    monkeypatch.setattr(main, "HomeAssistantWebsocket", forbidden)
    await main.main()
    assert len(calls) == 1 and calls[0].enabled
    assert len(ocpp_env.get_active_ocpp_sessions()) == 1
```

- [ ] **Step 2: Test ausführen, er muss fehlschlagen.** `python -m pytest tests/test_main_ocpp.py -q`
  Expected: FAIL (`AttributeError: module 'main' has no attribute '_transmit_requested'`).

- [ ] **Step 3: Implementieren** (exakter Diff gegen c951a30):

```diff
--- orig/main.py
+++ val/main.py
@@ -46,6 +46,9 @@
 
 # Ingress Web-Server für manuelle Sessions
 from web_server import start_web_server
+from ocpp_server.central_system import CentralSystemDeps
+from ocpp_server.server import OCPP_PORT, OcppServer
+from ocpp_server.settings import resolve_ocpp_settings
 
 # Logging Setup (D-17, D-20)
 LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
@@ -93,6 +96,10 @@
 # Platzhalter-Identität für auth_mode='none' (keine Autorisierungspflicht).
 _NO_AUTH_RFID = "NO_AUTH_REQUIRED"
 
+# Sofort-Übertragung anstoßen (z.B. nach einer OCPP-StopTransaction), statt
+# auf das nächste transmit_interval zu warten.
+_transmit_requested = asyncio.Event()
+
 
 def _parse_energy(value):
     """Sensor-Wert → float kWh oder None bei 'unavailable'/'unknown'/Müll."""
@@ -688,6 +695,94 @@
             _pending_auth = {'rfid_hex': rfid_val, 'time': time.time()}
 
 
+async def periodic_transmission():
+    """Periodische (und auf Anforderung sofortige) Übertragung an Dolibarr."""
+    last_transmit = 0.0
+    transmit_interval = current_config.get("api", {}).get("transmit_interval", 300)
+    while True:
+        if api_client:
+            now = time.time()
+            if (now - last_transmit) >= transmit_interval or _transmit_requested.is_set():
+                _transmit_requested.clear()
+                result = session_manager.transmit_completed_sessions(api_client)
+                if result["transmitted"] > 0:
+                    _LOGGER.info("Sessions an Dolibarr übertragen: %s", result["transmitted"])
+                if result["failed"] > 0:
+                    _LOGGER.error("Fehler bei API-Übertragung: %s Sessions fehlgeschlagen", result["failed"])
+                    if not api_client.check_connection():
+                        _LOGGER.warning("API-Verbindung verloren - deaktiviere temporär")
+                last_transmit = now
+        await asyncio.sleep(1)
+
+
+def build_ocpp_server(settings) -> OcppServer:
+    """Verdrahtet den OCPP-Server mit SessionManager, Whitelist und Live-Zustand."""
+    api_state['charge_points'] = {}
+    deps = CentralSystemDeps(
+        session_manager=session_manager,
+        whitelist=current_config.get('rfid_whitelist', []),
+        live=api_state['charge_points'],
+        min_kwh=float(current_config.get('min_session_kwh', 0.05)),
+        heartbeat_interval=settings.heartbeat_interval,
+        apply_recommended_config=settings.apply_recommended_config,
+        on_session_completed=lambda _session: _transmit_requested.set(),
+    )
+    return OcppServer(settings, deps)
+
+
+def ocpp_overdue_sessions(max_hours: float, now: Optional[datetime] = None) -> list:
+    """Aktive OCPP-Sessions, die länger als max_hours laufen."""
+    now = now or datetime.now()
+    overdue = []
+    for s in session_manager.get_active_ocpp_sessions():
+        try:
+            age_h = (now - datetime.fromisoformat(s['start_time'])).total_seconds() / 3600.0
+        except (TypeError, ValueError):
+            continue
+        if age_h >= max_hours:
+            overdue.append(s)
+    return overdue
+
+
+async def ocpp_stale_session_guard():
+    """Im OCPP-Betrieb beendet NUR die Wallbox eine Session (StopTransaction,
+    ggf. verspätet aus ihrer Offline-Queue). Die Wache warnt daher nur einmal
+    je Session, statt sie mit einem geratenen Zählerstand zu schließen."""
+    max_hours = float(current_config.get("max_session_hours", 24))
+    warned = set()
+    while True:
+        await asyncio.sleep(300)
+        try:
+            for s in ocpp_overdue_sessions(max_hours):
+                if s['id'] not in warned:
+                    warned.add(s['id'])
+                    _LOGGER.warning("OCPP-Session #%s (%s) läuft seit über %.0f h — Wallbox erreichbar? "
+                                    "Wird erst mit ihrer StopTransaction abgeschlossen.",
+                                    s['id'], s['charge_point_id'], max_hours)
+        except Exception as exc:  # Wache darf nie den Loop killen
+            _LOGGER.warning("ocpp_stale_session_guard Fehler: %s", exc)
+
+
+async def run_ocpp_mode(settings) -> None:
+    """Betriebsart session_source=ocpp: kein HA-Websocket, die Wallbox verbindet sich direkt."""
+    if not settings.charge_points:
+        _LOGGER.error("session_source=ocpp, aber keine ocpp_charge_points konfiguriert — "
+                      "jede Wallbox wird abgewiesen")
+    server = build_ocpp_server(settings)
+    await server.start('0.0.0.0', OCPP_PORT)
+    # KEINE Restart-Recovery wie im HA-Pfad: offene Sessions bleiben 'active' —
+    # die Wallbox liefert StopTransaction aus ihrer Offline-Queue nach.
+    open_sessions = session_manager.get_active_ocpp_sessions()
+    if open_sessions:
+        _LOGGER.info("%d laufende OCPP-Session(s) aus der Zeit vor dem Neustart — warte auf StopTransaction",
+                     len(open_sessions))
+    asyncio.create_task(ocpp_stale_session_guard())
+    if api_client:
+        asyncio.create_task(periodic_transmission())
+    asyncio.create_task(start_web_server(session_manager, current_config, api_state, port=8099))
+    await server.serve_forever()
+
+
 async def main():
     """Hauptschleife (D-03, D-10, D-11) - erweitert für Session-Tracking und API-Transmission"""
     global session_manager, current_config, ha_ws, api_client, api_state, profile
@@ -741,6 +836,13 @@
     else:
         _LOGGER.info("Keine Dolibarr API-Konfiguration — Addon läuft ohne API-Transmission")
 
+    ocpp_settings = resolve_ocpp_settings(current_config)
+    if ocpp_settings.enabled:
+        _LOGGER.info("Betriebsart: OCPP-Zentralserver (%d Wallbox(en) konfiguriert)",
+                     len(ocpp_settings.charge_points))
+        await run_ocpp_mode(ocpp_settings)
+        return
+
     # HA-Token ermitteln: SUPERVISOR_TOKEN hat Vorrang, Fallback auf ha_token aus Konfiguration
     supervisor_token = os.getenv('SUPERVISOR_TOKEN', '')
     config_ha_token  = current_config.get('ha_token', '')
@@ -765,34 +867,6 @@
         # Prüfen ob aktive Session nach Neustart existiert (PER-01)
         await check_startup_session()
 
-        # Periodic API Transmission als Hintergrund-Task (Task 4 - Fix: subscribe_entities blockiert)
-        async def periodic_transmission():
-            """Periodische API-Übertragung als Hintergrund-Task"""
-            import time
-            last_transmit = 0
-            transmit_interval = current_config.get("api", {}).get("transmit_interval", 300)
-
-            while True:
-                if api_client:
-                    current_time = time.time()
-                    if (current_time - last_transmit) >= transmit_interval:
-                        result = session_manager.transmit_completed_sessions(api_client)
-
-                        if result["transmitted"] > 0:
-                            _LOGGER.info("Sessions an Dolibarr übertragen: %s", result["transmitted"])
-
-                        if result["failed"] > 0:
-                            _LOGGER.error("Fehler bei API-Übertragung: %s Sessions fehlgeschlagen", result["failed"])
-                            # Bei Fehlern: Verbindung neu testen
-                            if not api_client.check_connection():
-                                _LOGGER.warning("API-Verbindung verloren - deaktiviere temporär")
-                                # api_client auf None setzen deaktiviert weitere Versuche
-                                # TODO: Reconnect-Logik in Zukunft
-
-                        last_transmit = current_time
-
-                await asyncio.sleep(1)
-
         # Sicherung gegen hängende Sessions: Eine Session endet normalerweise
         # beim Abstecken (state=Available). Falls dieses Event ausbleibt (z.B.
         # Sensor-/Websocket-Aussetzer), würde eine "active" Session ewig offen
```

**Hinweise:**
- `periodic_transmission` wandert unverändert auf die Modulebene; hinzu kommt nur `or _transmit_requested.is_set()` samt `clear()`. Der HA-Pfad ruft sie wie bisher über `asyncio.create_task(periodic_transmission())` auf.
- Die Verzweigung steht **vor** dem `try/finally` mit `ha_ws.disconnect()`. Im OCPP-Betrieb existiert kein `ha_ws`.
- Bekannte, bestehende Schwäche (nicht Teil dieses Plans): Ist Dolibarr beim Start nicht erreichbar, bleibt `api_client=None` bis zum Neustart (`main.py:736`, "TODO: Reconnect-Logik"). Gilt in beiden Betriebsarten.

- [ ] **Step 4: Tests ausführen.** `python -m pytest -q`
  Expected: alle grün, auch die 4 Tests in `test_startup_recovery.py` (HA-Pfad).

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_main_ocpp.py
git commit -m "feat: Betriebsart session_source=ocpp (Server statt HA-Websocket, Sofort-Übertragung)"
```

## Task 8: Web-UI zeigt Wallboxen und kWh je Session

**Files:**
- Modify: `wallbox-dolibarr/web_server.py`: `_db_active_sessions` (`:326`), neue Funktion `_charge_points_out`, `handle_live_json` (`:830-866`), JS `render()` im String `js_polling` (`:489-581`)
- Test: `wallbox-dolibarr/tests/test_web_live_ocpp.py`

**Interfaces:**
- Consumes: `api_state['charge_points']` (Task 7), Spalten `charge_point_id`, `last_meter_kwh` (Task 3)
- Produces: `live.json` bekommt zusätzlich `charge_points: [{id, wallbox_id, connected, vendor, model, status, energy_kwh, last_seen, last_rejected_id_tag}]`; im HA-Betrieb eine leere Liste. `sessions[].current_kwh` wird bei OCPP-Sessions aus `last_meter_kwh − start_energy_kwh` berechnet.

- [ ] **Step 1: Den fehlschlagenden Test schreiben** (`tests/test_web_live_ocpp.py`; nutzt `aiohttp.test_utils`, kein Zusatzpaket nötig):

```python
"""live.json im OCPP-Betrieb: Wallbox-Liste und kWh je Session."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from session_manager import SessionManager  # noqa: E402
from web_server import create_app  # noqa: E402


async def test_live_json_lists_charge_points_and_session_kwh(tmp_path):
    sm = SessionManager(db_path=str(tmp_path / "s.db"))
    tx = sm.start_ocpp_transaction("EFCD083E", "garage", "CP1", 1, 1000.0,
                                   "2026-09-30T10:00:00", "2026-09-30T08:00:00Z")
    sm.update_ocpp_meter(tx, "CP1", 1003.5)
    api_state = {"client": None, "current_energy": None, "wallbox_state": None, "last_update": None,
                 "charge_points": {"CP1": {"connected": True, "wallbox_id": "garage", "vendor": "Alfen BV",
                                           "connectors": {"1": {"status": "Charging", "energy_kwh": 1003.5}},
                                           "last_rejected_id_tag": "DEADBEEF"}}}
    async with TestClient(TestServer(create_app(sm, {"wallbox_id": "garage"}, api_state))) as client:
        data = await (await client.get("/live.json")).json()
    assert data["sessions"][0]["current_kwh"] == 3.5
    cp = data["charge_points"][0]
    assert cp["id"] == "CP1" and cp["status"] == "Charging" and cp["connected"] is True
    assert cp["last_rejected_id_tag"] == "DEADBEEF"


async def test_live_json_ha_mode_has_empty_charge_points(tmp_path):
    sm = SessionManager(db_path=str(tmp_path / "s.db"))
    api_state = {"client": None, "current_energy": 5.0, "wallbox_state": "Charging", "last_update": None}
    async with TestClient(TestServer(create_app(sm, {}, api_state))) as client:
        data = await (await client.get("/live.json")).json()
    assert data["charge_points"] == []
    assert data["sensor"]["current_energy"] == 5.0
```

- [ ] **Step 2: Test ausführen, er muss fehlschlagen.** `python -m pytest tests/test_web_live_ocpp.py -q`
  Expected: FAIL (`KeyError: 'charge_points'`).

- [ ] **Step 3: Implementieren** (exakter Diff):

```diff
--- orig/web_server.py
+++ val/web_server.py
@@ -323,13 +323,34 @@
     return names[month - 1]
 
 
+def _charge_points_out(api_state):
+    """OCPP-Live-Zustand je Wallbox für live.json (leer im HA-Sensor-Betrieb)."""
+    out = []
+    for cp_id, st in sorted(((api_state or {}).get('charge_points') or {}).items()):
+        connectors = st.get('connectors') or {}
+        main_conn = connectors.get('1') or next(iter(connectors.values()), {})
+        out.append({
+            'id':                  cp_id,
+            'wallbox_id':          st.get('wallbox_id'),
+            'connected':           bool(st.get('connected')),
+            'vendor':              st.get('vendor'),
+            'model':               st.get('model'),
+            'status':              main_conn.get('status'),
+            'energy_kwh':          main_conn.get('energy_kwh'),
+            'last_seen':           st.get('last_seen'),
+            'last_rejected_id_tag': st.get('last_rejected_id_tag'),
+        })
+    return out
+
+
 def _db_active_sessions(db_path):
     """Alle laufenden Sessions (status='active') aus SQLite"""
     conn = sqlite3.connect(db_path)
     conn.row_factory = sqlite3.Row
     cur = conn.cursor()
     cur.execute("""
-        SELECT id, rfid_hash, wallbox_id, start_time, start_energy_kwh
+        SELECT id, rfid_hash, wallbox_id, start_time, start_energy_kwh,
+               charge_point_id, last_meter_kwh
         FROM sessions
         WHERE status = 'active'
         ORDER BY start_time ASC
@@ -485,6 +506,9 @@
     var sensor=data.sensor||{};
     var hasSessions=sessions.length>0;
     var hasSensor=sensor.current_energy!==null && sensor.current_energy!==undefined;
+    // OCPP-Betrieb: Wallbox-Liste statt HA-Sensor
+    var cps=data.charge_points||[];
+    if(cps.length){ hasSensor=true; }
 
     // Verbindungs-Status im Header
     var cDot=document.getElementById('conn-dot');
@@ -500,7 +524,7 @@
     if(!hasSessions && !hasSensor){ card.style.display='none'; return; }
     card.style.display='block';
 
-    var state=sensor.wallbox_state||'';
+    var state=sensor.wallbox_state||(cps.length?(cps[0].status||''):'');
     var sl=state.toLowerCase();
     var stateColor='var(--muted)';
     if(sl.indexOf('charging')>=0 && sl.indexOf('stopped')<0) stateColor='var(--success)';
@@ -511,7 +535,23 @@
     document.getElementById('live-dot').style.background=stateColor;
 
     var banner='';
-    if(hasSensor){
+    if(cps.length){
+      banner=cps.map(function(c){
+        var st=c.connected?(c.status||'verbunden'):'getrennt';
+        var col=!c.connected?'var(--error)'
+          :(String(c.status||'').toLowerCase()==='charging'?'var(--success)':'var(--warn)');
+        var kwh=(c.energy_kwh!==null && c.energy_kwh!==undefined)?c.energy_kwh.toFixed(3)+' kWh':'—';
+        var rej=c.last_rejected_id_tag
+          ? '<div style="font-size:11px;color:var(--error)">Abgelehnte Karte: <code>'
+            +esc(c.last_rejected_id_tag)+'</code> — in rfid_whitelist und Dolibarr eintragen</div>'
+          : '';
+        return '<div>'+esc(c.wallbox_id||c.id)+' · Zähler: <strong>'+kwh+'</strong>'
+          +'<span style="background:'+col+';color:#0F172A;padding:1px 7px;border-radius:3px;'
+          +'font-size:11px;font-weight:700;margin-left:6px">'+esc(st)+'</span>'
+          +'<span style="float:right;color:var(--dim);font-size:11px">'+esc(c.last_seen||'')+'</span>'
+          +rej+'</div>';
+      }).join('');
+    } else if(hasSensor){
       var chip=state
         ? '<span style="background:'+stateColor+';color:#0F172A;padding:1px 7px;'
           +'border-radius:3px;font-size:11px;font-weight:700;margin-left:6px">'
@@ -843,7 +883,11 @@
             elapsed      = (now - start_dt).total_seconds()
             start_energy = float(s.get('start_energy_kwh') or 0.0)
             current_kwh  = None
-            if current_energy is not None and current_energy >= start_energy:
+            if s.get('charge_point_id'):
+                # OCPP: eigener Zählerstand je Session (letzter MeterValue)
+                if s.get('last_meter_kwh') is not None:
+                    current_kwh = max(0.0, float(s['last_meter_kwh']) - start_energy)
+            elif current_energy is not None and current_energy >= start_energy:
                 current_kwh = current_energy - start_energy
             sessions_out.append({
                 'id':              s['id'],
@@ -862,6 +906,7 @@
                 'last_update':    last_update,
             },
             'sessions': sessions_out,
+            'charge_points': _charge_points_out(api_state),
         })
 
     app = web.Application()
```

- [ ] **Step 4: Tests und JS-Syntax prüfen:**

```bash
python -m pytest -q
python - <<'EOF'
import re, inspect, web_server
js = re.search(r'js_polling = """(.*?)"""', inspect.getsource(web_server), re.S).group(1)
open('/tmp/live.js', 'w').write(re.sub(r'</?script[^>]*>', '', js))
EOF
node --check /tmp/live.js && echo "JS OK"
```

Expected: Tests grün, `JS OK`.

- [ ] **Step 5: Sichtprüfung im Browser** (manuell, erfordert laufendes Addon). Addon lokal mit `session_source: ocpp` starten (siehe Task 12, Step 1) und eine Simulator-Ladung starten. Erwartet: Der Live-Block zeigt je Wallbox eine Zeile mit Status-Chip (grün = Charging, gelb = sonstiger Status, rot = getrennt) und Zählerstand. Nach einer abgelehnten Karte erscheint der rote Hinweis "Abgelehnte Karte: DEADBEEF". Im HA-Betrieb ist die Anzeige unverändert.

- [ ] **Step 6: Commit**

```bash
git add web_server.py tests/test_web_live_ocpp.py
git commit -m "feat: Web-UI zeigt OCPP-Wallboxen, Status und abgelehnte Karten"
```

# Phase 3: Auslieferung

## Task 9: Addon-Konfiguration, Übersetzungen, Docker-Image

**Files:**
- Modify: `wallbox-dolibarr/config.yaml`, `wallbox-dolibarr/translations/de.yaml`, `wallbox-dolibarr/translations/en.yaml`, `wallbox-dolibarr/Dockerfile`

- [ ] **Step 1: `config.yaml` erweitern.**

`version: "1.9.2"` → `version: "2.0.0"`.

Den Block `ports` ersetzen (Host-Port-Default `null`, siehe D13/E2):

```yaml
ports:
  8099/tcp: null
  9000/tcp: null
ports_description:
  9000/tcp: "OCPP-Zentralserver (nur bei session_source: ocpp) — hier den Host-Port eintragen, z.B. 9000"
```

Unter `options:` direkt vor `wallbox_profile` einfügen:

```yaml
  # ── Betriebsart ────────────────────────────────────────────────────────
  # "ha_sensors" → Ladevorgänge aus Home-Assistant-Sensoren (bisheriges Verhalten)
  # "ocpp"       → Addon ist selbst OCPP-1.6J-Zentralserver, Wallbox verbindet
  #                sich direkt (ws://<HA-IP>:<Port>/<Charge-Point-ID>)
  session_source: "ha_sensors"
  ocpp_charge_points: []
  ocpp_heartbeat_interval: 300
  ocpp_apply_recommended_config: false
```

Unter `schema:` entsprechend:

```yaml
  session_source: list(ha_sensors|ocpp)
  ocpp_charge_points:
    - id: str
      password: password?
      wallbox_id: str?
  ocpp_heartbeat_interval: int(30,3600)
  ocpp_apply_recommended_config: bool
```

- [ ] **Step 2: Übersetzungen.** In `translations/de.yaml` unter `configuration:` ergänzen und in `en.yaml` sinngemäß auf Englisch. Außerdem den Top-Level-Schlüssel `network:` mit der Portbeschreibung anlegen:

```yaml
  session_source:
    name: "Datenquelle / Betriebsart"
    description: >-
      "ha_sensors": Ladevorgänge werden aus Home-Assistant-Sensoren abgeleitet
      (wallbox_profile, sensor_*). "ocpp": ExpenseCharge ist selbst der OCPP-
      Server — die Wallbox verbindet sich direkt, unbekannte Karten werden
      abgelehnt. Dann zusätzlich ocpp_charge_points ausfüllen und unter
      "Netzwerk" den Port 9000/tcp freigeben.
  ocpp_charge_points:
    name: "OCPP-Wallboxen"
    description: >-
      Nur bei session_source "ocpp". Je Wallbox: id = Charge-Point-ID (letzter
      Teil der Verbindungs-URL; eine unbekannte ID steht nach dem ersten
      Verbindungsversuch im Log), optional password (Basic Auth, mind. 16
      Zeichen; leer = ohne Passwort, nur im vertrauenswürdigen LAN) und
      optional wallbox_id (Name in Dolibarr, Standard = id).
  ocpp_heartbeat_interval:
    name: "OCPP-Heartbeat (Sekunden)"
    description: >-
      Intervall, das der Wallbox beim Anmelden mitgeteilt wird (30–3600).
  ocpp_apply_recommended_config:
    name: "Empfohlene Wallbox-Einstellungen setzen"
    description: >-
      Wenn aktiv, setzt ExpenseCharge nach jedem Wallbox-Start
      MeterValueSampleInterval=60, MeterValuesSampledData=Energy.Active.Import.Register
      und StopTransactionOnInvalidId=true. Standard aus — ohne Zustimmung
      wird nichts an der Wallbox verändert.

network:
  9000/tcp: "OCPP-Zentralserver (nur bei session_source: ocpp)"
```

Außerdem bei `rfid_whitelist` den Beschreibungstext ergänzen: "Im OCPP-Betrieb ohne Groß-/Kleinschreibung verglichen; Karten in Dolibarr in GROSSBUCHSTABEN eintragen."

- [ ] **Step 3: Dockerfile** (exakter Diff, im Prototyp gebaut und geprüft):

```diff
--- orig/Dockerfile
+++ val/Dockerfile
@@ -29,12 +29,22 @@
         python3 \
         py3-aiohttp \
         py3-requests \
+        py3-websockets \
+        py3-jsonschema \
+        py3-pip \
         sqlite-libs \
         ca-certificates \
       && break || { echo "apk fehlgeschlagen (Versuch $i) — warte 8s"; sleep 8; }; \
     done; \
     python3 -c "import aiohttp, requests, sqlite3; print('deps OK', __import__('sys').version)"
 
+# ocpp (MIT) gibt es nicht als apk-Paket → gepinnt per pip. --no-deps: einzige
+# Abhängigkeit jsonschema kommt oben aus apk (py3-jsonschema). websockets ist
+# keine ocpp-Abhängigkeit und kommt ebenfalls aus apk (py3-websockets >= 14 nötig).
+RUN pip install --no-cache-dir --break-system-packages --no-deps ocpp==2.1.0 \
+    && apk del --no-cache py3-pip \
+    && python3 -c "import ocpp, websockets, jsonschema; from websockets.asyncio.server import serve; print('ocpp OK', websockets.__version__)"
+
 # Addon-Dateien kopieren
 COPY main.py            /usr/local/bin/main.py
 COPY api_client.py      /usr/local/bin/api_client.py
@@ -42,6 +52,7 @@
 COPY web_server.py      /usr/local/bin/web_server.py
 COPY wallbox_profile.py /usr/local/bin/wallbox_profile.py
 COPY utils/             /usr/local/bin/utils/
+COPY ocpp_server/       /usr/local/bin/ocpp_server/
 RUN chmod a+x /usr/local/bin/main.py
 
 CMD ["/usr/bin/with-contenv", "python3", "/usr/local/bin/main.py"]
```

Außerdem `io.hass.description` im LABEL von "der Alfen Eve Wallbox" auf "von Wallboxen (HA-Sensoren oder OCPP 1.6J)" ändern.

- [ ] **Step 4: Image lokal bauen und darin testen** (aus `wallbox-dolibarr/`):

```bash
docker build --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base:3.23 -t expensecharge-ocpp .
docker run --rm -v "$PWD":/src:ro --entrypoint sh expensecharge-ocpp -c \
  'apk add -q --no-cache py3-pytest py3-pytest-asyncio >/dev/null; cp -r /src /work && cd /work && python3 -m pytest -q -p no:cacheprovider'
```

Expected: Build-Log enthält `ocpp OK 15.0.1`; Tests im Container grün (Prototyp: 75 passed).

- [ ] **Step 5: Konfiguration in einer echten HA-Instanz validieren** (manuell, erfordert Home Assistant). Prüfen:
  - Die Konfigurationsseite zeigt die neuen Felder ohne Schemafehler.
  - Der Abschnitt "Netzwerk" zeigt `9000/tcp` leer.
  - Mit `session_source: ha_sensors` startet das Addon wie bisher, keine Zeile "Betriebsart: OCPP".
  - Wenn lbbrhzn/ocpp installiert ist: Beide laufen parallel ohne Portkonflikt.

- [ ] **Step 6: Commit**

```bash
git add config.yaml translations/de.yaml translations/en.yaml Dockerfile
git commit -m "feat: Addon 2.0.0 — Optionen, Port und Image für den OCPP-Betrieb"
```

## Task 10: Dokumentation

**Files:**
- Modify: `README.md` (Root), `wallbox-dolibarr/README.md`, `INSTALL.md`

- [ ] **Step 1: `wallbox-dolibarr/README.md`**: neuen Abschnitt "Betriebsart OCPP (herstellerunabhängig, empfohlen für neue Installationen)" direkt vor "Wallbox-Profile" mit:
  - Wann welcher Betrieb passt (Tabelle `ha_sensors` vs. `ocpp`, inkl. "nur ein OCPP-Backend je Wallbox").
  - Einrichtung Schritt für Schritt:
    1. Port unter "Netzwerk" freigeben.
    2. `session_source: ocpp` setzen.
    3. Wallbox auf `ws://<HA-IP>:<Port>/` einstellen. Die meisten Wallboxen hängen ihre ID selbst an; sonst `ws://<HA-IP>:<Port>/<ID>`.
    4. Addon-Log lesen: "Unbekannte Wallbox 'XYZ' abgewiesen" zeigt die ID.
    5. Die ID in `ocpp_charge_points` eintragen und optional ein Passwort setzen. Autorisierung in der Wallbox auf "Central System / Backend" stellen.
    6. Karten in GROSSBUCHSTABEN in `rfid_whitelist` und in Dolibarr eintragen. Die ID einer abgelehnten Karte zeigt die Web-UI.
  - Beispielkonfiguration:

```yaml
session_source: ocpp
rfid_whitelist:
  - "EFCD083E"
ocpp_charge_points:
  - id: "ACE0123456"
    password: "bitte-mindestens-16-zeichen"
    wallbox_id: "garage"
ocpp_apply_recommended_config: true
```

  - Die Herstellertabelle aus Abschnitt 3 dieses Plans.
  - Verhalten bei Ausfällen: Wallbox offline → sie puffert und liefert nach. Addon-Neustart → offene Ladungen bleiben offen und werden mit dem Stop der Wallbox abgeschlossen. Dolibarr offline → SQLite-Puffer mit Retry.
  - Sicherheit: Security Profile 1 (Basic Auth) nur im vertrauenswürdigen LAN; für TLS einen Reverse-Proxy oder ein VPN nutzen. Den Port nicht ins Internet freigeben.
  - Moduswechsel nur ohne laufenden Ladevorgang.

- [ ] **Step 2: Root-`README.md`**: Die Aussage "RFID gelesen (`sensor.alfen_eve_tag_socket_1`) …" im Datenfluss als "Betriebsart `ha_sensors`" kennzeichnen. Einen Absatz "Betriebsart `ocpp`" mit Link auf den neuen Abschnitt ergänzen. Das Addon-Badge auf 2.0.0 setzen.

- [ ] **Step 3: `INSTALL.md`**: Einen Abschnitt "Variante OCPP" mit der Checkliste aus Step 1.2 ergänzen.

- [ ] **Step 4: Gegenprüfen.** Jede in der Doku genannte Option existiert in `config.yaml`:

```bash
grep -oh 'ocpp_[a-z_]*' ../README.md README.md ../INSTALL.md | sort -u
grep -o 'ocpp_[a-z_]*' config.yaml | sort -u
```

Expected: Jeder Doku-Schlüssel kommt auch in `config.yaml` vor.

- [ ] **Step 5: Commit**

```bash
git add ../README.md README.md ../INSTALL.md
git commit -m "docs: Betriebsart OCPP, Herstellerübersicht und Einrichtung"
```

## Task 11: Tests in CI

**Files:**
- Create: `.github/workflows/tests.yaml`

- [ ] **Step 1: Workflow anlegen:**

```yaml
name: Tests

on:
  push:
    paths:
      - "wallbox-dolibarr/**"
      - ".github/workflows/tests.yaml"
  pull_request:
    paths:
      - "wallbox-dolibarr/**"

jobs:
  pytest:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: wallbox-dolibarr
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements-dev.txt
      - run: python -m pytest -q
```

- [ ] **Step 2: Lokal gegenprüfen, dass `requirements-dev.txt` in einem frischen venv reicht:**

```bash
python3 -m venv /tmp/ci-venv && /tmp/ci-venv/bin/pip install -r requirements-dev.txt && /tmp/ci-venv/bin/python -m pytest -q
```

Expected: alle Tests grün.

- [ ] **Step 3: Commit**

```bash
git add ../.github/workflows/tests.yaml
git commit -m "ci: pytest bei jedem Push auf das Addon"
```

# Phase 4: Abnahme

## Task 12: Simulator-Ende-zu-Ende und Abnahme mit echter Wallbox

**Files:**
- Create: `wallbox-dolibarr/tests/manual/ACCEPTANCE-OCPP.md` (Abnahmeprotokoll)

Dieser Task braucht Docker, einen externen Simulator und die echte Wallbox des Nutzers. Was ohne diese Umgebung nicht laufen kann, wird im Protokoll als **offen** eingetragen und dem Nutzer gemeldet.

- [ ] **Step 1: Addon-Prozess lokal starten** (ohne HA):

```bash
mkdir -p data && cat > data/options.json <<'EOF'
{"session_source": "ocpp", "rfid_whitelist": ["EFCD083E"],
 "ocpp_charge_points": [{"id": "CS-SIM-00001"}], "api": {"dolibarr_url": "", "api_token": ""}}
EOF
docker run --rm -p 9000:9000 -p 8099:8099 -v "$PWD/data":/data expensecharge-ocpp
```

Expected im Log: `Betriebsart: OCPP-Zentralserver (1 Wallbox(en) konfiguriert)` und `OCPP-Zentralserver lauscht auf Port 9000`. Achtung: Ohne HA setzt niemand `TZ`, die Zeiten sind dann UTC.

- [ ] **Step 2: Unabhängiger Simulator** (SAP e-mobility-charging-stations-simulator, Apache-2.0, validiert gegen die offiziellen OCA-Schemas):

```bash
git clone --depth 1 https://github.com/SAP/e-mobility-charging-stations-simulator /tmp/evse-sim
cd /tmp/evse-sim && corepack enable && pnpm install
cp src/assets/config-template.json src/assets/config.json
```

In `src/assets/config.json` `supervisionUrls` auf `ws://127.0.0.1:9000` setzen und ein Template mit OCPP 1.6 wählen. Station-ID passend zu `ocpp_charge_points`. Dann `pnpm start`. Szenarien:

| # | Szenario | Erwartung |
|---|---|---|
| S1 | Boot, Heartbeat, StatusNotification | Wallbox "verbunden" in der Web-UI |
| S2 | Ladung mit `EFCD083E` | Session aktiv, kWh steigt in der UI, nach Stop `completed` |
| S3 | Ladung mit unbekanntem Tag | `Invalid`, keine Session, roter Hinweis in der UI |
| S4 | Addon während der Ladung stoppen, 2 min warten, starten | Die Session bleibt `active`, nach dem Stop genau eine `completed` Session |
| S5 | Verbindung trennen während Start und Stop | Nachgelieferte Nachrichten → genau eine Session, Zeitstempel der Wallbox |
| S6 | Station ohne Eintrag in `ocpp_charge_points` | Verbindung abgewiesen (404), Logzeile nennt die ID |

Findet der Simulator Schemafehler (`OCPPError`/`FormationViolation`), wird erst die Ursache per superpowers:systematic-debugging geklärt, dann gefixt und ein Regressionstest in `test_ocpp_central_system.py` ergänzt.

- [ ] **Step 3: Abnahme mit echter Wallbox** (Alfen Eve des Nutzers). Checkliste in `ACCEPTANCE-OCPP.md`:
  1. Vorher: aktuelle Backend-Einstellungen der Wallbox dokumentieren (Rollback).
  2. ACE Service Installer: Backend-URL `ws://<HA-IP>:9000/`, Protokoll "OCPP 1.6 JSON", Autorisierungsmodus auf Backend/Central System. Klären: Ist dafür eine Lizenz nötig? Welche ID hängt die Wallbox an?
  3. Die Addon-Logzeile "Unbekannte Wallbox '…'" auswerten und die ID in `ocpp_charge_points` eintragen.
  4. Echte Ladung mit registrierter Karte: kWh in Dolibarr == Anzeige der Wallbox (Abweichung < 0,01 kWh); Zeitstempel korrekt (Zeitzone!); `wallbox_id` in der Spesenzeile stimmt.
  5. Unbekannte Karte: Die Wallbox startet nicht (bzw. bricht mit `DeAuthorized` ab). Mit `ocpp_apply_recommended_config: true` wiederholen und `StopTransactionOnInvalidId` prüfen.
  6. Lastmanagement-Pause (falls vorhanden) → eine Session (`SuspendedEVSE/EV` beendet nichts).
  7. Addon-Neustart während der Ladung → keine verlorene oder doppelte Session.
  8. WLAN/LAN der Wallbox 5 min trennen, während die Ladung endet → Stop wird nachgeliefert.
  9. Parallelbetrieb: Funktioniert eine bestehende HA-Alfen-Integration (lokale API) weiter? (nur Information)
  10. Rollback testen: `session_source: ha_sensors` und Wallbox-Backend auf den alten Stand → Addon wie vor dem Update.

- [ ] **Step 4: Befunde einarbeiten.** Jede Abweichung wird als Bug per Test-First behoben (superpowers:systematic-debugging). Herstellerspezifische Eigenheiten kommen in die Doku-Tabelle.

- [ ] **Step 5: Commit**

```bash
git add tests/manual/ACCEPTANCE-OCPP.md
git commit -m "test: Abnahmeprotokoll OCPP (Simulator + Alfen Eve)"
```

# Phase 5 (per E4 NICHT in diesem Durchlauf)

## Task 13: Dolibarr entscheidet, welche Karte laden darf

**Status: zurückgestellt (E4).** v1 nutzt die Whitelist; Dolibarr lehnt nicht registrierte Karten bei der Übertragung ohnehin ab (404, Retry). Der vollständige Entwurf (`authorize.php`, `CachedAuthorizer`, `WallboxApiClient.is_rfid_registered`, `ocpp_authorize_via_dolibarr`) liegt in der ursprünglichen Planvorlage und wird bei Bedarf als eigener Plan umgesetzt. Nicht im Prototyp verifiziert, braucht eine Dolibarr-Testinstanz.

# Risiken und Gegenmaßnahmen

| Risiko | Wahrscheinlichkeit | Auswirkung | Gegenmaßnahme |
|---|---|---|---|
| Hersteller weicht von der Spezifikation ab (fehlende MeterValues, Wh/kWh, kaputte idTags) | mittel | falsche oder fehlende Abrechnung | D6-Fallbacks, `incomplete` statt Raten, SAP-Simulator, Hardware-Abnahme, Doku-Tabelle |
| Wallbox hat bereits ein Cloud-Backend | hoch (bei Firmenkunden) | OCPP-Betrieb nicht nutzbar | In der Doku klar benannt; `ha_sensors` bleibt |
| Portkonflikt 9000 mit lbbrhzn/ocpp | mittel | Addon startet nicht | Host-Port-Default `null` (D13), Test in Task 9 Step 5 |
| Alfen braucht eine Lizenz oder einen Installateur-Zugang für die Backend-URL | unklar | Einrichtung beim Nutzer blockiert | In Task 12 klären und dokumentieren, bevor 2.0.0 veröffentlicht wird |
| Karten-IDs in Dolibarr in anderer Schreibweise | mittel | 404 bei der Übertragung, Retry-Schleife | D5/E1, Hinweis in der UI bei abgelehnter Karte, Doku |
| Blockierende SQLite-Aufrufe im Event-Loop | gering | Antwortzeiten > Timeout der Wallbox | SQLite-Aufrufe liegen im ms-Bereich (bestehendes Muster) |
| Neue Logausgaben der Bibliothek mit Klartext | gering | Karten-ID im Log | D15 + `test_id_tag_plaintext_never_logged` läuft in CI |
| websockets-Update in Alpine (API-Bruch) | gering | Server startet nicht | Tests gegen 15.0.1 und 17.1 grün; CI-Job; Import-Check im Dockerfile |
| Unverschlüsselter Betrieb (Profil 0/1) | – | Mitlesen im LAN | Nur LAN/VPN, nie Internet; TLS per Reverse-Proxy dokumentiert |

# Rollback

- Die Konfiguration zurück auf `session_source: ha_sensors` und das Wallbox-Backend auf den alten Stand setzen → das bisherige Verhalten gilt vollständig.
- Die Schema-Änderungen sind rein additiv (neue Spalten sind bei HA-Sessions NULL). Ein Downgrade auf 1.9.x funktioniert mit derselben DB.
- Addon-Version 2.0.0 lässt sich in HA über "Neu installieren" einer älteren Version zurücksetzen. Die `/data/sessions.db` bleibt erhalten.

# Definition of Done

- [ ] Tasks 1–12 abgeschlossen, alle Commits auf `feat/ocpp-central-system`
- [ ] `python -m pytest -q` grün lokal (Prototyp-Referenz: 75 Tests); in CI und im Image, soweit die Umgebung es zulässt
- [ ] Abnahmeprotokoll (Task 12) mit Simulator S1–S6 und echter Wallbox — offene Punkte explizit benannt
- [ ] Doku vollständig
- [ ] Code-Review (Whole-Branch) ohne CRITICAL/HIGH
- [ ] Version 2.0.0; Merge erst nach Freigabe durch den Nutzer

# Anhang A: Nachrichtenfluss (OCPP 1.6J)

```
Wallbox                                   ExpenseCharge (OCPP-Server)                 Dolibarr
  |--- WS-Upgrade /ocpp/<ID> (Basic Auth) -->| process_request: ID bekannt? Passwort?
  |<-- 101 Switching Protocols (ocpp1.6) ----|
  |--- BootNotification -------------------->| Live: verbunden, Hersteller/Modell
  |<-- Accepted, interval=300 ---------------|  (@after: optional ChangeConfiguration)
  |--- StatusNotification(Preparing) ------->| Live-Status
  |--- Authorize(idTag) -------------------->| Whitelist
  |<-- Accepted | Invalid -------------------|
  |--- StartTransaction(idTag, meterStart) ->| erneute Prüfung → sessions (active), ID = sessions.id
  |<-- transactionId, Accepted --------------|
  |--- MeterValues(Energy.Active.Import) --->| last_meter_kwh, Live-kWh
  |--- StopTransaction(meterStop, reason) -->| completed | discarded | incomplete
  |<-- (leer) -------------------------------|  → _transmit_requested
  |                                          |--- POST receive.php {rfid_hash, kWh, …} ->| Spesenzeile
```
