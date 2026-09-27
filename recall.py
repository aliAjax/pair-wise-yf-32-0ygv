#!/usr/bin/env python3
"""供体安全召回——业务判定层。

规则：
- 协调员登记风险类型、报告来源、发生时刻；同一供体同一风险类型重复登记直接返回首次召回。
- 系统找出该供体全部在途分配：未植入的一律停住后续交接（状态置 recalled）；
  已植入的保留原状态，只生成受者随访。
- 每条通知由对应接收医院逐条确认；仍有未确认通知时召回不能关闭。
"""
from __future__ import annotations

import sqlite3
from typing import Any

from common import ApiError, iso, parse_time
from recall_store import RecallStore

# 与 app 中的定义解耦：召回登记时仍需处理的分配状态。
# withdrawn/expired 已退出流程，不再打扰；植入的只建随访。
AFFECTED_STATUSES = ("proposed", "accepted", "in_transit", "handed_off", "implanted")
IMPLANTED = "implanted"


class RecallService:
    def __init__(self, service: Any):
        self.service = service
        self.repo = service.repo

    @staticmethod
    def _audit(conn: sqlite3.Connection, allocation_id: int | None, donor_id: int | None,
               actor: str, role: str, action: str, detail: dict[str, Any]) -> None:
        # 审计表结构归主库 Repository 管，这里仅复用其落库方法。
        from app import Repository
        Repository.audit(conn, allocation_id, donor_id, actor, role, action, detail)

    # ---------------- 协调员登记召回 ----------------
    def register_recall(self, actor: str, role: str, body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        if role != "coordinator":
            raise ApiError(403, "recall_forbidden", "只有协调员可以登记供体感染风险召回")
        donor_id = body.get("donor_id")
        risk_type = str(body.get("risk_type", "")).strip()
        report_source = str(body.get("report_source", "")).strip()
        occurred_raw = body.get("occurred_at")
        if not isinstance(donor_id, int) or not risk_type or not report_source:
            raise ApiError(400, "missing_fields", "donor_id、risk_type、report_source 必填")
        occurred_at = iso(parse_time(occurred_raw))
        with self.repo.tx() as conn:
            donor = conn.execute("SELECT * FROM donors WHERE id=?", (donor_id,)).fetchone()
            if not donor:
                raise ApiError(404, "donor_not_found", "供体不存在")
            existing = RecallStore.find_by_donor_risk(conn, donor_id, risk_type)
            if existing:
                self._audit(conn, None, donor_id, actor, role, "donor_recall_duplicate",
                                 {"recall_id": existing["id"], "risk_type": risk_type})
                payload = self._build(conn, existing)
                payload["duplicate"] = True
                return payload, False
            recall_id = RecallStore.insert_recall(conn, donor_id, risk_type, report_source, occurred_at, actor, iso())
            stopped: list[int] = []
            followups: list[int] = []
            for item in RecallStore.affected_allocations(conn, donor_id, AFFECTED_STATUSES):
                if item["allocation_status"] == IMPLANTED:
                    # 已植入：保留分配原状态，仅生成受者临床随访并通知接收医院。
                    disposition = "followup_only"
                    followup_id = RecallStore.insert_followup(
                        conn, recall_id, item["allocation_id"], item["candidate_id"],
                        item["hospital"], risk_type, iso())
                    followups.append(followup_id)
                    self._audit(conn, item["allocation_id"], donor_id, actor, role,
                                     "recipient_followup_created",
                                     {"recall_id": recall_id, "followup_id": followup_id, "risk_type": risk_type})
                else:
                    # 未植入：立即停住后续交接与一切继续流转。
                    disposition = "stopped"
                    RecallStore.stop_allocation(conn, item["allocation_id"], iso())
                    stopped.append(item["allocation_id"])
                    self._audit(conn, item["allocation_id"], donor_id, actor, role,
                                     "allocation_recalled", {"recall_id": recall_id, "risk_type": risk_type})
                RecallStore.insert_notification(
                    conn, recall_id, item["allocation_id"], item["hospital"],
                    disposition, item["allocation_status"], iso())
            if stopped or donor["status"] != "used":
                # 仍有可能继续流转的供体一律冻结；已全部植入的供体保持 used。
                RecallStore.mark_donor_recalled(conn, donor_id)
            self._audit(conn, None, donor_id, actor, role, "donor_recall_registered",
                             {"recall_id": recall_id, "risk_type": risk_type, "report_source": report_source,
                              "occurred_at": occurred_at, "stopped_allocations": stopped, "followups": followups})
            return self._build(conn, RecallStore.get_recall(conn, recall_id)), True

    # ---------------- 接收医院逐条确认 ----------------
    def acknowledge(self, notification_id: int, actor: str, role: str, hospital: str) -> dict[str, Any]:
        if role != "hospital":
            raise ApiError(403, "ack_forbidden", "只有接收医院可以确认召回通知")
        with self.repo.tx() as conn:
            note = RecallStore.get_notification(conn, notification_id)
            if not note:
                raise ApiError(404, "notification_not_found", "召回通知不存在")
            if note["hospital"] != hospital:
                raise ApiError(403, "wrong_hospital", "只能确认发给本医院的召回通知")
            if not note["acknowledged"]:
                RecallStore.acknowledge(conn, notification_id, actor, iso())
                self._audit(conn, note["allocation_id"], None, actor, role,
                                 "recall_notification_acknowledged",
                                 {"recall_id": note["recall_id"], "notification_id": notification_id})
            note = RecallStore.get_notification(conn, notification_id)
            return {"notification": dict(note),
                    "pending": RecallStore.pending_count(conn, note["recall_id"])}

    # ---------------- 协调员关闭召回 ----------------
    def close_recall(self, recall_id: int, actor: str, role: str) -> dict[str, Any]:
        if role != "coordinator":
            raise ApiError(403, "recall_forbidden", "只有协调员可以关闭召回")
        with self.repo.tx() as conn:
            recall = RecallStore.get_recall(conn, recall_id)
            if not recall:
                raise ApiError(404, "recall_not_found", "召回不存在")
            if recall["status"] == "closed":
                return self._build(conn, recall)
            pending = RecallStore.pending_count(conn, recall_id)
            if pending:
                raise ApiError(409, "recall_pending_ack", f"仍有 {pending} 条通知未确认，召回不能关闭")
            RecallStore.close(conn, recall_id, actor, iso())
            self._audit(conn, None, recall["donor_id"], actor, role, "donor_recall_closed",
                             {"recall_id": recall_id})
            return self._build(conn, RecallStore.get_recall(conn, recall_id))

    # ---------------- 查询 ----------------
    def get_recall(self, recall_id: int, role: str, hospital: str) -> dict[str, Any]:
        if role == "viewer":
            raise ApiError(403, "recall_forbidden", "当前角色不能查看召回")
        with self.repo.tx() as conn:
            recall = RecallStore.get_recall(conn, recall_id)
            if not recall:
                raise ApiError(404, "recall_not_found", "召回不存在")
            payload = self._build(conn, recall)
            if role == "hospital":
                self._restrict_to_hospital(payload, hospital)
            return payload

    def list_recalls(self, role: str, hospital: str) -> dict[str, Any]:
        if role == "viewer":
            raise ApiError(403, "recall_forbidden", "当前角色不能查看召回")
        with self.repo.tx() as conn:
            rows = RecallStore.list_recalls(conn)
            recalls = []
            for row in rows:
                payload = self._build(conn, row)
                if role == "hospital":
                    if not any(n["hospital"] == hospital for n in payload["notifications"]):
                        continue
                    self._restrict_to_hospital(payload, hospital)
                recalls.append(payload)
            return {"recalls": recalls, "server_time": iso()}

    def hospital_digest(self, conn: sqlite3.Connection, hospital: str) -> dict[str, Any]:
        return {
            "recall_notifications": [dict(r) for r in RecallStore.notifications_for_hospital(conn, hospital)],
            "recipient_followups": [dict(r) for r in RecallStore.followups_for_hospital(conn, hospital)],
        }

    # ---------------- 供体/分配冻结拦截 ----------------
    @staticmethod
    def assert_donor_not_recalled(conn: sqlite3.Connection, donor_id: int) -> None:
        if RecallStore.exists_for_donor(conn, donor_id):
            raise ApiError(409, "donor_under_recall", "该供体已登记感染风险召回，不能提出新分配")

    # ---------------- 组装 ----------------
    @staticmethod
    def _restrict_to_hospital(payload: dict[str, Any], hospital: str) -> None:
        own = [n for n in payload["notifications"] if n["hospital"] == hospital]
        if not own:
            raise ApiError(403, "recall_forbidden", "医院不能查看与本机构无关的召回")
        payload["notifications"] = own
        payload["recipient_followups"] = [f for f in payload["recipient_followups"] if f["hospital"] == hospital]
        payload["affected_hospitals"] = [hospital]
        payload["total_notifications"] = len(own)
        payload["acknowledged_count"] = sum(1 for n in own if n["acknowledged"])
        payload["pending_count"] = sum(1 for n in own if not n["acknowledged"])

    def _build(self, conn: sqlite3.Connection, recall: sqlite3.Row) -> dict[str, Any]:
        notifications = [dict(r) for r in RecallStore.list_notifications(conn, recall["id"])]
        followups = [dict(r) for r in RecallStore.list_followups(conn, recall["id"])]
        pending = sum(1 for n in notifications if not n["acknowledged"])
        return {
            **dict(recall),
            "notifications": notifications,
            "recipient_followups": followups,
            "affected_hospitals": sorted({n["hospital"] for n in notifications}),
            "total_notifications": len(notifications),
            "acknowledged_count": len(notifications) - pending,
            "pending_count": pending,
            "stopped_count": sum(1 for n in notifications if n["disposition"] == "stopped"),
            "followup_count": sum(1 for n in notifications if n["disposition"] == "followup_only"),
            "duplicate": False,
        }
