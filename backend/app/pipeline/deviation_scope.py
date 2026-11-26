"""来源偏差受理范围：按来源核对采样数量与偏差阈值，隔离缺样并给出复验/重解析范围。

核心约定：
- 每个来源用自己的有效样本作分母，缺样单列，绝不把缺样当作零偏差样本；
- 有效样本数低于应采样本数时只判「证据不足」，不得签发来源复验单；
- 同一样本回执重复登记只算一次，保留首次登记内容；
- 重解析范围只覆盖确证偏差的来源，其余来源保留覆盖缺口说明。
"""
from __future__ import annotations

from typing import Any, Iterable

VERDICT_INSUFFICIENT = "证据不足"
VERDICT_REPAIRABLE = "可发起修复"
VERDICT_WITHIN_LIMIT = "偏差未超阈"

VERDICTS = (VERDICT_INSUFFICIENT, VERDICT_REPAIRABLE, VERDICT_WITHIN_LIMIT)

GROUP_INSUFFICIENT = "证据不足来源"
GROUP_REPAIRABLE = "可发起修复来源"
GROUP_WITHIN_LIMIT = "偏差未超阈来源"

DEFAULT_REQUIRED_SAMPLES = 5
DEFAULT_DEVIATION_RATE = 0.10

ARRIVED = "已到"
LATE = "迟到"


def _receipt_no(row: dict[str, Any]) -> str:
    return str(row.get("样本回执号") or "").strip()


def is_arrived(row: dict[str, Any]) -> bool:
    """样本是否已到达并可用于复核：到达状态为已到且未被标记无效。"""
    if str(row.get("有效标记") or "") in ("无效", "作废"):
        return False
    return str(row.get("到达状态") or ARRIVED) == ARRIVED


def is_deviation(row: dict[str, Any]) -> bool:
    """样本复核结果是否为偏差；迟到未复核的样本不产生偏差计数。"""
    return str(row.get("偏差标记") or "") == "偏差"


