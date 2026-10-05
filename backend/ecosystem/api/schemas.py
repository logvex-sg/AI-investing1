"""Request and response schemas for the HTTP API.

Decimals are carried as strings on the wire so that no rounding happens in
JSON, and enum values are validated by Pydantic before they reach a service.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from ecosystem.db.models.enums import AdapterKind, ProposalAction, UserRole


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12, max_length=256)
    display_name: str = Field(min_length=1, max_length=128)
    email: str | None = None
    role: UserRole = UserRole.OPERATOR


class SessionResponse(BaseModel):
    token: str
    user: dict


class TradeProposalRequest(BaseModel):
    agent_id: uuid.UUID
    portfolio_id: uuid.UUID | None = None
    action: ProposalAction
    symbol: str = Field(min_length=1, max_length=32)
    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    reason: str = Field(min_length=3, max_length=2000)
    strategy_id: uuid.UUID | None = None
    strategy_stage: str | None = None

    @field_validator("symbol")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper().strip()


class TransferProposalRequest(BaseModel):
    agent_id: uuid.UUID | None = None
    source_account_id: uuid.UUID
    destination_account_id: uuid.UUID
    amount: Decimal = Field(gt=0)
    reason: str = Field(min_length=3, max_length=2000)


class DecisionRequest(BaseModel):
    approve: bool
    reason: str | None = Field(default=None, max_length=2000)
    confirmation: str | None = None


class ExecuteRequest(BaseModel):
    adapter_kind: AdapterKind = AdapterKind.PAPER_TRADING
    market_price: Decimal | None = Field(default=None, gt=0)


class EmergencyStopRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class OverseerCycleRequest(BaseModel):
    objective: str | None = Field(default=None, max_length=1000)
    run_assignments: bool = True


class EvolveRequest(BaseModel):
    limit: int | None = Field(default=None, ge=1, le=8)
    reason: str | None = Field(default=None, max_length=1000)


class ResearchRequest(BaseModel):
    agent_id: uuid.UUID
    objective: str = Field(min_length=3, max_length=1000)


class ValidationRequest(BaseModel):
    strategy_id: uuid.UUID
    symbol: str | None = None
    interval: str = "1d"
    limit: int = Field(default=500, ge=100, le=5000)


class MemorySearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    agent_id: uuid.UUID | None = None
    limit: int = Field(default=10, ge=1, le=50)


class MarkRequest(BaseModel):
    interval: str = "1d"
