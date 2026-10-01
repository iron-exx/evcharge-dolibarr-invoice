# ExpenseCharge — Home Assistant Addon

Erfasst RFID-basierte Ladevorgänge einer Wallbox und schreibt sie direkt in die Dolibarr-Spesenabrechnung des jeweiligen Mitarbeiters. Herstellerunabhängig konfigurierbar — von Alfen Eve bis zu generischen RFID-Leser+Zähler-Kombinationen (siehe [Wallbox-Profile](#wallbox-profile-herstellerunabhängige-konfiguration)).

## Funktionen

- **Wallbox-Profile**: vorgefertigtes Alfen-Eve-Profil oder vollständig freie Konfiguration für andere Hersteller, Lastmanagement-Adapter mit vorgeschaltetem Zähler (z.B. Shelly EM) oder Wallboxen ganz ohne eigenen Zähler
- Session-Tracking (Start, Ende, kWh) mit lokalem SQLite-Buffer
- SHA-256-Hash für RFID — keine Klartext-Speicherung (DSGVO/Datensparsamkeit)
- 7-Sekunden-Debounce gegen Doppellesungen
- Robuste Status-Erkennung: substring-Match auf Wallbox-Statuswerte wie `Charging Power On`, `Available`, `Finishing`, `Faulted` (Alfen-Profil) — oder alternativ Leistungsschwelle, Zähler-Stillstand, bzw. externe Aktiv-Entity (Custom-Profil)
- End-Trigger via Status, Leistung/Zähler-Idle, externer Entity **oder** Zweit-Tap (`tag_toggle`)
- Automatische Übertragung an Dolibarr `receive.php` mit Token-Auth (Header `DOLAPIKEY`)
- Web-UI (Ingress):
  - **⚡ Erfassen** — Startseite mit eingebettetem Live-Block (laufende Sessions + Wallbox-Status, flackerfreies JS-Polling alle 5 s über `/live.json`) + manuelles Erfassen
  - **📋 Verlauf** — Historie + CSV-Export

## Installation

1. Repository hinzufügen: `https://github.com/iron-exx/ExpenseChrage`
2. Addon „ExpenseCharge" installieren
3. Konfiguration anpassen (siehe unten)
4. Addon starten

## Konfiguration

```yaml
log_level: INFO
wallbox_id: alfen_eve
rfid_whitelist:
  - "A1B2C3D4"
  - "12345678"
sensor_rfid:   sensor.alfen_eve_tag_socket_1
sensor_energy: sensor.alfen_eve_meter_reading_socket_1
sensor_state:  sensor.alfen_eve_main_state_socket_1
ha_token: ""                      # leer = SUPERVISOR_TOKEN wird automatisch genutzt
min_session_kwh: 0.05
api:
  dolibarr_url: "https://erp.example.com"
  api_token: "<gemeinsames API-Token, identisch mit Dolibarr-Modulkonfiguration>"
  transmit_interval: 300
  timeout: 30
```

| Schlüssel | Beschreibung |
|---|---|
| `wallbox_id` | Label, wird mit in der Spesenabrechnungs-Zeile angezeigt |
| `rfid_whitelist` | Liste der erlaubten RFID-Hex-Strings — alles andere wird ignoriert |
| `sensor_rfid` | HA-Entity für die RFID-Lesung (liefert Tag-ID oder `No Tag`) |
| `sensor_energy` | HA-Entity für den kumulativen Energiezähler in kWh |
| `sensor_state` | HA-Entity für den Wallbox-Status (Available / Charging / …) |
| `ha_token` | Nur nötig, falls `SUPERVISOR_TOKEN` nicht verfügbar ist — normalerweise leer lassen |
| `min_session_kwh` | Mindest-kWh ab dem eine Session als echte Ladung gewertet wird (Default 0.05). Karte gelesen ohne Anschluss → Session wird als `discarded` markiert, nicht übertragen |
| `api.dolibarr_url` | Basis-URL, ohne `/custom/wallboxbilling/...` Pfad |
| `api.api_token` | Gemeinsames Shared-Secret — muss identisch in der Dolibarr-Modulkonfiguration stehen (**kein** Dolibarr-Benutzer-DOLAPIKEY) |
| `api.transmit_interval` | Sekunden zwischen Retry-Loops (Default 300 = 5 min) |
| `api.timeout` | HTTP-Timeout in Sekunden für die Übertragung an Dolibarr |

## Betriebsart OCPP (herstellerunabhängig, empfohlen für neue Installationen)

Ab Version 2.0.0 kann das Addon **selbst OCPP-1.6J-Zentralserver** sein. Die Wallbox
verbindet sich dann direkt per WebSocket mit ExpenseCharge, statt dass HA-Sensoren
ausgewertet werden.

### Welcher Betrieb passt?

| | `session_source: ha_sensors` (Default) | `session_source: ocpp` |
|---|---|---|
| Datenquelle | HA-Sensoren (Tag, Zähler, Zustand) | Die Wallbox selbst, per OCPP |
| Zugriffskontrolle | keine — die Wallbox lädt, das Addon protokolliert nur | **echt**: unbekannte Karten laden nicht |
| Zählerstände | HA-Sensorwert zum Start-/Endzeitpunkt | `meterStart`/`meterStop` der Wallbox |
| Mehrere Wallboxen | eine Instanz je Wallbox | mehrere gleichzeitig in einer Instanz |
| HACS-Integration nötig | ja (z.B. Alfen) | nein |
| Wallbox offline | Lücke, Sensor liefert nichts | Wallbox puffert und liefert nach |
| Einschränkung | — | **Eine Wallbox kennt nur ein OCPP-Backend.** Wer schon ein Cloud-Backend nutzt, müsste darauf verzichten. |

Ein Moduswechsel sollte nur ohne laufenden Ladevorgang erfolgen.

### Einrichtung Schritt für Schritt

1. **Port freigeben**: Addon → *Konfiguration* → Abschnitt **Netzwerk** → bei `9000/tcp`
   einen Host-Port eintragen (z.B. `9000`). Der Default ist bewusst leer, weil Port 9000
   mit der HACS-Integration `lbbrhzn/ocpp` kollidieren würde.
2. `session_source: ocpp` setzen und das Addon neu starten.
3. **Wallbox einstellen**: Backend-URL `ws://<HA-IP>:<Port>/`, Protokoll „OCPP 1.6 JSON",
   Autorisierung auf *Central System* / *Backend*. Die meisten Wallboxen hängen ihre eigene
   ID selbst an die URL an; sonst `ws://<HA-IP>:<Port>/<Charge-Point-ID>`.
4. **Addon-Log lesen.** Beim ersten Verbindungsversuch steht dort:
   `Unbekannte Wallbox 'ACE0123456' abgewiesen — in ocpp_charge_points eintragen`.
   Das ist die ID, die die Wallbox sendet.
5. Diese ID in `ocpp_charge_points` eintragen, optional mit Passwort (Basic Auth).
6. **Karten in GROSSBUCHSTABEN** in `rfid_whitelist` und in Dolibarr eintragen. Die ID
   einer abgelehnten Karte zeigt die Ingress-UI im Live-Block rot an.

### Beispielkonfiguration

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

Optional lässt sich mit `ocpp_heartbeat_interval` (30–3600, Default 300) das
Heartbeat-Intervall festlegen, das der Wallbox beim Anmelden mitgeteilt wird.

`ocpp_apply_recommended_config: true` setzt nach jedem Wallbox-Start
`MeterValueSampleInterval=60`, `MeterValuesSampledData=Energy.Active.Import.Register`
und `StopTransactionOnInvalidId=true`. Ohne diese Option wird **nichts** an der Wallbox
verändert.

### Unterstützte Wallboxen

| Hersteller / Modell | OCPP lokal | Einrichtung | Hinweis |
|---|---|---|---|
| Alfen Eve Single/Double (Pro-line, S-line), NG9xx | 1.6J (NG9xx auch 2.0.1) | ACE Service Installer (Installateur-Zugang) | Die Charge-Box-ID wird angehängt |
| ABL eMH2/eMH3/eMC2/eMC3, eM4 | 1.6J | Web-UI `http://169.254.1.1:8300/` | eMH1: nur Modbus, kein OCPP |
| Keba P30 x-series, P40 | 1.6J | Web-UI „Central System Address/Path" | P30 c-series: kein OCPP |
| go-e V3/V4/V5 (FW ≥ 59.4), Pro (≥ 59.3) | 1.6J | App → Internet → OCPP | |
| Mennekes AMTRON / AMEDIO | 1.6J | Web-UI → Backend | |
| Compleo eBOX smart/professional | 1.6J | eCONFIG-App (Bluetooth) | |
| Easee | 1.6J „native OCPP" (FW ≥ 328) | Aktivierung über Easee-Cloud-API, danach lokal `ws://` | |
| Zaptec Go/Go2/Pro | 1.6J | Zaptec-Portal („Allow OCPP 1.6J") | Die Cloud bleibt aktiv |
| Wallbox Pulsar Plus / Copper SB / Commander 2 | 1.6J | myWallbox-App/Portal, Passwort leer | Bei FW 5.x teils keine MeterValues → Fallback greift |
| Heidelberg Energy Control | – | nur mit zusätzlicher Heidelberg Combox | |

### Verhalten bei Ausfällen

- **Wallbox offline**: sie puffert Start/Stop und liefert sie später nach. Die Ladung
  landet im Monat der echten Ladung, nicht im Monat der Nachlieferung.
- **Addon-Neustart während einer Ladung**: die Session bleibt `active` und wird erst mit
  der `StopTransaction` der Wallbox abgeschlossen. Es wird **kein** Zählerstand geraten.
- **Dolibarr offline**: die Session liegt im SQLite-Puffer, Retry mit Backoff.
- **Unbrauchbarer Endzählerstand**: die Session wird `incomplete` statt falsch abgerechnet —
  sichtbar für den Admin.

### Sicherheit

- **Ohne Passwort (`password: ""`) kann jedes Gerät im Netz, das Port und Charge-Point-ID
  kennt, Ladevorgänge frei erfinden** — mit der RFID einer echten Karte. Das Ergebnis ist
  eine Spesenzeile in der Abrechnung **eines echten Mitarbeiters**. Wer kein Passwort
  setzt, muss dem gesamten Netzsegment so weit trauen wie der Lohnbuchhaltung. Deshalb:
  Passwort setzen, wo die Wallbox eines unterstützt.
- Security Profile 1 (Basic Auth ohne TLS) ist nur im **vertrauenswürdigen LAN** vertretbar.
  Das Passwort sollte mindestens 16 Zeichen haben; der Benutzername **muss** die
  Charge-Point-ID sein (OCPP-Vorgabe A00.FR.204). Eine Ratebremse gibt es nicht — ein
  kurzes Passwort ist im LAN in Sekunden durchprobiert.
- Der Karten-Hash im Log ist **pseudonymisiert, nicht anonymisiert**: `hash_rfid` ist ein
  ungesalzenes SHA-256 über eine 8-stellige Hex-ID, also mit einer Rainbow-Table
  umkehrbar. Für die DSGVO-Bewertung zählt der Log damit als personenbezogen.
- Für TLS einen Reverse-Proxy (z.B. NGINX-Addon) oder ein VPN vorschalten.
- **Den Port niemals ins Internet freigeben.**
- Karten-IDs stehen nie im Klartext im Log — dort nur der Hash-Präfix. Der Klartext einer
  abgelehnten Karte erscheint ausschließlich flüchtig in der Ingress-UI.

## Betriebsart Modbus TCP

Die dritte Datenquelle, neben `ha_sensors` und `ocpp`. Sie löst ein Problem, das
OCPP nicht lösen kann: **eine Wallbox kennt nur ein OCPP-Backend.** Wer schon ein
Cloud-Backend des Herstellers nutzt, kann den OCPP-Betrieb nicht verwenden —
Modbus TCP läuft **parallel** dazu.

### Wann Modbus, wann OCPP?

| | OCPP 1.6J | Modbus TCP |
|---|---|---|
| Zugriffskontrolle | **ja** — unbekannte Karten laden nicht | nein, die Wallbox entscheidet selbst |
| Karten-ID | kommt mit der Transaktion | **nur wenn die Wallbox sie in ein Register legt** |
| Parallel zum Cloud-Backend | nein, Backend-Slot belegt | **ja** |
| Zählerstände | `meterStart`/`meterStop` der Transaktion | gepollte Register |
| Einrichtung | URL in der Wallbox eintragen | Registeradressen aus dem Handbuch übertragen |

**Die wichtigste Einschränkung:** Modbus liefert nur, was die Wallbox in Register
legt. Viele Hersteller geben Zählerstand und Zustand heraus, aber **nicht** die
Karten-ID — bei einer Alfen steckt der Tag im Transaktions-Log, das nur über die
HTTP-API erreichbar ist. Ohne Tag-Register gibt es keine Zuordnung pro Mitarbeiter;
dann ordnet `fixed_login` alle Ladungen einem Dolibarr-Benutzer zu.

### Einrichtung

Es gibt **keine** herstellerübergreifende Registerkarte. Adressen, Datentypen und
Skalierung legt jede Wallbox selbst fest — sie stehen im Modbus-Handbuch des
Herstellers. Eine geratene Adresse würde falsche kWh abrechnen, deshalb ist hier
alles explizit anzugeben, und eine unbrauchbare Konfiguration lässt das Addon
**absichtlich nicht starten**.

```yaml
session_source: modbus

modbus:
  host: "192.168.1.50"       # IP der Wallbox
  port: 502
  unit_id: 1                 # Slave-Adresse
  function_code: 3           # 3 = Holding-, 4 = Input-Register
  poll_interval: 5           # Sekunden
  rfid_hold_seconds: 1       # haftendes Tag-Register selbst zurücksetzen

  registers:
    energy:                  # PFLICHT — ohne Zähler keine Abrechnung
      address: 320
      type: "uint32"
      word_order: "big"      # "little" bei vielen Herstellern!
      scale: 0.001           # Wh → kWh

    state:                   # optional, steuert Start und Ende
      address: 1200
      type: "uint16"
      state_map:             # viele Wallboxen melden den Zustand als Zahl
        - "2:Available"
        - "3:Charging"
        - "4:Faulted"

    rfid:                    # optional — nur wenn die Wallbox den Tag liefert
      address: 1500
      type: "string"
      count: 4               # 4 Register = 8 Zeichen
```

### Die drei Stolpersteine

1. **`word_order`.** Innerhalb eines Registers ist Modbus immer Big-Endian, bei
   32-Bit-Werten über zwei Register unterscheiden sich die Hersteller aber. Steht
   ein unsinnig großer oder kleiner Zählerstand in der Oberfläche, ist fast immer
   das die Ursache — einfach auf `little` umstellen.
2. **`scale`.** OCPP schreibt Wh vor, Modbus schreibt nichts vor. Ein Register in
   Wh braucht `0.001`, eines in kWh `1`, eines in Zehntel-kWh `0.1`. Ist der Faktor
   um 1000 zu klein, landet jede Ladung unter `min_session_kwh` — sichtbar als
   „unvollständig", nicht als falsche Abrechnung.
3. **Haftendes Tag-Register.** Viele Wallboxen behalten die letzte Karte dauerhaft
   im Register. `rfid_hold_seconds: 1` lässt das Addon den Rückfall auf „kein Tag"
   selbst erzeugen. Dadurch ist allerdings eine **zweite Ladung derselben Karte
   nicht am Tag erkennbar** — sie wird über den Zustandssensor erkannt und dem
   zuletzt gesehenen Tag zugeordnet. `auth_mode: tag_toggle` funktioniert mit einem
   haftenden Register nicht.

### Fehlersuche

Das Addon meldet jeden Lesefehler **einmal** und überträgt dann **nichts** — statt
ersatzweise 0 zu liefern, was eine Ladung mit 0 kWh in die Abrechnung brächte:

```
Modbus-Lesefehler bei 192.168.1.50:502 — es wird NICHTS gemeldet,
damit keine falschen kWh entstehen: Modbus-Exception 2 (ungültige
Registeradresse) beim Lesen von 2 Register ab 320
```

`Modbus-Exception 2` heißt: die Adresse gibt es nicht — Handbuch prüfen, und
bedenken, dass manche Hersteller ab 1 statt ab 0 zählen. `Exception 1` heißt, der
Funktionscode passt nicht: `3` gegen `4` tauschen.

Im Tab **System** steht die wirksame Konfiguration, inklusive Werten, die aus einem
unzulässigen Bereich zurechtgezogen wurden.

## Karten verwalten: Lernmodus, geschäftlich und privat

Im Tab **Karten** der Web-UI lassen sich RFID-Karten direkt am Gerät anlernen,
benennen und einordnen — ohne die Konfiguration anzufassen.

### Eine neue Karte anlernen

1. Tab **Karten** öffnen, **Lernmodus starten**.
2. Karte an die Wallbox halten. Sie erscheint mit ihrer ID in der Liste.
3. Namen eintragen (z.B. „Firmenwagen 1") und einordnen:
   - **Geschäftlich** — die Ladung wird als Spesenposition an Dolibarr übertragen.
   - **Privat** — die Ladung bleibt **lokal** und erreicht Dolibarr **nie**.
4. **Speichern.** Danach darf die Karte laden. **Lernmodus beenden.**

Eine erkannte, aber noch nicht eingeordnete Karte kann **nicht** laden — das ist
Absicht: so landet keine unbekannte Karte versehentlich in der Abrechnung.

### Geschäftlich oder privat

| | Geschäftlich | Privat |
|---|---|---|
| Laden erlaubt | ja | ja |
| Übertragung an Dolibarr | ja | **nie** |
| Im Verlauf der Web-UI | ja | ja |
| Im CSV-Export | ja | ja |
| Status in der Datenbank | `completed` | `private` |

Wird eine Karte **nachträglich** auf privat umgestellt, stoppt das auch eine noch
nicht übertragene Ladung. Bereits übertragene Ladungen bleiben in Dolibarr —
sie müssen dort storniert werden.

### Verhältnis zur Whitelist und zu Dolibarr

Es gibt jetzt zwei Wege, eine Karte zum Laden zu berechtigen:

- `rfid_whitelist` in der Konfiguration (wie bisher) — gilt als geschäftlich
- die Kartenverwaltung in der Web-UI (neu) — mit Einordnung

Beide wirken parallel; bestehende Installationen ändern sich nicht. Die Zuordnung
**welcher Mitarbeiter** abgerechnet wird, bleibt wie gehabt Sache von Dolibarr
(`llx_wallbox_rfid`). Das Addon entscheidet nur, **ob** übertragen wird.

### Datenschutz

Gespeichert wird ausschließlich der **SHA-256-Hash** der Karte plus der von dir
vergebene Name. Der Klartext der Karten-ID erscheint **nur während des
Lernmodus**, nur im Arbeitsspeicher, höchstens 10 Minuten lang, und verschwindet
beim Beenden des Modus sofort. Die gerenderte Kartenliste zeigt nur den
Hash-Präfix.

## Haftender RFID-Wert (`rfid_hold_seconds`)

Manche Quellen halten den zuletzt gelesenen Tag **dauerhaft**. Die
Alfen-HA-Integration etwa leitet ihn aus dem **Transaktions-Log** der Wallbox ab,
also aus dem letzten *abgeschlossenen* Ladevorgang — der Wert bleibt deshalb
stehen, bis eine neue Transaktion auftaucht, und nicht nur solange jemand die
Karte vorhält. Dasselbe gilt für Modbus-Register, die den letzten Tag speichern.

Für die Zustandslogik ist das irreführend. Mit `rfid_hold_seconds: 1.0` setzt
ExpenseCharge den Tag nach einer Sekunde selbst auf „kein Tag" zurück.

```yaml
rfid_hold_seconds: 1.0
```

Default ist `0` (aus), damit bestehende Installationen unverändert bleiben. Im
Modbus-Betrieb ist der Default `1.0`, weil haftende Register dort die Regel sind.

**Bewusste Einschränkung:** Nach dem Reset löst derselbe Wert **nicht** erneut
aus. Bei einem haftenden Sensor ist „alte Karte klebt noch" nicht von „dieselbe
Karte erneut vorgehalten" zu unterscheiden — sonst startete nach jeder Ladung
eine Phantom-Session. Eine zweite Ladung derselben Karte erkennt stattdessen der
Zustandssensor; zugeordnet wird sie dem zuletzt gesehenen Tag.

## Standalone in Docker — ohne Home Assistant

ExpenseCharge läuft auch als einfacher Docker-Container, etwa auf einem Raspberry Pi.
Beide Wege bleiben verfügbar: **als HA-Addon** wie bisher, **oder** standalone.

> **Nur mit `session_source: ocpp`.** Ohne Home Assistant gibt es keine HA-Sensoren,
> also kann die Betriebsart `ha_sensors` dort nicht funktionieren. Im OCPP-Betrieb
> braucht ExpenseCharge Home Assistant ohnehin nicht — die Wallbox verbindet sich
> direkt mit dem Container.

### Was sich gegenüber dem Addon-Betrieb unterscheidet

| | HA-Addon | Standalone Docker |
|---|---|---|
| Konfiguration | HA-Oberfläche schreibt `/data/options.json` | `data/options.json` selbst anlegen |
| Betriebsarten | `ha_sensors` **und** `ocpp` | **nur** `ocpp` |
| Web-UI-Schutz | HA-Ingress mit HA-Login | **kein Schutz** — nur an `127.0.0.1` binden |
| Zeitzone | setzt der Supervisor | **selbst per `TZ` setzen** |
| Updates | HA-Addon-Store | `docker compose pull` bzw. neu bauen |

### Einrichtung

```bash
git clone https://github.com/systemwerk-GmbH-Co-KG/ExpenseCharge.git
cd ExpenseCharge/wallbox-dolibarr

mkdir -p data
cp options.standalone.example.json data/options.json
$EDITOR data/options.json          # Charge-Point-ID, Karten, Dolibarr-Zugang

docker compose up -d
docker compose logs -f
```

Im Log muss stehen:

```
Betriebsart: OCPP-Zentralserver (1 Wallbox(en) konfiguriert)
OCPP-Zentralserver lauscht auf Port 9000
```

Danach die Wallbox auf `ws://<host-ip>:9000/` stellen (Rest wie im Abschnitt
[Betriebsart OCPP](#betriebsart-ocpp-herstellerunabhängig-empfohlen-für-neue-installationen):
Log lesen, gemeldete ID in `ocpp_charge_points` eintragen, neu starten).

### Drei Dinge, die standalone leicht schiefgehen

1. **`TZ` nicht gesetzt** → alle Zeitstempel in UTC. Eine Ladung am Monatsletzten um
   23:30 landet dann im Folgemonat. Das Compose-File setzt `TZ` auf `Europe/Berlin`;
   per `TZ=...` in einer `.env` oder in der Umgebung überschreiben.
2. **Web-UI offen im Netz.** Im Addon-Betrieb sitzt der HA-Ingress mit Login davor;
   standalone gibt es das nicht. Das Compose-File bindet die UI daher an
   `127.0.0.1:8099`. Wer sie von außen braucht, stellt einen Reverse-Proxy mit eigener
   Authentifizierung davor — **nie** einfach `8099:8099`.
3. **`./data` nicht gemountet.** Darin liegen `options.json` **und** `sessions.db`.
   Ohne Volume sind nach jedem Container-Neustart alle noch nicht an Dolibarr
   übertragenen Ladungen verloren.

### Raspberry Pi

Ein **64-Bit-Betriebssystem** ist nötig (Raspberry Pi OS 64-bit, Ubuntu arm64 — also
Pi 4, Pi 5 oder Pi 3 mit 64-Bit-Image). Für 32-Bit (`armv7`) gibt es kein Base-Image,
siehe `build.yaml`. Der Ressourcenbedarf ist gering: Python-Prozess plus SQLite, kein
Browser, keine Datenbank-Engine.

### Betrieb

```bash
docker compose logs -f                 # Live-Log
docker compose restart                 # nach Änderung an data/options.json
docker compose down                    # stoppen (data/ bleibt erhalten)
sqlite3 data/sessions.db "SELECT id,status,total_kwh FROM sessions ORDER BY id DESC LIMIT 10;"
```

Die Konfiguration wird **beim Start** gelesen — nach jeder Änderung an
`data/options.json` ist ein `docker compose restart` nötig.

## Wallbox-Profile (herstellerunabhängige Konfiguration)

`wallbox_profile` schaltet zwischen zwei Betriebsarten um:

- **`alfen_eve`** (Default) — das bewährte, fest verdrahtete Alfen-Verhalten. Auth-Modus und Zustand-Erkennung sind fixiert (Tag hält an, Status-Keyword-Matching); nur die Entity-IDs (`sensor_rfid`/`sensor_energy`/`sensor_state`) bleiben anpassbar, z.B. für einen zweiten Anschluss.
- **`custom`** — Auth-Modus (`auth_mode`) und Zustand-Erkennung (`state_mode`) sind frei kombinierbar (Details unten).

> **Wichtig zur Bedienung der Home-Assistant-Konfigurationsoberfläche:** Home Assistant zeigt in der Addon-Konfiguration **immer alle Felder gleichzeitig** an — es gibt kein automatisches Ein-/Ausblenden je nach gewähltem `wallbox_profile`/`auth_mode`/`state_mode`. Jedes Feld hat inzwischen einen eigenen Hilfetext direkt in der HA-Oberfläche (Tooltip/Beschreibung unter dem Feldnamen), der erklärt, bei welcher Kombination es überhaupt wirksam ist. **Felder, die zur aktuellen Auswahl nicht passen, einfach leer/auf Default lassen — sie werden dann ignoriert.**

### Schritt für Schritt: Custom-Konfiguration einrichten

1. **`wallbox_profile` auf `custom` stellen.** Erst dann werden `auth_mode`/`state_mode` und ihre Zusatzfelder überhaupt ausgewertet (bei `alfen_eve` werden sie ignoriert).
2. **Eine Frage beantworten: "Wie merkt das System, WER laden darf?"** → das ist `auth_mode`, siehe Tabelle unten. Bei `none` `sensor_rfid` leer lassen.
3. **Eine zweite Frage beantworten: "Wie merkt das System, WANN geladen wird bzw. die Ladung endet?"** → das ist `state_mode`, siehe Tabelle unten.
4. **Nur die zu `state_mode` passenden Zusatzfelder ausfüllen:**
   - `state_keywords` → optional `end_keywords`/`pause_keywords` (leer = bewährte Alfen-Defaults, meist ausreichend)
   - `power_threshold` → `power_sensor` + `power_threshold_w` + `end_idle_minutes` ausfüllen
   - `energy_delta` → `end_idle_minutes` ausfüllen (kein extra Sensor nötig, nutzt `sensor_energy`)
   - `external_boolean` → `active_entity` ausfüllen
5. **`sensor_energy` immer setzen** — unabhängig vom Modus, das ist der kumulative kWh-Zähler, aus dem `total_kwh = Ende − Start` berechnet wird.
6. **Speichern → Addon neu starten.** Im Log erscheint beim Start eine Zeile `Wallbox-Profil: custom (auth_mode=..., state_mode=..., ...)` — damit lässt sich sofort prüfen, ob die gewählte Kombination korrekt angekommen ist.
7. **Testen:** einmal einen kompletten Ladevorgang durchspielen (bzw. simulieren) und die Addon-Logs beobachten (`Ladevorgang gestartet` / `Ladevorgang beendet`).

### Typische Ausgangssituationen → passende Kombination

| Deine Hardware-Situation | `auth_mode` | `state_mode` |
|---|---|---|
| Wallbox mit eigenem Status-Sensor und Tag-Sensor (wie Alfen) | `tag_hold` oder `tag_pulse` | `state_keywords` |
| Separater RFID-Leser (z.B. an einem Relais), Wallbox/Zähler ohne Status-Text | `tag_pulse` oder `tag_toggle` | `power_threshold` oder `external_boolean` |
| Vorgeschalteter Zähler (Shelly EM o.ä.) statt Wallbox-eigenem Zähler | beliebig | `power_threshold` (wenn Leistung verfügbar) sonst `energy_delta` |
| Wallbox ganz ohne eigenen Zähler und ohne Statusausgabe | `tag_hold`/`tag_pulse`/`tag_toggle` | `energy_delta` (mit externem Zähler als `sensor_energy`) |
| Reines Monitoring ohne Zugriffskontrolle (keine RFID-Pflicht) | `none` | `power_threshold`, `energy_delta` oder `external_boolean` |
| Eigene, selbst gebaute HA-Logik (Template mit Hysterese/Sonderfällen) | beliebig | `external_boolean` |

**Werte für `auth_mode`** (wer darf laden):

| Wert | Bedeutung |
|---|---|
| `tag_hold` | Tag liegt an, solange geladen wird |
| `tag_pulse` | Tag-Event nur kurz sichtbar (z.B. Wallbox setzt selbst zurück), Ende kommt aus `state_mode` |
| `tag_toggle` | 1. autorisierter Tap = Start, 2. Tap = Ende — unabhängig vom `state_mode` |
| `none` | keine Autorisierungspflicht, Start kommt rein aus `state_mode` (reines Logging/Monitoring) |

**Werte für `state_mode`** (wann wird geladen/beendet):

| Wert | Bedeutung | zusätzliche Optionen (nur bei diesem Wert relevant) |
|---|---|---|
| `state_keywords` | Substring-Match gegen `sensor_state` (Alfen-Standard) | `end_keywords`, `pause_keywords` (leer = Alfen-Defaults) |
| `power_threshold` | Ableitung aus einem Leistungssensor (z.B. vorgeschalteter Shelly EM ohne eigenen Wallbox-Status) | `power_sensor`, `power_threshold_w`, `end_idle_minutes` |
| `energy_delta` | Ende, wenn der kumulative Zähler `end_idle_minutes` lang stillsteht — für Wallboxen ganz ohne Status- oder Leistungssignal | `end_idle_minutes` |
| `external_boolean` | eine on/off-Entity (`active_entity`) bestimmt Start/Ende direkt — z.B. eine selbst gebaute HA-Template-Entity, die Leistung, Hysterese und eigene Pause-Logik kombiniert | `active_entity` |

**Beispiel — Shelly EM als vorgeschalteter Zähler + separater RFID-Leser, ohne Wallbox-Status:**

```yaml
wallbox_profile: custom
auth_mode: tag_pulse
sensor_rfid: sensor.nfc_reader_tag
sensor_energy: sensor.shelly_em_total_kwh
state_mode: power_threshold
power_sensor: sensor.shelly_em_power
power_threshold_w: 200
end_idle_minutes: 10
```

**Beispiel — Tag startet nur, eine selbst gebaute Template-Entity meldet das Ende:**

```yaml
wallbox_profile: custom
auth_mode: tag_pulse
sensor_energy: sensor.shelly_em_total_kwh
state_mode: external_boolean
active_entity: binary_sensor.wallbox_charging_active
```

## Voraussetzungen

- Home Assistant Core mit Alfen-Eve-Integration (Standardprofil) oder einer beliebigen anderen Wallbox/Zähler-Kombination (Custom-Profil, siehe oben)
- Dolibarr 20+ mit installiertem `wallboxbilling`-Modul (aktuelle Version siehe Repo-Root)

## API-Endpoint (Dolibarr-Seite)

Das Addon spricht ausschließlich `POST /custom/wallboxbilling/receive.php` an. Body:

```json
{
  "rfid_hash": "<sha256 hex>",
  "wallbox_id": "meine_wallbox",
  "start_time": "2026-05-19T08:42:00+02:00",
  "end_time":   "2026-05-19T09:15:00+02:00",
  "kwh": 12.345
}
```

Header: `DOLAPIKEY: <gemeinsames API-Token>`. Response:
- **200** mit `{"success": true, "expensereport_id": ..., "line_id": ...}` bei Erfolg
- **200** mit `{"success": false, "message": "Session already exists"}` bei bereits übertragener Session (idempotent, kein Fehler)
- **401** wenn das API-Token falsch/fehlt
- **404** wenn der RFID-Hash keinem Dolibarr-Mitarbeiter zugeordnet ist
- **400** bei fehlenden/ungültigen Feldern (z.B. `kwh` ≤ 0, ungültiges Zeitformat)
- **500** bei internen Dolibarr-Fehlern (Details im Dolibarr-Syslog, nicht in der Response)
- Bei 4xx/5xx: Addon retried automatisch