def dedupe_receipts(rows: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """相同样本回执重复登记只算一次，返回去重后的样本与重复回执号。"""
    seen: dict[str, dict[str, Any]] = {}
    duplicates: list[str] = []
    for row in rows:
        receipt = _receipt_no(row)
        if not receipt:
            continue
        if receipt in seen:
            duplicates.append(receipt)
            continue
        seen[receipt] = row
    return list(seen.values()), duplicates


def _ratio_text(value: int, denominator: int) -> str:
    rate = value / denominator if denominator > 0 else 0.0
    return f"{rate:.4f}"


def tally_source(
    source_id: str,
    samples: Iterable[dict[str, Any]],
    *,
    required_samples: int = DEFAULT_REQUIRED_SAMPLES,
    deviation_threshold: float = DEFAULT_DEVIATION_RATE,
    channel: str = "",
) -> dict[str, Any]:
    """按单个来源汇总真实样本结论。

    有效样本作分母；缺样单独计数并列出回执号，不进入分母也不计为零偏差。
    有效样本数不足应采样本数时，无条件判证据不足。
    """
    required = max(0, int(required_samples))
    threshold = float(deviation_threshold)
    unique, duplicates = dedupe_receipts(samples)
    valid = [row for row in unique if is_arrived(row)]
    missing = [row for row in unique if not is_arrived(row)]
    deviation = [row for row in valid if is_deviation(row)]

    valid_count = len(valid)
    missing_receipts = [_receipt_no(row) for row in missing if _receipt_no(row)]
    # 已登记回执不足以覆盖应采数量时，差额作为待补样本单列，不并入偏差分母。
    shortfall = max(0, required - valid_count - len(missing_receipts))
    missing_count = len(missing_receipts) + shortfall
    deviation_count = len(deviation)
    consistent_count = valid_count - deviation_count
    rate = deviation_count / valid_count if valid_count > 0 else 0.0

    evidence_sufficient = valid_count >= required
    deviation_confirmed = evidence_sufficient and rate > threshold

    if not evidence_sufficient:
        verdict = VERDICT_INSUFFICIENT
        if missing_receipts:
            reason = (
                f"有效样本 {valid_count} 份低于应采 {required} 份，"
                f"缺样 {missing_count} 份（{','.join(missing_receipts)} 迟到），不能受理"
            )
        else:
            reason = (
                f"有效样本 {valid_count} 份低于应采 {required} 份，"
                f"尚缺 {shortfall} 份待补，不能受理"
            )
    elif deviation_confirmed:
        verdict = VERDICT_REPAIRABLE
        reason = ""
    else:
        verdict = VERDICT_WITHIN_LIMIT
        reason = ""

    if verdict == VERDICT_REPAIRABLE:
        note = (
            f"有效样本 {valid_count} 份中偏差 {deviation_count} 份，"
            f"偏差比例 {rate:.1%} 超过阈值 {threshold:.0%}，可发起修复"
        )
    elif verdict == VERDICT_WITHIN_LIMIT:
        note = (
            f"有效样本 {valid_count} 份中偏差 {deviation_count} 份，"
            f"偏差比例 {rate:.1%} 未超过阈值 {threshold:.0%}，无需修复"
        )
    else:
        note = reason

    return {
        "来源标识": source_id,
        "来源渠道": channel,
        "应采样本数": str(required),
        "偏差阈值": f"{threshold:.2f}",
        "有效样本数": str(valid_count),
        "一致数": str(consistent_count),
        "偏差数": str(deviation_count),
        "偏差比例": f"{rate:.4f}",
        "分母口径": "有效样本",
        "使用样本回执": ",".join(_receipt_no(row) for row in valid if _receipt_no(row)),
        "偏差样本": ",".join(_receipt_no(row) for row in deviation if _receipt_no(row)),
        "缺样回执": ",".join(missing_receipts),
        "缺样数": str(missing_count),
        "重复回执": ",".join(duplicates),
        "证据充分": evidence_sufficient,
        "确证偏差": deviation_confirmed,
        "受理结论": verdict,
        "不可受理原因": reason,
        "受理说明": note,
        "_valid": valid_count,
        "_deviation": deviation_count,
        "_rate": round(rate, 4),
        "_threshold": threshold,
        "_required": required,
        "_missing": missing_count,
    }


def group_verdicts(tallies: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """把来源按受理结论分成确认页需要的三组，证据不足与可修复严格分开。"""
    groups: dict[str, list[dict[str, Any]]] = {
        GROUP_INSUFFICIENT: [],
        GROUP_REPAIRABLE: [],
        GROUP_WITHIN_LIMIT: [],
    }
    mapping = {
        VERDICT_INSUFFICIENT: GROUP_INSUFFICIENT,
        VERDICT_REPAIRABLE: GROUP_REPAIRABLE,
        VERDICT_WITHIN_LIMIT: GROUP_WITHIN_LIMIT,
    }
    for tally in tallies:
        groups[mapping[tally["受理结论"]]].append(dict(tally))
    return groups


def coverage_gap(tally: dict[str, Any], manifest_no: str = "") -> dict[str, Any]:
    """单来源重解析覆盖缺口：确证偏差来源按偏差样本纳入，其余来源整体不纳入。"""
    confirmed = bool(tally.get("确证偏差"))
    if confirmed:
        gap_parts = []
        missing = int(tally.get("缺样数") or 0)
        if missing:
            gap_parts.append(f"仍有 {missing} 份缺样未覆盖")
        if not manifest_no:
            gap_parts.append("尚未生成按来源重解析批次")
        return {
            "来源标识": tally["来源标识"],
            "受理结论": tally["受理结论"],
            "纳入重解析": True,
            "清单编号": manifest_no,
            "覆盖缺口": ";".join(gap_parts) if gap_parts else "无",
            "缺口数": missing + (0 if manifest_no else 1),
        }
    if tally["受理结论"] == VERDICT_INSUFFICIENT:
        reason = f"证据不足（{tally.get('不可受理原因') or '样本不足'}），不纳入重解析范围"
        gap_count = int(tally.get("缺样数") or 0)
    else:
        reason = "偏差未超阈，不纳入重解析范围"
        gap_count = 0
    return {
        "来源标识": tally["来源标识"],
        "受理结论": tally["受理结论"],
        "纳入重解析": False,
        "清单编号": "",
        "覆盖缺口": reason,
        "缺口数": gap_count,
    }
