#!/usr/bin/env bash
# ExpenseCharge aktualisieren:   sudo /opt/ExpenseCharge/wallbox-dolibarr/update.sh
#
# Holt die neue Version, baut neu und startet. Konfiguration, Ladungen und
# Konten (data/, .env) bleiben unberührt; vorher wird ein Backup angelegt.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
say()  { printf '\n\033[1;32m▶ %s\033[0m\n' "$*"; }
fail() { printf '\n\033[1;31m✖ %s\033[0m\n' "$*" >&2; exit 1; }

[ -d data ] || fail "Kein data/-Verzeichnis – ist das die richtige Installation? ($(pwd))"

say "Sichere data/ und .env"
mkdir -p data/backups
tar czf "data/backups/vor-update-$(date +%Y%m%d-%H%M%S).tgz" --exclude=data/backups --exclude=data/caddy data .env 2>/dev/null \
  || tar czf "data/backups/vor-update-$(date +%Y%m%d-%H%M%S).tgz" --exclude=data/backups --exclude=data/caddy data

say "Hole die neue Version"
git pull --ff-only || fail "git pull ging nicht – lokal geänderte Dateien? Anzeigen: git status"

say "Baue neu und starte (1–3 Minuten)"
docker compose up -d --build --force-recreate
docker image prune -f >/dev/null 2>&1 || true

VERSION=$(grep -m1 '^version:' config.yaml | tr -d '"' | awk '{print $2}')
printf '\n\033[1;32m✔ Aktualisiert auf %s.\033[0m Log: docker compose logs -f\n\n' "${VERSION:-?}"
