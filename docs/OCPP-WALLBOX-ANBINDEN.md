# Wallbox per OCPP anbinden — Schritt für Schritt

Diese Anleitung führt von „ExpenseCharge läuft“ bis zur ersten Ladung, die in der
Dolibarr-Spesenabrechnung steht. Sie gilt für **Standalone** (Docker, z.B. Proxmox-LXC)
und für das **Home-Assistant-Addon**. Wo sich beide unterscheiden, steht es dabei.

```
Wallbox ──OCPP 1.6J (WebSocket, Port 9000)──► ExpenseCharge ──HTTPS──► Dolibarr receive.php
   │                                              │
   └─ prüft jede Karte beim Server ◄──────────────┘  Karten-Freigabe, Zählerstände, Puffer
```

Bei OCPP ist ExpenseCharge der **Zentralserver** der Wallbox: die Wallbox meldet Start,
Ende und Zählerstände selbst und fragt bei jeder Karte nach, ob sie laden darf.
Unbekannte Karten laden nicht.

---

## 0 — Vorher klären

| Frage | Warum |
|---|---|
| Spricht die Wallbox **OCPP 1.6J** (JSON über WebSocket)? | Siehe Tabelle „Unterstützte Wallboxen“ in der [README](../wallbox-dolibarr/README.md#unterstützte-wallboxen). OCPP 2.0.1 allein reicht nicht. |
| Hängt die Wallbox schon an einem **anderen OCPP-Backend** (Cloud, Abrechnungsdienst)? | Eine Wallbox kennt nur **ein** Backend. Wird ExpenseCharge eingetragen, ist das andere weg. |
| Kommst du an die **Installateur-Einstellungen**? | Bei Alfen: ACE Service Installer mit Installateur-Zugang. |
| Erreicht die Wallbox den Server auf **Port 9000/tcp**? | Gleiches Netz/VLAN oder Routing, Firewall-Freigabe nur für das Wallbox-Netz. |
| Hat der Server eine **feste IP**? | Die IP steht fest in der Wallbox. Ändert sie sich (DHCP), ist die Wallbox weg. |
| Ist das **Dolibarr-Modul** eingerichtet? | Token gesetzt, Karten Mitarbeitern zugeordnet — siehe [INSTALL.md, Schritt 2](../INSTALL.md#2--dolibarr-modul-installieren). |

---

## 1 — Server vorbereiten

**Standalone:** ExpenseCharge läuft und die Ersteinrichtung im Browser ist abgeschlossen
(Admin-Konto, Dolibarr-Verbindung) — siehe [INSTALL.md 3.5](../INSTALL.md#35--variante-standalone-docker-ohne-home-assistant-zb-proxmox-lxc).
Der Port 9000 ist in der `docker-compose.yml` schon freigegeben.

**Home-Assistant-Addon:**
1. Addon → *Konfiguration* → Abschnitt **Netzwerk** → bei `9000/tcp` einen Host-Port
   eintragen, z.B. `9000`. Der ist bewusst leer vorbelegt, weil 9000 mit der
   HACS-Integration `lbbrhzn/ocpp` kollidieren würde.
2. In der Konfiguration `session_source: ocpp` setzen, speichern, Addon neu starten.

Kontrolle im Log (Standalone: `docker compose logs -f`, HA: Addon → Log):

```
OCPP-Zentralserver lauscht auf Port 9000 (ws://<host-ip>:9000/<charge-point-id>)
```

---

## 2 — Wallbox in ExpenseCharge eintragen

Du brauchst drei Angaben:

| Angabe | Woher | Beispiel |
|---|---|---|
| **Charge-Point-ID** | Kennung, mit der sich die Wallbox meldet. Alfen: die Seriennummer (Typenschild, ACE Service Installer) | `ACE0123456` |
| **Passwort** | frei wählbar, 16–40 Zeichen — oder erzeugen lassen | `9f3c…` (24 Zeichen) |
| **Name** | frei, erscheint in der Oberfläche und — als Kennung — in der Spesenzeile | `Wallbox 1` |

**Standalone (Browser):** **Wallboxen → Wallbox hinzufügen** (oder Assistent, Schritt 3).
- Charge-Point-ID und Name eintragen.
- `wallbox_id` leer lassen → wird aus dem Namen gebildet (`Wallbox 1` → `Wallbox_1`).
- Passwort leer lassen → ein sicheres wird erzeugt und auf der nächsten Seite **einmal**
  zum Kopieren angezeigt. Jetzt kopieren, es wird nicht wieder im Klartext gezeigt.
- Ist in der Wallbox schon ein Passwort hinterlegt (z.B. nach einer Neuinstallation des
  Servers), genau dieses eintragen — dann muss man an der Wallbox nichts ändern.

**HA-Addon:** in der Konfiguration ergänzen und neu starten:

```yaml
ocpp_charge_points:
  - id: "ACE0123456"
    password: "mindestens-16-zeichen-zufaellig"
    wallbox_id: "wallbox_1"
```

ID noch unbekannt? Diesen Schritt überspringen, Schritt 3 machen und dann in Schritt 4
die ID übernehmen.

---

## 3 — Wallbox einstellen

| Einstellung | Wert |
|---|---|
| Backend-/Central-System-URL | `ws://<Server-IP>:9000/` |
| Protokoll | OCPP 1.6 JSON (auch „OCPP-J 1.6“) |
| Charge-Point-ID / Identity | die ID aus Schritt 2 |
| Security Profile | **1** (Basic Auth über `ws://`) |
| Benutzer (falls abgefragt) | **die Charge-Point-ID** — OCPP schreibt das so vor |
| Passwort / Authorization Key | das Passwort aus Schritt 2 |
| Autorisierung | über das Backend/Central System (nicht „Free charging“, nicht nur lokale Liste) |

**URL:** Die meisten Wallboxen hängen ihre ID selbst an (`ws://…:9000/ACE0123456`). Tut
sie das nicht, die ID selbst anhängen: `ws://<Server-IP>:9000/<Charge-Point-ID>`.
ExpenseCharge nimmt immer das letzte Segment der URL als ID. `wss://` (TLS) geht nur mit
vorgeschaltetem Reverse-Proxy.

### Alfen Eve (Single/Double Pro-line, S-line, NG9xx)

Im **ACE Service Installer** mit Installateur-Zugang an der Wallbox anmelden. Die
Menüpunkte heißen je nach Version leicht anders; gesucht sind:

1. **Connectivity → Backoffice/OCPP**:
   - Backoffice-Verbindung über das Netzwerk (Ethernet bzw. WLAN) aktivieren,
     Protokoll **OCPP 1.6J**
   - **Backoffice-URL** `ws://<Server-IP>:9000/` — Alfen hängt die Seriennummer an
   - **Charge-Box-Identity** = Seriennummer (Vorgabe, so lassen)
   - **Security Profile 1**, Benutzer = Charge-Box-Identity, Passwort aus Schritt 2
2. **Authorization**: Autorisierung durch das **Backoffice**, RFID-Leser aktiv.
   Eine lokale Whitelist bzw. „Free charging“ würde Karten ohne Rückfrage laden lassen.
3. Speichern/Übertragen. Die Wallbox verbindet sich innerhalb von etwa einer Minute,
   sonst einmal neu starten.

Für andere Hersteller stehen die Einstellorte in der Tabelle
„[Unterstützte Wallboxen](../wallbox-dolibarr/README.md#unterstützte-wallboxen)“.

---

## 4 — Verbindung prüfen

**Standalone:** **Wallboxen** — die Wallbox steht als **verbunden**, mit Hersteller und
Modell. **HA:** im Log.

Im Log:

```
Wallbox 'ACE0123456' verbunden (('192.168.101.50', 51234))
[ACE0123456] BootNotification: Alfen BV NG910 (FW 6.4.0)
```

| Was du siehst | Bedeutung | Abhilfe |
|---|---|---|
| `Unbekannte Charge-Point-ID 'XYZ'` | Die Wallbox meldet sich mit einer anderen ID | Standalone: **Wallboxen → Wartende Wallboxen → Übernehmen**. HA: diese ID in `ocpp_charge_points` eintragen |
| `falsche oder fehlende Zugangsdaten` | Passwort oder Benutzer passt nicht | In der Wallbox Passwort neu eintragen, Benutzer = Charge-Point-ID; oder unter **Bearbeiten** ein neues erzeugen |
| `Passwort ist noch der Platzhalter aus der Vorlage` | Vorlagenwert nicht ersetzt | Unter **Bearbeiten** ein Passwort setzen |
| `sendet kein Subprotocol` | Harmlos | — |
| **nichts** im Log | Die Wallbox erreicht den Server nicht | Server-IP/Port in der Wallbox, Firewall (9000/tcp aus dem Wallbox-Netz), VLAN; vom Wallbox-Netz aus `nc -zv <Server-IP> 9000` |
| verbindet und trennt im Minutentakt | zwei Geräte mit derselben ID oder Netzproblem | ID eindeutig machen; `Wallbox … verbindet sich neu` im Log |

---

## 5 — Empfohlene Einstellungen setzen

Damit ExpenseCharge jede Minute einen Zählerstand bekommt und eine ungültige Karte die
Ladung beendet:

**Standalone:** **Wallboxen → (Wallbox) → Konfiguration lesen → Empfohlene übernehmen**.
Die Vorschau zeigt vorher, was sich ändert:

| Schlüssel | Wert |
|---|---|
| `MeterValueSampleInterval` | `60` |
| `MeterValuesSampledData` | `Energy.Active.Import.Register` |
| `StopTransactionOnInvalidId` | `true` |

Meldet die Wallbox `RebootRequired`: unter **Fernbefehle → Neustart der Wallbox (sanft)**.

Dauerhaft bei jedem Wallbox-Start: **Einstellungen → „Empfohlene OCPP-Einstellungen nach
jedem Wallbox-Start setzen“** (HA: `ocpp_apply_recommended_config: true`).

---

## 6 — Karten freigeben

Bei OCPP lädt nur eine Karte, die ExpenseCharge kennt.

1. **Karten → Lernmodus starten**.
2. Karte an die Wallbox halten. Sie erscheint im Lernmodus — auch wenn die Wallbox sie
   gerade abgelehnt hat.
3. Namen vergeben (Vorschläge kommen aus den Dolibarr-Mitarbeitern) und einordnen:
   - **Geschäftlich** — lädt, wird an Dolibarr übertragen
   - **Privat** — lädt, bleibt lokal, erscheint nie in der Spesenabrechnung
4. **Lernmodus beenden**.

Kennt man die Karten-ID schon: **Karten → Karte von Hand eintragen**.

**In Dolibarr** muss jede geschäftliche Karte einem Mitarbeiter zugeordnet sein
(*ExpenseCharge → RFID-Verwaltung*), sonst lehnt Dolibarr die Ladung mit
`RFID not registered` ab. Wer die Ladung bezahlt bekommt, entscheidet Dolibarr;
ExpenseCharge entscheidet nur, **ob** übertragen wird.

---

## 7 — Testladung

1. Karte vorhalten, Fahrzeug anstecken, einige Minuten laden, Ladung beenden.
2. **Wallboxen → (Wallbox)**: Connector-Status wechselt `Preparing` → `Charging` →
   `Finishing`; im **OCPP-Protokoll** stehen `StartTransaction`, `MeterValues`,
   `StopTransaction`.
3. **Ladevorgänge**: die Ladung steht dort mit kWh, Status **ausstehend**.
4. Spätestens nach dem Übertragungsintervall (Vorgabe 5 min) — oder sofort mit
   **Jetzt übertragen** — wechselt der Status auf **übertragen**.
5. In Dolibarr: Spesenabrechnung des Mitarbeiters im Monat des **Ladeendes** → Zeile
   „Wallbox Wallbox_1: 12.50 kWh“.
6. Für die Lohnakte: **Verlauf → Ladenachweis** — je Mitarbeiter eine Seite mit
   Zählerständen, zum Drucken bzw. als PDF.

| Problem | Ursache | Abhilfe |
|---|---|---|
| Wallbox lädt nicht, Log `Karte abgelehnt (nicht unter „Karten“ freigegeben)` | Karte unbekannt | Schritt 6 |
| Ladung **unvollständig** | Wallbox lieferte keinen brauchbaren Endstand | **Ladevorgänge** → kWh vom Fahrzeug/Display eintragen → **Abschließen**, oder **Verwerfen** |
| Ladung **abgelehnt** | Dolibarr kennt die Karte nicht (`RFID not registered`) | Karte in Dolibarr dem Mitarbeiter zuordnen → **Ladevorgänge → Erneut senden**; die übrigen Ladungen laufen derweil weiter |
| Ladung bleibt **ausstehend**, „Letzter Lauf“ zeigt Fehler | Dolibarr lehnt ab oder ist nicht erreichbar | Fehlertext lesen: `401` = Token, `RFID not registered` = Karte in Dolibarr zuordnen, `HTML statt JSON` = Modul nicht aktiv |
| Ladung **privat** | Karte ist als privat eingeordnet | gewollt — unter **Karten** umstellen, falls nicht |
| kWh viel zu klein (Faktor 1000) | Wallbox meldet kWh statt Wh | Ladung wird als unvollständig markiert statt still verworfen → von Hand abschließen; Hersteller-Einstellung prüfen |

---

## 8 — Betrieb

- **Wallbox offline:** sie puffert Start/Stop und schickt sie nach. Die Ladung landet
  im Monat der echten Ladung.
- **Server-Neustart während einer Ladung:** die Ladung bleibt offen und wird mit der
  `StopTransaction` der Wallbox abgeschlossen — es wird kein Zählerstand geraten.
- **Dolibarr offline:** Ladungen bleiben im lokalen Puffer und werden nachgereicht.
- **Ladung läuft länger als 24 h:** Warnung im Log (Schwelle unter **Einstellungen**).
- **Passwort ändern:** **Wallboxen → Bearbeiten → neues Passwort erzeugen**, dann sofort
  in der Wallbox eintragen. Bis dahin wird sie bei der nächsten Neuverbindung abgewiesen.
- **Wallbox tauschen:** neue Charge-Point-ID → neue Wallbox anlegen; dieselbe
  `wallbox_id` verwenden, wenn sie in Dolibarr gleich heißen soll.
- **Sicherheit:** Port 9000 nur aus dem Wallbox-Netz, nie aus dem Internet. Ohne Passwort
  (Security Profile 0) kann jedes Gerät im Netz Ladungen erfinden — immer ein Passwort
  setzen.
