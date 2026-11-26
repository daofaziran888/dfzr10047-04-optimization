"""重解析来源清单：承接偏差复核结论，按来源生成批次并核对各来源覆盖缺口。

核心约定：
- 只把执行复核后仍确证偏差的来源写进重解析清单，未证实来源不扩大范围；
- 每个来源的批次保留自己的分母、偏差数与阈值，便于回查；
- 覆盖缺口按来源逐条核对，缺样来源写明缺口，不并入重解析范围。
"""
from __future__ import annotations

from typing import Any, Iterable

from app.pipeline.deviation_scope import coverage_gap


def build_manifest(
    manifest_no: str,
    claim_no: str,
    order_no: str,
    coverage_strategy: str,
    tallies: Iterable[dict[str, Any]],
    channel_of,
    batches_of=None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str]:
    """按确证偏差来源生成重解析清单与批次，返回清单、批次和结论说明。"""
    all_tallies = list(tallies)
    confirmed = [t for t in all_tallies if t.get("确证偏差")]
    if not confirmed:
        return None, [], "没有任何来源确证偏差，不生成按来源重解析批次"

    batches: list[dict[str, Any]] = []
    for index, tally in enumerate(confirmed, start=1):
        channel = channel_of(tally["来源标识"])
        batches.append({
            "批次标识": f"{manifest_no}-B{index:02d}",
            "清单编号": manifest_no,
            "来源标识": tally["来源标识"],
            "来源渠道": channel,
            "关联投递批次": ",".join(batches_of(tally["来源标识"])) if batches_of else "",
            "应采样本数": tally.get("应采样本数", ""),
            "有效样本数": tally.get("有效样本数", ""),
            "偏差数": tally.get("偏差数", ""),
            "偏差比例": tally.get("偏差比例", ""),
            "偏差阈值": tally.get("偏差阈值", ""),
            "使用样本回执": tally.get("使用样本回执", ""),
            "偏差样本": tally.get("偏差样本", ""),
            "重解析范围": (
                f"仅重解析来源 {tally['来源标识']} 的偏差样本 "
                f"{tally.get('偏差样本', '') or '无'}"
            ),
        })

    gaps = [coverage_gap(t, manifest_no) for t in all_tallies]
    queued_sources = [b["来源标识"] for b in batches]
    skipped_sources = [
        t["来源标识"] for t in all_tallies if not t.get("确证偏差")
    ]
    manifest = {
        "清单编号": manifest_no,
        "关联申请": claim_no,
        "来源复验单": order_no,
        "覆盖策略": coverage_strategy,
        "来源批次": batches,
        "入队来源": queued_sources,
        "未入队来源": skipped_sources,
        "覆盖缺口": gaps,
        "覆盖缺口数": sum(int(g["缺口数"]) for g in gaps),
        "重解析范围": f"{len(batches)} 个来源批次（仅确证偏差来源）",
        "结论": (
            f"清单 {manifest_no} 按 {len(batches)} 个确证偏差来源生成批次，"
            f"{len(skipped_sources)} 个来源未纳入重解析"
        ),
    }
    return manifest, batches, manifest["结论"]


def source_reparse_task_rows(
    manifest: dict[str, Any],
    positions: dict[str, tuple[str, str]],
    reason: str,
) -> list[dict[str, Any]]:
    """把来源批次转成重解析单字段：每单只覆盖一个来源，位点取该来源自己的批次。"""
    rows: list[dict[str, Any]] = []
    for batch in manifest.get("来源批次", []):
        source = batch["来源标识"]
        start, end = positions.get(source, ("", ""))
        intake_batches = [
            part.strip()
            for part in str(batch.get("关联投递批次") or "").split(",")
            if part.strip()
        ]
        rows.append({
            "重解析编号": batch["批次标识"],
            "关联批次": intake_batches[0] if intake_batches else source,
            "重解析原因": f"{reason}（来源 {source}）",
            "覆盖策略": manifest.get("覆盖策略", "旧投递保留"),
            "起始位点": start,
            "截止位点": end,
            "受影响投递数": str(len(intake_batches) or 1),
            "受影响批次": ",".join(intake_batches) or source,
            "来源标识": source,
            "来源渠道": batch.get("来源渠道", ""),
            "偏差比例": batch.get("偏差比例", ""),
            "偏差阈值": batch.get("偏差阈值", ""),
            "使用样本回执": batch.get("使用样本回执", ""),
            "偏差样本": batch.get("偏差样本", ""),
            "重解析范围": batch.get("重解析范围", ""),
            "解析清单": manifest.get("清单编号", ""),
        })
    return rows


def sync_manifest_outcome(
    manifest: dict[str, Any],
    tallies: Iterable[dict[str, Any]],
    newly_confirmed: Iterable[dict[str, Any]],
    channel_of,
    manifest_no: str,
    batches_of=None,
) -> dict[str, Any]:
    """把复核结论的后续变化同步进清单：新确证偏差来源追加批次，缺口重新核对。"""
    known = set(manifest.get("入队来源", []))
    batches = list(manifest.get("来源批次", []))
    appended: list[str] = []
    for tally in newly_confirmed:
        source = tally["来源标识"]
        if source in known or not tally.get("确证偏差"):
            continue
        index = len(batches) + 1
        batches.append({
            "批次标识": f"{manifest_no}-B{index:02d}",
            "清单编号": manifest_no,
            "来源标识": source,
            "来源渠道": channel_of(source),
            "关联投递批次": ",".join(batches_of(source)) if batches_of else "",
            "应采样本数": tally.get("应采样本数", ""),
            "有效样本数": tally.get("有效样本数", ""),
            "偏差数": tally.get("偏差数", ""),
            "偏差比例": tally.get("偏差比例", ""),
            "偏差阈值": tally.get("偏差阈值", ""),
            "使用样本回执": tally.get("使用样本回执", ""),
            "偏差样本": tally.get("偏差样本", ""),
            "重解析范围": (
                f"仅重解析来源 {source} 的偏差样本 "
                f"{tally.get('偏差样本', '') or '无'}"
            ),
        })
        known.add(source)
        appended.append(source)

    current = {t["来源标识"]: t for t in tallies}
    manifest["来源批次"] = batches
    manifest["入队来源"] = [b["来源标识"] for b in batches]
    manifest["未入队来源"] = [
        source for source, tally in current.items() if not tally.get("确证偏差")
    ]
    manifest["覆盖缺口"] = [
        coverage_gap(current.get(source, {"来源标识": source, "受理结论": "证据不足",
                                          "确证偏差": False}), manifest_no)
        for source in current
    ]
    manifest["覆盖缺口数"] = sum(int(g["缺口数"]) for g in manifest["覆盖缺口"])
    manifest["重解析范围"] = f"{len(batches)} 个来源批次（仅确证偏差来源）"
    manifest["结论"] = (
        f"清单 {manifest_no} 已同步复核结论：追加 {len(appended)} 个来源批次，"
        f"覆盖缺口 {manifest['覆盖缺口数']} 项"
    )
    manifest["同步追加来源"] = appended
    return manifest
