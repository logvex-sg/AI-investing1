"""Evaluation criteria must be stable and outside agent control."""

import pytest

from ecosystem.domain.evaluation import (
    GATES,
    WEIGHTS,
    consistency,
    evaluate_agent,
    evaluate_backtest,
    out_of_sample_verdict,
    robustness_verdict,
)


def good_metrics(**overrides):
    base = {
        "trades": 40.0,
        "max_drawdown": 0.12,
        "sharpe": 1.4,
        "sortino": 1.8,
        "total_return": 0.35,
        "win_rate": 0.55,
        "total_costs": 200.0,
        "final_equity": 13500.0,
    }
    base.update(overrides)
    return base


def rising_curve(n=200, rate=0.002):
    curve = [10000.0]
    for _ in range(n):
        curve.append(curve[-1] * (1 + rate))
    return curve


def test_weights_sum_to_one():
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)


def test_good_strategy_passes():
    result = evaluate_backtest(good_metrics(), rising_curve())
    assert result.passed
    assert result.score > 0.5


def test_too_few_trades_fails_gate():
    result = evaluate_backtest(good_metrics(trades=2), rising_curve())
    assert not result.passed
    assert "min_trades" in result.gates_failed


def test_excessive_drawdown_fails_gate():
    result = evaluate_backtest(good_metrics(max_drawdown=0.6), rising_curve())
    assert not result.passed
    assert "max_drawdown" in result.gates_failed


def test_negative_sharpe_fails_gate():
    result = evaluate_backtest(good_metrics(sharpe=-2.0), rising_curve())
    assert not result.passed
    assert "min_sharpe" in result.gates_failed


def test_score_is_bounded():
    result = evaluate_backtest(
        good_metrics(sharpe=99, total_return=99, sortino=99, win_rate=1.0), rising_curve()
    )
    assert 0.0 <= result.score <= 1.0


def test_better_metrics_score_higher():
    low = evaluate_backtest(good_metrics(sharpe=0.2, total_return=0.02), rising_curve())
    high = evaluate_backtest(good_metrics(sharpe=2.5, total_return=0.8), rising_curve())
    assert high.score > low.score


def test_consistency_of_monotonic_curve_is_one():
    assert consistency(rising_curve()) == 1.0


def test_consistency_of_flat_curve_is_zero():
    assert consistency([10000.0] * 100) == 0.0


def test_out_of_sample_accepts_small_degradation():
    passed, degradation = out_of_sample_verdict(0.8, 0.65)
    assert passed
    assert degradation == pytest.approx(0.1875)


def test_out_of_sample_rejects_large_degradation():
    passed, degradation = out_of_sample_verdict(0.8, 0.2)
    assert not passed
    assert degradation > 0.35


def test_robustness_accepts_stable_scores():
    passed, drop, notes = robustness_verdict(0.8, [0.78, 0.75, 0.7])
    assert passed
    assert drop < 0.3


def test_robustness_rejects_fragile_strategy():
    passed, drop, notes = robustness_verdict(0.8, [0.9, 0.2])
    assert not passed
    assert notes


def test_robustness_without_runs_fails():
    passed, drop, notes = robustness_verdict(0.8, [])
    assert not passed


def test_evaluate_agent_rewards_reliability():
    consistent = [{"score": 0.6}] * 6
    erratic = [{"score": 0.9}, {"score": 0.1}] * 3
    a = evaluate_agent(consistent, generation_median=0.5)
    b = evaluate_agent(erratic, generation_median=0.5)
    assert a["reliability"] > b["reliability"]


def test_evaluate_agent_empty():
    result = evaluate_agent([], generation_median=0.5)
    assert result["score"] == 0.0
    assert result["experiments"] == 0


def test_gates_are_fixed_constants():
    assert GATES["min_trades"] == 5
    assert GATES["max_drawdown"] == 0.45
