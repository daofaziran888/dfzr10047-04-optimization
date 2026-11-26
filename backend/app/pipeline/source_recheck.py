"""来源复验单引擎：签发时锁定来源证据，执行时按来源重算再决定真实入队渠道。

核心约定：
- 复验单只接收证据充分且确证偏差的来源；缺样来源不得进入签发范围。
- 准备阶段仍可沿迟到样本复核结果追加来源，追加来源单独留痕，不改写原签发快照。
- 执行时按来源重新核验，只有复核后仍确证偏差的来源才排入重解析渠道；
  重算后不再超阈的来源保留在「未入队」清单里并写明原因。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

STATUS_PREPARING = "准备中"
STATUS_QUEUED = "已入队"


def _public(tally: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in tally.items() if not key.startswith("_")}


def evidence_line(tally: dict[str, Any]) -> str:
    """单来源证据摘要：分母、偏差数与阈值一并写清。"""
    return (
        f"{tally['来源标识']} {tally.get('偏差数', '0')}/{tally.get('有效样本数', '0')}"
        f"（{float(tally.get('偏差比例') or 0):.1%}，阈值 {float(tally.get('偏差阈值') or 0):.0%}）"
    )


@dataclass
class RecheckOrder:
    """一张来源复验单：签发范围、补样追加范围、执行结果分开留痕。"""

    order_no: str
    claim_no: str
    coverage_strategy: str
    operator: str
    issued_at: str
    status: str = STATUS_PREPARING
    # 签发时纳入的来源证据快照
    issued_scope: dict[str, dict[str, Any]] = field(default_factory=dict)
    # 准备阶段沿迟到样本追加的来源证据快照
    late_scope: dict[str, dict[str, Any]] = field(default_factory=dict)
    execution: dict[str, Any] | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    def include(self, tally: dict[str, Any]) -> tuple[bool, str]:
        """签发纳入来源：必须是证据充分且确证偏差的来源。"""
        source = tally["来源标识"]
        if source in self.issued_scope or source in self.late_scope:
            return False, f"来源 {source} 已在复验单 {self.order_no} 范围内"
        if not tally.get("确证偏差"):
            return False, (
                f"来源 {source} 受理结论为「{tally.get('受理结论')}」，"
                f"证据不充分或未超阈，不得写入复验单"
            )
        self.issued_scope[source] = _public(tally)
        self.history.append({
            "时间": self.issued_at, "动作": "签发纳入",
            "来源标识": source, "证据": evidence_line(tally),
            "受理结论": tally.get("受理结论"),
        })
        return True, f"来源 {source} 已写入复验单 {self.order_no} 签发范围"

    def include_late(self, tally: dict[str, Any], at: str) -> tuple[bool, str]:
        """准备阶段追加来源：只接受迟到样本复核后新确证偏差的来源。"""
        source = tally["来源标识"]
        if source in self.issued_scope:
            return False, f"来源 {source} 已在签发范围内，无需追加"
        if source in self.late_scope:
            return False, f"来源 {source} 已在补样范围内"
        if not tally.get("确证偏差"):
            return False, (
                f"来源 {source} 仍为「{tally.get('受理结论')}」，"
                f"不得追加进复验单执行范围"
            )
        if self.status != STATUS_PREPARING:
            return False, f"复验单 {self.order_no} 已{self.status}，不能再追加来源"
        self.late_scope[source] = _public(tally)
        self.history.append({
            "时间": at, "动作": "补样追加",
            "来源标识": source, "证据": evidence_line(tally),
            "受理结论": tally.get("受理结论"),
        })
        return True, f"来源 {source} 已按迟到样本复核结果追加进复验单"

    def source_scope(self) -> list[str]:
        return [*self.issued_scope, *self.late_scope]

    def queue(self, queued: list[dict[str, Any]], skipped: list[dict[str, Any]],
              channels: list[str], at: str) -> dict[str, Any]:
        """写入执行结果：真正排入的渠道与未入队来源分别记账。"""
        self.status = STATUS_QUEUED
        self.execution = {
            "执行时间": at,
            "入队来源": [t["来源标识"] for t in queued],
            "未入队来源": [
                {"来源标识": item["来源标识"], "原因": item["未入队原因"]}
                for item in skipped
            ],
            "实际入队渠道": channels,
            "复核依据": "；".join(evidence_line(t) for t in queued) or "无来源通过执行复核",
        }
        self.history.append({
            "时间": at, "动作": "执行入队",
            "来源标识": ",".join(self.execution["入队来源"]) or "无",
            "证据": self.execution["复核依据"],
            "受理结论": STATUS_QUEUED,
        })
        return dict(self.execution)

    def to_row(self, row_id: int) -> dict[str, Any]:
        included = [*self.issued_scope, *self.late_scope]
        execution = self.execution or {}
        queued = execution.get("入队来源", [])
        skipped = execution.get("未入队来源", [])
        gaps: list[str] = []
        for item in skipped:
            gaps.append(f"{item['来源标识']} 未入队：{item['原因']}")
        if self.status != STATUS_QUEUED and included:
            gaps.append("尚未写入执行队列")
        return {
            "id": row_id,
            "status": self.status,
            "pending": self.status == STATUS_PREPARING,
            "abnormal": False,
            "复验单号": self.order_no,
            "关联申请": self.claim_no,
            "来源范围": ",".join(self.issued_scope) or "",
            "补样来源": ",".join(self.late_scope) or "",
            "应采样本数": next(
                (t.get("应采样本数", "") for t in [*self.issued_scope.values(),
                                                   *self.late_scope.values()]), ""),
            "偏差阈值": next(
                (t.get("偏差阈值", "") for t in [*self.issued_scope.values(),
                                                 *self.late_scope.values()]), ""),
            "覆盖策略": self.coverage_strategy,
            "复验状态": self.status,
            "签发人标识": self.operator,
            "签发时间": self.issued_at,
            "执行时间": execution.get("执行时间", ""),
            "实际入队渠道": ",".join(execution.get("实际入队渠道", [])),
            "重解析来源": ",".join(queued),
            "未入队来源": ",".join(item["来源标识"] for item in skipped),
            "复核依据": execution.get("复核依据", ""),
            "覆盖缺口": ";".join(gaps),
            "_issued_scope": dict(self.issued_scope),
            "_late_scope": dict(self.late_scope),
            "_history": list(self.history),
        }


def plan_execution(
    order: RecheckOrder,
    current_tallies: dict[str, dict[str, Any]],
    channel_of: Callable[[str], str],
    at: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """按执行时点重算的结果拆分入队/未入队来源，并给出真实渠道清单。"""
    queued: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    channels: list[str] = []
    for source in order.source_scope():
        tally = current_tallies.get(source)
        if tally is None:
            skipped.append({"来源标识": source, "未入队原因": "执行时无样本数据"})
            continue
        if tally.get("确证偏差"):
            queued.append(tally)
            channel = channel_of(source)
            if channel and channel not in channels:
                channels.append(channel)
            continue
        reason = (
            f"{tally.get('受理结论')}：{tally.get('不可受理原因')}"
            if tally.get("不可受理原因")
            else (
                f"{tally.get('受理结论')}：执行复核偏差比例 "
                f"{float(tally.get('偏差比例') or 0):.1%} 未超过阈值"
            )
        )
        skipped.append({"来源标识": source, "未入队原因": reason})
    order.queue(queued, skipped, channels, at)
    return queued, skipped, channels


def build_order(
    order_no: str,
    claim_no: str,
    coverage_strategy: str,
    operator: str,
    issued_at: str,
    tallies: Iterable[dict[str, Any]],
) -> tuple[RecheckOrder | None, list[str], str]:
    """签发复验单：仅纳入可发起修复的来源，缺样来源返回拒绝原因。"""
    order = RecheckOrder(order_no, claim_no, coverage_strategy, operator, issued_at)
    rejected: list[str] = []
    for tally in tallies:
        ok, message = order.include(tally)
        if not ok:
            rejected.append(message)
    if not order.issued_scope:
        return None, rejected, "没有任何来源满足签发条件，未生成来源复验单"
    return order, rejected, (
        f"复验单 {order_no} 已签发，签发范围 {list(order.issued_scope)}"
    )
