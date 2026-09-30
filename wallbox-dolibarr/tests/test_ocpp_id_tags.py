import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ocpp_server.id_tags import is_whitelisted, normalize_id_tag  # noqa: E402


def test_normalize_trims_and_uppercases():
    assert normalize_id_tag("  efcd083e ") == "EFCD083E"


def test_normalize_non_string_is_empty():
    assert normalize_id_tag(None) == ""
    assert normalize_id_tag(1234) == ""


def test_whitelist_match_ignores_case():
    assert is_whitelisted("efcd083e", ["EFCD083E"])
    assert is_whitelisted("EFCD083E", ["efcd083e "])


def test_whitelist_rejects_unknown_empty_and_missing_list():
    assert not is_whitelisted("DEADBEEF", ["EFCD083E"])
    assert not is_whitelisted("", ["", "EFCD083E"])
    assert not is_whitelisted("EFCD083E", None)
