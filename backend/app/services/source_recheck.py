"""来源复验单领域服务：留存签发范围与执行时真正排入的重解析渠道，产出来源复验单数据集。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class SourceRecheckService(BaseModuleService):
    MODULE = "source_recheck"
    ENTITY = "来源复验单"
    OPERATION_TERM = "复验"
    LIST_FIELDS = [
        "复验单号", "关联申请", "来源范围", "补样来源", "应采样本数", "偏差阈值",
        "覆盖策略", "复验状态", "实际入队渠道", "重解析来源", "未入队来源",
    ]
    REQUIRED_FIELDS = ["复验单号", "关联申请", "来源范围"]
    STATUS_ORDER = ["准备中", "已入队", "已回退"]
    ACTION_RULES: dict[str, str] = {}
    NEGATIVE_ACTIONS: list[str] = []

    def _find(self, order_no: str) -> dict[str, Any] | None:
        for row in store.rows(self.MODULE):
            if row.get("复验单号") == order_no:
                return row
        return None

    def upsert(self, values: dict[str, Any]) -> dict[str, Any]:
        """签发与执行共用一条复验单记录：同一单号只保留一条，执行时覆盖真实渠道。"""
        order_no = str(values.get("复验单号") or "").strip()
        row = self._find(order_no)
        if row is None:
            rows = store.rows(self.MODULE)
            row = {
                "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
                "pending": True, "abnormal": False,
            }
            rows.append(row)
        for field in (*self.LIST_FIELDS, "签发人标识", "签发时间", "执行时间",
                      "复核依据", "覆盖缺口", "重解析任务"):
            if field in values:
                row[field] = values[field]
        status = str(values.get("复验状态") or row.get("复验状态") or "准备中")
        row["status"] = status if status in self.STATUS_ORDER else "准备中"
        row["pending"] = row["status"] == "准备中"
        row["abnormal"] = row["status"] == "已回退"
        return dict(row)

    def queued_channels(self, order_no: str) -> list[str]:
        row = self._find(order_no)
        if row is None:
            return []
        return [c.strip() for c in str(row.get("实际入队渠道") or "").split(",") if c.strip()]

    def scope_of(self, order_no: str) -> list[str]:
        row = self._find(order_no)
        if row is None:
            return []
        sources = [row.get("来源范围") or "", row.get("补样来源") or ""]
        return [
            part.strip()
            for text in sources
            for part in str(text).split(",")
            if part.strip()
        ]

    def open_orders(self) -> list[dict[str, Any]]:
        """列出仍处于准备中的复验单，供值班员继续确认待修来源。"""
        return [
            {
                "复验单号": row.get("复验单号"),
                "关联申请": row.get("关联申请"),
                "来源范围": row.get("来源范围"),
                "补样来源": row.get("补样来源"),
                "复验状态": row.get("复验状态"),
            }
            for row in store.rows(self.MODULE)
            if row.get("status") == "准备中"
        ]
