# Code-Review und Fix-Pass — feat/ocpp-central-system

Whole-Branch-Review durch einen Reviewer mit frischem Kontext (Opus), Umfang
c951a30..4fb01f6. Ergebnis: **1 Critical, 5 Important, 8 Minor.**
Urteil des Reviewers: *„Ready to merge? With fixes."*

Alle Critical/Important sind behoben, jeder Fix per Test-First (RED→GREEN).
Suite danach: **87 Tests grün** (vorher 75).

## Behoben

| # | Befund | Fix | Test |
|---|---|---|---|
| C1 | **D15 war nicht dicht.** Der Schutz vor Karten-Klartext im Log hing nur am Log-Level. Die `ocpp`-Bibliothek loggt bei **jedem Handler-Fehler und jedem Schema-Verstoß** die komplette Nachricht auf ERROR — inklusive `idTag`. Dieser Pfad ist im Betrieb normal (nicht spezifikationstreue Firmware, SQLite-Fehler). Die Zusage in README und D14 war damit **falsch**. | Neues Modul `ocpp_server/redact.py`: inhaltsbasierter Logging-Filter an beiden Protokoll-Loggern, unabhängig vom Level. | `test_id_tag_plaintext_never_logged_when_handler_raises`, `…_even_at_debug`, `test_ocpp_redact.py` |
| I2 | **Wallbox mit stehender Uhr verlor jede Ladung nach der ersten.** Die Duplikatsuche der `StartTransaction` lief über die ganze Historie, ohne Status- und Zeitgrenze. Meldet eine Wallbox immer denselben Start-Zeitstempel, erbte jede weitere Ladung die ID der ersten, fügte nichts ein, und ihr Stop lief ins Leere — nicht einmal als `incomplete` sichtbar. | Zusätzliches Unterscheidungsmerkmal `start_energy_kwh` (der Zähler läuft weiter) plus 1-Tages-Fenster. | `test_stuck_clock_does_not_swallow_later_charges`, `test_genuine_retry_still_idempotent_with_same_meter_start` |
| I3 | **Event-Loop-Blockade bis ~200 s.** `transmit_completed_sessions` ist synchrones `requests` mit Retry-Backoff (6×30 s + Backoff). Im OCPP-Betrieb bedient derselbe Loop die Wallbox — die bekam in dieser Zeit keine Antwort und verweigerte den Ladestart. Ausgelöst innerhalb einer Sekunde nach **jeder** beendeten Ladung. | `asyncio.to_thread` + Ausnahmebehandlung, damit der Task nicht dauerhaft stirbt. | `test_transmission_does_not_block_the_event_loop` (nebenläufiger Ticker), `test_transmission_survives_an_exception` |
| I4 | **Zähler in kWh statt Wh → 100 % Umsatzverlust, still.** Faktor 1000 zu klein, fällt unter `min_session_kwh`, wird `discarded` **und** als übertragen markiert — also nie erneut versucht und im Export wie ein gewolltes Ergebnis gelesen. Die Plausibilitätsgrenze war einseitig (nur nach oben). | Session über 15 min unter `min_kwh` gilt als Zählerfehler → `incomplete` (sichtbar), statt `discarded`. | `test_long_session_below_min_kwh_is_incomplete_not_discarded`, `test_short_session_below_min_kwh_stays_discarded` |
| I5 | Die README nannte den passwortlosen Betrieb nur „im vertrauenswürdigen LAN vertretbar", ohne die konkrete Folge. | README benennt jetzt: **erfundene Spesenzeilen auf dem Konto eines echten Mitarbeiters**, fehlende Ratebremse, und dass der Log-Hash pseudonymisiert statt anonymisiert ist. | — |
| M1 | Die Charge-Point-ID aus dem URL-Pfad eines **nicht authentifizierten** Peers ging unbegrenzt und unmaskiert ins Log: Zeilenumbruch schleust gefälschte Logzeilen ein, Länge flutet das Log. *(Von Minor hochgestuft.)* | `safe_cp_id_for_log()` — auf 64 Zeichen begrenzt, `repr()`-maskiert. | `test_charge_point_id_from_path_is_bounded_and_safe_to_log` |
| M6 | Das Image installiert `py3-websockets` aus apk, ungepinnt. Der Code braucht ≥ 14; ein Alpine-Bump wäre erst beim Verbindungsversuch der Wallbox aufgefallen. *(Hochgestuft.)* | Dockerfile bricht beim Build ab, wenn die Major-Version < 14 ist. | — |
| M5 | Tote Konstante `SUBPROTOCOLS` (wurde `serve()` nie übergeben). | Entfernt. | — |

