"""偏差修复申请领域服务：分来源核对受理范围、签发来源复验单并驱动按来源重解析。

核心行为：
- 复核员按来源确认受理范围，服务端用来源自己的有效样本作分母核对采样数量与偏差阈值；
- 证据不足的来源与可发起修复的来源在确认页分组展示，缺样单列且不当作零偏差；
- 只有证据充分且确证偏差的来源能写进来源复验单，迟到样本复核后可追加待修来源；
- 执行时按来源重新核验，原复验单记录真正排入的重解析渠道；
- 历史复核快照保留当时使用的样本与阈值，补齐样本后重算不改变历史偏差比例。

产出偏差修复申请数据集，供输出契约与重解析任务消费。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.pipeline.deviation_scope import (
    DEFAULT_DEVIATION_RATE,
    DEFAULT_REQUIRED_SAMPLES,
    GROUP_INSUFFICIENT,
    GROUP_REPAIRABLE,
    GROUP_WITHIN_LIMIT,
    VERDICT_INSUFFICIENT,
    VERDICT_REPAIRABLE,
    group_verdicts,
    tally_source,
)
from app.pipeline.reparse_manifest import (
    build_manifest,
    source_reparse_task_rows,
    sync_manifest_outcome,
)
from app.pipeline.source_recheck import (
    STATUS_PREPARING,
    RecheckOrder,
    build_order,
    plan_execution,
)
from app.services.base import BaseModuleService
from app.store import store

STATUS_PENDING = "待核对"
STATUS_CHECKING = "核对中"
STATUS_ISSUED = "复验单已签发"
STATUS_EXECUTED = "已执行"
STATUS_ACKED = "已回填"


class DeviationRepairService(BaseModuleService):
    MODULE = "deviation_repair"
    ENTITY = "偏差修复申请"
    OPERATION_TERM = "核对"
    LIST_FIELDS = [
        "申请编号", "关联复核", "应采样本数", "偏差阈值", "来源清单",
        "可修复来源", "证据不足来源", "复验单号", "复验状态", "实际入队渠道",
    ]
    REQUIRED_FIELDS = ["申请编号", "关联复核", "来源清单"]
    STATUS_ORDER = [STATUS_PENDING, STATUS_CHECKING, STATUS_ISSUED,
                    STATUS_EXECUTED, STATUS_ACKED]
    ACTION_RULES = {
        "核对受理范围": STATUS_CHECKING,
        "签发复验单": STATUS_ISSUED,
        "执行复验单": STATUS_EXECUTED,
        "确认回填": STATUS_ACKED,
    }
    NEGATIVE_ACTIONS: list[str] = []

    DEFAULT_OPERATOR = "值班员"

    # ------------------------------------------------------------------
    # 基础读写
    # ------------------------------------------------------------------
    def list_entries(self, **kwargs) -> tuple[list[dict[str, Any]], int]:
        """列表去掉内部快照字段，只保留受理范围与复验单展示口径。"""
        rows, total = super().list_entries(**kwargs)
        return [
            {k: v for k, v in row.items() if not k.startswith("_")} for row in rows
        ], total

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """登记申请时按来源清单初始化来源明细，每个来源独立持有自己的样本。"""
        entry, missing = super().create_entry(values)
        if entry is None:
            return entry, missing
        source_ids = [
            part.strip() for part in str(values.get("来源清单") or "").split(",")
            if part.strip()
        ]
        entry["_来源明细"] = [
            {
                "来源标识": source_id,
                "来源渠道": str(values.get("来源渠道") or ""),
                "关联投递批次": "",
                "应采样本数": str(values.get("应采样本数") or DEFAULT_REQUIRED_SAMPLES),
                "偏差阈值": str(values.get("偏差阈值") or f"{DEFAULT_DEVIATION_RATE:.2f}"),
                "样本回执": [],
            }
            for source_id in source_ids
        ]
        entry["_复核历史"] = []
        entry["_复验单编号"] = f"SRC-{entry.get('申请编号')}"
        entry["_重解析清单编号"] = f"RM-{entry.get('申请编号')}"
        return entry, []

    def _find_claim(self, claim_no: str) -> dict[str, Any] | None:
        for row in store.rows(self.MODULE):
            if row.get("申请编号") == claim_no:
                return row
        return None

    # 供输出契约等下游模块复用的稳定读取口
    claim_by_no = _find_claim

    def sources_of(self, claim: dict[str, Any]) -> list[dict[str, Any]]:
        return self._sources(claim)

    @staticmethod
    def source_batches(source: dict[str, Any]) -> list[str]:
        return DeviationRepairService._source_batches(source)

    def source_batches_of(self, source_id: str) -> list[str]:
        """来源标识对应的真实投递批次，供按来源重解析单落到具体批次。"""
        for row in store.rows(self.MODULE):
            for source in self._sources(row):
                if source.get("来源标识") == source_id:
                    return self._source_batches(source)
        return []

    def get_claim(self, claim_id: int) -> dict[str, Any] | None:
        return store.find(self.MODULE, claim_id)

    def _sources(self, claim: dict[str, Any]) -> list[dict[str, Any]]:
        return claim.setdefault("_来源明细", [])

    def _find_source(self, claim: dict[str, Any],
                     source_id: str) -> dict[str, Any] | None:
        return next(
            (s for s in self._sources(claim) if s.get("来源标识") == source_id), None)

    # ------------------------------------------------------------------
    # 受理范围核对
    # ------------------------------------------------------------------
    def tallies_for(self, claim: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """按来源逐个核对采样数量与偏差阈值，返回来源标识到真实结论的映射。"""
        required_default = int(str(claim.get("应采样本数") or DEFAULT_REQUIRED_SAMPLES))
        threshold_default = float(str(claim.get("偏差阈值") or DEFAULT_DEVIATION_RATE))
        result: dict[str, dict[str, Any]] = {}
        for source in self._sources(claim):
            required = int(str(source.get("应采样本数") or required_default))
            threshold = float(str(source.get("偏差阈值") or threshold_default))
            result[source["来源标识"]] = tally_source(
                source["来源标识"],
                source.get("样本回执", []),
                required_samples=required,
                deviation_threshold=threshold,
                channel=str(source.get("来源渠道") or ""),
            )
        return result

    def _record_snapshot(self, claim: dict[str, Any], action: str, at: str,
                         tallies: dict[str, dict[str, Any]]) -> None:
        """把本次核对实际使用的样本、分母与阈值写入历史快照，只追加不改写。"""
        history = claim.setdefault("_复核历史", [])
        for source_id, tally in tallies.items():
            history.append({
                "序号": len(history) + 1,
                "时间": at,
                "动作": action,
                "来源标识": source_id,
                "来源渠道": tally.get("来源渠道", ""),
                "应采样本数": tally.get("应采样本数", ""),
                "偏差阈值": tally.get("偏差阈值", ""),
                "有效样本数": tally.get("有效样本数", ""),
                "偏差数": tally.get("偏差数", ""),
                "偏差比例": tally.get("偏差比例", ""),
                "使用样本回执": tally.get("使用样本回执", ""),
                "缺样回执": tally.get("缺样回执", ""),
                "重复回执": tally.get("重复回执", ""),
                "受理结论": tally.get("受理结论", ""),
                "不可受理原因": tally.get("不可受理原因", ""),
                "说明": tally.get("受理说明", ""),
            })

    def _update_outcomes(self, claim: dict[str, Any],
                         tallies: dict[str, dict[str, Any]],
                         at: str) -> None:
        groups = group_verdicts(tallies.values())
        claim["可修复来源"] = ",".join(
            t["来源标识"] for t in groups[GROUP_REPAIRABLE])
        claim["证据不足来源"] = ",".join(
            t["来源标识"] for t in groups[GROUP_INSUFFICIENT])
        claim["偏差未超阈来源"] = ",".join(
            t["来源标识"] for t in groups[GROUP_WITHIN_LIMIT])
        missing = [
            t for t in tallies.values()
            if int(str(t.get("缺样数") or 0)) > 0
        ]
        claim["缺样来源"] = ",".join(
            f"{t['来源标识']}(缺{int(str(t.get('缺样数') or 0))})" for t in missing)
        claim["核对时间"] = at
        claim["_来源结论"] = {
            source: {k: v for k, v in tally.items() if not k.startswith("_")}
            for source, tally in tallies.items()
        }

        hints: list[str] = []
        for tally in tallies.values():
            if tally["受理结论"] == VERDICT_INSUFFICIENT:
                hints.append(
                    f"{tally['来源标识']} 证据不足：{tally.get('不可受理原因')}"
                )
            elif tally["受理结论"] == VERDICT_REPAIRABLE:
                if claim.get("复验单号"):
                    in_scope = tally["来源标识"] in self._order_scope(claim)
                    state = "已在复验单内" if in_scope else "尚未写入复验单"
                    hints.append(
                        f"{tally['来源标识']} 可发起修复（{state}）"
                    )
                else:
                    hints.append(f"{tally['来源标识']} 可发起修复，待签发复验单")
        claim["样本更新提示"] = "；".join(hints) if hints else "各来源样本未见更新"
        claim["提示状态"] = "待处理" if any(
            t["受理结论"] == VERDICT_INSUFFICIENT for t in tallies.values()
        ) or bool(claim.get("复验单号")) else "已处理"

        if claim.get("status") in (None, "", STATUS_PENDING):
            claim["status"] = STATUS_CHECKING
        claim["pending"] = claim["status"] != STATUS_ACKED
        claim["abnormal"] = bool(claim.get("证据不足来源")) or bool(
            claim.get("可修复来源"))

    def _order_scope(self, claim: dict[str, Any]) -> list[str]:
        order = self._order_of(claim)
        if isinstance(order, RecheckOrder):
            return order.source_scope()
        return [s for s in str(claim.get("来源范围") or "").split(",") if s]

    def verify_scope(self, claim_no: str, at: str) -> tuple[bool, str, dict[str, Any] | None]:
        """核对受理范围：证据不足与可修复来源分开，缺样来源不当作零偏差。"""
        claim = self._find_claim(claim_no)
        if claim is None:
            return False, f"偏差修复申请 {claim_no} 不存在", None
        tallies = self.tallies_for(claim)
        if not tallies:
            return False, f"申请 {claim_no} 没有登记任何来源", None
        self._record_snapshot(claim, "核对受理范围", at, tallies)
        self._update_outcomes(claim, tallies, at)
        repairable = claim.get("可修复来源") or "无"
        insufficient = claim.get("证据不足来源") or "无"
        return True, (
            f"申请 {claim_no} 受理范围已核对：可发起修复 {repairable}，"
            f"证据不足 {insufficient}；缺样来源 {claim.get('缺样来源') or '无'}"
        ), dict(claim)

    # ------------------------------------------------------------------
    # 样本登记与补样
    # ------------------------------------------------------------------
    def register_samples(self, claim_no: str, source_id: str,
                         receipts: list[dict[str, Any]], at: str
                         ) -> tuple[bool, str, dict[str, Any] | None]:
        """登记样本回执：相同回执号只算一次，迟到样本到齐后按新增样本重算。"""
        claim = self._find_claim(claim_no)
        if claim is None:
            return False, f"偏差修复申请 {claim_no} 不存在", None
        source = self._find_source(claim, source_id)
        if source is None:
            return False, f"申请 {claim_no} 没有来源 {source_id}", None
        if not receipts:
            return False, "未提供样本回执，未做任何变更", None

        samples = source.setdefault("样本回执", [])
        existing = {str(s.get("样本回执号")) for s in samples}
        added = 0
        duplicates: list[str] = []
        for receipt in receipts:
            receipt_no = str(receipt.get("样本回执号") or "").strip()
            if not receipt_no:
                continue
            if receipt_no in existing:
                duplicates.append(receipt_no)
                continue
            samples.append({
                "样本回执号": receipt_no,
                "到达状态": str(receipt.get("到达状态") or "已到"),
                "偏差标记": str(receipt.get("偏差标记") or "一致"),
                "复核结论": str(receipt.get("复核结论") or ""),
                "登记时间": str(receipt.get("登记时间") or at),
            })
            existing.add(receipt_no)
            added += 1

        tallies = self.tallies_for(claim)
        self._record_snapshot(claim, f"登记样本:{source_id}", at, tallies)
        self._update_outcomes(claim, tallies, at)
        tally = tallies.get(source_id, {})
        message = (
            f"来源 {source_id} 新增样本 {added} 份，当前 {tally.get('偏差数')}/"
            f"{tally.get('有效样本数')}（受理结论：{tally.get('受理结论')}）"
        )
        if duplicates:
            message += f"；重复回执 {','.join(duplicates)} 只记一次"
        return True, message, dict(claim)

    def complete_late_samples(self, claim_no: str, source_id: str, at: str
                              ) -> tuple[bool, str, dict[str, Any] | None]:
        """补齐迟到样本：把该来源登记为迟到的回执全部标记为已到，再重算真实数据。"""
        claim = self._find_claim(claim_no)
        if claim is None:
            return False, f"偏差修复申请 {claim_no} 不存在", None
        source = self._find_source(claim, source_id)
        if source is None:
            return False, f"申请 {claim_no} 没有来源 {source_id}", None
        late = [
            s for s in source.get("样本回执", [])
            if str(s.get("到达状态")) == "迟到"
        ]
        if not late:
            return False, f"来源 {source_id} 没有迟到样本待补齐", None
        for sample in late:
            sample["到达状态"] = "已到"
            sample["到达时间"] = at
        tallies = self.tallies_for(claim)
        self._record_snapshot(claim, f"补齐迟到样本:{source_id}", at, tallies)
        self._update_outcomes(claim, tallies, at)
        tally = tallies[source_id]
        return True, (
            f"来源 {source_id} 迟到样本 {len(late)} 份已补齐，"
            f"重算为 {tally.get('偏差数')}/{tally.get('有效样本数')}"
            f"（{float(tally.get('偏差比例') or 0):.1%}，"
            f"阈值 {float(tally.get('偏差阈值') or 0):.0%}），"
            f"受理结论：{tally.get('受理结论')}"
        ), dict(claim)

    # ------------------------------------------------------------------
    # 确认受理范围并签发复验单
    # ------------------------------------------------------------------
    def confirm_scope(self, claim_no: str, source_ids: list[str] | None,
                      at: str) -> tuple[bool, str, dict[str, Any] | None]:
        """复核员按来源确认受理范围：只允许勾选可发起修复的来源。"""
        claim = self._find_claim(claim_no)
        if claim is None:
            return False, f"偏差修复申请 {claim_no} 不存在", None
        tallies = self.tallies_for(claim)
        repairable = [
            source for source, tally in tallies.items()
            if tally["受理结论"] == VERDICT_REPAIRABLE
        ]
        selected = list(source_ids) if source_ids else repairable
        rejected = [
            source for source in selected
            if tallies.get(source, {}).get("受理结论") != VERDICT_REPAIRABLE
        ]
        if rejected:
            reasons = [
                f"{source}：{tallies.get(source, {}).get('受理结论', '来源不存在')}"
                for source in rejected
            ]
            return False, (
                f"以下来源不能写入复验单受理范围：{';'.join(reasons)}"
            ), dict(claim)
        if not selected:
            return False, "没有可发起修复的来源可确认受理", dict(claim)
        claim["确认受理来源"] = ",".join(selected)
        claim["确认时间"] = at
        claim["样本更新提示"] = (
            f"已确认受理来源 {','.join(selected)}；"
            f"{claim.get('证据不足来源') or '无'} 仍证据不足，补齐后可继续处理"
        )
        claim["提示状态"] = "待处理"
        claim["pending"] = True
        return True, (
            f"申请 {claim_no} 已确认受理来源 {','.join(selected)}；"
            f"证据不足来源 {claim.get('证据不足来源') or '无'} 不在受理范围"
        ), dict(claim)

    def issue_recheck(self, claim_no: str, operator: str, coverage_strategy: str,
                      at: str, source_ids: list[str] | None = None
                      ) -> tuple[bool, str, dict[str, Any] | None]:
        """签发来源复验单：每张单只纳入可发起修复的来源，缺样来源不得混入。"""
        claim = self._find_claim(claim_no)
        if claim is None:
            return False, f"偏差修复申请 {claim_no} 不存在", None
        if self._order_of(claim) is not None:
            return False, f"申请 {claim_no} 已签发复验单 {claim.get('复验单号')}", dict(claim)

        tallies = self.tallies_for(claim)
        confirmed = [t for t in tallies.values()
                     if t["受理结论"] == VERDICT_REPAIRABLE]
        if source_ids:
            confirmed = [t for t in confirmed if t["来源标识"] in source_ids]
        if not confirmed:
            return False, (
                f"申请 {claim_no} 没有证据充分且确证偏差的来源，不得签发复验单"
            ), dict(claim)

        order_no = str(claim.get("_复验单编号") or f"SRC-{claim_no}")
        order, rejected, message = build_order(
            order_no, claim_no, coverage_strategy, operator, at, confirmed)
        if order is None:
            return False, f"{message}；" + "；".join(rejected), dict(claim)

        claim["_复验单"] = order
        claim["复验单号"] = order_no
        claim["复验状态"] = order.status
        claim["来源范围"] = ",".join(order.issued_scope)
        claim["补样来源"] = ""
        claim["偏差阈值"] = next(
            (t.get("偏差阈值", "") for t in order.issued_scope.values()), "")
        claim["status"] = STATUS_ISSUED
        claim["pending"] = True
        self._record_snapshot(claim, "签发复验单", at, tallies)
        self._update_outcomes(claim, tallies, at)
        claim["status"] = STATUS_ISSUED
        self._write_order_row(claim, order)
        extra = f"；拒绝来源：{'；'.join(rejected)}" if rejected else ""
        return True, (
            f"{message}，覆盖策略「{coverage_strategy}」；"
            f"证据不足来源 {claim.get('证据不足来源') or '无'} 未写入签发范围{extra}"
        ), dict(claim)

    def include_late_source(self, claim_no: str, source_id: str, at: str
                            ) -> tuple[bool, str, dict[str, Any] | None]:
        """沿迟到样本复核结果继续处理待修来源：准备阶段追加进复验单范围。"""
        claim = self._find_claim(claim_no)
        if claim is None:
            return False, f"偏差修复申请 {claim_no} 不存在", None
        order = self._order_of(claim)
        if not isinstance(order, RecheckOrder):
            return False, f"申请 {claim_no} 尚未签发复验单，无法追加来源", None
        tallies = self.tallies_for(claim)
        tally = tallies.get(source_id)
        if tally is None:
            return False, f"申请 {claim_no} 没有来源 {source_id}", None
        ok, message = order.include_late(tally, at)
        if not ok:
            return False, message, dict(claim)

        claim["来源范围"] = ",".join(order.issued_scope)
        claim["补样来源"] = ",".join(order.late_scope)
        claim["复验状态"] = order.status
        claim["样本更新提示"] = (
            f"来源 {source_id} 已按迟到样本结论追加进复验单，"
            f"当前可执行来源 {','.join(order.source_scope())}"
        )
        claim["提示状态"] = "待处理"
        claim["pending"] = True
        self._record_snapshot(claim, f"追加待修来源:{source_id}", at, tallies)
        self._update_outcomes(claim, tallies, at)
        claim["复验状态"] = order.status
        self._write_order_row(claim, order)
        return True, message, dict(claim)

    def _write_order_row(self, claim: dict[str, Any], order: RecheckOrder) -> None:
        """把复验单状态同步到来源复验单数据集，并镜像到申请行的展示字段。"""
        from app.services.source_recheck import SourceRecheckService

        row = order.to_row(int(claim.get("id", 0)))
        for field in ("复验单号", "来源范围", "补样来源", "复验状态",
                      "实际入队渠道", "重解析来源", "未入队来源", "复核依据",
                      "执行时间", "覆盖缺口"):
            if field in row:
                claim[field] = row[field]
        claim["_复验单执行"] = row

        persisted = SourceRecheckService().upsert({
            "复验单号": order.order_no,
            "关联申请": order.claim_no,
            "来源范围": row["来源范围"],
            "补样来源": row["补样来源"],
            "应采样本数": row["应采样本数"],
            "偏差阈值": row["偏差阈值"],
            "覆盖策略": row["覆盖策略"],
            "复验状态": row["复验状态"],
            "签发人标识": order.operator,
            "签发时间": order.issued_at,
            "执行时间": row["执行时间"],
            "实际入队渠道": row["实际入队渠道"],
            "重解析来源": row["重解析来源"],
            "未入队来源": row["未入队来源"],
            "复核依据": row["复核依据"],
            "覆盖缺口": row["覆盖缺口"],
            "重解析任务": str(claim.get("重解析任务") or ""),
        })
        claim["复验单记录"] = persisted["id"]

    # ------------------------------------------------------------------
    # 执行复验单：按来源重算、生成重解析清单与按来源批次
    # ------------------------------------------------------------------
    def _order_of(self, claim: dict[str, Any]) -> RecheckOrder | None:
        """取复验单：内存里没有时按来源复验单数据集还原，页面刷新后仍可继续执行。"""
        order = claim.get("_复验单")
        if isinstance(order, RecheckOrder):
            return order
        order_no = str(claim.get("复验单号") or "").strip()
        if not order_no:
            return None
        from app.services.source_recheck import SourceRecheckService

        row = SourceRecheckService()._find(order_no)  # noqa: SLF001 - 同仓内部读取
        if row is None:
            return None
        tallies = self.tallies_for(claim)
        restored = RecheckOrder(
            order_no=order_no,
            claim_no=str(claim.get("申请编号") or ""),
            coverage_strategy=str(row.get("覆盖策略") or "旧投递保留"),
            operator=str(row.get("签发人标识") or ""),
            issued_at=str(row.get("签发时间") or ""),
            status=str(row.get("复验状态") or STATUS_PREPARING),
        )
        for field, bucket in (("来源范围", restored.issued_scope),
                              ("补样来源", restored.late_scope)):
            for source in str(row.get(field) or "").split(","):
                source = source.strip()
                if source:
                    tally = tallies.get(source)
                    bucket[source] = (
                        {k: v for k, v in tally.items() if not k.startswith("_")}
                        if tally else {"来源标识": source}
                    )
        if restored.status != STATUS_PREPARING:
            restored.execution = {
                "执行时间": row.get("执行时间", ""),
                "入队来源": [
                    s.strip() for s in str(row.get("重解析来源") or "").split(",")
                    if s.strip()
                ],
                "未入队来源": [
                    {"来源标识": s.strip(), "原因": "执行复核未确证偏差"}
                    for s in str(row.get("未入队来源") or "").split(",") if s.strip()
                ],
                "实际入队渠道": [
                    c.strip() for c in str(row.get("实际入队渠道") or "").split(",")
                    if c.strip()
                ],
                "复核依据": str(row.get("复核依据") or ""),
            }
        claim["_复验单"] = restored
        return restored

    def execute_recheck(self, claim_no: str, at: str
                        ) -> tuple[bool, str, dict[str, Any] | None]:
        """执行复验单：按当前复核结论重算，真正入队的来源才排进重解析渠道。"""
        claim = self._find_claim(claim_no)
        if claim is None:
            return False, f"偏差修复申请 {claim_no} 不存在", None
        order = self._order_of(claim)
        if not isinstance(order, RecheckOrder):
            return False, f"申请 {claim_no} 尚未签发复验单，不得执行", dict(claim)
        if order.status != STATUS_PREPARING:
            return False, f"复验单 {order.order_no} 已{order.status}，不能重复执行", dict(claim)

        tallies = self.tallies_for(claim)
        queued, skipped, channels = plan_execution(
            order, tallies, self.channel_of, at)
        # 签发范围之外的来源同样要记账：证据不足的来源不能因为没进复验单就消失。
        in_scope = set(order.source_scope())
        for source, tally in tallies.items():
            if source in in_scope:
                continue
            skipped.append({
                "来源标识": source,
                "原因": (
                    f"{tally.get('受理结论')}：{tally.get('不可受理原因')}"
                    if tally.get("不可受理原因")
                    else f"{tally.get('受理结论')}，未写入签发范围"
                ),
            })
        manifest, batches, manifest_msg = build_manifest(
            str(claim.get("_重解析清单编号") or f"RM-{claim_no}"),
            claim_no, order.order_no,
            order.coverage_strategy, tallies.values(), self.channel_of,
            self.source_batches_of,
        )

        task_rows: list[dict[str, Any]] = []
        if manifest is not None:
            claim["_重解析清单"] = manifest
            claim["重解析清单编号"] = manifest["清单编号"]
            task_rows = self._create_reparse_tasks(claim, manifest)
            manifest["重解析任务"] = [row["重解析编号"] for row in task_rows]
        claim["契约同步"] = self._sync_contracts(claim_no)
        claim["status"] = STATUS_EXECUTED
        claim["pending"] = False
        claim["复验状态"] = order.status
        claim["重解析任务"] = ",".join(row["重解析编号"] for row in task_rows)
        self._write_order_row(claim, order)
        claim["实际入队渠道"] = ",".join(channels)
        claim["重解析来源"] = ",".join(t["来源标识"] for t in queued)
        claim["未入队来源"] = ",".join(t["来源标识"] for t in skipped)
        claim["覆盖缺口"] = ";".join(
            f"{item['来源标识']}：{item['原因']}" for item in skipped) or "无"
        claim["样本更新提示"] = self._execution_hint(order, tallies, queued, skipped)
        claim["提示状态"] = "待处理"
        self._record_snapshot(claim, "执行复验单", at, tallies)

        message = (
            f"复验单 {order.order_no} 已执行：实际入队渠道 "
            f"{','.join(channels) or '无'}；重解析来源 "
            f"{','.join(t['来源标识'] for t in queued) or '无'}"
        )
        if skipped:
            message += "；未入队来源 " + "、".join(
                f"{item['来源标识']}（{item['原因']}）" for item in skipped)
        if manifest is None:
            message += f"；{manifest_msg}"
        else:
            message += f"；{manifest['结论']}"
        return True, message, dict(claim)

    def _sync_contracts(self, claim_no: str) -> dict[str, Any]:
        """执行后把实际样本结论同步到输出契约，契约校验结论保持独立。"""
        from app.services.output_contract import OutputContractService

        return OutputContractService().sync_deviation_review(claim_no)

    def _execution_hint(self, order: RecheckOrder,                        tallies: dict[str, dict[str, Any]],
                        queued: list[dict[str, Any]],
                        skipped: list[dict[str, Any]]) -> str:
        """执行后保留样本更新提示：新到样本仍可继续登记并重算。"""
        parts: list[str] = []
        if queued:
            parts.append(
                "已入队来源 " + ",".join(t["来源标识"] for t in queued)
            )
        if skipped:
            parts.append(
                "未入队来源 " + "、".join(
                    f"{item['来源标识']}（{item['原因']}）" for item in skipped)
            )
        late_pending = [
            source for source, tally in tallies.items()
            if int(str(tally.get("缺样数") or 0)) > 0
        ]
        if late_pending:
            parts.append(
                "仍有缺样来源 " + ",".join(late_pending) + " 可继续补样重算"
            )
        else:
            parts.append("如收到新样本可继续登记并重算，历史复核快照保持不变")
        return "；".join(parts)

    def _create_reparse_tasks(self, claim: dict[str, Any],
                              manifest: dict[str, Any]) -> list[dict[str, Any]]:
        """按来源生成重解析批次：每条只覆盖该来源的偏差样本。"""
        positions = self._source_positions(claim)
        rows = source_reparse_task_rows(
            manifest,
            positions,
            reason=f"抽样复核偏差复核结论:{claim['申请编号']} 按来源重解析",
        )
        tasks = store.rows("reparse_task")
        created: list[dict[str, Any]] = []
        for values in rows:
            if any(t.get("重解析编号") == values["重解析编号"] for t in tasks):
                continue
            entry = {
                "id": max((int(t.get("id", 0)) for t in tasks), default=0) + 1,
                "status": "待执行", "pending": True, "abnormal": False,
            }
            entry.update(values)
            entry["重解析结论"] = ""
            tasks.append(entry)
            created.append(entry)
        return created

    def _source_positions(self, claim: dict[str, Any]) -> dict[str, tuple[str, str]]:
        """来源重解析位点：取该来源关联投递批次的最小/最大投递时间。"""
        positions: dict[str, tuple[str, str]] = {}
        intakes = {str(r.get("批次编号")): str(r.get("投递时间") or "")
                   for r in store.rows("file_intake")}
        for source in self._sources(claim):
            times = [
                intakes[batch] for batch in self._source_batches(source)
                if intakes.get(batch)
            ]
            if not times:
                fallback = str(claim.get("核对时间") or claim.get("签发时间") or "")
                positions[source["来源标识"]] = (fallback, fallback)
                continue
            positions[source["来源标识"]] = (min(times), max(times))
        return positions

    @staticmethod
    def _source_batches(source: dict[str, Any]) -> list[str]:
        raw = source.get("关联投递批次") or source.get("关联批次") or ""
        if isinstance(raw, list):
            return [str(item) for item in raw if str(item)]
        return [part.strip() for part in str(raw).split(",") if part.strip()]

    def channel_of(self, source_id: str) -> str:
        """来源标识到渠道的映射，供复验单记录真正排入的渠道。"""
        for row in store.rows(self.MODULE):
            for source in self._sources(row):
                if source.get("来源标识") == source_id:
                    return str(source.get("来源渠道") or "")
        return ""

    # ------------------------------------------------------------------
    # 回填与查询
    # ------------------------------------------------------------------
    def acknowledge(self, claim_no: str, at: str) -> tuple[bool, str, dict[str, Any] | None]:
        """确认回填：处理完样本更新提示后关闭申请，历史快照仍可回查。"""
        claim = self._find_claim(claim_no)
        if claim is None:
            return False, f"偏差修复申请 {claim_no} 不存在", None
        if claim.get("status") != STATUS_EXECUTED:
            return False, (
                f"申请 {claim_no} 当前为 {claim.get('status')}，"
                f"只有已执行的申请可以确认回填"
            ), dict(claim)
        claim["status"] = STATUS_ACKED
        claim["pending"] = False
        claim["提示状态"] = "已处理"
        claim["回填时间"] = at
        claim["样本更新提示"] = (
            f"回填已完成；历史复核共 {len(claim.get('_复核历史', []))} 条快照可回查"
        )
        return True, f"申请 {claim_no} 已确认回填", dict(claim)

    def handle_sample_update(self, claim_no: str, at: str
                             ) -> tuple[bool, str, dict[str, Any] | None]:
        """处理样本更新提示：按当前样本重新核对，并把新确证来源同步进重解析清单。"""
        claim = self._find_claim(claim_no)
        if claim is None:
            return False, f"偏差修复申请 {claim_no} 不存在", None
        if claim.get("提示状态") != "待处理":
            return False, f"申请 {claim_no} 当前没有待处理的样本更新", dict(claim)
        tallies = self.tallies_for(claim)
        before = set((claim.get("_重解析清单") or {}).get("入队来源", []))
        manifest = claim.get("_重解析清单")
        added: list[str] = []
        if manifest is not None:
            newly = [
                tally for source, tally in tallies.items()
                if source not in before and tally.get("确证偏差")
            ]
            if newly:
                sync_manifest_outcome(
                    manifest, tallies.values(), newly, self.channel_of,
                    manifest["清单编号"], self.source_batches_of)
                added = manifest.get("同步追加来源", [])
        self._record_snapshot(claim, "处理样本更新", at, tallies)
        self._update_outcomes(claim, tallies, at)
        claim["提示状态"] = "已处理"
        claim["样本更新提示"] = (
            f"样本更新提示已处理；重解析清单新追加来源 {','.join(added) or '无'}，"
            f"历史复核快照保持不变"
        )
        return True, (
            f"申请 {claim_no} 的样本更新提示已处理，"
            f"重解析清单新追加来源 {','.join(added) or '无'}"
        ), dict(claim)

    def history(self, claim_no: str) -> tuple[bool, list[dict[str, Any]], str]:
        """回查历史复核：返回每次核对实际使用的样本与阈值，原偏差比例不变。"""
        claim = self._find_claim(claim_no)
        if claim is None:
            return False, [], f"偏差修复申请 {claim_no} 不存在"
        history = [dict(item) for item in claim.get("_复核历史", [])]
        return True, history, f"申请 {claim_no} 共 {len(history)} 条复核快照"

    def detail(self, claim_no: str) -> dict[str, Any] | None:
        """确认页数据：证据不足与可修复来源分开，附带复验单、清单和历史。"""
        claim = self._find_claim(claim_no)
        if claim is None:
            return None
        tallies = self.tallies_for(claim)
        sources = []
        source_views: dict[str, dict[str, Any]] = {}
        for source in self._sources(claim):
            tally = tallies[source["来源标识"]]
            view = {k: v for k, v in tally.items() if not k.startswith("_")}
            view["关联投递批次"] = source.get("关联投递批次", "")
            view["样本回执"] = [dict(s) for s in source.get("样本回执", [])]
            view["迟到样本"] = [
                dict(s) for s in source.get("样本回执", [])
                if str(s.get("到达状态")) == "迟到"
            ]
            sources.append(view)
            source_views[source["来源标识"]] = view
        groups = {
            key: [dict(source_views[tally["来源标识"]]) for tally in items]
            for key, items in group_verdicts(tallies.values()).items()
        }
        order = self._order_of(claim)
        order_view: dict[str, Any] | None = None
        if isinstance(order, RecheckOrder):
            order_view = {
                "复验单号": order.order_no,
                "关联申请": order.claim_no,
                "覆盖策略": order.coverage_strategy,
                "复验状态": order.status,
                "签发人标识": order.operator,
                "签发时间": order.issued_at,
                "签发范围": list(order.issued_scope),
                "补样范围": list(order.late_scope),
                "执行结果": order.execution,
                "动作留痕": [dict(item) for item in order.history],
            }
        return {
            "申请": {k: v for k, v in claim.items() if not k.startswith("_")},
            "来源分组": groups,
            "来源明细": sources,
            "复核历史": [dict(item) for item in claim.get("_复核历史", [])],
            "复验单": order_view,
            "重解析清单": claim.get("_重解析清单"),
            "样本更新提示": {
                "提示": claim.get("样本更新提示", ""),
                "状态": claim.get("提示状态", ""),
                "可处理": claim.get("提示状态") == "待处理",
            },
        }

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        """单条查询返回确认页结构，列表接口仍沿用通用字段。"""
        claim = store.find(self.MODULE, entry_id)
        if claim is None:
            return None
        detail = self.detail(str(claim.get("申请编号")))
        return detail or claim

    # ------------------------------------------------------------------
    # 通用动作：页面未带参数时按默认口径执行
    # ------------------------------------------------------------------
    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        claim = store.find(self.MODULE, entry_id)
        if claim is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        claim_no = str(claim.get("申请编号"))
        at = self._now(claim)
        if action == "核对受理范围":
            ok, message, _ = self.verify_scope(claim_no, at)
        elif action == "签发复验单":
            ok, message, _ = self.issue_recheck(
                claim_no, self.DEFAULT_OPERATOR, "旧投递保留", at)
        elif action == "执行复验单":
            ok, message, _ = self.execute_recheck(claim_no, at)
        elif action == "确认回填":
            ok, message, _ = self.acknowledge(claim_no, at)
        else:
            return None, f"动作「{action}」不属于{self.MODULE}可执行范围"
        public = {k: v for k, v in claim.items() if not k.startswith("_")}
        return public, message

    def _now(self, claim: dict[str, Any]) -> str:
        """动作时间按历史快照条数递增，保证历史复核顺序可读且可重复。"""
        base = str(claim.get("核对时间") or claim.get("签发时间")
                   or "2026-09-03T10:00:00")
        history = claim.get("_复核历史") or []
        try:
            stamp = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return "2026-09-03T10:00:00"
        return (stamp + timedelta(minutes=len(history))).strftime("%Y-%m-%dT%H:%M:%S")
