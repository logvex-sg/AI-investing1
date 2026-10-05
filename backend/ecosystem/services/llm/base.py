"""LLM provider interface and response schemas.

Every provider returns text; every *agent* asks for JSON conforming to one of
the schemas below. The model's output is treated as an untrusted proposal: it
is validated and clamped before it can influence a strategy, and it can never
influence accounting, risk or approval.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field, field_validator


class LLMRequest(BaseModel):
    system: str
    prompt: str
    temperature: float = 0.7
    max_tokens: int = 1024
    json_mode: bool = True


class LLMResponse(BaseModel):
    text: str
    model: str
    provider: str
    latency_ms: int = 0
    tokens_estimate: int = 0
    from_cache: bool = False


class LLMProvider(Protocol):
    name: str

    async def generate(self, request: LLMRequest) -> LLMResponse: ...

    async def health(self) -> dict: ...


# ---------------------------------------------------------------------------
# Structured outputs. These bound what an agent may ask for.
# ---------------------------------------------------------------------------


class RuleSpec(BaseModel):
    indicator: str = Field(description="sma | ema | rsi | momentum | volatility")
    period: int = Field(default=14, ge=2, le=400)
    op: str = Field(default=">", description=">, <, >=, <=, price_above, price_below")
    value: float = 0.0

    @field_validator("indicator")
    @classmethod
    def _known_indicator(cls, v: str) -> str:
        allowed = {"sma", "ema", "rsi", "momentum", "volatility"}
        if v not in allowed:
            raise ValueError(f"indicator must be one of {sorted(allowed)}")
        return v

    @field_validator("op")
    @classmethod
    def _known_op(cls, v: str) -> str:
        allowed = {">", "<", ">=", "<=", "price_above", "price_below",
                   "crosses_above", "crosses_below"}
        if v not in allowed:
            raise ValueError(f"op must be one of {sorted(allowed)}")
        return v


class StrategyProposal(BaseModel):
    """A strategy an agent wants to test. Bounds are enforced here, so a model
    cannot propose a 10,000-period lookback or an unsupported indicator."""

    name: str = Field(max_length=120)
    thesis: str = Field(max_length=2000)
    symbol: str = Field(default="BTCUSD")
    time_horizon: str = Field(default="medium")
    entry_rules: list[RuleSpec] = Field(default_factory=list, max_length=4)
    exit_rules: list[RuleSpec] = Field(default_factory=list, max_length=4)
    allocation_pct: float = Field(default=0.2, ge=0.01, le=0.25)
    rationale: str = Field(default="", max_length=2000)


class HypothesisProposal(BaseModel):
    statement: str = Field(max_length=2000)
    rationale: str = Field(default="", max_length=2000)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    test_symbol: str = Field(default="BTCUSD")


class ResearchQuestionProposal(BaseModel):
    question: str = Field(max_length=2000)
    rationale: str = Field(default="", max_length=2000)
    priority: float = Field(default=0.5, ge=0.0, le=1.0)


class OverseerPlan(BaseModel):
    objective: str = Field(max_length=2000)
    research_question: ResearchQuestionProposal
    assignments: list[dict] = Field(default_factory=list, max_length=8)
    notes: str = Field(default="", max_length=2000)
