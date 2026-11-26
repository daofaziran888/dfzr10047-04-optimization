"""分来源偏差复核与来源复验单测试：真实样本分母、缺样单列、签发范围与执行渠道。"""
from __future__ import annotations

import pytest

from app.pipeline.deviation_scope import (
    VERDICT_INSUFFICIENT,
    VERDICT_REPAIRABLE,
    VERDICT_WITHIN_LIMIT,
    group_verdicts,
    tally_source,
)
from app.pipeline.integration import ParsePipelineOrchestrator
from app.store import store
from app.services import SERVICES


@pytest.fixture(autouse=False)
def fresh_store():
    store.reload_seed()
    yield store
    store.reload_seed()


def receipt(no: str, deviation: bool = False, arrival: str = "已到") -> dict:
    return {
        "样本回执号": no,
        "到达状态": arrival,
        "偏差标记": "偏差" if deviation else "一致",
        "复核结论": "取值偏差" if deviation else "取值一致",
    }


def receipts(prefix: str, count: int, deviation_count: int = 0,
             arrival: str = "已到") -> list[dict]:
    return [
        receipt(f"{prefix}-{index:02d}", deviation=index <= deviation_count,
                arrival=arrival)
        for index in range(1, count + 1)
    ]


class TestSourceTally:
    def test_a_two_of_ten_is_repairable(self):
        tally = tally_source("A", receipts("A", 10, 2), required_samples=10)
        assert (tally["偏差数"], tally["有效样本数"]) == ("2", "10")
        assert tally["偏差比例"] == "0.2000"
        assert tally["受理结论"] == VERDICT_REPAIRABLE
        assert tally["确证偏差"] is True

    def test_missing_samples_never_count_as_zero_deviation(self):
        """B 只有一份有效样本时，1/1 只能判证据不足，不能当成 1/5 或零偏差。"""
        samples = [receipt("B-01", deviation=True)] + receipts(
            "B", 5, 0, arrival="迟到")[1:]
        tally = tally_source("B", samples, required_samples=5)
        assert tally["有效样本数"] == "1"
        assert tally["偏差数"] == "1"
        assert tally["偏差比例"] == "1.0000"
        assert tally["缺样数"] == "4"
        assert tally["缺样回执"] == "B-02,B-03,B-04,B-05"
        assert tally["受理结论"] == VERDICT_INSUFFICIENT

    def test_shortfall_without_late_receipts_is_still_insufficient(self):
        tally = tally_source("C", receipts("C", 2), required_samples=5)
        assert tally["有效样本数"] == "2"
        assert tally["缺样数"] == "3"
        assert tally["缺样回执"] == ""
        assert tally["受理结论"] == VERDICT_INSUFFICIENT

    def test_duplicate_receipts_count_once(self):
        samples = receipts("D", 5, 2) + [receipt("D-01", deviation=False)] * 3
        tally = tally_source("D", samples, required_samples=5)
        assert tally["有效样本数"] == "5"
        assert tally["偏差数"] == "2"
        assert tally["重复回执"] == "D-01,D-01,D-01"
        assert tally["受理结论"] == VERDICT_REPAIRABLE

    def test_threshold_is_per_source(self):
        tight = tally_source("E", receipts("E", 10, 2), required_samples=10,
                             deviation_threshold=0.15)
        loose = tally_source("E", receipts("E", 10, 2), required_samples=10,
                             deviation_threshold=0.25)
        assert tight["受理结论"] == VERDICT_REPAIRABLE
        assert loose["受理结论"] == VERDICT_WITHIN_LIMIT

    def test_groups_separate_insufficient_from_repairable(self):
        groups = group_verdicts([
            tally_source("A", receipts("A", 10, 2), required_samples=10),
            tally_source("B", [receipt("B-01", deviation=True)], required_samples=5),
            tally_source("C", receipts("C", 5, 0), required_samples=5),
        ])
        assert [t["来源标识"] for t in groups["可发起修复来源"]] == ["A"]
        assert [t["来源标识"] for t in groups["证据不足来源"]] == ["B"]
        assert [t["来源标识"] for t in groups["偏差未超阈来源"]] == ["C"]


