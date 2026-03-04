from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class CreateOrderRequest(BaseModel):
    # H-2-FIX: amount_inr removed — price is now server-side only.
    # Previously the client supplied the price, meaning any user could
    # purchase the enterprise plan for ₹0.01 by sending amount_inr=1.
    plan: str = Field(..., pattern=r"^(starter|pro|enterprise)$")


class CreateOrderResponse(BaseModel):
    order_id: str
    amount: int
    currency: str
    plan: str
    billing_record_id: uuid.UUID


class BillingRecordResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    razorpay_order_id: str | None
    razorpay_payment_id: str | None
    amount_inr: Decimal
    plan: str
    status: str
    invoice_url: str | None
    created_at: datetime
    model_config = {"from_attributes": True}


class BillingHistoryResponse(BaseModel):
    records: list[BillingRecordResponse]
    total: int


class TokenUsageSummary(BaseModel):
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: Decimal


class UsageSummaryResponse(BaseModel):
    month: str
    organization_id: uuid.UUID
    breakdown: list[TokenUsageSummary]
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_cost_usd: Decimal
