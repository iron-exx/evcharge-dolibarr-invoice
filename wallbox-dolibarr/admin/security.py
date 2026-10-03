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


def _write_private(path: str, text: str) -> None:
    tmp = f'{path}.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class AccountStore:
    """Genau ein Admin-Konto (data/admin.json)."""

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, 'admin.json')
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

    def exists(self) -> bool:
        return self._load() is not None

    def username(self) -> str:
        return (self._load() or {}).get('username', '')

    def create(self, username: str, password: str) -> None:
        _write_private(self.path, json.dumps(
            {'username': username, 'password': hash_password(password), 'epoch': 0}))

    def change_password(self, password: str) -> None:
        """Neues Passwort; alle bestehenden Sitzungen werden ungültig."""
        acc = self._load()
        acc['password'] = hash_password(password)
        acc['epoch'] = acc.get('epoch', 0) + 1
        _write_private(self.path, json.dumps(acc))

    def check(self, username: str, password: str) -> bool:
        acc = self._load()
        if not acc:
            return False
        # Passwort immer prüfen, damit die Laufzeit den Benutzernamen nicht verrät.
        ok_pw = verify_password(password, acc['password'])
        return ok_pw and hmac.compare_digest(username.encode(), acc['username'].encode())

    # -- Sitzungen -------------------------------------------------------------
    def _sign(self, payload: str) -> str:
        return hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()

    def issue(self, now: float = None) -> str:
        acc = self._load()
        payload = f"{acc['username']}|{int((now or time.time()) + SESSION_SECONDS)}|{acc.get('epoch', 0)}"
        return f'{payload}|{self._sign(payload)}'

    def session_user(self, cookie: str, now: float = None):
        """Benutzername zu einem gültigen Sitzungs-Cookie, sonst None."""
        acc = self._load()
        if not acc or not cookie or cookie.count('|') != 3:
            return None
        payload, sig = cookie.rsplit('|', 1)
        if not hmac.compare_digest(sig.encode(), self._sign(payload).encode()):
            return None
        user, expires, epoch = payload.split('|')
        if user != acc['username'] or epoch != str(acc.get('epoch', 0)):
            return None
        if int(expires) < (now or time.time()):
            return None
        return user

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