class TestDeviationRepairService:
    def test_verify_scope_splits_groups(self, fresh_store):
        svc = SERVICES["deviation_repair"]
        ok, message, claim = svc.verify_scope("DRP-001", "2026-09-03T10:00:00")
        assert ok is True
        assert claim["可修复来源"] == "SRC-A"
        assert claim["证据不足来源"] == "SRC-B"
        assert claim["缺样来源"] == "SRC-B(缺4)"
        assert "SRC-B" in message

    def test_issue_recheck_rejects_insufficient_source(self, fresh_store):
        svc = SERVICES["deviation_repair"]
        svc.verify_scope("DRP-001", "2026-09-03T10:00:00")
        ok, message, _claim = svc.issue_recheck(
            "DRP-001", "reviewer-01", "旧投递保留", "2026-09-03T10:05:00",
            source_ids=["SRC-B"])
        assert ok is False
        assert "不得签发" in message
        assert svc._find_claim("DRP-001").get("复验单号") in (None, "")

    def test_late_samples_recompute_and_join_order(self, fresh_store):
        svc = SERVICES["deviation_repair"]
        svc.verify_scope("DRP-001", "2026-09-03T10:00:00")
        ok, _msg, _claim = svc.issue_recheck(
            "DRP-001", "reviewer-01", "旧投递保留", "2026-09-03T10:05:00")
        assert ok is True

        ok, message, _claim = svc.complete_late_samples(
            "DRP-001", "SRC-B", "2026-09-03T10:10:00")
        assert ok is True
        assert "2/5" in message

        ok, message, claim = svc.include_late_source(
            "DRP-001", "SRC-B", "2026-09-03T10:11:00")
        assert ok is True
        assert claim["补样来源"] == "SRC-B"
        assert claim["来源范围"] == "SRC-A"

    def test_execute_queues_real_channels_and_manifest(self, fresh_store):
        svc = SERVICES["deviation_repair"]
        svc.verify_scope("DRP-001", "2026-09-03T10:00:00")
        svc.issue_recheck("DRP-001", "reviewer-01", "旧投递保留",
                          "2026-09-03T10:05:00")
        svc.complete_late_samples("DRP-001", "SRC-B", "2026-09-03T10:10:00")
        svc.include_late_source("DRP-001", "SRC-B", "2026-09-03T10:11:00")

        ok, message, claim = svc.execute_recheck("DRP-001", "2026-09-03T10:20:00")
        assert ok is True
        assert claim["实际入队渠道"] == "sftp-node-03,sftp-node-04"
        assert claim["重解析来源"] == "SRC-A,SRC-B"

        manifest = claim["_重解析清单"]
        assert [b["来源标识"] for b in manifest["来源批次"]] == ["SRC-A", "SRC-B"]
        assert manifest["来源批次"][0]["有效样本数"] == "10"
        assert manifest["来源批次"][1]["有效样本数"] == "5"
        # 覆盖缺口逐来源核对：两个来源都纳入重解析，无缺口
        assert all(g["纳入重解析"] for g in manifest["覆盖缺口"])

        tasks = [
            row for row in store.rows("reparse_task")
            if str(row.get("重解析编号", "")).startswith("RM-DRP-001")
        ]
        assert [t["来源标识"] for t in tasks] == ["SRC-A", "SRC-B"]
        assert [t["来源渠道"] for t in tasks] == ["sftp-node-03", "sftp-node-04"]
        assert tasks[0]["偏差比例"] == "0.2000"
        assert tasks[1]["偏差比例"] == "0.4000"

    def test_order_row_records_actual_channels(self, fresh_store):
        svc = SERVICES["deviation_repair"]
        svc.verify_scope("DRP-001", "2026-09-03T10:00:00")
        svc.issue_recheck("DRP-001", "reviewer-01", "旧投递保留",
                          "2026-09-03T10:05:00")
        svc.execute_recheck("DRP-001", "2026-09-03T10:20:00")
        row = next(
            r for r in store.rows("source_recheck")
            if r["复验单号"] == "SRC-DRP-001")
        assert row["复验状态"] == "已入队"
        assert row["实际入队渠道"] == "sftp-node-03"
        assert row["重解析任务"] == "RM-DRP-001-B01"

    def test_history_keeps_original_denominator(self, fresh_store):
        """补齐样本后重算不改写历史快照里原来的偏差比例。"""
        svc = SERVICES["deviation_repair"]
        svc.verify_scope("DRP-001", "2026-09-03T10:00:00")
        ok, history, _msg = svc.history("DRP-001")
        assert ok is True
        first_b = next(h for h in history if h["来源标识"] == "SRC-B")
        assert (first_b["有效样本数"], first_b["偏差数"]) == ("1", "1")

        svc.complete_late_samples("DRP-001", "SRC-B", "2026-09-03T10:10:00")
        _ok, history, _msg = svc.history("DRP-001")
        first_b = next(h for h in history if h["来源标识"] == "SRC-B")
        assert (first_b["有效样本数"], first_b["偏差数"]) == ("1", "1")
        assert first_b["受理结论"] == VERDICT_INSUFFICIENT
        latest_b = [h for h in history if h["来源标识"] == "SRC-B"][-1]
        assert (latest_b["有效样本数"], latest_b["偏差数"]) == ("5", "2")

    def test_register_samples_idempotent_receipts(self, fresh_store):
        svc = SERVICES["deviation_repair"]
        ok, message, _claim = svc.register_samples(
            "DRP-001", "SRC-B", [receipt("RC-B-01")], "2026-09-03T10:00:00")
        assert ok is True
        assert "重复回执 RC-B-01" in message
        source = svc._find_source(svc._find_claim("DRP-001"), "SRC-B")
        assert len(source["样本回执"]) == 5

    def test_execute_twice_rejected(self, fresh_store):
        svc = SERVICES["deviation_repair"]
        svc.verify_scope("DRP-001", "2026-09-03T10:00:00")
        svc.issue_recheck("DRP-001", "reviewer-01", "旧投递保留",
                          "2026-09-03T10:05:00")
        svc.execute_recheck("DRP-001", "2026-09-03T10:20:00")
        ok, message, _claim = svc.execute_recheck("DRP-001", "2026-09-03T10:21:00")
        assert ok is False
        assert "不能重复执行" in message

    def test_sample_update_reopens_hint(self, fresh_store):
        svc = SERVICES["deviation_repair"]
        svc.verify_scope("DRP-001", "2026-09-03T10:00:00")
        svc.issue_recheck("DRP-001", "reviewer-01", "旧投递保留",
                          "2026-09-03T10:05:00")
        _ok, _msg, claim = svc.execute_recheck(
            "DRP-001", "2026-09-03T10:20:00")
        assert claim["提示状态"] == "待处理"
        ok, _msg, claim = svc.handle_sample_update(
            "DRP-001", "2026-09-03T10:25:00")
        assert ok is True
        assert claim["提示状态"] == "已处理"

    def test_summary_row_hides_internal_snapshots(self, fresh_store):
        svc = SERVICES["deviation_repair"]
        svc.verify_scope("DRP-001", "2026-09-03T10:00:00")
        rows, total = svc.list_entries()
        assert total == 3
        assert all(not any(key.startswith("_") for key in row) for row in rows)


