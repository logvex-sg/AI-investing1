"""The risk engine is the safety boundary: test every rule."""

from decimal import Decimal

from ecosystem.domain.money import to_decimal
from ecosystem.domain.risk_rules import (
    DEFAULT_ALLOWLIST,
    PortfolioSnapshot,
    ProposalSnapshot,
    RiskLimits,
    evaluate,
    is_high_impact,
)


def D(x) -> Decimal:
    return to_decimal(x)


def portfolio(**overrides) -> PortfolioSnapshot:
    base = dict(
        cash=D("10000"),
        reserved_capital=D("0"),
        positions_value=D("0"),
        peak_value=D("10000"),
        starting_value=D("10000"),
        current_value=D("10000"),
        day_start_value=D("10000"),
        position_values={},
        trades_today=0,
    )
    base.update(overrides)
    return PortfolioSnapshot(**base)


def proposal(**overrides) -> ProposalSnapshot:
    base = dict(
        action="BUY", symbol="BTCUSD", quantity=D("0.05"),
        price=D("40000"), amount=D("2000"),
    )
    base.update(overrides)
    return ProposalSnapshot(**base)


def test_clean_buy_passes():
    # 500 of a 10000 portfolio: a 5% position, under every cap and impact
    # threshold, so nothing is raised at all.
    verdict = evaluate(
        proposal(quantity=D("0.0125"), amount=D("500")), portfolio(), RiskLimits()
    )
    assert verdict.decision == "PASS"
    assert verdict.passed
    assert verdict.violations == []
    assert verdict.warnings == []


def test_emergency_stop_rejects_everything():
    limits = RiskLimits(emergency_stop=True)
    verdict = evaluate(proposal(), portfolio(), limits)
    assert verdict.decision == "REJECT"
    assert verdict.violations[0].rule == "emergency_shutdown"


def test_asset_not_on_allowlist_rejected():
    verdict = evaluate(proposal(symbol="DOGEUSD"), portfolio(), RiskLimits())
    assert verdict.decision == "REJECT"
    assert any(v.rule == "asset_allowlist" for v in verdict.violations)


def test_allowlist_default_covers_eur_usd_btc():
    assert set(DEFAULT_ALLOWLIST) == {"EUR", "USD", "BTC"}


def test_position_limit_rejected():
    # 3000 of a 10000 portfolio is 30% > 25% cap.
    verdict = evaluate(
        proposal(quantity=D("0.075"), amount=D("3000")), portfolio(), RiskLimits()
    )
    assert verdict.decision == "REJECT"
    assert any(v.rule == "max_position" for v in verdict.violations)


def test_position_limit_boundary_passes():
    # 25% is exactly the cap, so it passes (the rule rejects only >).
    verdict = evaluate(
        proposal(quantity=D("0.0625"), amount=D("2500")), portfolio(), RiskLimits()
    )
    assert verdict.decision == "WARN"
    assert verdict.passed
    assert not any(v.rule == "max_position" for v in verdict.violations)


def test_exposure_limit_rejected():
    # Exposure is spread across two assets; a further buy pushes gross
    # exposure past 80%.
    p = portfolio(
        cash=D("2000"), positions_value=D("8000"),
        position_values={"EUR": D("4000"), "USD": D("4000")},
    )
    verdict = evaluate(
        proposal(symbol="BTC", quantity=D("0.005"), price=D("40000"), amount=D("200")),
        p, RiskLimits(),
    )
    assert verdict.decision == "REJECT"
    assert any(v.rule == "max_exposure" for v in verdict.violations)


def test_concentration_limit_rejected():
    # Holding 4000 USD of a 10000 portfolio is 40% (at the cap); adding more
    # to that same asset breaches it.
    p = portfolio(
        cash=D("6000"), positions_value=D("4000"),
        position_values={"USD": D("4000")},
    )
    verdict = evaluate(
        proposal(symbol="USD", quantity=D("100"), price=D("1"), amount=D("100")),
        p, RiskLimits(),
    )
    assert verdict.decision == "REJECT"
    assert any(v.rule == "max_concentration" for v in verdict.violations)


