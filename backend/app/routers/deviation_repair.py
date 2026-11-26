"""偏差修复接口：确认页分组核对、分来源补样、签发与执行来源复验单。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.schemas import ActionResult
from app.services import service_for
from app.services.deviation_repair import DeviationRepairService

router = APIRouter(prefix="/api/deviation_repair", tags=["偏差修复"])


def require_service() -> DeviationRepairService:
    service = service_for("deviation_repair")
    if not isinstance(service, DeviationRepairService):  # pragma: no cover - 注册表固定存在
        raise HTTPException(status_code=503, detail="偏差修复服务未注册")
    return service


class SampleReceipt(BaseModel):
    receipt_no: str = Field(alias="receipt_no")
    arrival: str = Field(default="已到", alias="arrival")
    deviation: bool = Field(default=False, alias="deviation")
    conclusion: str = Field(default="", alias="conclusion")
    registered_at: str = Field(default="", alias="registered_at")


class ScopePayload(BaseModel):
    at: str = ""
    sources: list[str] = Field(default_factory=list)


class SamplePayload(BaseModel):
    at: str = ""
    source: str
    receipts: list[SampleReceipt] = Field(default_factory=list)


class LateSamplesPayload(BaseModel):
    at: str = ""
    source: str


class IssuePayload(BaseModel):
    at: str = ""
    operator: str = "复核员"
    coverage_strategy: str = "旧投递保留"
    sources: list[str] = Field(default_factory=list)


class LateSourcePayload(BaseModel):
    at: str = ""
    source: str


class ExecutePayload(BaseModel):
    at: str = ""


def _result(ok: bool, message: str, payload: dict[str, Any] | None = None) -> ActionResult:
    return ActionResult(ok=ok, message=message, entry=payload)


def _service() -> DeviationRepairService:
    return require_service()


@router.get("/{claim_no}/detail", response_model=dict)
def claim_detail(claim_no: str) -> dict[str, Any]:
    """确认页数据：来源按证据不足/可修复/未超阈分组，并带复验单与清单。"""
    detail = _service().detail(claim_no)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"偏差修复申请 {claim_no} 不存在")
    return detail


@router.get("/{claim_no}/history", response_model=dict)
def claim_history(claim_no: str) -> dict[str, Any]:
    """回查历史复核：每次使用的样本、分母与阈值原样保留。"""
    ok, history, message = _service().history(claim_no)
    if not ok:
        raise HTTPException(status_code=404, detail=message)
    return {"ok": True, "message": message, "items": history}


@router.post("/{claim_no}/verify", response_model=ActionResult)
def verify_scope(claim_no: str, payload: ScopePayload) -> ActionResult:
    ok, message, claim = _service().verify_scope(claim_no, payload.at)
    return _result(ok, message, claim)


@router.post("/{claim_no}/samples", response_model=ActionResult)
def register_samples(claim_no: str, payload: SamplePayload) -> ActionResult:
    receipts = [
        {
            "样本回执号": item.receipt_no,
            "到达状态": item.arrival,
            "偏差标记": "偏差" if item.deviation else "一致",
            "复核结论": item.conclusion,
            "登记时间": item.registered_at or payload.at,
        }
        for item in payload.receipts
    ]
    ok, message, claim = _service().register_samples(
        claim_no, payload.source, receipts, payload.at)
    return _result(ok, message, claim)


@router.post("/{claim_no}/late-samples", response_model=ActionResult)
def complete_late_samples(claim_no: str, payload: LateSamplesPayload) -> ActionResult:
    ok, message, claim = _service().complete_late_samples(
        claim_no, payload.source, payload.at)
    return _result(ok, message, claim)


@router.post("/{claim_no}/confirm", response_model=ActionResult)
def confirm_scope(claim_no: str, payload: ScopePayload) -> ActionResult:
    ok, message, claim = _service().confirm_scope(
        claim_no, payload.sources, payload.at)
    return _result(ok, message, claim)


@router.post("/{claim_no}/recheck", response_model=ActionResult)
def issue_recheck(claim_no: str, payload: IssuePayload) -> ActionResult:
    ok, message, claim = _service().issue_recheck(
        claim_no, payload.operator, payload.coverage_strategy, payload.at,
        payload.sources or None)
    return _result(ok, message, claim)


@router.post("/{claim_no}/late-source", response_model=ActionResult)
def include_late_source(claim_no: str, payload: LateSourcePayload) -> ActionResult:
    ok, message, claim = _service().include_late_source(
        claim_no, payload.source, payload.at)
    return _result(ok, message, claim)


@router.post("/{claim_no}/execute", response_model=ActionResult)
def execute_recheck(claim_no: str, payload: ExecutePayload) -> ActionResult:
    ok, message, claim = _service().execute_recheck(claim_no, payload.at)
    return _result(ok, message, claim)


@router.post("/{claim_no}/sample-update", response_model=ActionResult)
def handle_sample_update(claim_no: str, payload: ExecutePayload) -> ActionResult:
    ok, message, claim = _service().handle_sample_update(claim_no, payload.at)
    return _result(ok, message, claim)


@router.post("/{claim_no}/acknowledge", response_model=ActionResult)
def acknowledge(claim_no: str, payload: ExecutePayload) -> ActionResult:
    ok, message, claim = _service().acknowledge(claim_no, payload.at)
    return _result(ok, message, claim)


@router.post("/{claim_no}/contract-sync", response_model=ActionResult)
def sync_contract(claim_no: str) -> ActionResult:
    """把实际样本结论同步到输出契约，并给出按来源覆盖缺口。"""
    from app.services.output_contract import OutputContractService

    result = OutputContractService().sync_deviation_review(claim_no)
    return _result(bool(result.get("ok")), str(result.get("message")), result)