class TestOutputContractDeviationSync:
    def test_sync_writes_real_sample_conclusion(self, fresh_store):
        repair = SERVICES["deviation_repair"]
        contract = SERVICES["output_contract"]
        repair.verify_scope("DRP-001", "2026-09-03T10:00:00")
        result = contract.sync_deviation_review("DRP-001")
        assert result["ok"] is True
        assert result["confirmed_sources"] == ["SRC-A"]
        assert result["gap_sources"] == ["SRC-B"]
        assert "仅覆盖确证偏差来源 SRC-A" in result["reparse_scope"]
        oc001 = next(r for r in store.rows("output_contract")
                     if r["契约编号"] == "OC-001")
        assert oc001["偏差结论同步"].startswith("SRC-A 2/10")
        oc003 = next(r for r in store.rows("output_contract")
                     if r["契约编号"] == "OC-003")
        assert "偏差结论同步" in oc003
        assert not oc003.get("重解析来源")

    def test_sync_unknown_claim(self, fresh_store):
        contract = SERVICES["output_contract"]
        result = contract.sync_deviation_review("DRP-NOPE")
        assert result["ok"] is False


class TestLink11SourceScopeRecheck:
    def test_link11_creates_batches_only_for_confirmed_sources(self, fresh_store):
        orch = ParsePipelineOrchestrator()
        result = orch.link11_source_scope_recheck(
            claim_no="DRP-L11",
            sources=[
                {"source": "SRC-A", "samples": receipts("A", 10, 2)},
                {"source": "SRC-B", "samples": [
                    receipt("B-01", deviation=True)] + receipts(
                        "B", 5, 0, arrival="迟到")[1:]},
            ],
            required_samples=10,
            deviation_threshold=0.10,
            channel_by_source={"SRC-A": "sftp-node-03", "SRC-B": "sftp-node-04"},
            review_time="2026-09-04T09:00:00",
        )
        assert result["confirmed_sources"] == ["SRC-A"]
        assert [b["来源标识"] for b in result["reparse_batches"]] == ["SRC-A"]
        assert result["reparse_batches"][0]["来源渠道"] == "sftp-node-03"
        assert result["coverage_gaps"][0]["来源标识"] == "SRC-B"
        assert "证据不足" in result["coverage_gaps"][0]["覆盖缺口"]
        tasks = [
            row for row in store.rows("reparse_task")
            if row["重解析编号"] == "RT-SRC-DRP-L11-01"
        ]
        assert tasks[0]["偏差比例"] == "0.2000"
        assert tasks[0]["来源渠道"] == "sftp-node-03"