def test_drawdown_blocks_new_buys():
    p = portfolio(current_value=D("7500"), peak_value=D("10000"), positions_value=D("0"),
                  cash=D("7500"))
    verdict = evaluate(proposal(quantity=D("0.01"), amount=D("400")), p, RiskLimits())
    assert verdict.decision == "REJECT"
    assert any(v.rule == "max_drawdown" for v in verdict.violations)


def test_drawdown_allows_risk_reducing_sell():
    p = portfolio(
        current_value=D("7500"), peak_value=D("10000"),
        positions_value=D("5000"), position_values={"BTCUSD": D("5000")},
        cash=D("2500"),
    )
    verdict = evaluate(
        proposal(action="SELL", quantity=D("0.05"), amount=D("2000")), p, RiskLimits()
    )
    # A risk-reducing sell is never blocked by the drawdown rule.
    assert verdict.passed
    assert not any(v.rule == "max_drawdown" for v in verdict.violations)


def test_daily_loss_blocks_buys():
    p = portfolio(day_start_value=D("10000"), current_value=D("9400"), cash=D("9400"))
    verdict = evaluate(proposal(quantity=D("0.01"), amount=D("400")), p, RiskLimits())
    assert verdict.decision == "REJECT"
    assert any(v.rule == "max_daily_loss" for v in verdict.violations)


def test_frequency_limit_rejected():
    p = portfolio(trades_today=20)
    verdict = evaluate(proposal(), p, RiskLimits())
    assert verdict.decision == "REJECT"
    assert any(v.rule == "max_frequency" for v in verdict.violations)


def test_insufficient_capital_rejected():
    p = portfolio(cash=D("100"), current_value=D("100"), peak_value=D("100"))
    verdict = evaluate(proposal(quantity=D("0.01"), amount=D("400")), p, RiskLimits())
    assert verdict.decision == "REJECT"
    assert any(v.rule == "account_limits" for v in verdict.violations)


def test_reserved_capital_is_not_spendable():
    p = portfolio(cash=D("10000"), reserved_capital=D("9500"))
    verdict = evaluate(proposal(quantity=D("0.02"), amount=D("800")), p, RiskLimits())
    assert verdict.decision == "REJECT"
    assert any(v.rule == "account_limits" for v in verdict.violations)


def test_unauthorized_strategy_stage_rejected():
    limits = RiskLimits(authorized_strategies=("PAPER_TRADING",))
    verdict = evaluate(
        proposal(strategy_stage="IDEA"), portfolio(), limits
    )
    assert verdict.decision == "REJECT"
    assert any(v.rule == "strategy_authorization" for v in verdict.violations)


def test_large_impact_produces_warning_not_rejection():
    # 1500 on a 10000 portfolio is 15% -> warning only.
    verdict = evaluate(
        proposal(quantity=D("0.0375"), amount=D("1500")), portfolio(), RiskLimits()
    )
    assert verdict.decision == "WARN"
    assert verdict.passed
    assert any(w.rule == "large_impact" for w in verdict.warnings)


def test_is_high_impact_threshold():
    assert is_high_impact(proposal(amount=D("1000")), portfolio()) is True
    assert is_high_impact(proposal(amount=D("500")), portfolio()) is False


def test_is_high_impact_transfer_always_true():
    p = proposal(action="TRANSFER", symbol="EUR", amount=D("1"))
    assert is_high_impact(p, portfolio()) is True


def test_verdict_serialises():
    verdict = evaluate(proposal(symbol="DOGEUSD"), portfolio(), RiskLimits())
    payload = verdict.to_dict()
    assert payload["decision"] == "REJECT"
    assert payload["violations"][0]["rule"] == "asset_allowlist"
