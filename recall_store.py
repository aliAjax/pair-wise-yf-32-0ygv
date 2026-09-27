#!/usr/bin/env python3
"""供体安全召回——记录存取层。

只管召回相关三张表的读写与状态落库，不包含任何业务判定；
复用主库连接，由调用方决定事务边界。
"""
from __future__ import annotations

import sqlite3
from typing import Sequence

SCHEMA = """
CREATE TABLE IF NOT EXISTS recalls(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    donor_id INTEGER NOT NULL REFERENCES donors(id),
    risk_type TEXT NOT NULL,
    report_source TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    closed_by TEXT,
    closed_at TEXT,
    UNIQUE(donor_id, risk_type)
);
CREATE TABLE IF NOT EXISTS recall_notifications(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recall_id INTEGER NOT NULL REFERENCES recalls(id),
    allocation_id INTEGER NOT NULL REFERENCES allocations(id),
    hospital TEXT NOT NULL,
    disposition TEXT NOT NULL,
    allocation_status TEXT NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0,
    acknowledged_by TEXT,
    acknowledged_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(recall_id, allocation_id)
);
CREATE TABLE IF NOT EXISTS recipient_followups(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recall_id INTEGER NOT NULL REFERENCES recalls(id),
    allocation_id INTEGER NOT NULL REFERENCES allocations(id),
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    hospital TEXT NOT NULL,
    risk_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    UNIQUE(recall_id, allocation_id)
);
"""


