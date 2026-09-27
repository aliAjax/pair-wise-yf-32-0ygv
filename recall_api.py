#!/usr/bin/env python3
"""供体安全召回——请求入口。

只做路径分发和 HTTP 状态码映射，不写业务规则：
- POST   /api/recalls                          协调员登记风险召回（201 新建，200 重复返回首次）
- GET    /api/recalls                          召回列表（医院只看本机构相关）
- GET    /api/recalls/{id}                     召回详情
- POST   /api/recalls/{id}/close               协调员关闭（仍有未确认时 409）
- POST   /api/recalls/notifications/{id}/ack   接收医院逐条确认
"""
from __future__ import annotations

from typing import Any


def dispatch_get(handler: Any, parts: list[str], actor: str, role: str, hospital: str) -> tuple[int, Any] | None:
    if parts[:1] != ["api"] or "recalls" not in parts:
        return None
    recalls = handler.service.recalls
    if parts == ["api", "recalls"]:
        return 200, recalls.list_recalls(role, hospital)
    if len(parts) == 3 and parts[1].isdigit():
        return 200, recalls.get_recall(int(parts[1]), role, hospital)
    return None


def dispatch_post(handler: Any, parts: list[str], actor: str, role: str, hospital: str,
                  body: dict[str, Any]) -> tuple[int, Any] | None:
    if parts[:1] != ["api"]:
        return None
    recalls = handler.service.recalls
    if parts == ["api", "recalls"]:
        payload, created = recalls.register_recall(actor, role, body)
        return (201 if created else 200), payload
    if len(parts) == 4 and parts[1] == "recalls" and parts[2].isdigit() and parts[3] == "close":
        return 200, recalls.close_recall(int(parts[2]), actor, role)
    if len(parts) == 5 and parts[1] == "recalls" and parts[2] == "notifications" and parts[3].isdigit() and parts[4] == "ack":
        return 200, recalls.acknowledge(int(parts[3]), actor, role, hospital)
    return None
