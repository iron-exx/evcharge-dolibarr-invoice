"""Phase 1 Bausteine: Passwort-Hash, Sitzungen, Sperre, atomares Schreiben, Audit, Validierung."""
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from admin import validate  # noqa: E402
from admin.security import (AccountStore, LoginLimiter, hash_password,  # noqa: E402
                            verify_password, SESSION_SECONDS)
from admin.store import AuditLog, ConfigStore, MAX_BACKUPS, mask  # noqa: E402


# -- Passwort & Sitzung -------------------------------------------------------

def test_password_hash_is_salted_and_verifiable():
    a, b = hash_password('geheim-12345'), hash_password('geheim-12345')
    assert a != b and 'geheim' not in a
    assert verify_password('geheim-12345', a)
    assert not verify_password('falsch', a)
    assert not verify_password('x', 'kaputt')


def test_account_file_has_no_plaintext(tmp_path):
    acc = AccountStore(str(tmp_path))
    acc.create('admin', 'sehr-geheim-123')
    raw = (tmp_path / 'admin.json').read_text()
    assert 'sehr-geheim-123' not in raw
    assert oct((tmp_path / 'admin.json').stat().st_mode)[-3:] == '600'
    assert acc.check('admin', 'sehr-geheim-123')
    assert not acc.check('root', 'sehr-geheim-123')


def test_session_cookie_valid_expired_tampered_revoked(tmp_path):
    acc = AccountStore(str(tmp_path))
    acc.create('admin', 'sehr-geheim-123')
    cookie = acc.issue(now=1000)
    assert acc.session_user(cookie, now=1001) == 'admin'
    assert acc.session_user(cookie, now=1000 + SESSION_SECONDS + 1) is None
    assert acc.session_user(cookie.replace('admin', 'root', 1), now=1001) is None
    assert acc.session_user('', now=1001) is None
    # Neustart: neue AccountStore-Instanz, derselbe Schlüssel → Sitzung bleibt
    assert AccountStore(str(tmp_path)).session_user(cookie, now=1001) == 'admin'
    acc.revoke_all()
    assert acc.session_user(cookie, now=1001) is None


def test_limiter_locks_after_five_failures_for_five_minutes():
    t = [0.0]
    lim = LoginLimiter(clock=lambda: t[0])
    for _ in range(4):
        lim.failure('1.2.3.4')
    assert lim.locked_for('1.2.3.4') == 0
    lim.failure('1.2.3.4')
    assert 290 < lim.locked_for('1.2.3.4') <= 300
    assert lim.locked_for('5.6.7.8') == 0, "andere Adresse nicht betroffen"
    t[0] = 301
    assert lim.locked_for('1.2.3.4') == 0
    lim.failure('1.2.3.4')
    assert lim.locked_for('1.2.3.4') == 0, "nach Ablauf wird neu gezählt"


# -- Atomares Schreiben & Backups ----------------------------------------------

def test_update_writes_atomically_with_backup(tmp_path):
    (tmp_path / 'options.json').write_text(json.dumps({'api': {'dolibarr_url': 'alt'}, 'x': 1}))
    store = ConfigStore(str(tmp_path))
    live = {'api': {'dolibarr_url': 'alt'}, 'x': 1, 'aus_env': True}
    diff = store.update({'api.dolibarr_url': 'neu', 'x': 1}, live)
    assert diff == [('api.dolibarr_url', 'alt', 'neu')]
    assert json.loads((tmp_path / 'options.json').read_text())['api']['dolibarr_url'] == 'neu'
    assert live['api']['dolibarr_url'] == 'neu' and live['aus_env'] is True
    backups = [n for n in os.listdir(tmp_path) if n.startswith('options.json.bak.')]
    assert len(backups) == 1
    assert 'alt' in (tmp_path / backups[0]).read_text()
    assert not (tmp_path / 'options.json.tmp').exists()
    assert oct((tmp_path / 'options.json').stat().st_mode)[-3:] == '600'


def test_no_change_no_write(tmp_path):
    (tmp_path / 'options.json').write_text('{"x": 1}')
    assert ConfigStore(str(tmp_path)).update({'x': 1}) == []
    assert not [n for n in os.listdir(tmp_path) if '.bak.' in n]


def test_at_most_ten_backups(tmp_path):
    store = ConfigStore(str(tmp_path))
    for i in range(MAX_BACKUPS + 5):
        store.update({'n': i})
    backups = sorted(n for n in os.listdir(tmp_path) if n.startswith('options.json.bak.'))
    assert len(backups) == MAX_BACKUPS
    assert '"n": 13' in (tmp_path / backups[-1]).read_text(), "die neuesten bleiben"


def test_failed_write_keeps_old_file(tmp_path, monkeypatch):
    (tmp_path / 'options.json').write_text('{"x": 1}')
    store = ConfigStore(str(tmp_path))
    monkeypatch.setattr(os, 'replace', lambda *a: (_ for _ in ()).throw(OSError('Platte voll')))
    with pytest.raises(OSError):
        store.update({'x': 2})
    assert json.loads((tmp_path / 'options.json').read_text()) == {'x': 1}


