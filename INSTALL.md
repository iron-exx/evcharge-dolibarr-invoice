# ExpenseCharge — Installationsanleitung

Komplette Inbetriebnahme: **Home Assistant** (RFID-Erfassung) → **Dolibarr** (direkt in die Spesenabrechnung).

```
Wallbox  ──►  Home Assistant Addon  ──POST──►  Dolibarr receive.php  ──►  Spesenabrechnung
                  │                                                          des Mitarbeiters
                  └─ SQLite-Buffer (Crash-Recovery, Retry)
```

> **Verlustsicherheit:** Das HA-Addon puffert Sessions lokal in SQLite (WAL-Mode). Bei Netzausfall werden sie beim nächsten Erreichen von Dolibarr nachgereicht. Dolibarr verhindert Duplikate durch Abgleich von Mitarbeiter + Ladeende-Zeitstempel + Wallbox-ID gegen bereits vorhandene Spesenzeilen.

---

## 1 — Voraussetzungen

| Komponente | Mindestens | Empfohlen |
|---|---|---|
| Dolibarr | 20.0 | 21.x / 22.x |
| PHP | 8.0 | 8.2 |
| MariaDB / MySQL | 10.5 / 8.0 | 10.11 / 8.0.30+ |
| Home Assistant Core | 2024.6 | aktuelles Stable |
| Python (im HA-Addon) | 3.12 | 3.12+ |
| Wallbox | beliebig — HA muss `power`, `energy`, `rfid`, `state` als Sensoren liefern |

In Dolibarr muss **kein** zusätzliches Modul aktiv sein. Das wallboxbilling-Modul nutzt direkt Dolibarrs Spesenabrechnungs-Tabellen (`llx_expensereport`, `llx_expensereport_det`).

---

## 2 — Dolibarr-Modul installieren

1. Anmelden als **Admin**.
2. *Home → Konfiguration → Module/Anwendungen → Externes Modul hinzufügen*.
3. Aktuelle `module_wallboxbilling-*.zip` hochladen (siehe Repo-Root für die neueste Version).
4. Modul **aktivieren** (orangenen Schalter klicken).
5. Unter *Home → Konfiguration → ExpenseCharge Konfiguration* öffnen — wenn die Seite lädt und keine Fehlermeldung zeigt, ist die Installation erfolgreich.

### 2.1 — API-Token setzen

Die Authentifizierung läuft über ein **einzelnes gemeinsames Token** (Shared Secret) — **nicht** über einen Dolibarr-Benutzer-DOLAPIKEY.

1. *Home → Konfiguration → ExpenseCharge Konfiguration → Tab „Konfiguration"*.
2. Feld **API-Token** ausfüllen (langer Zufallsstring, z.B. mit `openssl rand -hex 32` erzeugt).
3. Speichern.

> Dasselbe Token muss identisch im HA-Addon unter `api.api_token` eingetragen werden. Alle Wallbox-Instanzen, die für diese Firma senden dürfen, nutzen **dasselbe** Token — es identifiziert nicht den einzelnen Mitarbeiter (das macht die RFID-Zuordnung), sondern nur den Endpunkt-Zugriff.

### 2.2 — RFID-Karten zuordnen

