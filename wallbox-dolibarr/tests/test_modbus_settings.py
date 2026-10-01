"""Auflösung der Modbus-Konfiguration."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from modbus_source.settings import ModbusConfigError, resolve_modbus_settings  # noqa: E402

MINIMAL = {"session_source": "modbus",
           "modbus": {"host": "192.168.1.50",
                      "registers": {"energy": {"address": 100, "type": "uint32", "scale": 0.001}}}}


def test_disabled_by_default():
    s = resolve_modbus_settings({})
    assert s.enabled is False


def test_minimal_config_with_defaults():
    s = resolve_modbus_settings(MINIMAL)
    assert s.enabled is True
    assert (s.host, s.port, s.unit_id, s.function_code) == ("192.168.1.50", 502, 1, 3)
    assert s.poll_interval == 5.0
    assert s.energy.address == 100 and s.energy.scale == 0.001
    assert s.state is None and s.rfid is None and s.power is None
    assert s.fixed_login == ""


def test_energy_register_is_mandatory():
    """Ohne Zählerstand kann nichts abgerechnet werden — das darf nicht
    stillschweigend als 0 kWh enden."""
    with pytest.raises(ModbusConfigError, match="energy"):
        resolve_modbus_settings({"session_source": "modbus", "modbus": {"host": "x", "registers": {}}})


def test_host_is_mandatory():
    with pytest.raises(ModbusConfigError, match="host"):
        resolve_modbus_settings({"session_source": "modbus",
                                 "modbus": {"registers": {"energy": {"address": 1}}}})


def test_invalid_type_and_word_order_are_rejected():
    for reg in ({"address": 1, "type": "uint64"}, {"address": 1, "word_order": "middle"}):
        with pytest.raises(ModbusConfigError):
            resolve_modbus_settings({"session_source": "modbus",
                                     "modbus": {"host": "x", "registers": {"energy": reg}}})


def test_function_code_and_poll_interval_are_validated():
    with pytest.raises(ModbusConfigError, match="function_code"):
        resolve_modbus_settings({"session_source": "modbus",
                                 "modbus": {"host": "x", "function_code": 6,
                                            "registers": {"energy": {"address": 1}}}})
    s = resolve_modbus_settings({**MINIMAL, "modbus": {**MINIMAL["modbus"], "poll_interval": 0.1}})
    assert s.poll_interval == 1.0, "zu kurzes Intervall muss angehoben werden (Wallboxen sind langsam)"


def test_state_map_translates_numbers_to_text():
    """Viele Wallboxen melden den Zustand als Zahl. Die bestehende
    Zustandslogik arbeitet mit Text — also wird hier übersetzt."""
    s = resolve_modbus_settings({"session_source": "modbus", "modbus": {
        "host": "x",
        "registers": {"energy": {"address": 100},
                      "state": {"address": 200, "state_map": {"3": "Charging", "2": "Available"}}}}})
    assert s.state.state_map == {3: "Charging", 2: "Available"}
    assert s.state.translate(3) == "Charging"
    assert s.state.translate(2) == "Available"
    assert s.state.translate(9) == "9", "unbekannter Code bleibt sichtbar, statt verschluckt zu werden"


def test_string_state_needs_no_map():
    s = resolve_modbus_settings({"session_source": "modbus", "modbus": {
        "host": "x",
        "registers": {"energy": {"address": 100},
                      "state": {"address": 200, "type": "string", "count": 8}}}})
    assert s.state.type == "string" and s.state.count == 8
    assert s.state.translate("Charging Power On") == "Charging Power On"


def test_rfid_register_defaults_to_string():
    s = resolve_modbus_settings({"session_source": "modbus", "modbus": {
        "host": "x",
        "registers": {"energy": {"address": 100}, "rfid": {"address": 300, "count": 4}}}})
    assert s.rfid.type == "string" and s.rfid.count == 4


def test_fixed_login_for_wallboxes_without_a_card_reader():
    s = resolve_modbus_settings({**MINIMAL,
                                 "modbus": {**MINIMAL["modbus"], "fixed_login": "m.mustermann"}})
    assert s.fixed_login == "m.mustermann"


def test_state_map_also_accepts_the_list_form():
    """Das HA-Konfigurationsschema kann ein Dict mit beliebigen Schlüsseln nicht
    validieren. Darum zusätzlich die Listenform "code:Text", die es kann."""
    s = resolve_modbus_settings({"session_source": "modbus", "modbus": {
        "host": "x",
        "registers": {"energy": {"address": 100},
                      "state": {"address": 200,
                                "state_map": ["3:Charging", "2: Available", "4:Faulted"]}}}})
    assert s.state.state_map == {3: "Charging", 2: "Available", 4: "Faulted"}
    assert s.state.translate(2) == "Available"


def test_list_form_rejects_entries_without_a_code():
    import pytest as _pt
    with _pt.raises(ModbusConfigError, match="state_map"):
        resolve_modbus_settings({"session_source": "modbus", "modbus": {
            "host": "x",
            "registers": {"energy": {"address": 100},
                          "state": {"address": 200, "state_map": ["Charging"]}}}})


def test_list_form_rejects_a_non_numeric_code():
    import pytest as _pt
    with _pt.raises(ModbusConfigError, match="state_map"):
        resolve_modbus_settings({"session_source": "modbus", "modbus": {
            "host": "x",
            "registers": {"energy": {"address": 100},
                          "state": {"address": 200, "state_map": ["drei:Charging"]}}}})


def test_text_may_contain_a_colon():
    s = resolve_modbus_settings({"session_source": "modbus", "modbus": {
        "host": "x",
        "registers": {"energy": {"address": 100},
                      "state": {"address": 200, "state_map": ["7:Fehler: Schütz"]}}}})
    assert s.state.state_map == {7: "Fehler: Schütz"}