class RecallStore:
    """召回、逐条通知、受者随访的 SQL 存取。"""

    @staticmethod
    def init(conn: sqlite3.Connection) -> None:
        conn.executescript(SCHEMA)

    # ---------------- recalls ----------------
    @staticmethod
    def insert_recall(conn: sqlite3.Connection, donor_id: int, risk_type: str, report_source: str,
                      occurred_at: str, created_by: str, created_at: str) -> int:
        cur = conn.execute(
            "INSERT INTO recalls(donor_id,risk_type,report_source,occurred_at,created_by,created_at) VALUES(?,?,?,?,?,?)",
            (donor_id, risk_type, report_source, occurred_at, created_by, created_at))
        return cur.lastrowid

    @staticmethod
    def get_recall(conn: sqlite3.Connection, recall_id: int) -> sqlite3.Row | None:
        return conn.execute("SELECT * FROM recalls WHERE id=?", (recall_id,)).fetchone()

    @staticmethod
    def find_by_donor_risk(conn: sqlite3.Connection, donor_id: int, risk_type: str) -> sqlite3.Row | None:
        return conn.execute("SELECT * FROM recalls WHERE donor_id=? AND risk_type=?", (donor_id, risk_type)).fetchone()

    @staticmethod
    def exists_for_donor(conn: sqlite3.Connection, donor_id: int) -> bool:
        return conn.execute("SELECT 1 FROM recalls WHERE donor_id=? LIMIT 1", (donor_id,)).fetchone() is not None

    @staticmethod
    def list_recalls(conn: sqlite3.Connection) -> list[sqlite3.Row]:
        return list(conn.execute("SELECT * FROM recalls ORDER BY id DESC"))

    @staticmethod
    def close(conn: sqlite3.Connection, recall_id: int, closed_by: str, closed_at: str) -> None:
        conn.execute("UPDATE recalls SET status='closed',closed_by=?,closed_at=? WHERE id=?",
                     (closed_by, closed_at, recall_id))

    # ---------------- allocations affected by a donor ----------------
    @staticmethod
    def affected_allocations(conn: sqlite3.Connection, donor_id: int, statuses: Sequence[str]) -> list[sqlite3.Row]:
        placeholders = ",".join("?" * len(statuses))
        return list(conn.execute(
            f"""SELECT a.id allocation_id, a.status allocation_status, a.candidate_id, c.hospital hospital
                FROM allocations a JOIN candidates c ON c.id=a.candidate_id
                WHERE a.donor_id=? AND a.status IN ({placeholders})""",
            (donor_id, *statuses)))

    @staticmethod
    def stop_allocation(conn: sqlite3.Connection, allocation_id: int, at: str) -> None:
        conn.execute("UPDATE allocations SET status='recalled',revision=revision+1,updated_at=? WHERE id=?",
                     (at, allocation_id))

    @staticmethod
    def mark_donor_recalled(conn: sqlite3.Connection, donor_id: int) -> None:
        conn.execute("UPDATE donors SET status='recalled',revision=revision+1 WHERE id=?", (donor_id,))

    # ---------------- per-hospital notifications ----------------
    @staticmethod
    def insert_notification(conn: sqlite3.Connection, recall_id: int, allocation_id: int, hospital: str,
                            disposition: str, allocation_status: str, created_at: str) -> int:
        cur = conn.execute(
            """INSERT INTO recall_notifications(recall_id,allocation_id,hospital,disposition,allocation_status,created_at)
               VALUES(?,?,?,?,?,?)""",
            (recall_id, allocation_id, hospital, disposition, allocation_status, created_at))
        return cur.lastrowid

    @staticmethod
    def list_notifications(conn: sqlite3.Connection, recall_id: int) -> list[sqlite3.Row]:
        return list(conn.execute("SELECT * FROM recall_notifications WHERE recall_id=? ORDER BY id", (recall_id,)))

    @staticmethod
    def get_notification(conn: sqlite3.Connection, notification_id: int) -> sqlite3.Row | None:
        return conn.execute("SELECT * FROM recall_notifications WHERE id=?", (notification_id,)).fetchone()

    @staticmethod
    def acknowledge(conn: sqlite3.Connection, notification_id: int, actor: str, at: str) -> None:
        conn.execute("UPDATE recall_notifications SET acknowledged=1,acknowledged_by=?,acknowledged_at=? WHERE id=?",
                     (actor, at, notification_id))

    @staticmethod
    def pending_count(conn: sqlite3.Connection, recall_id: int) -> int:
        return conn.execute(
            "SELECT COUNT(*) FROM recall_notifications WHERE recall_id=? AND acknowledged=0", (recall_id,)).fetchone()[0]

    @staticmethod
    def notifications_for_hospital(conn: sqlite3.Connection, hospital: str) -> list[sqlite3.Row]:
        return list(conn.execute(
            """SELECT n.id notification_id, n.recall_id, n.allocation_id, n.disposition, n.allocation_status,
                      n.acknowledged, n.acknowledged_by, n.acknowledged_at, n.created_at,
                      r.donor_id, r.risk_type, r.report_source, r.occurred_at, r.status recall_status
               FROM recall_notifications n JOIN recalls r ON r.id=n.recall_id
               WHERE n.hospital=? ORDER BY n.id DESC""", (hospital,)))

    # ---------------- recipient follow-ups (implanted organs) ----------------
    @staticmethod
    def insert_followup(conn: sqlite3.Connection, recall_id: int, allocation_id: int, candidate_id: int,
                        hospital: str, risk_type: str, created_at: str) -> int:
        cur = conn.execute(
            """INSERT INTO recipient_followups(recall_id,allocation_id,candidate_id,hospital,risk_type,created_at)
               VALUES(?,?,?,?,?,?)""",
            (recall_id, allocation_id, candidate_id, hospital, risk_type, created_at))
        return cur.lastrowid

    @staticmethod
    def list_followups(conn: sqlite3.Connection, recall_id: int) -> list[sqlite3.Row]:
        return list(conn.execute("SELECT * FROM recipient_followups WHERE recall_id=? ORDER BY id", (recall_id,)))

    @staticmethod
    def followups_for_hospital(conn: sqlite3.Connection, hospital: str) -> list[sqlite3.Row]:
        return list(conn.execute(
            """SELECT f.*, r.report_source, r.occurred_at, r.status recall_status
               FROM recipient_followups f JOIN recalls r ON r.id=f.recall_id
               WHERE f.hospital=? ORDER BY f.id DESC""", (hospital,)))
