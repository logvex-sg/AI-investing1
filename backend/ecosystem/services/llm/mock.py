"""Deterministic offline provider.

Lets the whole ecosystem run with no model server, and makes agent behaviour
reproducible in tests. It is not a language model: it reads the requested
schema and the agent's specialization from the prompt and synthesises a valid,
plausible response seeded by a hash of that prompt.

This is the honest fallback described in the brief: a realistic service that
implements the same interface the real provider will implement, rather than a
fake button that does nothing.
"""

from __future__ import annotations

import hashlib
import json
import random
import time

from ecosystem.services.llm.base import LLMRequest, LLMResponse

# Indicator vocabularies per specialization, so different agents genuinely
# explore different hypotheses.
SPECIALIZATION_RULES: dict[str, list[dict]] = {
    "momentum": [
        {"indicator": "momentum", "period": 20, "op": ">", "value": 0.05},
        {"indicator": "sma", "period": 50, "op": "price_above", "value": 0.0},
    ],
    "quantitative": [
        {"indicator": "ema", "period": 12, "op": "price_above", "value": 0.0},
        {"indicator": "volatility", "period": 30, "op": "<", "value": 0.9},
    ],
    "value": [
        {"indicator": "sma", "period": 200, "op": "price_below", "value": 0.0},
        {"indicator": "rsi", "period": 14, "op": "<", "value": 35},
    ],
    "defensive": [
        {"indicator": "volatility", "period": 20, "op": "<", "value": 0.4},
        {"indicator": "rsi", "period": 14, "op": ">", "value": 45},
    ],
    "macro": [
        {"indicator": "sma", "period": 100, "op": "price_above", "value": 0.0},
        {"indicator": "momentum", "period": 60, "op": ">", "value": 0.0},
    ],
    "volatility": [
        {"indicator": "rsi", "period": 14, "op": "<", "value": 30},
        {"indicator": "momentum", "period": 5, "op": ">", "value": 0.0},
    ],
    "experimental": [
        {"indicator": "ema", "period": 8, "op": "crosses_above", "value": 0.0},
        {"indicator": "momentum", "period": 3, "op": ">", "value": 0.0},
    ],
    "diversified": [
        {"indicator": "sma", "period": 20, "op": "price_above", "value": 0.0},
        {"indicator": "volatility", "period": 30, "op": "<", "value": 0.6},
    ],
}

DEFAULT_RULES = [
    {"indicator": "sma", "period": 20, "op": "price_above", "value": 0.0},
]

EXIT_RULES = [
    {"indicator": "sma", "period": 20, "op": "price_below", "value": 0.0},
]

THESES = {
    "momentum": "Assets that have risen over the past month tend to continue rising, "
    "so I will ride established trends and cut them when the trend breaks.",
    "quantitative": "Short-horizon mean reversion after volatility compression offers a "
    "measurable, repeatable edge that survives costs.",
    "value": "Deep drawdowns against the long-run average revert upward, so buying "
    "weakness below the trend offers asymmetric payoff.",
    "defensive": "Capital preservation dominates: I will only hold when volatility is "
    "low and momentum is mildly positive.",
    "macro": "The long trend carries the regime; I follow it and stay out of chop.",
    "volatility": "Overreaction to short-term selling creates snapback opportunities "
    "with well-defined downside.",
    "experimental": "Fast crossover signals on short windows capture regime shifts "
    "earlier than conventional moving averages.",
    "diversified": "A trend filter combined with a volatility cap gives broad, steady "
    "participation across assets.",
}


