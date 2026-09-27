"""供体安全召回判定：纯业务规则，不依赖存储与 HTTP 层。

- 同一风险的判重身份：供体 + 风险类型 + 发生时刻；
- 未植入分配立即停住后续交接，已植入分配只生成受者随访并保留原状态；
- 接收医院全部确认前召回不能关闭。
"""
from __future__ import annotations

from typing import Any

# 未植入、仍可继续流转的分配状态：召回后一律停住后续交接
HOLD_STATUSES = ("proposed", "accepted", "in_transit", "handed_off")
# 已植入：保留原状态，只生成受者随访
FOLLOW_UP_STATUSES = ("implanted",)


class RecallPolicy:
    """供体安全召回的判定规则。"""

    @staticmethod
    def identity(donor_id: int, risk_type: str, occurred_at: str) -> tuple[int, str, str]:
        """同一风险的判定键：供体、风险类型、发生时刻（入参需已规范化）。"""
        return (donor_id, risk_type.strip(), occurred_at)

    @staticmethod
    def notification_kind(allocation_status: str) -> str | None:
        """单条分配的召回处置：hold=停住后续交接，follow_up=受者随访，None=无需通知。"""
        if allocation_status in HOLD_STATUSES:
            return "hold"
        if allocation_status in FOLLOW_UP_STATUSES:
            return "follow_up"
        return None

    @classmethod
    def plan_notifications(cls, allocations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """找出该供体全部分配，逐条判定接收医院应收到的召回通知。"""
        plan = []
        for allocation in allocations:
            kind = cls.notification_kind(allocation["status"])
            if kind:
                plan.append({"allocation_id": allocation["id"], "hospital": allocation["candidate_hospital"], "kind": kind})
        return plan

    @staticmethod
    def flow_blocked(has_recall: bool, allocation_status: str) -> bool:
        """供体已登记召回时，未植入分配禁止继续流转；已植入保留原状态不受影响。"""
        return has_recall and allocation_status in HOLD_STATUSES

    @staticmethod
    def can_close(pending_count: int) -> bool:
        """仍存在未确认通知时召回不能关闭。"""
        return pending_count == 0