class TestRestoredOrderAndRouter:
    def test_seed_issued_order_can_execute_after_restore(self, fresh_store):
        """页面刷新后内存里没有复验单对象，仍要能按数据集还原并执行。"""
        svc = SERVICES["deviation_repair"]
        ok, message, claim = svc.execute_recheck("DRP-002", "2026-09-03T12:00:00")
        assert ok is True
        assert claim["实际入队渠道"] == "web-upload"
        assert claim["重解析来源"] == "SRC-C"
        assert claim["未入队来源"] == "SRC-D"
        assert "证据不足" in claim["覆盖缺口"]

    def test_restored_order_rejects_insufficient_late_source(self, fresh_store):
        svc = SERVICES["deviation_repair"]
        ok, message, _claim = svc.include_late_source(
            "DRP-002", "SRC-D", "2026-09-03T12:00:00")
        assert ok is False
        assert "证据不足" in message


class TestDeviationRepairHttp:
    @pytest.fixture()
    def client(self, fresh_store):
        pytest.importorskip("httpx", reason="TestClient 需要 httpx，缺失时跳过接口级用例")
        from fastapi.testclient import TestClient

        from app.main import app

        return TestClient(app)

    def test_confirm_page_detail_groups(self, client):
        response = client.get("/api/deviation_repair/DRP-001/detail")
        assert response.status_code == 200
        payload = response.json()
        assert payload["来源分组"]["证据不足来源"][0]["来源标识"] == "SRC-B"
        assert payload["来源分组"]["可发起修复来源"][0]["偏差数"] == "2"

    def test_confirm_page_404(self, client):
        response = client.get("/api/deviation_repair/DRP-NOPE/detail")
        assert response.status_code == 404

    def test_issue_and_execute_over_http(self, client):
        at = {"at": "2026-09-04T10:00:00"}
        assert client.post(
            "/api/deviation_repair/DRP-001/verify", json=at).json()["ok"] is True
        issued = client.post(
            "/api/deviation_repair/DRP-001/recheck",
            json={**at, "operator": "reviewer-01",
                  "coverage_strategy": "旧投递保留", "sources": ["SRC-A"]}).json()
        assert issued["ok"] is True
        assert issued["entry"]["复验单号"] == "SRC-DRP-001"

        executed = client.post(
            "/api/deviation_repair/DRP-001/execute", json=at).json()
        assert executed["ok"] is True
        assert executed["entry"]["实际入队渠道"] == "sftp-node-03"

        order = client.get("/api/source_recheck?size=50").json()
        row = next(item for item in order["items"]
                   if item["复验单号"] == "SRC-DRP-001")
        assert row["复验状态"] == "已入队"
        assert row["重解析任务"] == "RM-DRP-001-B01"

    def test_issue_rejects_insufficient_source_over_http(self, client):
        at = {"at": "2026-09-04T10:00:00"}
        client.post("/api/deviation_repair/DRP-001/verify", json=at)
        response = client.post(
            "/api/deviation_repair/DRP-001/recheck",
            json={**at, "sources": ["SRC-B"]}).json()
        assert response["ok"] is False
        assert "不得签发" in response["message"]

    def test_history_endpoint_keeps_original_ratio(self, client):
        at = {"at": "2026-09-04T10:00:00"}
        client.post("/api/deviation_repair/DRP-001/verify", json=at)
        client.post("/api/deviation_repair/DRP-001/late-samples",
                    json={**at, "source": "SRC-B"})
        history = client.get("/api/deviation_repair/DRP-001/history").json()
        first_b = next(item for item in history["items"]
                       if item["来源标识"] == "SRC-B")
        assert first_b["有效样本数"] == "1"
        assert first_b["偏差比例"] == "1.0000"
        assert first_b["受理结论"] == "证据不足"
        assert "低于应采" in first_b["不可受理原因"]

    def test_contract_sync_endpoint(self, client):
        client.post("/api/deviation_repair/DRP-001/verify",
                    json={"at": "2026-09-04T10:00:00"})
        response = client.post("/api/deviation_repair/DRP-001/contract-sync",
                              json={}).json()
        assert response["ok"] is True
        assert response["entry"]["confirmed_sources"] == ["SRC-A"]
        assert response["entry"]["gap_sources"] == ["SRC-B"]