class MockProvider:
    name = "mock"

    def __init__(self, seed: int = 0):
        self.seed = seed
        self._calls = 0

    async def generate(self, request: LLMRequest) -> LLMResponse:
        started = time.perf_counter()
        self._calls += 1
        schema = _detect_schema(request.prompt)
        specialization = _detect_specialization(request.prompt)
        rng = random.Random(
            int.from_bytes(
                hashlib.sha256(
                    f"{self.seed}:{schema}:{specialization}:{request.prompt[:400]}".encode()
                ).digest()[:8],
                "big",
            )
        )
        payload = self._build(schema, specialization, request.prompt, rng)
        text = json.dumps(payload, indent=2)
        latency_ms = int((time.perf_counter() - started) * 1000)
        return LLMResponse(
            text=text,
            model="mock-deterministic",
            provider=self.name,
            latency_ms=latency_ms,
            tokens_estimate=len(text) // 4,
        )

    def _build(self, schema: str, specialization: str, prompt: str, rng: random.Random) -> dict:
        symbol = _detect_symbol(prompt)
        if schema == "StrategyProposal":
            base_rules = SPECIALIZATION_RULES.get(specialization, DEFAULT_RULES)
            rules = []
            for rule in base_rules:
                variant = dict(rule)
                # Small, bounded jitter keeps agents distinct but valid.
                variant["period"] = max(2, int(round(rule["period"] * rng.uniform(0.8, 1.25))))
                if rule["op"] in (">", "<", ">=", "<="):
                    variant["value"] = round(rule["value"] * rng.uniform(0.7, 1.3), 4)
                rules.append(variant)
            return {
                "name": f"{specialization.capitalize()} {symbol} v{rng.randint(1, 9)}",
                "thesis": THESES.get(specialization, THESES["diversified"]),
                "symbol": symbol,
                "time_horizon": rng.choice(["short", "medium", "long"]),
                "entry_rules": rules,
                "exit_rules": [dict(r) for r in EXIT_RULES],
                "allocation_pct": round(rng.uniform(0.08, 0.22), 3),
                "rationale": (
                    f"Following my {specialization} mandate, I propose this rule set to "
                    f"test whether the effect persists after costs on {symbol}."
                ),
            }
        if schema == "HypothesisProposal":
            return {
                "statement": (
                    f"{specialization.capitalize()} conditions on {symbol} predict the "
                    f"next-period return better than a passive hold."
                ),
                "rationale": THESES.get(specialization, THESES["diversified"]),
                "confidence": round(rng.uniform(0.35, 0.75), 3),
                "test_symbol": symbol,
            }
        if schema == "ResearchQuestionProposal":
            return {
                "question": (
                    f"Which measurable condition on {symbol} produces a positive, "
                    f"cost-adjusted edge that survives out-of-sample testing?"
                ),
                "rationale": "We need evidence, not opinion, before allocating capital.",
                "priority": round(rng.uniform(0.4, 0.9), 3),
            }
        if schema == "OverseerPlan":
            agents = _detect_agents(prompt)
            assignments = [
                {
                    "codename": codename,
                    "specialization": spec,
                    "task": f"Investigate a {spec} signal on {symbol}.",
                    "priority": round(rng.uniform(0.4, 0.9), 3),
                }
                for codename, spec in agents[:8]
            ]
            return {
                "objective": (
                    f"Determine whether any {symbol} signal family produces a robust, "
                    f"cost-adjusted edge worth paper trading."
                ),
                "research_question": {
                    "question": (
                        f"Which signal families on {symbol} survive backtest, "
                        f"out-of-sample and robustness testing?"
                    ),
                    "rationale": "Falsifiable and comparable across agents.",
                    "priority": 0.8,
                },
                "assignments": assignments,
                "notes": "Disagreement between agents is expected and should be "
                "resolved with additional experiments, not consensus.",
            }
        if schema == "Analysis":
            return {
                "summary": (
                    f"Reviewed the results. Performance is dominated by "
                    f"{specialization} exposure; costs and drawdown are the binding "
                    f"constraints."
                ),
                "recommendation": "Continue experimentation; do not change risk limits.",
            }
        return {"text": "Acknowledged."}

    async def health(self) -> dict:
        return {"ok": True, "provider": self.name, "mode": "deterministic", "calls": self._calls}


def _detect_schema(prompt: str) -> str:
    for schema in ("StrategyProposal", "HypothesisProposal", "ResearchQuestionProposal",
                   "OverseerPlan", "Analysis"):
        if f"SCHEMA:{schema}" in prompt or f'"schema": "{schema}"' in prompt:
            return schema
    return "Analysis"


def _detect_specialization(prompt: str) -> str:
    lowered = prompt.lower()
    for name in SPECIALIZATION_RULES:
        if f"specialization: {name}" in lowered or f"specialization={name}" in lowered:
            return name
    return "diversified"


def _detect_symbol(prompt: str) -> str:
    for symbol in ("BTCUSD", "EURUSD", "SPX", "BTC", "EUR", "USD"):
        if symbol in prompt:
            return symbol
    return "BTCUSD"


def _detect_agents(prompt: str) -> list[tuple[str, str]]:
    """Recover (codename, specialization) pairs from an Overseer prompt."""
    pairs: list[tuple[str, str]] = []
    for line in prompt.splitlines():
        lowered = line.lower()
        if "codename:" in lowered and "specialization:" in lowered:
            parts = {}
            for chunk in line.split("|"):
                if ":" in chunk:
                    key, _, value = chunk.partition(":")
                    parts[key.strip().lower()] = value.strip()
            codename = parts.get("codename")
            specialization = parts.get("specialization", "").lower()
            if codename:
                pairs.append((codename, specialization))
    return pairs
