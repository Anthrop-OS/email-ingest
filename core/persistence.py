import sqlite3
import uuid
from datetime import datetime
from typing import Optional
from typing_extensions import Literal
from pathlib import Path

class PersistenceManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
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
        self.conn.commit()

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

    def log_audit(self, account_id: str, before_uid: int, after_uid: int, emails_processed: int, status: Literal["SUCCESS", "FAIL"], error_msg: Optional[str] = None):
        cursor = self.conn.cursor()
        run_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        cursor.execute('''
            INSERT INTO audit_logs (run_id, account_id, timestamp, before_uid, after_uid, emails_processed, status, error_msg)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (run_id, account_id, now, before_uid, after_uid, emails_processed, status, error_msg))
        self.conn.commit()

    def close(self):
        self.conn.close()
