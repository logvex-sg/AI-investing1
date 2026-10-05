"""Strategy and agent evaluation.

Evaluation criteria live here, outside agent authority. An agent cannot
change its own grade. Scoring is a transparent weighted sum of measured
metrics, with hard gates that reject a strategy outright.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# Hard gates: failing any of these rejects a strategy regardless of score.
GATES = {
    "min_trades": 5,
    "max_drawdown": 0.45,
    "min_sharpe": -0.5,
}

# Relative importance of each metric in the composite score. They sum to 1.0.
WEIGHTS = {
    "sharpe": 0.28,
    "total_return": 0.18,
    "max_drawdown": 0.18,
    "consistency": 0.12,
    "sortino": 0.10,
    "cost_efficiency": 0.08,
    "win_rate": 0.06,
}


@dataclass
class EvaluationResult:
    score: float
    passed: bool
    gates_failed: list[str] = field(default_factory=list)
    components: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _norm(value: float, low: float, high: float) -> float:
    """Map a metric onto 0..1, clamped. Handles inverted ranges."""
    if high == low:
        return 0.0
    x = (value - low) / (high - low)
    return max(0.0, min(1.0, x))


def _finite(value: float, default: float = 0.0) -> float:
    return value if isinstance(value, (int, float)) and math.isfinite(value) else default


def consistency(equity_curve: list[float], window: int = 30) -> float:
    """Fraction of rolling windows with a positive return. Rewards steady
    progress over a single lucky spike."""
    if len(equity_curve) < window + 1:
        return 0.0
    positives = 0
    total = 0
    for i in range(window, len(equity_curve)):
        if equity_curve[i - window] > 0:
            total += 1
            if equity_curve[i] > equity_curve[i - window]:
                positives += 1
    return positives / total if total else 0.0


def evaluate_backtest(
    metrics: dict[str, float],
    equity_curve: list[float],
    benchmark_return: float = 0.0,
) -> EvaluationResult:
    """Grade a completed backtest."""
    gates_failed: list[str] = []
    notes: list[str] = []

    trades = _finite(metrics.get("trades", 0))
    if trades < GATES["min_trades"]:
        gates_failed.append("min_trades")
        notes.append(f"Only {int(trades)} trades: insufficient evidence.")

    max_dd = _finite(metrics.get("max_drawdown", 0))
    if max_dd > GATES["max_drawdown"]:
        gates_failed.append("max_drawdown")
        notes.append(f"Drawdown {max_dd:.1%} exceeds the {GATES['max_drawdown']:.0%} ceiling.")

    sharpe = _finite(metrics.get("sharpe", 0))
    if sharpe < GATES["min_sharpe"]:
        gates_failed.append("min_sharpe")
        notes.append(f"Sharpe {sharpe:.2f} below the {GATES['min_sharpe']} floor.")

    comp = {
        "sharpe": _norm(sharpe, -1.0, 3.0),
        "total_return": _norm(_finite(metrics.get("total_return", 0)), -0.2, 1.0),
        "max_drawdown": 1.0 - _norm(max_dd, 0.0, 0.6),
        "consistency": consistency(equity_curve),
        "sortino": _norm(_finite(metrics.get("sortino", 0)), -1.0, 4.0),
        "cost_efficiency": _cost_efficiency(metrics),
        "win_rate": _norm(_finite(metrics.get("win_rate", 0)), 0.0, 0.7),
    }
    score = sum(comp[k] * WEIGHTS[k] for k in WEIGHTS)

    # Benchmark-relative adjustment: beating a passive benchmark is worth more
    # than absolute return in a rising market.
    excess = _finite(metrics.get("total_return", 0)) - benchmark_return
    score += 0.10 * _norm(excess, -0.1, 0.3)
    score = max(0.0, min(1.0, score))

    return EvaluationResult(
        score=round(score, 6),
        passed=not gates_failed,
        gates_failed=gates_failed,
        components={k: round(v, 6) for k, v in comp.items()},
        notes=notes,
    )


def _cost_efficiency(metrics: dict[str, float]) -> float:
    """1.0 when costs are negligible relative to gross gains, 0.0 when costs
    consume the result."""
    costs = _finite(metrics.get("total_costs", 0))
    gross = _finite(metrics.get("total_return", 0)) * _finite(metrics.get("final_equity", 0))
    if gross <= 0:
        return 0.0
    return _norm(1.0 - costs / max(gross, 1e-9), 0.0, 1.0)


def out_of_sample_verdict(
    in_sample_score: float,
    out_of_sample_score: float,
    degradation_tolerance: float = 0.35,
) -> tuple[bool, float]:
    """A strategy survives out-of-sample only if its score holds up.

    Returns (passed, degradation_fraction).
    """
    if in_sample_score <= 0:
        return False, 1.0
    degradation = (in_sample_score - out_of_sample_score) / in_sample_score
    return degradation <= degradation_tolerance, degradation


def robustness_verdict(
    base_score: float,
    perturbed_scores: list[float],
    max_score_drop: float = 0.30,
) -> tuple[bool, float, list[str]]:
    """A strategy is robust when small perturbations barely move the score."""
    if not perturbed_scores:
        return False, 1.0, ["no perturbation runs"]
    worst = min(perturbed_scores)
    drop = (base_score - worst) / base_score if base_score > 0 else 1.0
    notes: list[str] = []
    if drop > max_score_drop:
        notes.append(
            f"Score fell {drop:.1%} under perturbation (tolerance {max_score_drop:.0%})."
        )
    passed = drop <= max_score_drop
    return passed, drop, notes


def evaluate_agent(agent_metrics: list[dict[str, float]], generation_median: float) -> dict:
    """Aggregate an agent's experiments into a generation ranking score."""
    if not agent_metrics:
        return {"score": 0.0, "experiments": 0, "relative": 0.0, "detail": {}}
    scores = [_finite(m.get("score", 0)) for m in agent_metrics]
    avg = sum(scores) / len(scores)
    best = max(scores)
    consistency_ratio = sum(1 for s in scores if s >= 0.5) / len(scores)
    # Weighted blend: average performance, best result, and reliability.
    score = 0.55 * avg + 0.25 * best + 0.20 * consistency_ratio
    return {
        "score": round(score, 6),
        "experiments": len(scores),
        "average": round(avg, 6),
        "best": round(best, 6),
        "reliability": round(consistency_ratio, 6),
        "relative": round(score - generation_median, 6),
        "detail": {"scores": [round(s, 6) for s in scores]},
    }
