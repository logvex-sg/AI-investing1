"""Prompt construction.

Prompts are assembled from bounded, labelled sections. The agent identity is
written in a parseable form (`specialization: momentum`) so the provider — and
the deterministic mock — can key off it. Every prompt ends with the
constraints, and the constraints say plainly what the model may not do.
"""

from __future__ import annotations

from typing import Any

from ecosystem.domain.strategy_signals import describe_strategy

SYSTEM_AGENT = (
    "You are an investor agent inside a local-first research ecosystem. "
    "You propose falsifiable hypotheses and structured strategies. "
    "You never state performance numbers: a deterministic engine computes all "
    "results and you only interpret them. You cannot change risk limits, "
    "accounting rules, or approval policy."
)

SYSTEM_OVERSEER = (
    "You are the Overseer of a research ecosystem of investor agents. "
    "You coordinate research, compare agents, and design experiments. "
    "You cannot approve your own actions, change evaluation criteria, alter "
    "risk limits, or move capital. You only propose; deterministic services "
    "and a human decide."
)


def _section(title: str, body: str) -> str:
    return f"## {title}\n{body.strip()}\n"


def agent_identity(agent: Any) -> str:
    return "\n".join(
        [
            f"codename: {agent.codename}",
            f"display_name: {agent.display_name}",
            f"specialization: {agent.specialization}",
            f"generation: {agent.generation_number}",
            f"time_horizon: {agent.time_horizon}",
            f"assets: {', '.join(agent.asset_preferences) or 'BTCUSD'}",
        ]
    )


def strategy_prompt(
    *,
    agent: Any,
    objective: str,
    memories: list[dict],
    market_context: str,
    prior_failures: list[str],
) -> str:
    memory_text = (
        "\n".join(
            f"- ({m['kind']}, relevance {m['relevance']:.2f}) {m['title']}: {m['content'][:240]}"
            for m in memories
        )
        or "No relevant memories yet."
    )
    failure_text = "\n".join(f"- {f}" for f in prior_failures) or "No recorded failures."
    return "\n".join(
        [
            _section("Identity", agent_identity(agent)),
            _section("Objective", objective),
            _section("Relevant memories", memory_text),
            _section("Known failures (avoid repeating)", failure_text),
            _section("Market context", market_context),
            _section(
                "Task",
                "Propose ONE structured strategy to test this objective. Return JSON "
                "matching SCHEMA:StrategyProposal with fields: name, thesis, symbol, "
                "time_horizon, entry_rules, exit_rules, allocation_pct, rationale. "
                "entry_rules and exit_rules are arrays of "
                '{"indicator", "period", "op", "value"} with indicator in '
                "{sma, ema, rsi, momentum, volatility} and op in "
                "{>, <, >=, <=, price_above, price_below, crosses_above, crosses_below}. "
                "allocation_pct must be <= 0.25.",
            ),
            _section(
                "Constraints",
                "You may not propose changes to risk limits, accounting rules, "
                "evaluation criteria, or approval policy. You may not state returns, "
                "drawdowns, or prices as fact; the engine measures them.",
            ),
        ]
    )


def hypothesis_prompt(*, agent: Any, question: str, memories: list[dict]) -> str:
    memory_text = (
        "\n".join(f"- {m['title']}: {m['content'][:200]}" for m in memories)
        or "No relevant memories yet."
    )
    return "\n".join(
        [
            _section("Identity", agent_identity(agent)),
            _section("Research question", question),
            _section("Relevant memories", memory_text),
            _section(
                "Task",
                "State ONE falsifiable hypothesis. Return JSON matching "
                "SCHEMA:HypothesisProposal with fields: statement, rationale, "
                "confidence (0..1), test_symbol.",
            ),
            _section(
                "Constraints",
                "A hypothesis must be testable and refutable. Do not assert results.",
            ),
        ]
    )


def overseer_prompt(
    *,
    objective: str,
    agents: list[Any],
    performance: dict[str, Any],
    open_questions: list[str],
    recent_events: list[str],
) -> str:
    roster = "\n".join(
        f"- codename: {a.codename} | specialization: {a.specialization} | "
        f"generation: {a.generation_number} | status: {a.status.value}"
        for a in agents
    )
    questions = "\n".join(f"- {q}" for q in open_questions) or "None open."
    events = "\n".join(f"- {e}" for e in recent_events) or "No recent events."
    return "\n".join(
        [
            _section("Role", "You coordinate the agent roster and research agenda."),
            _section("Current objective", objective),
            _section("Agent roster", roster),
            _section("Performance summary", str(performance)),
            _section("Open research questions", questions),
            _section("Recent events", events),
            _section(
                "Task",
                "Return JSON matching SCHEMA:OverseerPlan with fields: objective, "
                "research_question {question, rationale, priority}, assignments "
                "[{codename, specialization, task, priority}], notes. Assign work to "
                "at most 8 agents. Preserve disagreement; do not force consensus.",
            ),
            _section(
                "Constraints",
                "You cannot approve actions, change evaluation criteria, modify risk "
                "limits, erase history, or grant permissions. You propose only.",
            ),
        ]
    )


def analysis_prompt(*, subject: str, evidence: str) -> str:
    return "\n".join(
        [
            _section("Subject", subject),
            _section("Evidence", evidence),
            _section(
                "Task",
                "Return JSON matching SCHEMA:Analysis with fields: summary, "
                "recommendation. Interpret the evidence; do not invent numbers.",
            ),
        ]
    )


def market_context(
    symbol: str, price: float, regime: str, volatility: float, recent_return: float
) -> str:
    return (
        f"{symbol}: last close {price:.4f}, regime '{regime}', "
        f"annualised volatility {volatility:.2%}, recent window return "
        f"{recent_return:.2%}. Treat high-volatility assets as volatile; they are "
        f"not stable stores of value."
    )


def strategy_summary(parameters: dict, entry_rules: list[dict], exit_rules: list[dict]) -> str:
    return describe_strategy(parameters, entry_rules, exit_rules)
