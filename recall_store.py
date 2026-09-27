"""供体安全召回记录存取：recalls 与 recall_notifications 两张表的读写。

召回判定见 recall_policy.py，请求入口见 recall_api.py。
"""
from __future__ import annotations

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS recalls(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    donor_id INTEGER NOT NULL,
    risk_type TEXT NOT NULL,
    report_source TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    closed_by TEXT,
    closed_at TEXT,
    UNIQUE(donor_id, risk_type, occurred_at)
);
CREATE TABLE IF NOT EXISTS recall_notifications(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recall_id INTEGER NOT NULL REFERENCES recalls(id),
    allocation_id INTEGER NOT NULL,
    hospital TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    acknowledged_by TEXT,
    acknowledged_at TEXT,
    UNIQUE(recall_id, hospital)
);
"""


class RecallStore:
    """召回与通知记录的存取层，与领域服务共用同一 SQLite 连接和事务。"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def ensure_schema(self) -> None:
        self.conn.executescript(SCHEMA)

    def create_recall(self, donor_id: int, risk_type: str, report_source: str, occurred_at: str, actor: str, now: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO recalls(donor_id,risk_type,report_source,occurred_at,created_by,created_at) VALUES(?,?,?,?,?,?)",
            (donor_id, risk_type, report_source, occurred_at, actor, now))
        return cur.lastrowid

    def find_by_identity(self, donor_id: int, risk_type: str, occurred_at: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM recalls WHERE donor_id=? AND risk_type=? AND occurred_at=?",
            (donor_id, risk_type, occurred_at)).fetchone()

    def get(self, recall_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM recalls WHERE id=?", (recall_id,)).fetchone()

    def list_recalls(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM recalls ORDER BY id DESC").fetchall()

    def add_notification(self, recall_id: int, allocation_id: int, hospital: str, kind: str, now: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO recall_notifications(recall_id,allocation_id,hospital,kind,created_at) VALUES(?,?,?,?,?)",
            (recall_id, allocation_id, hospital, kind, now))
        return cur.lastrowid

    def notifications(self, recall_id: int) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM recall_notifications WHERE recall_id=? ORDER BY id", (recall_id,)).fetchall()

    def all_notifications(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM recall_notifications ORDER BY id").fetchall()

    def notification_for(self, recall_id: int, hospital: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM recall_notifications WHERE recall_id=? AND hospital=?", (recall_id, hospital)).fetchone()

    def notifications_for_hospital(self, hospital: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT n.*,r.donor_id,r.risk_type,r.occurred_at,r.status recall_status
               FROM recall_notifications n JOIN recalls r ON r.id=n.recall_id
               WHERE n.hospital=? ORDER BY n.id DESC""", (hospital,)).fetchall()

    def mark_acknowledged(self, notification_id: int, actor: str, now: str) -> None:
        self.conn.execute(
            "UPDATE recall_notifications SET status='acknowledged',acknowledged_by=?,acknowledged_at=? WHERE id=?",
            (actor, now, notification_id))

    def pending_count(self, recall_id: int) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) c FROM recall_notifications WHERE recall_id=? AND status='pending'", (recall_id,)).fetchone()["c"]

    def close(self, recall_id: int, actor: str, now: str) -> None:
        self.conn.execute("UPDATE recalls SET status='closed',closed_by=?,closed_at=? WHERE id=?", (actor, now, recall_id))

    def donor_has_recall(self, donor_id: int) -> bool:
        return self.conn.execute("SELECT 1 FROM recalls WHERE donor_id=? LIMIT 1", (donor_id,)).fetchone() is not None
