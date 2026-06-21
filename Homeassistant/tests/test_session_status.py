"""
Tests for upload_status and upload_error SQLite writes in SessionManager.
Phase 6, MON-02/MON-03 — Wave 0 stubs (RED phase).

Expected behavior after Wave 1 implementation:
  - transmit_completed_sessions() with success -> upload_status='ok', upload_error=NULL
  - transmit_completed_sessions() with failure -> upload_status='error', upload_error='HTTP 503: ...'
  - _init_database() adds upload_status and upload_error columns idempotently
"""
import pytest


@pytest.mark.xfail(reason="Wave 1 not yet implemented: upload_status column migration")
def test_init_database_creates_upload_status_column(in_memory_session_manager):
    """_init_database() creates upload_status and upload_error columns in sessions table"""
    import sqlite3
    sm = in_memory_session_manager
    conn = sqlite3.connect(':memory:')
    # Check column presence via PRAGMA
    cursor = sm._conn.execute("PRAGMA table_info(sessions)")
    columns = [row[1] for row in cursor.fetchall()]
    assert 'upload_status' in columns
    assert 'upload_error' in columns


@pytest.mark.xfail(reason="Wave 1 not yet implemented: upload_status='ok' on success")
def test_transmit_success_writes_upload_status_ok(
    in_memory_session_manager, mock_api_client_success
):
    """After successful transmission, upload_status='ok' and upload_error=NULL in SQLite"""
    sm = in_memory_session_manager
    # Insert a completed session to transmit
    sm._conn.execute(
        "INSERT INTO sessions (wallbox_id, rfid_hash, status, kwh) VALUES (?, ?, ?, ?)",
        ("wb-01", "abc123", "completed", 12.5)
    )
    sm._conn.commit()
    sm.transmit_completed_sessions(mock_api_client_success)
    row = sm._conn.execute(
        "SELECT upload_status, upload_error FROM sessions WHERE wallbox_id='wb-01'"
    ).fetchone()
    assert row is not None
    assert row[0] == 'ok'
    assert row[1] is None


@pytest.mark.xfail(reason="Wave 1 not yet implemented: upload_status='error' on failure")
def test_transmit_failure_writes_upload_status_error(
    in_memory_session_manager, mock_api_client_failure
):
    """After failed transmission, upload_status='error' and upload_error contains message"""
    sm = in_memory_session_manager
    sm._conn.execute(
        "INSERT INTO sessions (wallbox_id, rfid_hash, status, kwh) VALUES (?, ?, ?, ?)",
        ("wb-02", "def456", "completed", 7.3)
    )
    sm._conn.commit()
    sm.transmit_completed_sessions(mock_api_client_failure)
    row = sm._conn.execute(
        "SELECT upload_status, upload_error FROM sessions WHERE wallbox_id='wb-02'"
    ).fetchone()
    assert row is not None
    assert row[0] == 'error'
    assert row[1] is not None
    assert 'HTTP 503' in row[1]
