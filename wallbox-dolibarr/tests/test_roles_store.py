"""Mehrere Konten mit Rollen: Admin (admin.json) plus Buchhaltung/Mitarbeiter (users.json)."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from admin.security import AccountStore  # noqa: E402


@pytest.fixture()
def acc(tmp_path):
    a = AccountStore(str(tmp_path))
    a.create('admin', 'admin-passwort-1')
    return a


def test_roles_login_and_sessions(acc):
    acc.add_user('buchhaltung', 'bh-passwort-12', 'buchhaltung')
    acc.add_user('mmueller', 'mm-passwort-12', 'mitarbeiter', cards=['84610f4c410a5cbf'])
    assert acc.check('mmueller', 'mm-passwort-12') and not acc.check('mmueller', 'admin-passwort-1')
    assert acc.role('admin') == 'admin' and acc.role('buchhaltung') == 'buchhaltung' and acc.role('x') is None
    assert acc.cards('mmueller') == ['84610f4c410a5cbf']
    cookie = acc.issue('mmueller')
    assert acc.session_user(cookie) == 'mmueller'
    admin_cookie = acc.issue()
    acc.revoke('mmueller')
    assert acc.session_user(cookie) is None and acc.session_user(admin_cookie) == 'admin', "nur das eigene Konto"


def test_password_change_and_remove(acc):
    acc.add_user('bh', 'bh-passwort-12', 'buchhaltung')
    cookie = acc.issue('bh')
    acc.change_password('neues-passwort-1', 'bh')
    assert acc.check('bh', 'neues-passwort-1') and acc.session_user(cookie) is None
    acc.remove_user('bh')
    assert not acc.check('bh', 'neues-passwort-1') and acc.list_users() == []


def test_names_unique_and_roles_validated(acc):
    with pytest.raises(ValueError):
        acc.add_user('admin', 'irgendwas-1234', 'buchhaltung')
    acc.add_user('bh', 'bh-passwort-12', 'buchhaltung')
    with pytest.raises(ValueError):
        acc.add_user('bh', 'bh-passwort-12', 'buchhaltung')
    with pytest.raises(ValueError):
        acc.add_user('y', 'bh-passwort-12', 'chef')
    assert 'password' not in acc.list_users()[0], "Hash nie nach außen"
