"""审计复核样本接口：生成脱敏样本、字段确认、补证回放和整笔事务证明。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.audit_review import AuditReviewError, audit_review_service

router = APIRouter(prefix="/api/audit-review", tags=["审计复核样本"])


class RevokePayload(BaseModel):
    table: str
    field: str | None = None


class GrantPayload(BaseModel):
    table: str
    fields: list[str] = Field(default_factory=list)


class ConfirmPayload(BaseModel):
    account_id: str
    member_id: str
    field: str
    decision: str = "确认一致"
    remark: str = ""


def _call(func, *args: Any, **kwargs: Any) -> Any:
    try:
        return func(*args, **kwargs)
    except AuditReviewError as error:
        raise HTTPException(status_code=error.status_code, detail=error.message) from error


@router.get("/accounts")
def list_accounts() -> dict[str, Any]:
    return _call(audit_review_service.list_accounts)


@router.post("/accounts/{account_id}/revoke")
def revoke(account_id: str, payload: RevokePayload) -> dict[str, Any]:
    return _call(
        audit_review_service.revoke_grant, account_id, payload.table, payload.field
    )


@router.post("/accounts/{account_id}/grants")
def grant(account_id: str, payload: GrantPayload) -> dict[str, Any]:
    return _call(
        audit_review_service.grant_access, account_id, payload.table, payload.fields
    )


@router.get("/transactions")
def list_transactions() -> dict[str, Any]:
    return _call(audit_review_service.list_transactions)


@router.post("/transactions/{tx_id}/samples")
def generate_sample(tx_id: str, account_id: str = Query(...)) -> dict[str, Any]:
    return _call(audit_review_service.generate_sample, tx_id, account_id)


@router.get("/samples")
def list_samples(
    account_id: str = Query(...),
    tx_id: str | None = Query(default=None),
) -> dict[str, Any]:
    return _call(audit_review_service.list_samples, account_id, tx_id)


@router.get("/samples/{sample_id}")
def get_sample(sample_id: str, account_id: str = Query(...)) -> dict[str, Any]:
    return _call(audit_review_service.get_sample, sample_id, account_id)


@router.post("/samples/{sample_id}/confirmations")
def confirm_field(sample_id: str, payload: ConfirmPayload) -> dict[str, Any]:
    return _call(
        audit_review_service.confirm_field,
        sample_id,
        payload.account_id,
        payload.member_id,
        payload.field,
        payload.decision,
        payload.remark,
    )


@router.get("/transactions/{tx_id}/confirmations")
def list_confirmations(tx_id: str, account_id: str = Query(...)) -> dict[str, Any]:
    return _call(audit_review_service.list_confirmations, tx_id, account_id)


@router.post("/transactions/{tx_id}/replay")
def replay(tx_id: str, account_id: str = Query(...)) -> dict[str, Any]:
    return _call(audit_review_service.replay, tx_id, account_id)


@router.post("/transactions/{tx_id}/attest")
def attest(tx_id: str, account_id: str = Query(...)) -> dict[str, Any]:
    return _call(audit_review_service.attest, tx_id, account_id)
