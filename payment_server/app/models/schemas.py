from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime
from enum import IntEnum


# ── PLAN DEFINITIONS ─────────────────────────────────────────────────────────
PLANS = {
    7:   {"name": "Gói 7 ngày",         "price": 99000},
    14:  {"name": "Gói 14 ngày",        "price": 169000},
    30:  {"name": "Gói 30 ngày",        "price": 249000},
    60:  {"name": "Gói 60 ngày",        "price": 449000},
    90:  {"name": "Gói 90 ngày",        "price": 599000},
    365: {"name": "Gói 1 năm (365 ngày)", "price": 1799000},
}

OrderStatus = Literal["pending", "paid", "expired", "cancelled"]


# ── REQUEST / RESPONSE SCHEMAS ────────────────────────────────────────────────
class CreateOrderRequest(BaseModel):
    days: int = Field(..., description="Số ngày gói: 7/14/30/60/90/365")


class CreateOrderResponse(BaseModel):
    order_id: str
    username: str
    days: int
    plan_name: str
    amount: int
    transfer_content: str
    qr_url: str
    bank_id: str
    account_no: str
    account_name: str
    expires_at_order: datetime     # Hết hạn QR / order
    status: str


class PaymentStatusResponse(BaseModel):
    order_id: str
    status: OrderStatus
    amount: int
    paid_at: Optional[datetime] = None
    subscription_expires_at: Optional[datetime] = None
    plan_name: str


class SepayWebhookPayload(BaseModel):
    id: Optional[int] = None
    gateway: Optional[str] = None
    transactionDate: Optional[str] = None
    accountNumber: Optional[str] = None
    subAccount: Optional[str] = None
    transferType: Optional[str] = None
    transferAmount: float = 0
    accumulated: Optional[float] = None
    code: Optional[str] = None
    content: Optional[str] = ""
    referenceCode: Optional[str] = None
    description: Optional[str] = None


class LicenseStatusResponse(BaseModel):
    username: str
    is_active: bool
    plan_name: Optional[str] = None
    expires_at: Optional[datetime] = None
    days_remaining: Optional[int] = None


# ── Auth (tool user accounts) ─────────────────────────────────────────────────
class AuthRegisterRequest(BaseModel):
    phone: str = Field(..., min_length=9, max_length=15)
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=6, max_length=128)


class AuthLoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=6, max_length=128)


class AuthResponse(BaseModel):
    token: str
    username: str
    phone: str


class AuthVerifyResponse(BaseModel):
    valid: bool = True
    token: str
    username: str
    phone: str
