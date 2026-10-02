"""Gemeinsame Test-Einstellungen."""
import pytest


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    """main() legt im Standalone-Betrieb Dateien in EXPENSECHARGE_DATA an
    (admin.json, secret.key) — nie im echten /data eines Tests."""
    monkeypatch.setenv('EXPENSECHARGE_DATA', str(tmp_path / 'data'))
    (tmp_path / 'data').mkdir()