1. *Home → Konfiguration → ExpenseCharge Konfiguration → Tab „RFID-Verwaltung"*.
2. Pro Mitarbeiter in der Zeile „Tag hinzufügen": RFID-Hex eingeben, optional Label/Preis/Kostenstelle setzen → **Tag hinzufügen**.
   - **Mehrere Karten pro Mitarbeiter möglich** — jede erscheint als eigene Zeile mit eigenem Preis/Kostenstelle/Label.
   - Der eingegebene RFID-Code wird als SHA-256-Hash gespeichert und **kann danach nicht mehr im Klartext angezeigt werden** (Sicherheitsdesign). Das **Label**-Feld ist ein frei wählbarer Merktext (z.B. „Blaue Ersatzkarte") — dort darf **nicht** der echte Tag-Code eingetragen werden.
   - **Löschen** entfernt eine Zuordnung endgültig (mit Bestätigungsdialog). Ein Reaktivieren gibt es nicht — bei Bedarf einfach neu anlegen.

---

## 3 — Home Assistant Addon installieren

### 3.1 — Repository hinzufügen

1. *Einstellungen → Add-ons → Add-on-Store → ⋮ → Repositories*.
2. URL eintragen: `https://github.com/systemwerk-GmbH-Co-KG/ExpenseCharge`.
3. „Hinzufügen" → Repository erscheint im Store.

> Falls das alte Repository (`evcharge-dolibarr-invoice`) noch eingetragen ist: entfernen — dort werden keine Updates mehr veröffentlicht.

### 3.2 — Addon installieren

1. Im Store: „ExpenseCharge" → **Installieren**.
2. Karteireiter *Konfiguration*:
   ```yaml
   log_level: INFO
   wallbox_id: alfen_eve
   rfid_whitelist:
     - "A1B2C3D4"
     - "12345678"
   sensor_rfid:   sensor.alfen_eve_tag_socket_1
   sensor_energy: sensor.alfen_eve_meter_reading_socket_1
   sensor_state:  sensor.alfen_eve_main_state_socket_1
   ha_token: ""                      # leer lassen — Supervisor-Token wird automatisch genutzt
   api:
     dolibarr_url: "https://erp.example.com"
     api_token: "<API-Token aus 2.1>"
     transmit_interval: 300          # alle 5 min an Dolibarr senden
     timeout: 30
   ```

   > **Wichtig:** Die drei Sensoren müssen wirklich existieren — prüfen mit
   > *Entwicklerwerkzeuge → Zustände → Filter „alfen_eve"*. Bei abweichenden
   > Wallboxen die entsprechenden Sensoren eintragen.
   >
   > Für Nicht-Alfen-Wallboxen, einen vorgeschalteten Zähler (z.B. Shelly EM) oder
   > Wallboxen ganz ohne eigenen Zähler: `wallbox_profile: custom` setzen und
   > Auth-Modus (`auth_mode`) + Zustand-Erkennung (`state_mode`) frei kombinieren —
   > Details und Beispiele in
   > [wallbox-dolibarr/README.md](wallbox-dolibarr/README.md#wallbox-profile-herstellerunabhängige-konfiguration).

3. **Speichern → Starten**.
4. Logs prüfen: muss zeigen `Dolibarr API Verbindung erfolgreich`.

### 3.3 — Ingress-UI

Über *Add-on öffnen* erreichbar, zwei Tabs:
- **⚡ Erfassen** — Live-Block (Wallbox-Status + laufende Sessions, JS-Polling alle 5 s) + manuelles Nachtragen + Sofort-Übertragen-Button
- **📋 Verlauf** — Historie pro Monat + CSV-Export
- Übertragungs-Status pro Session

---

## 3.4 — Variante OCPP (statt HA-Sensoren)

Ab Addon 2.0.0 kann ExpenseCharge selbst OCPP-1.6J-Zentralserver sein. Dann entfällt die
HACS-Integration, und unbekannte Karten laden nicht.

Checkliste:

1. Addon → *Konfiguration* → **Netzwerk**: bei `9000/tcp` einen Host-Port eintragen
   (z.B. `9000`). Default ist leer, weil Port 9000 mit der HACS-Integration
   `lbbrhzn/ocpp` kollidieren würde.
2. `session_source: ocpp` setzen, Addon neu starten.
3. Wallbox auf `ws://<HA-IP>:<Port>/` einstellen, Protokoll „OCPP 1.6 JSON",
   Autorisierung auf *Central System* / *Backend*. Die meisten Wallboxen hängen ihre
   ID selbst an; sonst `ws://<HA-IP>:<Port>/<Charge-Point-ID>`.
4. Addon-Log lesen — `Unbekannte Charge-Point-ID 'XYZ'` nennt die ID der Wallbox.
5. Diese ID in `ocpp_charge_points` eintragen, optional mit Passwort (mind. 16 Zeichen):

   ```yaml
   session_source: ocpp
   ocpp_charge_points:
     - id: "ACE0123456"
       password: "bitte-mindestens-16-zeichen"
       wallbox_id: "garage"
   ```
6. Karten in **GROSSBUCHSTABEN** in `rfid_whitelist` und in Dolibarr eintragen. Die ID
   einer abgelehnten Karte zeigt die Ingress-UI rot an.

Details, Herstellertabelle und Sicherheitshinweise:
[wallbox-dolibarr/README.md](wallbox-dolibarr/README.md#betriebsart-ocpp-herstellerunabhängig-empfohlen-für-neue-installationen)

## 3.5 — Variante Standalone: Docker ohne Home Assistant

Wer kein Home Assistant hat oder will, überspringt Schritt 3 komplett und fährt
ExpenseCharge als einfachen Container (Raspberry Pi mit 64-Bit-OS, Debian-LXC, …).
Quelle ist **`systemwerk-GmbH-Co-KG/ExpenseCharge`**, Branch `feat/ocpp-central-system`
(öffentlich — kein Token, kein Deploy Key nötig):

```bash
git clone -b feat/ocpp-central-system https://github.com/systemwerk-GmbH-Co-KG/ExpenseCharge.git
cd ExpenseCharge/wallbox-dolibarr

./setup-standalone.sh                        # fragt alles ab, gibt die Wallbox-Daten aus
docker compose up -d --build --force-recreate
docker compose logs -f
```

Das Skript schreibt `data/options.json` (mit zufälligem OCPP-Passwort) und die `.env`
und gibt am Ende Backend-URL `ws://<IP>:9000/`, Charge-Point-ID und Passwort für die
Wallbox aus. Web-UI im LAN/VPN (`WEB_BIND=0.0.0.0`), Ersteinrichtung und Anmeldung im Browser,
Konfiguration per `.env` und welcher Befehl nach welcher Änderung nötig ist
(`restart` reicht bei Ports **nicht**, dann `docker compose up -d --force-recreate`):
Proxmox-CT-Firewall (8099 nur Admin-Netz, 9000 nur Wallbox-Netz):
siehe [wallbox-dolibarr/README.md → Standalone](wallbox-dolibarr/README.md#standalone-in-docker--ohne-home-assistant).

## 4 — Funktionsprüfung (End-to-End)

1. Karte an die Wallbox halten → Ladevorgang starten.
2. HA-Logs (`Add-on → Log`) sollten zeigen:
   - `RFID autorisiert: <hash16>…`
   - `Session gestartet: ID=…`
3. Ladung beenden (Stecker ziehen / Wallbox auf Idle).
4. Innerhalb von `transmit_interval` Sekunden (Standard 5 min):
   - `Session erfolgreich übertragen` im Log
5. In Dolibarr: *Geschäftspartner → Spesenabrechnungen* → Draft für den aktuellen Monat des Mitarbeiters → Zeile mit kWh × Preis vorhanden.

---

## 5 — Fehlerbehebung

| Symptom | Ursache | Lösung |
|---|---|---|
| `HTTP 401 Unauthorized` | API-Token falsch/fehlt | Token in Dolibarr (Konfiguration-Tab) und HA-Addon (`api.api_token`) abgleichen — muss identisch sein |
| `HTTP 404 RFID not registered` | Karte nicht zugeordnet | Unter *ExpenseCharge Konfiguration → RFID-Verwaltung* der Karte einen Mitarbeiter zuordnen |
| `HTTP 400` mit Feldname | Pflichtfeld fehlt/ungültig (z.B. `kwh` ≤ 0, ungültiges Zeitformat) | Payload/Sensor-Werte prüfen |
| `Server returned HTML statt JSON` | Modul nicht (mehr) aktiv, falscher Endpunkt-Pfad | Modul-Status prüfen, `receive.php` per Browser aufrufen (muss JSON-Fehler zeigen, kein Dolibarr-Login) |
| Session-Daten fehlen in Spesenreport | Falscher Monat | `end_time` prüfen — Report landet im Monat des Ladeendes, nicht „heute" |
| Kein Icon/falscher Name im HA Add-on-Store | Store-Cache | Add-on-Store → ⋮ → „Neu laden", notfalls Repository entfernen + neu hinzufügen |

---

## 6 — Upgrade

1. Neue ZIP im Modulmanager hochladen → „Überschreiben? **Ja**".
2. Modul deaktivieren + wieder aktivieren → `init()` läuft erneut (idempotent, `CREATE TABLE IF NOT EXISTS`).
3. RFID-Mappings, Preise, API-Token und Spesenabrechnungen bleiben erhalten — die Tabelle `llx_wallbox_rfid` wird bei Deaktivierung/Entfernen **nicht** gelöscht.

---

## 7 — Sicherheits-Checkliste

- [ ] API-Token nicht im Git eingecheckt, nur in Dolibarr-Konfiguration + HA `secrets.yaml`
- [ ] Dolibarr ausschließlich über HTTPS (Reverse-Proxy / Let's Encrypt)
- [ ] DB-Backup enthält `llx_wallbox_rfid` und `llx_expensereport*`
- [ ] HA-Backup enthält das Addon-Volume (`/data/sessions.db`)
- [ ] Klartext-RFIDs werden nicht geloggt oder angezeigt (nur SHA-256-Hash in der DB)

---

Fertig. Mehrere Testladungen vor produktivem Betrieb durchführen.
