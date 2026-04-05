import pytest
from core.content_hasher import compute_email_fingerprint


@pytest.fixture
def sample_email():
    return {
        "sender": "alice@example.com",
        "date": "Mon, 05 Apr 2026 10:00:00 +0000",
        "subject": "Q2 Budget Review",
        "body": "Please review the attached budget spreadsheet for Q2.",
    }


def test_deterministic_hash(sample_email):
    """Same input must always produce the same hash."""
    h1 = compute_email_fingerprint(sample_email)
    h2 = compute_email_fingerprint(sample_email)
    assert h1 == h2
    assert isinstance(h1, str)
    assert len(h1) == 16  # 16 hex characters


def test_different_sender_different_hash(sample_email):
    other = {**sample_email, "sender": "bob@example.com"}
    assert compute_email_fingerprint(sample_email) != compute_email_fingerprint(other)


def test_different_subject_different_hash(sample_email):
    other = {**sample_email, "subject": "Q3 Budget Review"}
    assert compute_email_fingerprint(sample_email) != compute_email_fingerprint(other)


def test_different_date_different_hash(sample_email):
    other = {**sample_email, "date": "Tue, 06 Apr 2026 10:00:00 +0000"}
    assert compute_email_fingerprint(sample_email) != compute_email_fingerprint(other)


def test_different_body_different_hash(sample_email):
    other = {**sample_email, "body": "Completely different body content."}
    assert compute_email_fingerprint(sample_email) != compute_email_fingerprint(other)


def test_body_truncated_at_2000_chars(sample_email):
    """Body beyond 2000 chars should not affect the hash."""
    prefix = "A" * 2000
    email_a = {**sample_email, "body": prefix + "XXXX_TAIL_A"}
    email_b = {**sample_email, "body": prefix + "YYYY_TAIL_B"}
    assert compute_email_fingerprint(email_a) == compute_email_fingerprint(email_b)


def test_empty_fields_handled():
    """Missing fields should produce a valid hash, not crash."""
    empty_email = {}
    h = compute_email_fingerprint(empty_email)
    assert isinstance(h, str)
    assert len(h) == 16


def test_none_body_handled():
    email = {"sender": "x@y.com", "date": "now", "subject": "hi", "body": None}
    h = compute_email_fingerprint(email)
    assert isinstance(h, str)
    assert len(h) == 16
