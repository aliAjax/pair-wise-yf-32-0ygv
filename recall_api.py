"""供体安全召回请求入口：HTTP 路由分发。

召回判定见 recall_policy.py，记录存取见 recall_store.py；
本模块只把请求映射到 OrganAllocationService 的召回方法，返回 (状态码, 响应体)，
路径不匹配时返回 None，由主 Handler 继续处理。
"""
from __future__ import annotations

from typing import Any


def _parts(path: str) -> list[str]:
    return [p for p in path.split("/") if p]


def dispatch_get(service: Any, path: str, role: str) -> tuple[int, Any] | None:
    parts = _parts(path)
    if parts == ["api", "recalls"]:
        return 200, {"recalls": service.recall_summaries(role)}
    if len(parts) == 3 and parts[:2] == ["api", "recalls"] and parts[2].isdigit():
        return 200, service.recall_detail(int(parts[2]), role)
    return None


def dispatch_post(service: Any, path: str, actor: str, role: str, hospital: str, body: dict[str, Any]) -> tuple[int, Any] | None:
    parts = _parts(path)
    if parts == ["api", "recalls"]:
        payload = service.register_recall(actor, role, body)
        return (200 if payload.get("duplicate") else 201), payload
    if len(parts) == 4 and parts[:2] == ["api", "recalls"] and parts[2].isdigit():
        recall_id, action = int(parts[2]), parts[3]
        if action == "acknowledge":
            return 200, service.acknowledge_recall(recall_id, actor, role, hospital)
        if action == "close":
            return 200, service.close_recall(recall_id, actor, role)
    return None