def test_remove_key(tmp_path):
    (tmp_path / 'options.json').write_text('{"web_auth": {"password": "pw"}, "x": 1}')
    ConfigStore(str(tmp_path)).remove('web_auth')
    assert json.loads((tmp_path / 'options.json').read_text()) == {'x': 1}


# -- Audit ------------------------------------------------------------------------

def test_audit_masks_secrets(tmp_path):
    log = AuditLog(str(tmp_path))
    log.record('admin', 'api.api_token', 'altes-token-abcd', 'neues-token-wxyz-1234')
    log.record('admin', 'ocpp_charge_points', [], [{'id': 'CP1', 'password': 'pw-pw-pw-pw-pw-pw-99'}])
    log.record('admin', 'api.dolibarr_url', 'https://a', 'https://b')
    raw = (tmp_path / 'audit.log').read_text()
    assert 'altes-token' not in raw and 'neues-token' not in raw and 'pw-pw-pw' not in raw
    entries = log.entries()
    assert entries[0]['new'] == 'https://b', "neueste zuerst"
    assert entries[2]['new'] == mask('neues-token-wxyz-1234') == '••••1234'
    assert 'CP1' in entries[1]['new']


def test_mask():
    assert mask('') == '(leer)'
    assert mask('kurz') == '••••'
    assert mask('ein-langes-token-9876') == '••••9876'


# -- Validierung --------------------------------------------------------------------

@pytest.mark.parametrize('fn,good,bad', [
    (validate.username, 'admin', ['ab', 'mit leer', 'x' * 33]),
    (validate.dolibarr_url, 'https://erp.firma.de', ['erp.firma.de', 'ftp://x', 'https://erp.example.com']),
    (validate.api_token, 'abcdefgh', ['kurz', 'mit leerzeichen drin']),
    (validate.charge_point_id, 'ACE0123456', ['', 'a/b', 'x' * 49]),
    (validate.wallbox_id, 'garage', ['', 'a b']),
    (validate.ocpp_password, 'a' * 16, ['a' * 15, 'a' * 41, 'a' * 15 + '\n']),
    (validate.rfid, 'EFCD083E', ['ABC', 'EF-CD', 'A' * 21]),
])
def test_validators(fn, good, bad):
    assert fn(good)
    for value in bad:
        with pytest.raises(ValueError):
            fn(value)


def test_admin_password_rules():
    assert validate.admin_password('0123456789', '0123456789')
    with pytest.raises(ValueError, match='mindestens'):
        validate.admin_password('kurz', 'kurz')
    with pytest.raises(ValueError, match='überein'):
        validate.admin_password('0123456789', '0123456780')


def test_card_lines():
    assert validate.card_lines('efcd083e; Firmenwagen\n\n04A1B2C3, Pool\nEFCD083E') == \
        [('EFCD083E', 'Firmenwagen'), ('04A1B2C3', 'Pool')]
    with pytest.raises(ValueError, match='XY'):
        validate.card_lines('XY')


# -- main: Warnung und Übernahme von web_auth ----------------------------------------

def test_exposed_without_account_warns(monkeypatch, caplog):
    import main
    monkeypatch.delenv('SUPERVISOR_TOKEN', raising=False)
    monkeypatch.setenv('WEB_BIND', '0.0.0.0')
    main.warn_if_web_exposed(False)
    assert any(r.levelname == 'WARNING' and 'Ersteinrichtung' in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize('bind,has_account,sup', [('127.0.0.1', False, None), ('0.0.0.0', True, None),
                                                  ('0.0.0.0', False, 'x'), (None, False, None)])
def test_no_warning_when_safe(monkeypatch, caplog, bind, has_account, sup):
    import main
    for k, v in (('WEB_BIND', bind), ('SUPERVISOR_TOKEN', sup)):
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    main.warn_if_web_exposed(has_account)
    assert not [r for r in caplog.records if r.levelname == 'WARNING']


def test_web_auth_becomes_hashed_admin_account(tmp_path, monkeypatch):
    import main
    monkeypatch.setenv('EXPENSECHARGE_DATA', str(tmp_path))
    (tmp_path / 'options.json').write_text(json.dumps(
        {'web_auth': {'username': 'chef', 'password': 'altes-klartext-pw'}, 'x': 1}))
    monkeypatch.setattr(main, 'current_config', json.loads((tmp_path / 'options.json').read_text()))
    monkeypatch.setattr(main, 'session_manager', None)
    ctx = main.build_admin_context()
    assert ctx.accounts.check('chef', 'altes-klartext-pw')
    assert ctx.setup_code is None
    assert 'altes-klartext-pw' not in (tmp_path / 'options.json').read_text()
    assert 'web_auth' not in main.current_config


def test_no_account_logs_setup_code(tmp_path, monkeypatch, caplog):
    import main
    monkeypatch.setenv('EXPENSECHARGE_DATA', str(tmp_path))
    monkeypatch.setattr(main, 'current_config', {})
    ctx = main.build_admin_context()
    assert ctx.setup_code and ctx.setup_code in caplog.text
