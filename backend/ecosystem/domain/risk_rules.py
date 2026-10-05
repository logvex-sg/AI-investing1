"""Deterministic risk rules.

This module is the authority on whether a proposal may proceed. It is pure:
given the same snapshot it always returns the same verdict. No LLM, no
database, no clock. The service layer only loads the snapshot and persists
the verdict this module produces.

Agents cannot modify these rules; limits arrive from configuration and from
the `risk_limits` table, both of which are outside agent authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ecosystem.domain.accounting_math import (
    available_capital,
    concentration_ratio,
    exposure_fraction,
)
from ecosystem.domain.money import ZERO, money, safe_div

# Default allowlist of base assets. Adding an asset is a human decision, not
# an agent one. Trades may be expressed as pairs (BTCUSD) or as the base asset
# itself (BTC); `base_asset` normalises both to the allowlisted form.
DEFAULT_ALLOWLIST: tuple[str, ...] = ("EUR", "USD", "BTC")

_QUOTES = ("USDT", "USDC", "USD", "EUR")


def base_asset(symbol: str) -> str:
    """Reduce a traded symbol to its base asset: BTCUSD -> BTC, EURUSD -> EUR."""
    upper = symbol.upper()
    for quote in _QUOTES:
        if upper.endswith(quote) and len(upper) > len(quote):
            return upper[: -len(quote)]
    return upper


@dataclass(frozen=True)
class RiskLimits:
    """Effective limits for one evaluation."""

    max_position_pct: Decimal = Decimal("0.25")
    max_exposure_pct: Decimal = Decimal("0.80")
    max_drawdown_pct: Decimal = Decimal("0.20")
    max_daily_loss_pct: Decimal = Decimal("0.05")
    max_concentration_pct: Decimal = Decimal("0.40")
    max_trades_per_day: int = 20
    asset_allowlist: tuple[str, ...] = DEFAULT_ALLOWLIST
    authorized_strategies: tuple[str, ...] = ()
    emergency_stop: bool = False


@dataclass(frozen=True)
class ProposalSnapshot:
    """Everything the rules need to judge one proposal."""

    action: str  # BUY | SELL | HOLD | TRANSFER
    symbol: str
    quantity: Decimal
    price: Decimal
    amount: Decimal
    strategy_stage: str | None = None


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Current state of the account the proposal would touch."""

    cash: Decimal
    reserved_capital: Decimal
    positions_value: Decimal
    peak_value: Decimal
    starting_value: Decimal
    current_value: Decimal
    day_start_value: Decimal
    position_values: dict[str, Decimal] = field(default_factory=dict)
    trades_today: int = 0

    def value_if(self, extra_position_value: Decimal) -> Decimal:
        return money(self.current_value + extra_position_value)


@dataclass(frozen=True)
class Violation:
    rule: str
    message: str
    observed: Decimal | None = None
    limit: Decimal | None = None
    severity: str = "CRITICAL"


