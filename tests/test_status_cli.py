"""Tests for the ``status`` subcommand added in Anthrop-OS/email-ingest#17.

The ``status`` subcommand lets downstream consumers (e.g. the OpenClaw
``email-triage`` skill) check whether the ingest DB is past first run
without opening the SQLite file directly.

These tests exercise the full path: argv parse → config load →
PersistenceManager open → get_all_cursors → JSON emit → exit code.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import main as cli
from core.persistence import PersistenceManager


@pytest.fixture
def tmp_config(tmp_path: Path) -> Path:
    """Create a minimal on-disk config.yaml + the env vars it references."""
    db_path = tmp_path / "email_ingest.sqlite"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
settings:
  db_path: "{db_path.as_posix()}"
  default_dry_run: true

email_accounts:
  - account_id: "test_acct"
    imap_server: "imap.test.com"
    imap_port: 993
    use_ssl: true
    username: "test@test.com"
    password_env_var: "TEST_STATUS_PASSWORD"
    fetch_folder: "INBOX"

llm_provider:
  provider_type: "openai"
  model: "test-model"
  api_key_env_var: "TEST_STATUS_API_KEY"
""",
        encoding="utf-8",
    )
    return config_path


@pytest.fixture(autouse=True)
def env_vars():
    with patch.dict(
        os.environ,
        {
            "TEST_STATUS_PASSWORD": "pw",
            "TEST_STATUS_API_KEY": "key",
            "LLM_BASE_URL": "",
        },
        clear=False,
    ):
        yield


def _run_status(config_path: Path) -> int:
    """Invoke main.main() with argv set to `status --config <path>` and
    return the exit code (0 on successful return, otherwise the SystemExit
    code propagated out of main())."""
    with patch.object(sys, "argv", ["main.py", "--config", str(config_path), "status"]):
        try:
            cli.main()
            return 0
        except SystemExit as exc:
            return exc.code if isinstance(exc.code, int) else 1


def test_status_fresh_db_reports_not_initialized(tmp_config: Path, capsys):
    """A brand new DB has zero account_cursors rows → initialized=false."""
    code = _run_status(tmp_config)
    assert code == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["initialized"] is False
    assert payload["accounts"] == []
    assert payload["db_path"].endswith("email_ingest.sqlite")


def test_status_populated_db_reports_initialized(tmp_config: Path, capsys):
    """After one cursor update, status must report initialized=true and
    include the recorded account in the accounts list."""
    # Seed the DB via PersistenceManager so we reuse the canonical schema.
    import yaml

    with open(tmp_config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    db_path = cfg["settings"]["db_path"]

    pm = PersistenceManager(db_path)
    pm.update_cursor("test_acct", 42)
    pm.close()

    code = _run_status(tmp_config)
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["initialized"] is True
    assert len(payload["accounts"]) == 1
    assert payload["accounts"][0]["account_id"] == "test_acct"
    assert payload["accounts"][0]["last_uid"] == 42
    assert "updated_at" in payload["accounts"][0]


def test_status_missing_config_exits_nonzero(tmp_path: Path, capsys):
    missing = tmp_path / "no_such_config.yaml"
    code = _run_status(missing)
    assert code == 1


def test_status_json_is_stable_shape(tmp_config: Path, capsys):
    """The emitted payload must always contain the three top-level keys
    (initialized, accounts, db_path) so downstream consumers can rely on
    them without defensive coding."""
    _run_status(tmp_config)
    payload = json.loads(capsys.readouterr().out)
    assert set(payload.keys()) == {"initialized", "accounts", "db_path"}
    assert isinstance(payload["initialized"], bool)
    assert isinstance(payload["accounts"], list)
    assert isinstance(payload["db_path"], str)
