import json
import sqlite3
import uuid
from datetime import datetime
from typing import Optional, Dict, Any
from typing_extensions import Literal
from pathlib import Path

class PersistenceManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        if db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self._init_db()

    def _init_db(self):
        cursor = self.conn.cursor()
        # Create account_cursors table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS account_cursors (
                account_id TEXT PRIMARY KEY,
                last_uid INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Create audit_logs table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS audit_logs (
                run_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                before_uid INTEGER NOT NULL,
                after_uid INTEGER NOT NULL,
                emails_processed INTEGER NOT NULL,
                status TEXT NOT NULL,
                error_msg TEXT
            )
        ''')
        # Create nlp_cache table — keyed by content hash for cross-account dedup
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS nlp_cache (
                content_hash  TEXT PRIMARY KEY,
                account_id    TEXT NOT NULL,
                uid           INTEGER NOT NULL,
                result_json   TEXT NOT NULL,
                model_version TEXT NOT NULL,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Index on account_id for per-account cache invalidation
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_nlp_cache_account
            ON nlp_cache(account_id)
        ''')
        self.conn.commit()

    # ── Cursor Management ──────────────────────────────────────────────

    def get_cursor(self, account_id: str) -> int:
        cursor = self.conn.cursor()
        cursor.execute("SELECT last_uid FROM account_cursors WHERE account_id = ?", (account_id,))
        row = cursor.fetchone()
        return row[0] if row else 0

    def update_cursor(self, account_id: str, new_uid: int):
        cursor = self.conn.cursor()
        now = datetime.utcnow().isoformat()
        cursor.execute('''
            INSERT INTO account_cursors (account_id, last_uid, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET 
                last_uid = excluded.last_uid,
                updated_at = excluded.updated_at
        ''', (account_id, new_uid, now))
        self.conn.commit()

    # ── Audit Logging ──────────────────────────────────────────────────

    def log_audit(self, account_id: str, before_uid: int, after_uid: int, emails_processed: int, status: Literal["SUCCESS", "FAIL"], error_msg: Optional[str] = None):
        cursor = self.conn.cursor()
        run_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        cursor.execute('''
            INSERT INTO audit_logs (run_id, account_id, timestamp, before_uid, after_uid, emails_processed, status, error_msg)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (run_id, account_id, now, before_uid, after_uid, emails_processed, status, error_msg))
        self.conn.commit()

    # ── NLP Result Cache ───────────────────────────────────────────────

    def get_cached_nlp(self, content_hash: str, model_version: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Look up a cached NLP result by content hash.
        Returns the deserialized result dict, or None on cache miss.

        If model_version is provided, only returns a cache hit when the
        cached entry was produced by the same model. This enables automatic
        per-message reprocessing when the configured model changes.
        """
        cursor = self.conn.cursor()
        if model_version:
            cursor.execute(
                "SELECT result_json FROM nlp_cache WHERE content_hash = ? AND model_version = ?",
                (content_hash, model_version)
            )
        else:
            cursor.execute(
                "SELECT result_json FROM nlp_cache WHERE content_hash = ?",
                (content_hash,)
            )
        row = cursor.fetchone()
        if row:
            return json.loads(row[0])
        return None

    def put_cached_nlp(self, content_hash: str, account_id: str, uid: int,
                       result: Dict[str, Any], model_version: str):
        """
        Persist an NLP result keyed by content hash.
        Uses INSERT OR REPLACE so force-reprocess can overwrite stale entries.
        """
        cursor = self.conn.cursor()
        now = datetime.utcnow().isoformat()
        cursor.execute('''
            INSERT OR REPLACE INTO nlp_cache
                (content_hash, account_id, uid, result_json, model_version, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (content_hash, account_id, uid, json.dumps(result, ensure_ascii=False), model_version, now))
        self.conn.commit()

    def invalidate_nlp_cache(self, account_id: Optional[str] = None):
        """
        Clear NLP cache entries. If account_id is provided, only clear
        entries originally cached by that account. Otherwise clear all.
        """
        cursor = self.conn.cursor()
        if account_id:
            cursor.execute("DELETE FROM nlp_cache WHERE account_id = ?", (account_id,))
        else:
            cursor.execute("DELETE FROM nlp_cache")
        deleted = cursor.rowcount
        self.conn.commit()
        return deleted

    # ── Lifecycle ──────────────────────────────────────────────────────

    def close(self):
        self.conn.close()
