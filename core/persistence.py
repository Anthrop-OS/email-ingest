import json
import sqlite3
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List
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
        # Create emails table — append-only store for agent consumption
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS emails (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id          TEXT    NOT NULL,
                account_id      TEXT    NOT NULL,
                uid             INTEGER NOT NULL,
                content_hash    TEXT    NOT NULL,
                subject         TEXT,
                sender          TEXT,
                date            TEXT,
                body_preview    TEXT,
                priority        TEXT,
                summary         TEXT,
                key_entities    TEXT,
                action_required INTEGER,
                is_truncated    INTEGER,
                model_version   TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_emails_account ON emails(account_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_emails_run     ON emails(run_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_emails_date    ON emails(date)')
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

    # ── Email Records (agent-facing append-only store) ───────────────

    BODY_PREVIEW_LIMIT = 10240  # 10 KB

    def insert_email_record(
        self,
        run_id: str,
        account_id: str,
        uid: int,
        content_hash: str,
        email_data: Dict[str, Any],
        nlp_result: Optional[Dict[str, Any]] = None,
        model_version: Optional[str] = None,
    ) -> int:
        """
        Append one email + NLP result to the emails table.
        Always INSERTs (even on force-reprocess) so the monotonic id
        sequence is never broken.  Returns the new row id.
        """
        body_raw = email_data.get("body") or ""
        body_preview = body_raw[: self.BODY_PREVIEW_LIMIT]

        priority = summary = key_entities_json = None
        action_required = is_truncated = None
        if nlp_result:
            priority = nlp_result.get("priority")
            summary = nlp_result.get("summary")
            entities = nlp_result.get("key_entities", [])
            key_entities_json = json.dumps(entities, ensure_ascii=False)
            action_required = 1 if nlp_result.get("action_required") else 0
            is_truncated = 1 if nlp_result.get("is_truncated") else 0

        cur = self.conn.cursor()
        now = datetime.utcnow().isoformat()
        cur.execute(
            """
            INSERT INTO emails
                (run_id, account_id, uid, content_hash,
                 subject, sender, date, body_preview,
                 priority, summary, key_entities,
                 action_required, is_truncated, model_version, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, account_id, uid, content_hash,
                email_data.get("subject"), email_data.get("sender"),
                email_data.get("date"), body_preview,
                priority, summary, key_entities_json,
                action_required, is_truncated, model_version, now,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def query_emails(
        self,
        after_id: int = 0,
        account_id: Optional[str] = None,
        run_id: Optional[str] = None,
        priority: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """
        Query the emails table with optional filters.

        Date filters (--since / --until) match against the email's original
        Date header so that the consumer can reason in terms of "when was the
        email sent", not "when did the pipeline run".

        Returns rows ordered by id ASC (oldest-first) so the consumer can
        simply record max(id) as its next cursor.
        """
        clauses: List[str] = ["id > ?"]
        params: list = [after_id]

        if account_id:
            clauses.append("account_id = ?")
            params.append(account_id)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if priority:
            clauses.append("priority = ?")
            params.append(priority)
        if since:
            clauses.append("date >= ?")
            params.append(since)
        if until:
            clauses.append("date <= ?")
            params.append(until)

        where = " AND ".join(clauses)
        sql = f"SELECT * FROM emails WHERE {where} ORDER BY id ASC LIMIT ?"
        params.append(limit)

        cur = self.conn.cursor()
        cur.execute(sql, params)
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        return [dict(zip(columns, row)) for row in rows]

    # ── Lifecycle ──────────────────────────────────────────────────────

    def close(self):
        self.conn.close()
