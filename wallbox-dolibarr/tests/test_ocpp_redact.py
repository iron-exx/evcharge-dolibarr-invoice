import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ocpp_server.redact import redact_id_tags  # noqa: E402


def test_redacts_json_and_repr_form():
    assert redact_id_tags('{"idTag":"EFCD083E"}') == '{"idTag":"REDACTED"}'
    assert redact_id_tags("{'idTag': 'efcd083e'}") == "{'idTag': 'REDACTED'}"


def test_redacts_all_occurrences_and_keeps_the_rest():
    src = '[2,"x","StartTransaction",{"connectorId":1,"idTag":"AB12","meterStart":0}]'
    out = redact_id_tags(src)
    assert "AB12" not in out
    assert '"connectorId":1' in out and '"meterStart":0' in out


def test_untouched_without_id_tag():
    src = '{"meterStop":9000,"reason":"Local"}'
    assert redact_id_tags(src) == src