class TestManifestSyncAfterExecution:
    def test_newly_confirmed_source_appends_batch(self, fresh_store):
        """执行后补齐样本让另一来源确证偏差时，清单要追加按来源批次。"""
        svc = SERVICES["deviation_repair"]
        _ok, _msg, claim = svc.execute_recheck("DRP-002", "2026-09-03T12:00:00")
        assert claim["重解析来源"] == "SRC-C"

        receipts_new = [
            receipt("RC-D-03", deviation=True),
            receipt("RC-D-04"),
            receipt("RC-D-05"),
        ]
        ok, message, _claim = svc.register_samples(
            "DRP-002", "SRC-D", receipts_new, "2026-09-03T12:10:00")
        assert ok is True
        assert "2/5" in message

        ok, message, claim = svc.handle_sample_update(
            "DRP-002", "2026-09-03T12:20:00")
        assert ok is True
        assert "SRC-D" in message
        manifest = claim["_重解析清单"]
        assert [b["来源标识"] for b in manifest["来源批次"]] == ["SRC-C", "SRC-D"]
        assert manifest["覆盖缺口数"] == 0


class TestConfirmScopeSelection:
    def test_issue_only_covers_confirmed_sources(self, fresh_store):
        """复核员只勾 A 时，即使 B 已补到 2/5 也不能被顺带写进签发范围。"""
        svc = SERVICES["deviation_repair"]
        svc.verify_scope("DRP-001", "2026-09-03T10:00:00")
        svc.complete_late_samples("DRP-001", "SRC-B", "2026-09-03T10:05:00")
        tallies = svc.tallies_for(svc._find_claim("DRP-001"))
        assert tallies["SRC-B"]["受理结论"] == "可发起修复"

        confirmed, message, _claim = svc.confirm_scope(
            "DRP-001", ["SRC-A"], "2026-09-03T10:06:00")
        assert confirmed is True
        assert "SRC-A" in message

        issued, message, claim = svc.issue_recheck(
            "DRP-001", "reviewer-01", "旧投递保留", "2026-09-03T10:07:00",
            source_ids=["SRC-A"])
        assert issued is True
        assert claim["来源范围"] == "SRC-A"
        assert claim["补样来源"] == ""

        # 执行时 B 未在签发范围内，必须出现在未入队来源与覆盖缺口里
        _executed, _message, claim = svc.execute_recheck(
            "DRP-001", "2026-09-03T10:08:00")
        assert claim["实际入队渠道"] == "sftp-node-03"
        assert claim["未入队来源"] == "SRC-B"
        assert "SRC-B" in claim["覆盖缺口"]

    def test_confirm_rejects_insufficient_and_unseen_source(self, fresh_store):
        svc = SERVICES["deviation_repair"]
        svc.verify_scope("DRP-001", "2026-09-03T10:00:00")
        ok, message, _claim = svc.confirm_scope(
            "DRP-001", ["SRC-A", "SRC-B"], "2026-09-03T10:02:00")
        assert ok is False
        assert "SRC-B" in message and "证据不足" in message

    def test_confirm_unknown_source(self, fresh_store):
        svc = SERVICES["deviation_repair"]
        svc.verify_scope("DRP-001", "2026-09-03T10:00:00")
        ok, message, _claim = svc.confirm_scope(
            "DRP-001", ["SRC-Z"], "2026-09-03T10:02:00")
        assert ok is False
        assert "来源不存在" in message
