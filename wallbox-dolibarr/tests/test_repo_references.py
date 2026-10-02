"""Verweise auf das eigene Repository müssen stimmen.

Hintergrund: das Projekt verwies an 14 Stellen auf `iron-exx/ExpenseChrage` —
ein Name, der nie existierte (Tippfehler: "Chrage"). Niemandem fiel es auf,
weil die URL nie aufgerufen wurde. Die Folgen waren handfest: das in
repository.yaml angegebene Addon-Repository ließ sich in Home Assistant nicht
hinzufügen, die Image-CI lief wegen einer nie erfüllten Bedingung nirgends,
und config.yaml zeigte auf eine Registry, die den Code nicht enthält.

Dieser Test hält das fest, damit sich der nächste tote Verweis nicht wieder
unbemerkt einschleicht.
"""
import os
import re
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OWNER = 'systemwerk-GmbH-Co-KG'
REPO = 'ExpenseCharge'
# GHCR-Namensräume sind immer klein geschrieben.
GHCR_OWNER = OWNER.lower()

# Namen, die es nicht gibt bzw. die nicht mehr gelten. Der Spiegel
# iron-exx/evcharge-dolibarr-invoice existiert und darf genannt werden.
DEAD = ('ExpenseChrage', 'iron-exx/ExpenseCharge', 'ghcr.io/iron-exx')


# Dateien, die die toten Namen absichtlich nennen und daher nicht geprüft werden:
#   - dieser Test selbst (sein Docstring erklärt den Hintergrund)
#   - die Planungsdokumente (sie halten den historischen Stand fest)
# ACHTUNG: geprüft werden nur GETRACKTE Dateien. Eine noch nicht committete Datei
# ist unsichtbar — genau daran ist dieser Test beim ersten Mal in der CI
# fehlgeschlagen, nachdem er lokal gruen war.
_EXEMPT = ('wallbox-dolibarr/tests/test_repo_references.py',
           'docs/superpowers/plans/')


def _tracked_text_files():
    out = subprocess.run(['git', 'ls-files'], cwd=ROOT, capture_output=True, text=True).stdout
    keep = ('.md', '.yaml', '.yml', '.json', '.py', '.php', '.lang', 'Dockerfile')
    for rel in out.splitlines():
        if rel.startswith(_EXEMPT) or rel in _EXEMPT:
            continue
        if rel.endswith(keep) or os.path.basename(rel) == 'Dockerfile':
            path = os.path.join(ROOT, rel)
            if os.path.isfile(path):
                yield rel, path


def test_no_dead_repository_references():
    hits = []
    for rel, path in _tracked_text_files():
        try:
            text = open(path, encoding='utf-8', errors='replace').read()
        except OSError:
            continue
        for dead in DEAD:
            for n, line in enumerate(text.splitlines(), 1):
                if dead in line:
                    hits.append(f'{rel}:{n} enthält {dead!r}')
    assert not hits, ("Verweise auf ein nicht existierendes Repository:\n  "
                      + "\n  ".join(hits))


def test_addon_repository_points_at_this_repo():
    text = open(os.path.join(ROOT, 'repository.yaml'), encoding='utf-8').read()
    assert f'github.com/{OWNER}/{REPO}' in text, \
        "repository.yaml muss auf dieses Repo zeigen, sonst scheitert das " \
        "Hinzufügen in Home Assistant"


def test_addon_image_namespace_matches_the_repo_owner():
    text = open(os.path.join(ROOT, 'wallbox-dolibarr', 'config.yaml'), encoding='utf-8').read()
    m = re.search(r'^image:\s*"([^"]+)"', text, re.M)
    assert m, "config.yaml braucht ein image:-Feld"
    assert m.group(1).startswith(f'ghcr.io/{GHCR_OWNER}/'), \
        f"image: zeigt auf {m.group(1)} — erwartet ghcr.io/{GHCR_OWNER}/…"
    assert m.group(1) == m.group(1).lower(), "GHCR-Namensräume sind klein geschrieben"


def test_image_workflow_runs_in_this_repo():
    """Die Bedingung schützt Forks vor Push-Versuchen — sie muss aber auf das
    eigene Repo passen, sonst baut die CI niemals ein Image."""
    path = os.path.join(ROOT, '.github', 'workflows', 'builder.yaml')
    text = open(path, encoding='utf-8').read()
    conditions = re.findall(r"github\.repository\s*==\s*'([^']+)'", text)
    assert conditions, "builder.yaml hat keine repository-Bedingung"
    for cond in conditions:
        assert cond == f'{OWNER}/{REPO}', \
            f"Bedingung prüft auf {cond!r} — die CI läuft damit hier nie"


def test_workflow_pushes_to_the_matching_registry():
    """Nur die EIGENEN Images prüfen. ghcr.io/home-assistant/…-base ist das
    Basis-Image, das gezogen wird — kein Push-Ziel."""
    text = open(os.path.join(ROOT, '.github', 'workflows', 'builder.yaml'),
                encoding='utf-8').read()
    # [^\n]*? statt [^\s]*: zwischen Namensraum und Image-Namen steht
    # ${{ matrix.arch }} — mit Leerzeichen.
    own = re.findall(r'ghcr\.io/([a-z0-9._-]+)/[^\n]*?addon-expensecharge', text)
    assert own, "builder.yaml pusht kein Addon-Image"
    for ref in own:
        assert ref == GHCR_OWNER, f"Workflow pusht nach ghcr.io/{ref}/ statt {GHCR_OWNER}"


def test_runtime_data_directory_is_never_committed():
    """data/ enthält options.json mit Wallbox-Passwort und Dolibarr-Token. Die
    Repos sind öffentlich — ein versehentliches 'git add -A' auf dem Server
    würde beides veröffentlichen.

    Der alte Eintrag '/data/' in der Root-.gitignore griff NUR für data/ im
    Repo-Wurzelverzeichnis, nicht für wallbox-dolibarr/data/, wo
    docker-compose das Volume anlegt."""
    for rel in ('wallbox-dolibarr/data/options.json',
                'wallbox-dolibarr/data/sessions.db',
                'wallbox-dolibarr/data/irgendwas.json'):
        r = subprocess.run(['git', 'check-ignore', '-q', '--no-index', rel], cwd=ROOT)
        assert r.returncode == 0, f"{rel} wird NICHT ignoriert — Zugangsdaten könnten ins öffentliche Repo"
