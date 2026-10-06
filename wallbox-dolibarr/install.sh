#!/usr/bin/env bash
# ExpenseCharge in einem Befehl installieren (Debian, Ubuntu, Raspberry Pi OS 64-bit):
#
#   curl -fsSL https://raw.githubusercontent.com/systemwerk-GmbH-Co-KG/ExpenseCharge/main/wallbox-dolibarr/install.sh | sudo bash
#
# Installiert bei Bedarf git und Docker, holt den Code nach /opt/ExpenseCharge,
# legt die Grundkonfiguration an, startet ExpenseCharge und zeigt Adresse und
# Einrichtungscode. Ein zweiter Aufruf aktualisiert nur (wie update.sh).
set -euo pipefail

REPO="https://github.com/systemwerk-GmbH-Co-KG/ExpenseCharge.git"
DIR="${EC_DIR:-/opt/ExpenseCharge}"
say()  { printf '\n\033[1;32m▶ %s\033[0m\n' "$*"; }
fail() { printf '\n\033[1;31m✖ %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "Bitte als root ausführen (… | sudo bash)."
command -v apt-get >/dev/null || fail "Nur für Debian/Ubuntu/Raspberry Pi OS – sonst siehe INSTALL.md."
case "$(uname -m)" in x86_64|aarch64|arm64) ;; *) fail "64-Bit-System nötig (gefunden: $(uname -m))." ;; esac

if ! command -v git >/dev/null || ! command -v curl >/dev/null; then
  say "Installiere git und curl"
  apt-get update -qq && apt-get install -y -qq git curl ca-certificates >/dev/null
fi

if ! docker compose version >/dev/null 2>&1; then
  say "Installiere Docker (offizielles Installationsskript von get.docker.com)"
  curl -fsSL https://get.docker.com | sh
fi

if [ -d "$DIR/.git" ]; then
  say "ExpenseCharge ist schon installiert – aktualisiere"
  exec "$DIR/wallbox-dolibarr/update.sh"
fi

say "Hole ExpenseCharge nach $DIR"
git clone --quiet "$REPO" "$DIR"
cd "$DIR/wallbox-dolibarr"

mkdir -p data
[ -f data/options.json ] || cp options.standalone.example.json data/options.json
if [ ! -f .env ]; then
  printf 'WEB_BIND=0.0.0.0\nTZ=%s\n' "$(cat /etc/timezone 2>/dev/null || echo Europe/Berlin)" > .env
fi

say "Baue und starte ExpenseCharge (beim ersten Mal 1–3 Minuten)"
docker compose up -d --build

say "Warte auf den Start"
CODE=""
for _ in $(seq 1 90); do
  CODE=$(docker compose logs expensecharge 2>/dev/null | grep -o 'Einrichtungscode: [0-9-]*' | tail -1 | cut -d' ' -f2 || true)
  [ -n "$CODE" ] && break
  sleep 2
done

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
printf '\n\033[1;32m✔ ExpenseCharge läuft.\033[0m\n\n'
printf '  Im Browser öffnen:   http://%s:8099/\n' "${IP:-<IP-dieses-Servers>}"
if [ -n "$CODE" ]; then
  printf '  Einrichtungscode:    %s\n' "$CODE"
else
  printf '  Einrichtungscode:    docker compose -f %s/wallbox-dolibarr/docker-compose.yml logs | grep Einrichtungscode\n' "$DIR"
fi
printf '\n  Der Assistent im Browser führt durch den Rest (Admin-Konto, Dolibarr, Wallbox, Karten).\n'
printf '  Später aktualisieren:  %s/wallbox-dolibarr/update.sh\n\n' "$DIR"
