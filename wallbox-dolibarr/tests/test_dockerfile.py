"""Das Image muss alles enthalten, was das Addon importiert.

Diese Fehlerklasse ist tückisch: die Tests laufen lokal grün, das Image baut
ohne Fehler, und erst beim Start im Container kommt ein ImportError. Darum wird
hier abgeglichen, was main.py importiert und was das Dockerfile kopiert.
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

# Nicht aus dem Projekt — die kommen per apk bzw. pip ins Image.
_EXTERNAL = {'aiohttp', 'requests', 'yaml', 'ocpp', 'websockets', 'jsonschema', 'urllib3'}


def _own_top_level_imports(path):
    """Projekteigene Top-Level-Module und -Pakete, die eine Datei importiert."""
    tree = ast.parse(open(path, encoding='utf-8').read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split('.')[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split('.')[0])
    own = set()
    for name in names:
        if name in _EXTERNAL or name in sys.stdlib_module_names:
            continue
        if (os.path.isfile(os.path.join(HERE, name + '.py'))
                or os.path.isdir(os.path.join(HERE, name))):
            own.add(name)
    return own


def _copied_in_dockerfile():
    text = open(os.path.join(HERE, 'Dockerfile'), encoding='utf-8').read()
    out = set()
    for target in re.findall(r'^COPY\s+(\S+)', text, re.M):
        out.add(target.rstrip('/').split('/')[-1].replace('.py', ''))
    return out


def test_every_module_main_imports_is_in_the_image():
    needed = _own_top_level_imports(os.path.join(HERE, 'main.py'))
    # Transitiv: was die eigenen Module selbst noch importieren
    seen, queue = set(needed), list(needed)
    while queue:
        name = queue.pop()
        path = os.path.join(HERE, name + '.py')
        if not os.path.isfile(path):
            continue
        for dep in _own_top_level_imports(path):
            if dep not in seen:
                seen.add(dep)
                queue.append(dep)

    copied = _copied_in_dockerfile()
    missing = sorted(n for n in seen if n not in copied)
    assert not missing, (
        "Diese Module werden importiert, aber NICHT ins Image kopiert — "
        f"der Container startet nicht: {missing}")


def test_packages_are_copied_as_directories():
    """Ein Paket braucht den Schrägstrich, sonst kopiert Docker nur den Inhalt
    der Datei an einen falschen Ort."""
    text = open(os.path.join(HERE, 'Dockerfile'), encoding='utf-8').read()
    for name in ('utils', 'ocpp_server', 'modbus_source', 'alfen_source'):
        assert re.search(rf'^COPY\s+{name}/\s', text, re.M), \
            f"COPY {name}/ fehlt oder ohne Schrägstrich"
