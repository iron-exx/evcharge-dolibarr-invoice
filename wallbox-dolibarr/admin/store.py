"""options.json atomar schreiben (mit Backups) und Audit-Log."""
import copy
import json
import os
from datetime import datetime

MAX_BACKUPS = 10
SECRET_HINTS = ('password', 'token', 'secret', 'api_key')


def is_secret(field: str) -> bool:
    return any(h in field.lower() for h in SECRET_HINTS)


def mask(value) -> str:
    """'••••abcd' — nur die letzten 4 Zeichen, bei kurzen Werten gar keine."""
    text = '' if value is None else str(value)
    if not text:
        return '(leer)'
    return '••••' + (text[-4:] if len(text) >= 12 else '')


class ConfigStore:
    """Liest und schreibt <data>/options.json.

    Schreiben: temporäre Datei + fsync + rename (nie eine halbe Datei), vorher
    Kopie nach options.json.bak.<Zeitstempel>; die ältesten über MAX_BACKUPS
    werden gelöscht.
    """

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.path = os.path.join(data_dir, 'options.json')

    def load(self) -> dict:
        try:
            with open(self.path) as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def _backup(self) -> None:
        if not os.path.exists(self.path):
            return
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        with open(self.path, 'rb') as src:
            data = src.read()
        fd = os.open(f'{self.path}.bak.{stamp}', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as dst:
            dst.write(data)
        backups = sorted(n for n in os.listdir(self.data_dir) if n.startswith('options.json.bak.'))
        for old in backups[:-MAX_BACKUPS]:
            os.remove(os.path.join(self.data_dir, old))

    def write(self, config: dict) -> None:
        text = json.dumps(config, indent=2, ensure_ascii=False) + '\n'
        json.loads(text)   # nie etwas schreiben, das sich nicht wieder lesen lässt
        self._backup()
        tmp = f'{self.path}.tmp'
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def update(self, changes: dict, live_config: dict = None) -> list:
        """Setzt Felder ('api.api_token' → Wert), schreibt, gibt [(feld, alt, neu)] zurück.

        live_config (die im Prozess wirksame Konfiguration) wird gleich mit
        angepasst, damit Hot-Reload-fähige Teile den neuen Wert sehen.
        """
        config = self.load()
        diff = []
        for field, value in changes.items():
            old = _get(config, field)
            if old != value:
                diff.append((field, copy.deepcopy(old), copy.deepcopy(value)))
                _set(config, field, value)
                if live_config is not None:
                    _set(live_config, field, copy.deepcopy(value))
        if diff:
            self.write(config)
        return diff

    def remove(self, key: str) -> None:
        config = self.load()
        if key in config:
            del config[key]
            self.write(config)


def _get(config: dict, field: str):
    node = config
    for part in field.split('.'):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _set(config: dict, field: str, value) -> None:
    parts = field.split('.')
    node = config
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value


def _display(field: str, value) -> str:
    if is_secret(field):
        return mask(value)
    if isinstance(value, list) and value and isinstance(value[0], dict):
        # z.B. ocpp_charge_points: Passwörter darin maskieren
        value = [{k: (mask(v) if is_secret(k) else v) for k, v in item.items()} for item in value]
    return json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value


class AuditLog:
    """Eine JSON-Zeile je Änderung in <data>/audit.log. Geheimnisse nur maskiert."""

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, 'audit.log')

    def record(self, user: str, field: str, old=None, new=None, note: str = '') -> None:
        entry = {'time': datetime.now().isoformat(timespec='seconds'), 'user': user or '-',
                 'field': field, 'old': _display(field, old), 'new': _display(field, new)}
        if note:
            entry['note'] = note
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, 'a') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    def record_diff(self, user: str, diff: list, note: str = '') -> None:
        for field, old, new in diff:
            self.record(user, field, old, new, note)

    def entries(self, limit: int = 200) -> list:
        try:
            with open(self.path) as f:
                lines = f.readlines()[-limit:]
        except FileNotFoundError:
            return []
        out = []
        for line in reversed(lines):
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out