@dataclass(frozen=True)
class RiskVerdict:
    decision: str  # PASS | WARN | REJECT
    violations: list[Violation] = field(default_factory=list)
    warnings: list[Violation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.decision != "REJECT"

    def to_dict(self) -> dict:
        """JSON-safe summary. Decimals become strings, never floats, so a
        limit can be displayed and audited without rounding drift."""
        return {
            "decision": self.decision,
            "violations": [_violation_dict(v) for v in self.violations],
            "warnings": [_violation_dict(v) for v in self.warnings],
        }


def _violation_dict(violation: Violation) -> dict:
    return {
        "rule": violation.rule,
        "message": violation.message,
        "observed": str(violation.observed) if violation.observed is not None else None,
        "limit": str(violation.limit) if violation.limit is not None else None,
        "severity": violation.severity,
    }


def _buy_exposure_delta(proposal: ProposalSnapshot, portfolio: PortfolioSnapshot) -> Decimal:
    """How much the proposal adds to gross exposure (0 for reducing trades)."""
    if proposal.action == "BUY":
        return money(proposal.quantity * proposal.price)
    return ZERO


def evaluate(
    proposal: ProposalSnapshot,
    portfolio: PortfolioSnapshot,
    limits: RiskLimits,
) -> RiskVerdict:
    """Apply every rule and return an aggregate verdict.

    A single CRITICAL violation rejects. Warnings alone pass but are recorded
    so the approval screen can surface them to the human.
    """
    violations: list[Violation] = []
    warnings: list[Violation] = []

    # 1. Emergency stop dominates everything else.
    if limits.emergency_stop:
        violations.append(
            Violation(
                rule="emergency_shutdown",
                message="Emergency stop is active: new proposals are blocked.",
            )
        )
        return RiskVerdict("REJECT", violations, warnings)

    # 2. Asset allowlist (compared on the base asset).
    if base_asset(proposal.symbol) not in limits.asset_allowlist:
        violations.append(
            Violation(
                rule="asset_allowlist",
                message=f"{proposal.symbol} is not on the asset allowlist.",
            )
        )

    # 3. Strategy authorization: only strategies at an authorized stage may
    #    propose real (paper) execution.
    if limits.authorized_strategies and proposal.strategy_stage is not None:
        if proposal.strategy_stage not in limits.authorized_strategies:
            violations.append(
                Violation(
                    rule="strategy_authorization",
                    message=(
                        f"Strategy stage {proposal.strategy_stage} is not authorized "
                        f"for execution."
                    ),
                )
            )

    # 4. Frequency limit.
    if portfolio.trades_today >= limits.max_trades_per_day:
        violations.append(
            Violation(
                rule="max_frequency",
                message="Daily trade limit reached.",
                observed=Decimal(portfolio.trades_today),
                limit=Decimal(limits.max_trades_per_day),
            )
        )

    # 5. Drawdown ceiling: at or beyond the limit, only risk-reducing trades
    #    are permitted.
    current_dd = (
        money((portfolio.peak_value - portfolio.current_value) / portfolio.peak_value)
        if portfolio.peak_value > 0
        else ZERO
    )
    if current_dd >= limits.max_drawdown_pct and proposal.action == "BUY":
        violations.append(
            Violation(
                rule="max_drawdown",
                message="Maximum drawdown reached: risk-increasing trades are blocked.",
                observed=current_dd,
                limit=limits.max_drawdown_pct,
            )
        )

    # 6. Daily loss ceiling.
    if portfolio.day_start_value > 0:
        day_loss = safe_div(
            portfolio.day_start_value - portfolio.current_value,
            portfolio.day_start_value,
        )
        if day_loss >= limits.max_daily_loss_pct and proposal.action == "BUY":
            violations.append(
                Violation(
                    rule="max_daily_loss",
                    message="Daily loss limit reached: risk-increasing trades are blocked.",
                    observed=money(day_loss),
                    limit=limits.max_daily_loss_pct,
                )
            )

    # 7. Per-position cap, measured after the trade.
    if proposal.action == "BUY":
        existing = portfolio.position_values.get(proposal.symbol, ZERO)
        prospective = money(existing + proposal.quantity * proposal.price)
        projected_total = portfolio.value_if(ZERO)
        position_pct = safe_div(prospective, projected_total)
        if position_pct > limits.max_position_pct:
            violations.append(
                Violation(
                    rule="max_position",
                    message=(
                        f"Position in {proposal.symbol} would be "
                        f"{float(position_pct):.2%} of the portfolio."
                    ),
                    observed=money(position_pct),
                    limit=limits.max_position_pct,
                )
            )

    # 8. Gross exposure cap.
    if proposal.action == "BUY":
        projected_positions = money(
            portfolio.positions_value + _buy_exposure_delta(proposal, portfolio)
        )
        projected_total = portfolio.value_if(ZERO)
        exposure = exposure_fraction(projected_positions, projected_total)
        if exposure > limits.max_exposure_pct:
            violations.append(
                Violation(
                    rule="max_exposure",
                    message=f"Gross exposure would be {float(exposure):.2%}.",
                    observed=exposure,
                    limit=limits.max_exposure_pct,
                )
            )

    # 9. Concentration cap, measured against total portfolio value (cash
    #    included) so that holding a single asset is not automatically a breach.
    if proposal.action == "BUY":
        projected = dict(portfolio.position_values)
        projected[proposal.symbol] = money(
            projected.get(proposal.symbol, ZERO) + proposal.quantity * proposal.price
        )
        projected_total = portfolio.value_if(ZERO)
        concentration = safe_div(max(projected.values(), default=ZERO), projected_total)
        if concentration > limits.max_concentration_pct:
            violations.append(
                Violation(
                    rule="max_concentration",
                    message=f"Concentration would reach {float(concentration):.2%}.",
                    observed=concentration,
                    limit=limits.max_concentration_pct,
                )
            )

    # 10. Account limits: a buy must be affordable with unreserved cash.
    if proposal.action == "BUY":
        spendable = available_capital(portfolio.cash, portfolio.reserved_capital)
        if proposal.amount > spendable:
            violations.append(
                Violation(
                    rule="account_limits",
                    message="Insufficient available capital for this proposal.",
                    observed=money(proposal.amount),
                    limit=spendable,
                )
            )

    # 11. A sell may not exceed the held quantity.
    if proposal.action == "SELL":
        held = portfolio.position_values.get(proposal.symbol)
        if held is None or proposal.quantity * proposal.price > money(held + ZERO) * Decimal("1.0001"):
            # Compare in value terms only as a coarse guard; exact quantity is
            # enforced by the accounting service.
            warnings.append(
                Violation(
                    rule="oversell_check",
                    message="Sell size approaches or exceeds the held position.",
                    severity="WARNING",
                )
            )

    # Advisory warnings, not blocks.
    if proposal.amount > 0 and portfolio.current_value > 0:
        impact = safe_div(proposal.amount, portfolio.current_value)
        if impact > Decimal("0.10"):
            warnings.append(
                Violation(
                    rule="large_impact",
                    message=f"Proposal is {float(impact):.2%} of portfolio value.",
                    observed=money(impact),
                    severity="WARNING",
                )
            )

    if violations:
        decision = "REJECT"
    elif warnings:
        decision = "WARN"
    else:
        decision = "PASS"
    return RiskVerdict(decision, violations, warnings)


def is_high_impact(proposal: ProposalSnapshot, portfolio: PortfolioSnapshot) -> bool:
    """High-impact proposals require an explicit confirmation phrase."""
    if portfolio.current_value <= 0:
        return False
    if proposal.action == "TRANSFER":
        return True
    return safe_div(proposal.amount, portfolio.current_value) >= Decimal("0.10")