## Bewusst nicht geändert

**I1 — unplausible Zeitstempel lassen die Leistungsgrenze auf 1,0 kWh kollabieren.**
Liefert eine Wallbox mit ungestellter Uhr Start und Stop offline nach, fallen beide
Zeitstempel auf „jetzt", die Dauer wird 0, und eine echte Ladung landet in `incomplete`.

Der Reviewer nannte das „silently drops the whole charge". Das trifft nicht zu, und der
Plan nennt genau diesen Fall im Review Focus ausdrücklich **„gewollt und für den Admin
sichtbar"**. Nachgeprüft: `incomplete` erscheint im Verlauf als „unvollständig"
(`web_server.py:852`, `:454`, `:675`). Es entspricht D6: lieber eine Session zur
manuellen Prüfung als eine falsche Abrechnung.

*Folge, falls die Einschätzung falsch ist:* eine solche Ladung muss vom Admin manuell
nachgetragen werden, statt automatisch abgerechnet zu werden.

## Aufgeschoben (Minor)

| # | Befund |
|---|---|
| M2 | `extract_energy_kwh` nimmt den letzten passenden Sample statt bevorzugt `context == 'Transaction.End'`. Heute durch die Monotonie-Prüfung abgefangen (fällt sicher nach `discarded`, rechnet nie zu viel ab). |
| M3 | Die Web-UI zeigt nur Connector „1" — die zweite Buchse einer Alfen Eve Double ist unsichtbar, rechnet aber korrekt ab. |
| M4 | Vor der ersten erfolgreichen Verbindung zeigt die UI nichts. Gerade der häufigste Ersteinrichtungsfehler (unbekannte ID, falsches Passwort) steht nur im Log. |
| M7 | Hintergrund-Tasks werden nicht referenziert (`create_task`-Handles verworfen) — bestehendes Muster, auch im HA-Pfad. |
| M8 | `hash_rfid` ist ungesalzenes SHA-256 über 8 Hex-Zeichen, per Rainbow-Table umkehrbar. Bestehender Entwurf (D-14/D-19), jetzt wenigstens in der README benannt. |
| — | Spiegelfall zu I2: geht `/data/sessions.db` verloren, startet AUTOINCREMENT neu, und eine alte gepufferte `StopTransaction` könnte eine fremde neue Session schließen. Durch die Zählerstand-Prüfung meist zu `incomplete` entschärft. |

## Vom Reviewer ausdrücklich bestätigt

- Kein Pfad gefunden, der **doppelt abrechnet** oder **falsche kWh** abrechnet.
- Der `ha_sensors`-Pfad ist nachweislich unverändert: additive Spalten (NULL für HA-Zeilen),
  `charge_point_id`-Filter (`NULL = 'CP1'` ist NULL), `charge_points: []` in `live.json`,
  Host-Port-Default `null` — kein Konflikt mit `lbbrhzn/ocpp`.
- Die Verlagerung von `periodic_transmission` auf Modulebene ist verhaltensneutral.
- Keine Einwände gegen die Architektur: Modulschnitt, `sessions.id` als `transactionId`,
  und der Verzicht auf Crash-Recovery im OCPP-Betrieb.
- Zur 404-vor-401-Reihenfolge: bewusst so belassen (die Einrichtung braucht die Logzeile,
  und die ID ist ohnehin der Basic-Auth-Benutzername im Klartext).
