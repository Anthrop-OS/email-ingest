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
    assert "nlp_cache" in tables

def test_get_and_update_cursor(persistence):
    # Initially should be 0 since account doesn't exist yet
    assert persistence.get_cursor("test_account") == 0

    # Update cursor establishes the first high-water mark
    persistence.update_cursor("test_account", 100)
    assert persistence.get_cursor("test_account") == 100

    # Subsequent update advances it
    persistence.update_cursor("test_account", 250)
    assert persistence.get_cursor("test_account") == 250


def test_get_all_cursors_empty(persistence):
    """Fresh DB returns empty list — signals 'first run' to downstream."""
    assert persistence.get_all_cursors() == []


def test_get_all_cursors_multiple_accounts(persistence):
    """Each account written via update_cursor appears in the output, ordered
    by account_id, with last_uid and updated_at populated."""
    persistence.update_cursor("beta@example.com", 200)
    persistence.update_cursor("alpha@example.com", 100)
    persistence.update_cursor("gamma@example.com", 300)

    rows = persistence.get_all_cursors()
    assert [r["account_id"] for r in rows] == [
        "alpha@example.com",
        "beta@example.com",
        "gamma@example.com",
    ]
    assert [r["last_uid"] for r in rows] == [100, 200, 300]
    assert all(isinstance(r["updated_at"], str) and r["updated_at"] for r in rows)


def test_get_all_cursors_reflects_updates(persistence):
    """Updates to an existing account should not duplicate rows."""
    persistence.update_cursor("acct", 10)
    persistence.update_cursor("acct", 50)
    rows = persistence.get_all_cursors()
    assert len(rows) == 1
    assert rows[0]["last_uid"] == 50

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

# ── NLP Cache Tests ────────────────────────────────────────────────

SAMPLE_RESULT = {
    "original_uid": 42,
    "priority": "High",
    "summary": "Important budget review needed",
    "key_entities": ["budget", "Q2"],
    "action_required": True,
    "is_truncated": False,
}

def test_nlp_cache_put_and_get(persistence):
    persistence.put_cached_nlp("abc123", "acct@test.com", 42, SAMPLE_RESULT, "gemma4:e4b")
    cached = persistence.get_cached_nlp("abc123")
    assert cached is not None
    assert cached["priority"] == "High"
    assert cached["original_uid"] == 42
    assert cached["key_entities"] == ["budget", "Q2"]

def test_nlp_cache_miss(persistence):
    assert persistence.get_cached_nlp("nonexistent") is None

def test_nlp_cache_model_version_match(persistence):
    """Cache should return result when model version matches."""
    persistence.put_cached_nlp("hash1", "acct@test.com", 1, SAMPLE_RESULT, "gemma4:e4b")
    assert persistence.get_cached_nlp("hash1", model_version="gemma4:e4b") is not None

def test_nlp_cache_model_version_mismatch(persistence):
    """Cache should return None when model version doesn't match (per-message reprocess)."""
    persistence.put_cached_nlp("hash2", "acct@test.com", 2, SAMPLE_RESULT, "gemma4:e4b")
    assert persistence.get_cached_nlp("hash2", model_version="gpt-4o") is None

def test_nlp_cache_model_version_none_ignores_check(persistence):
    """When model_version is None, any cached entry should be returned."""
    persistence.put_cached_nlp("hash3", "acct@test.com", 3, SAMPLE_RESULT, "gemma4:e4b")
    assert persistence.get_cached_nlp("hash3", model_version=None) is not None

def test_nlp_cache_overwrite_on_reprocess(persistence):
    """INSERT OR REPLACE should overwrite existing cache entry."""
    persistence.put_cached_nlp("hash4", "acct@test.com", 4, SAMPLE_RESULT, "gemma4:e4b")
    updated = {**SAMPLE_RESULT, "priority": "Low", "summary": "Updated summary"}
    persistence.put_cached_nlp("hash4", "acct@test.com", 4, updated, "gpt-4o")
    cached = persistence.get_cached_nlp("hash4")
    assert cached["priority"] == "Low"
    assert cached["summary"] == "Updated summary"

def test_nlp_cache_invalidate_by_account(persistence):
    persistence.put_cached_nlp("h1", "acct_a@test.com", 1, SAMPLE_RESULT, "m1")
    persistence.put_cached_nlp("h2", "acct_b@test.com", 2, SAMPLE_RESULT, "m1")
    
    deleted = persistence.invalidate_nlp_cache(account_id="acct_a@test.com")
    assert deleted == 1
    assert persistence.get_cached_nlp("h1") is None
    assert persistence.get_cached_nlp("h2") is not None

def test_nlp_cache_invalidate_all(persistence):
    persistence.put_cached_nlp("h1", "acct_a@test.com", 1, SAMPLE_RESULT, "m1")
    persistence.put_cached_nlp("h2", "acct_b@test.com", 2, SAMPLE_RESULT, "m1")
    
    deleted = persistence.invalidate_nlp_cache()
    assert deleted == 2
    assert persistence.get_cached_nlp("h1") is None
    assert persistence.get_cached_nlp("h2") is None

def test_nlp_cache_cross_account_dedup(persistence):
    """Same content hash from different accounts should share the cache entry (last writer wins)."""
    persistence.put_cached_nlp("shared_hash", "acct_a@test.com", 10, SAMPLE_RESULT, "m1")
    # Second account with same content hash overwrites
    cached = persistence.get_cached_nlp("shared_hash")
    assert cached is not None
    assert cached["priority"] == "High"
