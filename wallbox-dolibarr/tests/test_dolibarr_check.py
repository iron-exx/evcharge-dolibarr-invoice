"""'Verbindung testen': jede Fehlerart mit klarer Meldung."""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from admin.dolibarr_check import check_dolibarr  # noqa: E402


class _Dolibarr(BaseHTTPRequestHandler):
    status = 200

    def do_GET(self):
        if self.path.endswith('/custom/wallboxbilling/employees.php'):
            if self.status != 200:
                self.send_response(self.status)
                self.end_headers()
                return
            if self.headers.get('DOLAPIKEY') != 'richtig-123':
                self.send_response(401)
                self.end_headers()
                return
            body = json.dumps({'success': True, 'employees': [{'login': 'a'}, {'login': 'b'}]}).encode()
            self.send_response(200)
            self.send_header('X-Wallboxbilling-Version', '2.3.6')
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture()
def dolibarr():
    srv = HTTPServer(('127.0.0.1', 0), _Dolibarr)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{srv.server_address[1]}', _Dolibarr
    srv.shutdown()
    _Dolibarr.status = 200


def _by_step(steps):
    return {s['step']: s for s in steps}


def test_all_good(dolibarr):
    steps = _by_step(check_dolibarr(dolibarr[0], 'richtig-123'))
    assert all(s['ok'] for s in steps.values())
    assert '2 Mitarbeiter' in steps['Token']['detail']
    assert '2.3.6' in steps['Modul']['detail']


def test_wrong_token(dolibarr):
    steps = _by_step(check_dolibarr(dolibarr[0], 'falsch-123'))
    assert not steps['Token']['ok'] and 'WALLBOXBILLING_API_TOKEN' in steps['Token']['detail']


def test_module_missing(dolibarr):
    dolibarr[1].status = 404
    steps = _by_step(check_dolibarr(dolibarr[0], 'richtig-123'))
    assert not steps['Modul']['ok'] and 'installieren' in steps['Modul']['detail']


def test_dns_failure_names_host_and_next_step():
    steps = check_dolibarr('https://gibt-es-nicht.invalid', 'x' * 10)
    assert len(steps) == 1 and not steps[0]['ok']
    assert 'gibt-es-nicht.invalid nicht auflösbar' in steps[0]['detail']
    assert 'Einstellungen → Dolibarr' in steps[0]['detail']


def test_connection_refused():
    steps = check_dolibarr('http://127.0.0.1:9', 'x' * 10)
    assert steps[-1]['step'] == 'Verbindung' and not steps[-1]['ok']
