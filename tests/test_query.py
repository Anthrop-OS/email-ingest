import json
import pytest
from core.persistence import PersistenceManager
from modules.query_handler import QueryHandler


@pytest.fixture
def persistence():
    pm = PersistenceManager(":memory:")
    yield pm
    pm.close()


@pytest.fixture
def handler(persistence):
    return QueryHandler(persistence)


SAMPLE_EMAIL = {
    "uid": 100,
    "account_id": "user@example.com",
    "subject": "Q2 Budget Review",
    "sender": "cfo@company.com",
    "date": "2026-03-31",
    "body": "Please review the attached budget spreadsheet." * 50,
}

SAMPLE_NLP = {
    "original_uid": 100,
    "priority": "High",
    "summary": "CFO requests budget approval",
    "key_entities": ["Q2 Budget", "CFO"],
    "action_required": True,
    "is_truncated": False,
}


def _insert(persistence, run_id="run1", email=None, nlp=None, **overrides):
    """Helper to insert an email record with optional overrides."""
    e = {**SAMPLE_EMAIL, **(email or {}), **overrides}
    n = {**SAMPLE_NLP, **(nlp or {})}
    return persistence.insert_email_record(
        run_id=run_id,
        account_id=e["account_id"],
        uid=e["uid"],
        content_hash=f"hash_{e['uid']}",
        email_data=e,
        nlp_result=n,
        model_version="test-model",
    )


# ── emails table schema ──────────────────────────────────────────

def test_emails_table_created(persistence):
    cur = persistence.conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cur.fetchall()}
    assert "emails" in tables


# ── insert_email_record ──────────────────────────────────────────

def test_insert_returns_monotonic_ids(persistence):
    id1 = _insert(persistence, uid=100)
    id2 = _insert(persistence, uid=101)
    assert id2 > id1


def test_insert_stores_body_preview(persistence):
    big_body = "A" * 20000
    _insert(persistence, uid=200, email={"body": big_body})
    rows = persistence.query_emails(after_id=0)
    assert len(rows[0]["body_preview"]) == PersistenceManager.BODY_PREVIEW_LIMIT


def test_insert_without_nlp(persistence):
    """skip-nlp path: nlp_result=None should store NULL NLP fields."""
    persistence.insert_email_record(
        run_id="run_skip",
        account_id="user@example.com",
        uid=300,
        content_hash="hash_300",
        email_data=SAMPLE_EMAIL,
        nlp_result=None,
    )
    rows = persistence.query_emails(after_id=0)
    assert rows[0]["priority"] is None
    assert rows[0]["summary"] is None


def test_force_reprocess_inserts_new_row(persistence):
    """Same content_hash should produce two distinct rows with different ids."""
    persistence.insert_email_record(
        run_id="run1", account_id="a@b.com", uid=100,
        content_hash="same_hash", email_data=SAMPLE_EMAIL,
        nlp_result=SAMPLE_NLP, model_version="v1",
    )
    persistence.insert_email_record(
        run_id="run2", account_id="a@b.com", uid=100,
        content_hash="same_hash", email_data=SAMPLE_EMAIL,
        nlp_result={**SAMPLE_NLP, "priority": "Low"}, model_version="v2",
    )
    rows = persistence.query_emails(after_id=0)
    assert len(rows) == 2
    assert rows[0]["id"] != rows[1]["id"]
    assert rows[0]["priority"] == "High"
    assert rows[1]["priority"] == "Low"


# ── query_emails filters ─────────────────────────────────────────

def test_query_after_id(persistence):
    id1 = _insert(persistence, uid=10)
    id2 = _insert(persistence, uid=11)
    rows = persistence.query_emails(after_id=id1)
    assert len(rows) == 1
    assert rows[0]["id"] == id2


def test_query_by_account(persistence):
    _insert(persistence, uid=10, account_id="alice@x.com")
    _insert(persistence, uid=11, account_id="bob@x.com")
    rows = persistence.query_emails(account_id="alice@x.com")
    assert len(rows) == 1
    assert rows[0]["account_id"] == "alice@x.com"


def test_query_by_run_id(persistence):
    _insert(persistence, uid=10, run_id="run_a")
    _insert(persistence, uid=11, run_id="run_b")
    rows = persistence.query_emails(run_id="run_a")
    assert len(rows) == 1
    assert rows[0]["run_id"] == "run_a"


def test_query_by_priority(persistence):
    _insert(persistence, uid=10, nlp={"priority": "High"})
    _insert(persistence, uid=11, nlp={"priority": "Low"})
    rows = persistence.query_emails(priority="High")
    assert len(rows) == 1
    assert rows[0]["priority"] == "High"


def test_query_date_range(persistence):
    _insert(persistence, uid=10, email={"date": "2026-03-28"})
    _insert(persistence, uid=11, email={"date": "2026-03-31"})
    _insert(persistence, uid=12, email={"date": "2026-04-02"})

    rows = persistence.query_emails(since="2026-03-30", until="2026-04-01")
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-03-31"


def test_query_limit(persistence):
    for i in range(5):
        _insert(persistence, uid=i)
    rows = persistence.query_emails(limit=3)
    assert len(rows) == 3


def test_query_combined_filters(persistence):
    _insert(persistence, uid=10, account_id="a@x.com", email={"date": "2026-03-28"}, nlp={"priority": "High"})
    _insert(persistence, uid=11, account_id="a@x.com", email={"date": "2026-04-01"}, nlp={"priority": "High"})
    _insert(persistence, uid=12, account_id="b@x.com", email={"date": "2026-04-01"}, nlp={"priority": "High"})
    _insert(persistence, uid=13, account_id="a@x.com", email={"date": "2026-04-01"}, nlp={"priority": "Low"})

    rows = persistence.query_emails(
        account_id="a@x.com", priority="High", since="2026-03-30",
    )
    assert len(rows) == 1
    assert rows[0]["uid"] == 11


# ── QueryHandler output ──────────────────────────────────────────

def test_handler_json_output(handler, persistence):
    _insert(persistence, uid=10)
    response = handler.execute()
    assert response["meta"]["count"] == 1
    assert response["meta"]["max_id"] > 0

    # key_entities should be deserialized to list
    assert isinstance(response["results"][0]["key_entities"], list)
    # booleans should be actual bools
    assert response["results"][0]["action_required"] is True

    output = handler.format_output(response, fmt="json")
    parsed = json.loads(output)
    assert parsed["meta"]["count"] == 1


def test_handler_has_more(handler, persistence):
    for i in range(3):
        _insert(persistence, uid=i)
    response = handler.execute(limit=2)
    assert response["meta"]["has_more"] is True
    assert response["meta"]["count"] == 2


def test_handler_table_output(handler, persistence):
    _insert(persistence, uid=10)
    response = handler.execute()
    output = handler.format_output(response, fmt="table")
    assert "Q2 Budget Review" in output
    assert "cfo@company.com" in output


def test_handler_empty(handler, persistence):
    response = handler.execute()
    assert response["meta"]["count"] == 0
    assert response["meta"]["max_id"] == 0
    output = handler.format_output(response, fmt="table")
    assert output == "No results."
