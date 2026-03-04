# placeholder
"""Billing API routes."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import structlog
from fastapi import APIRouter, Header, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_session_factory
from app.dependencies import AdminContext, CurrentContext
from app.middleware.tenant import TenantContext, set_tenant_context
from app.models.billing import BillingRecord, TokenUsage
from app.schemas.billing import (
    BillingHistoryResponse,
    BillingRecordResponse,
    CreateOrderRequest,
    CreateOrderResponse,
    TokenUsageSummary,
    UsageSummaryResponse,
)
from app.services.audit import log_action

logger = structlog.get_logger()

router = APIRouter()

# H-2-FIX: Server-authoritative plan prices (INR). The client previously
# supplied amount_inr, allowing any user to purchase any plan for ₹0.01.
# All pricing is now server-side only — CreateOrderRequest only takes `plan`.
_PLAN_PRICES_INR: dict[str, int] = {
    "starter": 999,
    "pro": 2999,
    "enterprise": 9999,
}


@router.post("/orders", response_model=CreateOrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(body: CreateOrderRequest, ctx: CurrentContext) -> CreateOrderResponse:
    """Create a Razorpay order for the given plan.

    Price is determined server-side from _PLAN_PRICES_INR — client cannot
    supply or influence the amount. Stores a pending BillingRecord, then
    calls the Razorpay v1/orders API.
    """
    settings = get_settings()
    key_id = getattr(settings, "razorpay_key_id", "")
    key_secret = getattr(settings, "razorpay_key_secret", "")
    if not key_id or not key_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Payment gateway not configured",
        )
    # H-2-FIX: Price from server-side table, not client input
    amount_inr = _PLAN_PRICES_INR.get(body.plan, 0)
    if amount_inr == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown plan")
    amount_paise = amount_inr * 100
    factory = get_session_factory()
    billing_record_id = uuid.uuid4()
    async with factory() as session:
        async with session.begin():
            await set_tenant_context(session, TenantContext(
                organization_id=ctx.organization_id,
                user_id=ctx.user_id,
                role=ctx.role,
            ))
            record = BillingRecord(
                id=billing_record_id,
                organization_id=uuid.UUID(ctx.organization_id),
                plan=body.plan,
                amount_inr=Decimal(amount_inr),  # server-side price
                status="pending",
            )
            session.add(record)
    # Call Razorpay v1/orders API with Basic auth
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.razorpay.com/v1/orders",
                auth=(key_id, key_secret),
                json={
                    "amount": amount_paise,
                    "currency": "INR",
                    "receipt": str(billing_record_id),
                    "notes": {"plan": body.plan, "org_id": ctx.organization_id},
                },
            )
            resp.raise_for_status()
            order_data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error("razorpay_order_failed", status=exc.response.status_code, org=ctx.organization_id)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Payment gateway error")
    except httpx.RequestError as exc:
        logger.error("razorpay_request_error", error=str(exc)[:200])
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Payment gateway unreachable")
    # Update the BillingRecord with the Razorpay order_id
    razorpay_order_id = order_data["id"]
    async with factory() as session:
        async with session.begin():
            await set_tenant_context(session, TenantContext(
                organization_id=ctx.organization_id,
                user_id=ctx.user_id,
                role=ctx.role,
            ))
            result = await session.execute(
                select(BillingRecord).where(BillingRecord.id == billing_record_id)
            )
            rec = result.scalar_one_or_none()
            if rec is not None:
                rec.razorpay_order_id = razorpay_order_id
    logger.info("razorpay_order_created", order_id=razorpay_order_id, plan=body.plan, org=ctx.organization_id)
    return CreateOrderResponse(
        order_id=razorpay_order_id,
        amount=amount_paise,
        currency="INR",
        plan=body.plan,
        billing_record_id=billing_record_id,
    )


@router.post("/webhook")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str = Header(default=""),
) -> dict:
    """Razorpay payment webhook (public endpoint, no auth).

    Verifies HMAC-SHA256 signature with razorpay_webhook_secret.
    On payment.captured: marks BillingRecord as paid.
    Always returns 200 -- Razorpay retries on non-200 responses.
    """
    settings = get_settings()
    webhook_secret = getattr(settings, "razorpay_webhook_secret", "")
    payload_bytes = await request.body()

    # F-3+C-1-FIX: Signature verification is now mandatory.
    # Previously: if webhook_secret was empty (the default), ALL events were
    # processed without any verification — any POST would upgrade billing records.
    # Now: empty secret in production returns 503 (misconfiguration); in dev
    # it logs a loud warning. In all cases a provided secret IS verified.
    if not webhook_secret:
        if settings.is_production:
            logger.error("razorpay_webhook_secret_not_configured")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Webhook processing not configured",
            )
        logger.warning(
            "razorpay_webhook_signature_verification_disabled",
            note="Set RAZORPAY_WEBHOOK_SECRET in production",
        )
    else:
        expected_sig = hmac.new(
            webhook_secret.encode(),
            payload_bytes,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected_sig, x_razorpay_signature):
            logger.warning("razorpay_webhook_invalid_signature")
            return {"status": "ignored"}
    import json
    try:
        event = json.loads(payload_bytes)
    except json.JSONDecodeError:
        logger.warning("razorpay_webhook_invalid_json")
        return {"status": "ignored"}

    event_type = event.get("event", "")
    logger.info("razorpay_webhook_received", event=event_type)

    if event_type == "payment.captured":
        payment = event.get("payload", {}).get("payment", {}).get("entity", {})
        razorpay_payment_id = payment.get("id", "")
        razorpay_order_id = payment.get("order_id", "")
        if razorpay_order_id:
            try:
                factory = get_session_factory()
                async with factory() as session:
                    async with session.begin():
                        result = await session.execute(
                            select(BillingRecord).where(
                                BillingRecord.razorpay_order_id == razorpay_order_id
                            )
                        )
                        rec = result.scalar_one_or_none()
                        if rec is not None and rec.status != "paid":
                            # F-3-FIX: Idempotency guard — only transition pending→paid.
                            # Razorpay delivers at-least-once. Without this check,
                            # duplicate events re-trigger side effects on an already-
                            # paid record (plan upgrades, credit grants, etc.).
                            rec.status = "paid"
                            rec.razorpay_payment_id = razorpay_payment_id
                            logger.info("billing_record_paid", order_id=razorpay_order_id, payment_id=razorpay_payment_id)
                        elif rec is not None:
                            logger.info("billing_webhook_duplicate_ignored", order_id=razorpay_order_id)
            except Exception as exc:
                logger.error("webhook_db_error", error=str(exc)[:200])
    return {"status": "ok"}


@router.get("/records", response_model=BillingHistoryResponse)
async def list_billing_records(
    ctx: AdminContext,  # M-3-FIX: Billing records are financial data — admin-only
    limit: int = 50,   # H-8-FIX: Paginate to prevent unbounded result sets
    offset: int = 0,
) -> BillingHistoryResponse:
    """List billing records for the current organization. Requires admin role.

    M-3-FIX: Previously any role (developer, viewer) could see billing history.
    Financial records should be restricted to org_admin / super_admin.
    H-8-FIX: Added limit/offset pagination — unbounded queries are a DoS vector.
    """
    limit = min(max(limit, 1), 200)  # clamp to [1, 200]
    offset = max(offset, 0)
    factory = get_session_factory()
    async with factory() as session:
        async with session.begin():
            await set_tenant_context(session, TenantContext(
                organization_id=ctx.organization_id,
                user_id=ctx.user_id,
                role=ctx.role,
            ))
            total_result = await session.execute(
                select(func.count(BillingRecord.id))
                .where(BillingRecord.organization_id == uuid.UUID(ctx.organization_id))
            )
            total = total_result.scalar_one()
            result = await session.execute(
                select(BillingRecord)
                .where(BillingRecord.organization_id == uuid.UUID(ctx.organization_id))
                .order_by(BillingRecord.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            records = result.scalars().all()
    return BillingHistoryResponse(
        records=[BillingRecordResponse.model_validate(r) for r in records],
        total=total,
    )


@router.get("/usage", response_model=UsageSummaryResponse)
async def get_usage_summary(ctx: CurrentContext) -> UsageSummaryResponse:
    """Return current calendar month token usage, aggregated by model."""
    now = datetime.now(tz=timezone.utc)
    month_str = now.strftime("%Y-%m")
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    factory = get_session_factory()
    async with factory() as session:
        async with session.begin():
            await set_tenant_context(session, TenantContext(
                organization_id=ctx.organization_id,
                user_id=ctx.user_id,
                role=ctx.role,
            ))
            result = await session.execute(
                select(
                    TokenUsage.model,
                    func.sum(TokenUsage.input_tokens).label("input_tokens"),
                    func.sum(TokenUsage.output_tokens).label("output_tokens"),
                    func.sum(TokenUsage.cost_usd).label("cost_usd"),
                )
                .where(
                    TokenUsage.organization_id == uuid.UUID(ctx.organization_id),
                    TokenUsage.recorded_at >= month_start,
                )
                .group_by(TokenUsage.model)
                .order_by(TokenUsage.model)
            )
            rows = result.all()
    breakdown = [
        TokenUsageSummary(
            model=r.model,
            input_tokens=r.input_tokens or 0,
            output_tokens=r.output_tokens or 0,
            total_tokens=(r.input_tokens or 0) + (r.output_tokens or 0),
            cost_usd=Decimal(str(r.cost_usd or 0)),
        )
        for r in rows
    ]
    total_input = sum(b.input_tokens for b in breakdown)
    total_output = sum(b.output_tokens for b in breakdown)
    total_cost = sum(b.cost_usd for b in breakdown)
    return UsageSummaryResponse(
        month=month_str,
        organization_id=uuid.UUID(ctx.organization_id),
        breakdown=breakdown,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        total_tokens=total_input + total_output,
        total_cost_usd=total_cost,
    )
