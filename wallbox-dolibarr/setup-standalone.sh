#!/usr/bin/env bash
# shellcheck disable=SC2154  # Variablen setzt ask() per printf -v
# ExpenseCharge standalone einrichten (OCPP-Betrieb): legt data/options.json und
# .env an, erzeugt ein zufälliges OCPP-Passwort und gibt am Ende aus, was in
# der Wallbox einzutragen ist.  Aufruf:  ./setup-standalone.sh
set -euo pipefail
cd "$(dirname "$0")"

TEMPLATE=options.standalone.example.json
OPTIONS=data/options.json

die() { echo "FEHLER: $*" >&2; exit 1; }
ask() {  # ask <Variable> <Frage> [Vorgabe]
    local answer
    read -r -p "$2${3:+ [$3]}: " answer
    printf -v "$1" '%s' "${answer:-${3:-}}"
}

if ! command -v jq >/dev/null; then
    if [ "$(id -u)" = 0 ] && command -v apt-get >/dev/null; then
        echo "jq wird zum sicheren Schreiben der JSON-Datei gebraucht — installiere ..."
        apt-get install -y -qq jq >/dev/null || die "jq konnte nicht installiert werden"
    else
        die "jq fehlt — bitte installieren: sudo apt install jq"
    fi
fi
[ -f "$TEMPLATE" ] || die "$TEMPLATE fehlt — Skript im Ordner wallbox-dolibarr ausführen"

mkdir -p data
if [ -f "$OPTIONS" ]; then
    ask overwrite "$OPTIONS existiert schon. Überschreiben? (alte Datei wird gesichert) j/N" "N"
    [[ "$overwrite" =~ ^[jJyY] ]] || { echo "Abgebrochen, nichts geändert."; exit 0; }
    backup="$OPTIONS.$(date +%Y%m%d-%H%M%S).bak"
    cp -p "$OPTIONS" "$backup"
    echo "Gesichert: $backup"
fi

echo
echo "── Wallbox ──"
echo "Die Charge-Point-ID steht in der Wallbox-Konfiguration (Alfen: Seriennummer,"
echo "z.B. ACE0123456). Falsch? Das Log meldet später 'Unbekannte Charge-Point-ID <ID>'."
ask cp_id "Charge-Point-ID"
[ -n "$cp_id" ] || die "Charge-Point-ID ist Pflicht"
ask wallbox_id "Name der Wallbox in Dolibarr (wallbox_id)" "garage"
ocpp_pw=$(openssl rand -hex 12 2>/dev/null || head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')

echo
echo "── Dolibarr ──"
while :; do
    ask dolibarr_url "Dolibarr-URL (https://...)"
    [[ "$dolibarr_url" =~ ^https?://[^/]+ && "$dolibarr_url" != *example.com* ]] && break
    echo "  Bitte vollständige URL mit http:// oder https:// angeben."
done
read -r -s -p "API-Token (identisch mit dem Dolibarr-Modul, Eingabe unsichtbar): " api_token; echo
[ -n "$api_token" ] || die "API-Token ist Pflicht"

echo
echo "── RFID-Karten ──"
echo "Karten-IDs kommagetrennt (z.B. EFCD083E,AABBCCDD). Leer lassen und später"
echo "in der Web-UI unter 'Karten' anlernen geht auch."
ask cards "Karten" ""

echo
echo "── Web-UI ──"
echo "WEB_BIND: 127.0.0.1 = nur auf diesem Host / per SSH-Tunnel,"
echo "          0.0.0.0   = im ganzen LAN/VPN (dann Anmeldung einrichten!)"
while :; do
    ask web_bind "WEB_BIND" "127.0.0.1"
    [[ "$web_bind" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] && break
    echo "  Bitte eine IPv4-Adresse angeben, z.B. 127.0.0.1 oder 0.0.0.0."
done
[ "$web_bind" = 127.0.0.1 ] && auth_default=n || auth_default=j
ask want_auth "Admin-Konto für die Web-UI gleich hier anlegen? (sonst Ersteinrichtung im Browser) j/n" "$auth_default"
web_user=""; web_pw=""
if [[ "$want_auth" =~ ^[jJyY] ]]; then
    ask web_user "Benutzername" "admin"
    read -r -s -p "Passwort, mind. 10 Zeichen (leer = zufällig erzeugen, Eingabe unsichtbar): " web_pw; echo
    [ -z "$web_pw" ] || [ ${#web_pw} -ge 10 ] || die "Passwort zu kurz (mind. 10 Zeichen)"
    [ -n "$web_pw" ] || web_pw=$(openssl rand -hex 8 2>/dev/null || head -c 8 /dev/urandom | od -An -tx1 | tr -d ' \n')
fi

tmp=$(mktemp)
jq --arg cp "$cp_id" --arg pw "$ocpp_pw" --arg wb "$wallbox_id" \
   --arg url "$dolibarr_url" --arg token "$api_token" --arg cards "$cards" \
   --arg wu "$web_user" --arg wp "$web_pw" '
    .session_source = "ocpp"
  | .ocpp_charge_points = [{id: $cp, password: $pw, wallbox_id: $wb}]
  | .api.dolibarr_url = $url
  | .api.api_token = $token
  | .rfid_whitelist = ($cards | split(",") | map(gsub("^\\s+|\\s+$"; "")) | map(select(length > 0)))
  | .web_auth = {username: $wu, password: $wp}
' "$TEMPLATE" > "$tmp"
jq empty "$tmp" || die "erzeugtes JSON ungültig — nichts geschrieben"
if grep -q -e 'example\.com' -e 'bitte-mindestens-16-zeichen' -e 'gemeinsames-token' "$tmp"; then
    rm -f "$tmp"; die "Vorlagenwerte nicht vollständig ersetzt — nichts geschrieben"
fi
install -m 600 "$tmp" "$OPTIONS"; rm -f "$tmp"

[ -f .env ] || cp .env.example .env
if grep -q '^WEB_BIND=' .env; then
    sed -i "s/^WEB_BIND=.*/WEB_BIND=$web_bind/" .env
else
    echo "WEB_BIND=$web_bind" >> .env
fi
chmod 600 .env

ip=$(hostname -I 2>/dev/null | awk '{print $1}')
case "$web_bind" in
    127.0.0.1) gui="http://127.0.0.1:8099/  (nur auf diesem Host bzw. per SSH-Tunnel)" ;;
    0.0.0.0)   gui="http://${ip:-<host-ip>}:8099/" ;;
    *)         gui="http://$web_bind:8099/" ;;
esac
cat <<MSG

Fertig: $OPTIONS und .env geschrieben (nur für den Eigentümer lesbar).

Web-UI:            $gui
MSG
if [ -n "$web_pw" ]; then
    cat <<MSG
  Benutzer:        $web_user
  Passwort:        $web_pw
MSG
elif [ "$web_bind" != 127.0.0.1 ]; then
    echo "  WARNUNG: im Netz erreichbar OHNE Anmeldung."
fi
cat <<MSG

In der Wallbox eintragen (OCPP 1.6J):
  Backend-URL:      ws://${ip:-<host-ip>}:9000/
  Charge-Point-ID:  $cp_id
  Passwort:         $ocpp_pw

Jetzt starten:
  docker compose up -d --build --force-recreate
  docker compose logs -f
MSG
