"""Die Vorgaben in config.yaml müssen die Schema-Prüfung des HA-Supervisors bestehen.

Der Supervisor verlangt jedes verschachtelte Objekt aus dem Schema — auch wenn
alle Felder darin optional sind. Fehlte modbus.registers.state in den Vorgaben,
ließ sich die Konfiguration nicht speichern: "Missing option 'state' in registers".
"""
import os

import yaml

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _missing(schema, options, path):
    """Nachbau von supervisor.addons.options._check_missing_options (rekursiv)."""
    out = []
    for key, sub in schema.items():
        if key not in options:
            optional = (isinstance(sub, str) and sub.endswith('?')) or \
                       (isinstance(sub, list) and sub and str(sub[0]).endswith('?'))
            if not optional:
                out.append(f'{path}{key}')
        elif isinstance(sub, dict) and isinstance(options[key], dict):
            out += _missing(sub, options[key], f'{path}{key}.')
    return out


def test_default_options_contain_every_required_option():
    cfg = yaml.safe_load(open(os.path.join(HERE, 'config.yaml'), encoding='utf-8'))
    assert _missing(cfg['schema'], cfg['options'], '') == []
