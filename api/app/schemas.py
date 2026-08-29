"""Request/response bodies."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models import ORDER_STATUSES
from app.state_store import FaultState, RemediationState

OrderStatus = Literal["new", "paid", "shipped", "cancelled"]
assert set(ORDER_STATUSES) == set(OrderStatus.__args__)  # keep model + schema in sync


class OrderCreate(BaseModel):
    item: str = Field(min_length=1, max_length=120)
    quantity: int = Field(gt=0)
    price_cents: int = Field(default=0, ge=0)


class OrderUpdate(BaseModel):
    status: Optional[OrderStatus] = None
    quantity: Optional[int] = Field(default=None, gt=0)


class OrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    item: str
    quantity: int
    price_cents: int
    status: str
    created_at: datetime


# --- admin ------------------------------------------------------------------
class LatencyFault(BaseModel):
    type: Literal["latency"]
    ms: int = Field(ge=0, le=60_000)
    target: Literal["api", "db"] = "api"


class ErrorFault(BaseModel):
    type: Literal["error"]
    magnitude: float = Field(ge=0.0, le=1.0)


class ClearFault(BaseModel):
    type: Literal["clear"]


class RemediationRequest(BaseModel):
    action: Literal[
        "enable_cache",
        "disable_feature_flag",
        "enable_rate_limit",
        "reset_pool",
    ]
    enabled: bool = True  # lets enable_* actions also turn a knob back off


class StateResponse(BaseModel):
    fault: FaultState
    remediation: RemediationState
    replica: str
