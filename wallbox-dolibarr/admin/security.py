"""Admin-Konto, signierte Sitzungen, Sperre nach Fehlversuchen.

- Passwort: scrypt (Standardbibliothek) mit Salt, in data/admin.json —
  nie im Klartext, nie in options.json.
- Sitzung: signiertes Cookie (HMAC, Schlüssel in data/secret.key). Übersteht
  Neustarts; Abmelden erhöht die "epoch" des Kontos und macht alle bisher
  ausgegebenen Cookies ungültig.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

_SCRYPT = dict(n=2 ** 14, r=8, p=1, dklen=32)
SESSION_SECONDS = 12 * 3600
MAX_FAILURES = 5
LOCK_SECONDS = 300


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return 'scrypt$' + base64.b64encode(salt).decode() + '$' + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split('$')
        if scheme != 'scrypt':
            return False
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), **_SCRYPT)
        return hmac.compare_digest(actual, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = 'scrypt$' + base64.b64encode(b'0' * 16).decode() + '$' + base64.b64encode(b'0' * 32).decode()


def _write_private(path: str, text: str) -> None:
    tmp = f'{path}.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


ROLES = ('buchhaltung', 'mitarbeiter')      # dazu 'admin' (data/admin.json)


class AccountStore:
    """Das Admin-Konto (data/admin.json) plus weitere Konten mit Rolle (data/users.json).

    buchhaltung: alles lesen und exportieren, nichts ändern.
    mitarbeiter: nur die eigenen Ladungen (über die zugeordneten Karten-Hash-Präfixe).
    """

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, 'admin.json')
        self.users_path = os.path.join(data_dir, 'users.json')
        key_path = os.path.join(data_dir, 'secret.key')
        if not os.path.exists(key_path):
            _write_private(key_path, secrets.token_hex(32))
        with open(key_path) as f:
            self._key = bytes.fromhex(f.read().strip())

    def _load(self):
        try:
            with open(self.path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _users(self) -> list:
        try:
            with open(self.users_path) as f:
                users = json.load(f)
            return users if isinstance(users, list) else []
        except (OSError, ValueError):
            return []

    def _save_users(self, users: list) -> None:
        _write_private(self.users_path, json.dumps(users))

    def _account(self, username: str):
        """→ (konto, rolle) oder (None, None)."""
        acc = self._load()
        if acc and acc.get('username') == username:
            return acc, 'admin'
        for user in self._users():
            if user.get('username') == username:
                return user, user.get('role')
        return None, None

    def exists(self) -> bool:
        return self._load() is not None

    def username(self) -> str:
        return (self._load() or {}).get('username', '')

    def create(self, username: str, password: str) -> None:
        _write_private(self.path, json.dumps(
            {'username': username, 'password': hash_password(password), 'epoch': 0}))

    def check(self, username: str, password: str) -> bool:
        acc, _ = self._account(username)
        # Passwort immer prüfen, damit die Laufzeit den Benutzernamen nicht verrät.
        ok_pw = verify_password(password, (acc or {}).get('password') or _DUMMY_HASH)
        return bool(acc) and ok_pw

    def role(self, username: str):
        return self._account(username)[1] if username else None

    def cards(self, username: str) -> list:
        acc, role = self._account(username)
        return list((acc or {}).get('cards') or []) if role == 'mitarbeiter' else []

    # -- weitere Konten ----------------------------------------------------------
    def list_users(self) -> list:
        return [{k: v for k, v in u.items() if k != 'password'} for u in self._users()]

    def add_user(self, username: str, password: str, role: str, cards=()) -> None:
        if role not in ROLES:
            raise ValueError('Rolle: Buchhaltung oder Mitarbeiter')
        if self._account(username)[0] is not None:
            raise ValueError(f'Benutzer {username} gibt es schon')
        users = self._users()
        users.append({'username': username, 'password': hash_password(password), 'role': role,
                      'cards': list(cards), 'epoch': 0})
        self._save_users(users)

    def update_cards(self, username: str, cards) -> None:
        users = self._users()
        for u in users:
            if u.get('username') == username:
                u['cards'] = list(cards)
        self._save_users(users)

    def remove_user(self, username: str) -> None:
        self._save_users([u for u in self._users() if u.get('username') != username])

    def change_password(self, password: str, username: str = None) -> None:
        """Neues Passwort; alle bestehenden Sitzungen dieses Kontos werden ungültig."""
        if username is None or self.role(username) == 'admin':
            acc = self._load()
            acc['password'] = hash_password(password)
            acc['epoch'] = acc.get('epoch', 0) + 1
            _write_private(self.path, json.dumps(acc))
            return
        users = self._users()
        for u in users:
            if u.get('username') == username:
                u['password'] = hash_password(password)
                u['epoch'] = u.get('epoch', 0) + 1
        self._save_users(users)

    # -- Sitzungen -------------------------------------------------------------
    def _sign(self, payload: str) -> str:
        return hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()

    def issue(self, username: str = None, now: float = None) -> str:
        acc, _ = self._account(username) if username else (self._load(), 'admin')
        payload = f"{acc['username']}|{int((now or time.time()) + SESSION_SECONDS)}|{acc.get('epoch', 0)}"
        return f'{payload}|{self._sign(payload)}'

    def session_user(self, cookie: str, now: float = None):
        """Benutzername zu einem gültigen Sitzungs-Cookie, sonst None."""
        if not cookie or cookie.count('|') != 3 or not self.exists():
            return None
        payload, sig = cookie.rsplit('|', 1)
        if not hmac.compare_digest(sig.encode(), self._sign(payload).encode()):
            return None
        user, expires, epoch = payload.split('|')
        acc, _ = self._account(user)
        if acc is None or epoch != str(acc.get('epoch', 0)):
            return None
        if int(expires) < (now or time.time()):
            return None
        return user

    def revoke(self, username: str) -> None:
        """Alle Sitzungen dieses Kontos beenden (Abmelden)."""
        if self.role(username) == 'admin':
            self.revoke_all()
            return
        users = self._users()
        for u in users:
            if u.get('username') == username:
                u['epoch'] = u.get('epoch', 0) + 1
        self._save_users(users)

    def revoke_all(self) -> None:
        acc = self._load()
        if acc:
            acc['epoch'] = acc.get('epoch', 0) + 1
            _write_private(self.path, json.dumps(acc))


class LoginLimiter:
    """Sperrt eine Absenderadresse nach MAX_FAILURES Fehlversuchen für LOCK_SECONDS."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._failures = {}   # key → (anzahl, gesperrt_bis)

    def locked_for(self, key: str) -> int:
        _, until = self._failures.get(key, (0, 0))
        return max(0, int(until - self._clock() + 0.999))

    def failure(self, key: str) -> None:
        count, until = self._failures.get(key, (0, 0))
        if until and until <= self._clock():
            count = 0          # Sperre abgelaufen → neu zählen
        count += 1
        self._failures[key] = (count, self._clock() + LOCK_SECONDS if count >= MAX_FAILURES else 0)

    def success(self, key: str) -> None:
        self._failures.pop(key, None)
