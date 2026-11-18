import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

WELCOME_TTL = 24 * 60 * 60


@dataclass(frozen=True)
class WelcomeJob:
    id: str
    conversation_id: str
    service_url: str
    attempts: int


class WelcomeQueue:
    def __init__(self, path: Path, *, capacity: int = 5000) -> None:
        self.capacity = capacity
        self.db = sqlite3.connect(path)
        with self.db:
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS welcomes (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, service_url TEXT NOT NULL,
                    state TEXT NOT NULL, attempts INTEGER NOT NULL,
                    ready_at REAL NOT NULL, expires_at REAL NOT NULL
                )
            """)
            self.db.execute("CREATE INDEX IF NOT EXISTS welcome_ready ON welcomes (state, ready_at)")
            self.db.execute("CREATE INDEX IF NOT EXISTS welcome_expiry ON welcomes (expires_at)")
            self.db.execute("""
                UPDATE welcomes SET state = 'unknown', conversation_id = '', service_url = ''
                WHERE state = 'sending'
            """)

    def enqueue(self, job_id: UUID, conversation_id: str, service_url: str) -> bool:
        now = time.time()
        with self.db:
            self.db.execute("DELETE FROM welcomes WHERE expires_at <= ?", (now,))
            if self.db.execute("SELECT 1 FROM welcomes WHERE id = ?", (str(job_id),)).fetchone():
                return True
            if self.db.execute("SELECT COUNT(*) FROM welcomes").fetchone()[0] >= self.capacity:
                return False
            self.db.execute(
                "INSERT INTO welcomes VALUES (?, ?, ?, 'pending', 0, ?, ?)",
                (str(job_id), conversation_id, service_url, now, now + WELCOME_TTL),
            )
        return True

    def claim(self) -> WelcomeJob | None:
        now = time.time()
        with self.db:
            self.db.execute("DELETE FROM welcomes WHERE expires_at <= ?", (now,))
            row = self.db.execute("""
                UPDATE welcomes SET state = 'sending', attempts = attempts + 1
                WHERE id = (
                    SELECT id FROM welcomes WHERE state = 'pending' AND ready_at <= ?
                    ORDER BY ready_at LIMIT 1
                )
                RETURNING id, conversation_id, service_url, attempts
            """, (now,)).fetchone()
        return WelcomeJob(*row) if row else None

    def finish(self, job: WelcomeJob, state: str) -> None:
        with self.db:
            self.db.execute("""
                UPDATE welcomes SET state = ?, conversation_id = '', service_url = '' WHERE id = ?
            """, (state, job.id))

    def retry(self, job: WelcomeJob, delay: float) -> bool:
        ready_at = time.time() + delay
        with self.db:
            result = self.db.execute("""
                UPDATE welcomes SET state = 'pending', ready_at = ?
                WHERE id = ? AND expires_at > ?
            """, (ready_at, job.id, ready_at))
        return bool(result.rowcount)

    def close(self) -> None:
        self.db.close()
