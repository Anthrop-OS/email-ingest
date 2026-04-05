import pytest
import sqlite3
from core.persistence import PersistenceManager

@pytest.fixture
def persistence():
    # Use in-memory SQLite database for testing to ensure no side effects
    pm = PersistenceManager(":memory:")
    yield pm
    pm.close()

def test_init_creates_tables(persistence):
    cursor = persistence.conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = {row[0] for row in cursor.fetchall()}
    assert "account_cursors" in tables
    assert "audit_logs" in tables

def test_get_and_update_cursor(persistence):
    # Initially should be 0 since account doesn't exist yet
    assert persistence.get_cursor("test_account") == 0
    
    # Update cursor establishes the first high-water mark
    persistence.update_cursor("test_account", 100)
    assert persistence.get_cursor("test_account") == 100
    
    # Subsequent update advances it
    persistence.update_cursor("test_account", 250)
    assert persistence.get_cursor("test_account") == 250

def test_log_audit_record(persistence):
    persistence.log_audit(
        account_id="test_account",
        before_uid=100,
        after_uid=150,
        emails_processed=5,
        status="SUCCESS",
        error_msg=None
    )
    
    cursor = persistence.conn.cursor()
    cursor.execute("SELECT account_id, before_uid, after_uid, emails_processed, status, error_msg FROM audit_logs")
    rows = cursor.fetchall()
    
    assert len(rows) == 1
    assert rows[0] == ("test_account", 100, 150, 5, "SUCCESS", None)
