# ExpenseCharge

Ladevorgänge. Spesen. Abgerechnet.

RFID-basierte Abrechnung von Wallbox-Ladevorgängen — Sessions werden vom Home-Assistant-Addon **direkt in die Dolibarr-Spesenabrechnung** des jeweiligen Mitarbeiters geschrieben.



![Dolibarr-Modul](https://img.shields.io/badge/Dolibarr--Modul-2.2.0-blue)
![HA-Addon](https://img.shields.io/badge/HA--Addon-2.0.0-blue)
![Dolibarr](https://img.shields.io/badge/Dolibarr-20.x--22.x-green)
![Python](https://img.shields.io/badge/Python-3.12+-green)
![License](https://img.shields.io/badge/License-Proprietary-red)




<img width="1254" height="1254" alt="cover" src="https://github.com/user-attachments/assets/da61923e-c9f7-47de-9d21-eebe6b6c36a9" />





## Funktionsweise

```
┌─────────────────┐                ┌──────────────────────┐
│  Home Assistant │   POST JSON    │      Dolibarr        │
│  Addon (Python) │ ─────────────► │  receive.php (PHP)   │
│                 │   DOLAPIKEY    │                      │
│  - RFID-Reader  │                │  ① RFID→User Lookup  │
│  - Energie-Zähl │                │  ② Spesenabrechnung  │
│  - SQLite-Buffer│                │     des Monats       │
│  - Retry-Loop   │                │     finden / anlegen │
└─────────────────┘                │  ③ Zeile in          │
                                   │     expensereport_det│
                                   └──────────────────────┘
```

Jede Ladung landet sofort als Position im Spesenreport des Mitarbeiters. Pro Monat und Mitarbeiter wird automatisch ein Draft-Report erstellt oder ein bestehender erweitert. Es gibt **keinen Cronjob**, **keine eigene Sessions-Tabelle** und **keine separate Abrechnungsseite** — alles steckt nativ in Dolibarrs Spesenabrechnungsmodul.

## Features

### Home Assistant Addon
- RFID-Authentifizierung mit SHA-256-Hash (kein Klartext-Speichern)
- Whitelist-Prüfung + 7s-Debounce gegen Doppellesungen
- Multi-Wallbox-Support
- SQLite-Buffer mit WAL-Mode für Crash-Recovery
- Restart-Recovery: laufende Sessions werden beim Neustart behandelt
- Auto-Retry mit Exponential-Backoff bei API-Fehlern
- Web-UI (Ingress): manuelle Sessions, History, CSV-Export
- **Kartenverwaltung mit Lernmodus**: Karte an die Wallbox halten, in der Oberfläche benennen und einordnen — **geschäftlich** (wird abgerechnet) oder **privat** (bleibt lokal, erreicht Dolibarr nie)

### Dolibarr-Modul
- Direkter Insert in `llx_expensereport` / `llx_expensereport_det` als Ausgabentyp `TF_OTHER` (Fallback: erste aktive Kategorie)
- **Multi-RFID pro Mitarbeiter**: beliebig viele Karten pro Benutzer, je mit eigenem Preis/Kostenstelle/Label
- Pro-Tag-Preis (€/kWh) oder globaler Default
- Duplikat-Schutz: Abgleich Mitarbeiter + Ladeende-Zeitstempel + Wallbox-ID gegen bereits vorhandene Spesenzeilen
- RFID-Zuordnung endgültig löschbar (kein Soft-Delete/Reaktivierung)
- Auth per gemeinsamem API-Token (Header `DOLAPIKEY`), RFID nur als SHA-256-Hash gespeichert (Dolibarr 20+)

## Betriebsvarianten

ExpenseCharge läuft auf zwei Wegen — beide werden unterstützt:

| | Als Home-Assistant-Addon | Standalone in Docker |
|---|---|---|
| Wofür | HA ist schon im Haus; Sensoren sollen genutzt werden | minimal, z.B. auf einem Raspberry Pi; kein HA vorhanden oder gewollt |
| Betriebsarten | `ha_sensors` (HA-Sensoren) **und** `ocpp` | **nur** `ocpp` (die Wallbox verbindet sich direkt) |
| Einrichtung | Addon-Store, Konfiguration in der HA-Oberfläche | `docker compose up -d` mit `.env` |
| Web-UI | HA-Ingress, durch HA-Login geschützt | nur an `127.0.0.1` gebunden — kein eingebauter Schutz |

Standalone-Anleitung: [wallbox-dolibarr/README.md](wallbox-dolibarr/README.md#standalone-in-docker--ohne-home-assistant)

### Datenquellen

Woher die Ladevorgänge kommen, bestimmt `session_source`:

| | `ha_sensors` (Default) | `alfen_http` | `ocpp` | `modbus` |
|---|---|---|---|---|
| Braucht Home Assistant | ja | **nein** | nein | nein |
| Zugriffskontrolle | nein | nein | **ja** | nein |
| Karten-ID | aus dem HA-Sensor | **aus dem Transaktions-Log** | aus der OCPP-Transaktion | nur mit Tag-Register |
| Parallel zum Cloud-Backend | ja | **ja** | nein (Slot belegt) | **ja** |
| Hersteller | alle | **nur Alfen** | alle mit OCPP 1.6J | alle mit Modbus TCP |
| Einrichtung | Sensoren zuordnen | IP und Zugangsdaten | URL in der Wallbox | Registeradressen |

Für eine **Alfen** ist `alfen_http` die beste Wahl: Zähler, Zustand und Karte aus
einer Quelle, ohne HA und ohne den OCPP-Backend-Slot zu belegen.

## Voraussetzungen

| Komponente | Version |
|---|---|
| Dolibarr | 20.x – 22.x |
| Home Assistant | 2024.x+ mit Supervisor — **nur für die Addon-Variante** |
| Docker | für die Standalone-Variante; Raspberry Pi braucht ein 64-Bit-OS |
| Python | 3.12+ (im Container mitgeliefert) |

## Schnellinstallation

### 1. Dolibarr-Modul

1. Aktuelle `module_wallboxbilling-*.zip` im Dolibarr-Modulmanager hochladen
2. Modul **aktivieren**
3. Unter „ExpenseCharge Konfiguration":
   - Default-Preis pro kWh setzen
   - API-Token (Shared Secret) setzen — muss identisch im HA-Addon (`api_token`) stehen
   - RFID-Karten pro Mitarbeiter zuordnen (RFID-Verwaltung-Tab)

### 2. Home Assistant Addon

1. Repository hinzufügen: `https://github.com/systemwerk-GmbH-Co-KG/ExpenseCharge`
2. Addon „ExpenseCharge" installieren
3. Konfiguration:
   ```yaml
   wallbox_id: alfen_eve
   rfid_whitelist:
     - "A1B2C3D4"
   sensor_rfid:   sensor.alfen_eve_tag_socket_1
   sensor_energy: sensor.alfen_eve_meter_reading_socket_1
   sensor_state:  sensor.alfen_eve_main_state_socket_1
   api:
     dolibarr_url: "https://erp.example.com"
     api_token: "<gemeinsames API-Token, identisch mit Dolibarr-Modulkonfiguration>"
     transmit_interval: 300
   ```

   > Das obige Beispiel nutzt das Default-Profil `wallbox_profile: alfen_eve`. Für andere
   > Hersteller, einen vorgeschalteten Zähler (z.B. Shelly EM) oder Wallboxen ganz ohne
   > eigenen Zähler gibt es `wallbox_profile: custom` mit frei kombinierbarem Auth-Modus
   > und Zustand-Erkennung — siehe [wallbox-dolibarr/README.md](wallbox-dolibarr/README.md#wallbox-profile-herstellerunabhängige-konfiguration).

### 3. Addon-Web-UI

Zwei Tabs im Ingress:

- **⚡ Erfassen** — Hauptseite mit:
  - **Live-Block oben** (sofern Sensoren liefern): aktueller Zählerstand, Wallbox-Status als farbiger Chip (grün=Charging, grau=Idle, rot=Faulted), laufende Sessions mit RFID-Prefix, Start-Zeit, Dauer und Live-kWh-Delta. **Flackerfreies JS-Polling** (`fetch('live.json')` alle 5 s)
  - Manuelles Erfassen einer Ladung
  - „Jetzt an Dolibarr übertragen" Button
- **📋 Verlauf** — abgeschlossene Sessions pro Monat, CSV-Export

## Datenfluss im Detail

### Betriebsart `ha_sensors` (Default, bisheriges Verhalten)

1. **RFID gelesen** (`sensor.alfen_eve_tag_socket_1`): wechselt von `No Tag` auf eine Tag-ID (z.B. `A1B2C3D4`)
2. **Whitelist + Debounce**: 7-Sekunden-Sperre gegen Doppellesungen
3. **Session starten**: lokale SQLite speichert `start_time` + `start_energy_kwh` (Zählerstand aus `sensor.alfen_eve_meter_reading_socket_1`)
4. **Session beenden** — getriggert durch:
   - Wallbox-Status (`sensor.alfen_eve_main_state_socket_1`) wechselt auf `Available`, `Finishing`, `Stopped`, `Faulted`, … (substring-Match auf `charging`/`idle`/etc.), **oder**
   - RFID wechselt zurück auf `No Tag` (Karte abgezogen)
   - `total_kwh = end_zähler − start_zähler`
5. **Ghost-Session-Filter**: Sessions unter `min_session_kwh` (Default 0.05 kWh) werden als `discarded` markiert und NICHT übertragen — passiert wenn die Karte gehalten wird ohne dass eine Ladung tatsächlich beginnt
6. **Transmit** (alle 5 min oder sofort):
   - POST an `receive.php` mit `{rfid_hash, wallbox_id, start_time, end_time, kwh}`
   - PHP-Endpoint: RFID→User → Spesenabrechnung suchen/anlegen → Zeile rein
   - Bei Erfolg: `transmitted_at` lokal gesetzt
   - Bei `HTTP 404 RFID not registered`: Admin muss die Karte erst zuordnen, Addon retried automatisch
   - Bei `HTTP 401 Unauthorized`: API-Token in Dolibarr und Addon stimmen nicht überein

### Betriebsart `ocpp` (ab Addon 2.0.0)

Mit `session_source: ocpp` ist das Addon **selbst OCPP-1.6J-Zentralserver**. Die Wallbox
verbindet sich direkt per WebSocket (`ws://<HA-IP>:<Port>/<Charge-Point-ID>`), statt dass
HA-Sensoren ausgewertet werden. Damit gibt es echte Zugriffskontrolle — **unbekannte Karten
laden nicht** —, die Zählerstände kommen unmittelbar aus `meterStart`/`meterStop` der
Wallbox, und mehrere Wallboxen laufen gleichzeitig in einer Instanz. Eine HACS-Integration
ist dann nicht mehr nötig.

Einschränkung: eine Wallbox kennt nur **ein** OCPP-Backend. Wer bereits ein Cloud-Backend
nutzt, müsste darauf verzichten. `ha_sensors` bleibt unverändert verfügbar.

Einrichtung, Herstellertabelle und Sicherheitshinweise:
[wallbox-dolibarr/README.md](wallbox-dolibarr/README.md#betriebsart-ocpp-herstellerunabhängig-empfohlen-für-neue-installationen)

## Sicherheit & Compliance

- ✅ RFID nur als SHA-256-Hash persistiert, Klartext nie gespeichert oder angezeigt (Datensparsamkeit, DSGVO)
- ✅ Gemeinsames API-Token (Header `DOLAPIKEY`), im HA-Secret bzw. Dolibarr-Konfiguration hinterlegt
- ✅ SQL-Injection-Schutz via `$db->escape()`
- ✅ Schutz gegen versehentliches Kapern eines Tags durch einen anderen Benutzer
- ✅ Spesenabrechnungen unterliegen Dolibarrs Standard-Audit-Trail
- ✅ Modul-Deinstallation droppt **keine** Tabellen (`llx_wallbox_rfid` bleibt erhalten)

## Projektstruktur

```
ExpenseCharge/
├── Dolibarr/htdocs/custom/wallboxbilling/   # Dolibarr-Modul
│   ├── receive.php                          # POST-Endpoint (Token-Auth)
│   ├── index.php                            # Sessions-Übersicht (Skeleton)
│   ├── admin/admin.php                      # Konfiguration + RFID-Verwaltung
│   ├── class/api_wallboxbilling.class.php   # REST-API (Dolibarr Web-Services)
│   ├── core/modules/modWallboxbilling.class.php
│   └── langs/de_DE/wallboxbilling.lang
├── wallbox-dolibarr/                        # Addon bzw. Standalone-Container
│   ├── main.py                              # Hauptloop + Websocket
│   ├── session_manager.py                   # SQLite + RFID
│   ├── api_client.py                        # Dolibarr POST
│   ├── web_server.py                        # Ingress UI
│   ├── tag_learning.py                      # Lernmodus (flüchtiger Klartext)
│   ├── tag_release.py                       # haftenden RFID-Wert zurücksetzen
│   ├── utils/hash.py                        # SHA-256
│   ├── icon.png / logo.png                  # Addon-Branding
│   ├── Dockerfile
│   ├── docker-compose.yml                   # Standalone-Betrieb ohne HA
│   ├── .env.example                         # Konfigurationsvorlage dafür (Umgebungsvariablen)
│   ├── options.standalone.example.json      # alternativ: Konfiguration als JSON
│   └── config.yaml
└── module_wallboxbilling-*.zip              # Build-Artefakte der Dolibarr-Module
```

## Lizenz

Proprietär — alle Rechte vorbehalten. Siehe `LICENSE`.

## Support

GitHub: [systemwerk-GmbH-Co-KG/ExpenseCharge](https://github.com/systemwerk-GmbH-Co-KG/ExpenseCharge)
