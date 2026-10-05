"""Experiment service.

Runs the deterministic validation pipeline for a strategy version:

    BACKTEST -> OUT_OF_SAMPLE -> ROBUSTNESS

The engine computes every number; the LLM only reads the results afterwards.
Each stage persists an `ExperimentResult` and feeds a memory entry so the
agent learns from its own evidence.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ecosystem.db.models.enums import (
    EventCategory,
    ExperimentStage,
    ExperimentStatus,
    MemoryKind,
    StrategyStage,
)
from ecosystem.db.models.research import Experiment, ExperimentResult
from ecosystem.db.models.strategies import Strategy, StrategyVersion
from ecosystem.domain.backtest import BacktestSpec, run_backtest
from ecosystem.domain.evaluation import (
    evaluate_backtest,
    out_of_sample_verdict,
    robustness_verdict,
)
from ecosystem.domain.market import Series
from ecosystem.domain.strategy_signals import build_signal_fn
from ecosystem.services import events, market_data, memory, strategies


class ExperimentError(Exception):
    pass


def _default_spec(symbol: str, overrides: dict | None = None) -> BacktestSpec:
    data = {
        "symbol": symbol,
        "initial_capital": 10_000.0,
        "allocation_pct": 0.25,
        "fee_bps": 10.0,
        "slippage_bps": 5.0,
        "seed": 7,
    }
    if overrides:
        data.update({k: v for k, v in overrides.items() if k in data})
    return BacktestSpec(**data)


async def create_experiment(
    session: AsyncSession,
    *,
    title: str,
    description: str,
    strategy: Strategy,
    version: StrategyVersion,
    agent_id: uuid.UUID | None,
    generation_number: int,
    stage: ExperimentStage = ExperimentStage.BACKTEST,
    spec: dict | None = None,
    assigned_by: str = "OVERSEER",
) -> Experiment:
    experiment = Experiment(
        title=title,
        description=description,
        strategy_id=strategy.id,
        strategy_version_id=version.id,
        agent_id=agent_id,
        generation_number=generation_number,
        stage=stage,
        status=ExperimentStatus.PROPOSED,
        spec=spec or {},
        assigned_by=assigned_by,
    )
    session.add(experiment)
    await session.flush()
    await events.emit(
        session,
        EventCategory.RESEARCH,
        "experiment_created",
        f"Experiment '{title}' created for strategy '{strategy.name}'.",
        source="experiments",
        agent_id=agent_id,
        payload={"experiment_id": str(experiment.id), "stage": stage.value},
    )
    return experiment


async def list_experiments(
    session: AsyncSession,
    *,
    agent_id: uuid.UUID | None = None,
    strategy_id: uuid.UUID | None = None,
    generation_number: int | None = None,
    limit: int = 100,
) -> list[dict]:
    """Experiments with their latest result, for the Research and Agent tabs."""
    stmt = select(Experiment).order_by(Experiment.created_at.desc()).limit(limit)
    if agent_id is not None:
        stmt = stmt.where(Experiment.agent_id == agent_id)
    if strategy_id is not None:
        stmt = stmt.where(Experiment.strategy_id == strategy_id)
    if generation_number is not None:
        stmt = stmt.where(Experiment.generation_number == generation_number)
    experiments = list((await session.execute(stmt)).scalars().all())

    out: list[dict] = []
    for experiment in experiments:
        result = (
            await session.execute(
                select(ExperimentResult)
                .where(ExperimentResult.experiment_id == experiment.id)
                .order_by(ExperimentResult.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        out.append(_experiment_dict(experiment, result))
    return out


def _experiment_dict(experiment: Experiment, result: ExperimentResult | None) -> dict:
    return {
        "id": str(experiment.id),
        "title": experiment.title,
        "description": experiment.description,
        "strategy_id": str(experiment.strategy_id) if experiment.strategy_id else None,
        "strategy_version_id": str(experiment.strategy_version_id)
        if experiment.strategy_version_id
        else None,
        "agent_id": str(experiment.agent_id) if experiment.agent_id else None,
        "generation_number": experiment.generation_number,
        "stage": experiment.stage.value,
        "status": experiment.status.value,
        "priority": float(experiment.priority),
        "assigned_by": experiment.assigned_by,
        "started_at": experiment.started_at.isoformat() if experiment.started_at else None,
        "completed_at": experiment.completed_at.isoformat()
        if experiment.completed_at
        else None,
        "error": experiment.error,
        "result": (
            {
                "id": str(result.id),
                "stage": result.stage.value,
                "metrics": result.metrics,
                "score": float(result.score),
                "passed": result.passed,
                "trades_count": result.trades_count,
                "equity_curve": result.equity_curve,
                "notes": result.notes,
                "created_at": result.created_at.isoformat() if result.created_at else None,
            }
            if result
            else None
        ),
    }


async def run_experiment(
    session: AsyncSession, experiment: Experiment, series: Series | None = None
) -> ExperimentResult:
    """Execute a single experiment stage and persist its result."""
    experiment.status = ExperimentStatus.RUNNING
    experiment.started_at = datetime.now(timezone.utc)
    await session.flush()

    strategy = await session.get(Strategy, experiment.strategy_id)
    version = await session.get(StrategyVersion, experiment.strategy_version_id)
    if strategy is None or version is None:
        experiment.status = ExperimentStatus.FAILED
        experiment.error = "strategy or version missing"
        raise ExperimentError("strategy or version missing")

    spec_data = experiment.spec or {}
    symbol = spec_data.get("symbol") or (
        strategy.asset_universe[0] if strategy.asset_universe else "BTCUSD"
    )
    interval = spec_data.get("interval", "1d")
    limit = int(spec_data.get("limit", 500))

    if series is None:
        _, series = await market_data.get_or_load_series(session, symbol, interval, limit)

    spec = _default_spec(symbol, spec_data)
    signal_fn = build_signal_fn(version.entry_rules, version.exit_rules)

    try:
        result = run_backtest(series, signal_fn, spec)
    except Exception as exc:  # a malformed rule set must not crash the run
        experiment.status = ExperimentStatus.FAILED
        experiment.error = str(exc)
        experiment.completed_at = datetime.now(timezone.utc)
        await events.emit(
            session,
            EventCategory.RESEARCH,
            "experiment_failed",
            f"Experiment '{experiment.title}' failed: {exc}",
            source="experiments",
            severity="ERROR",
            agent_id=experiment.agent_id,
            payload={"experiment_id": str(experiment.id)},
        )
        raise ExperimentError(str(exc)) from exc

    benchmark = await _benchmark_return(session, symbol, interval, limit)
    evaluation = evaluate_backtest(result.metrics, result.equity_curve, benchmark)

    row = ExperimentResult(
        created_at=datetime.now(timezone.utc),
        experiment_id=experiment.id,
        stage=experiment.stage,
        metrics=result.metrics,
        equity_curve=result.equity_curve[-500:],
        trades_count=len(result.trades),
        passed=evaluation.passed,
        score=evaluation.score,
        notes="; ".join(evaluation.notes) or None,
        engine_version=result.engine_version,
        deterministic_seed=spec.seed,
    )
    session.add(row)

    experiment.status = ExperimentStatus.COMPLETED
    experiment.completed_at = datetime.now(timezone.utc)
    await session.flush()

    await _advance_stage(session, strategy, experiment.stage, evaluation.passed)
    await _record_memory(session, experiment, row, evaluation)

    await events.emit(
        session,
        EventCategory.RESEARCH,
        "experiment_completed",
        f"Experiment '{experiment.title}' scored {evaluation.score:.3f} "
        f"({'passed' if evaluation.passed else 'failed'}).",
        source="experiments",
        agent_id=experiment.agent_id,
        payload={
            "experiment_id": str(experiment.id),
            "score": evaluation.score,
            "passed": evaluation.passed,
        },
    )
    return row


async def run_full_validation(
    session: AsyncSession,
    *,
    strategy: Strategy,
    version: StrategyVersion,
    agent_id: uuid.UUID | None,
    generation_number: int,
    symbol: str | None = None,
    interval: str = "1d",
    limit: int = 500,
) -> dict:
    """Run backtest, out-of-sample and robustness stages in sequence.

    Returns a summary describing what survived. A strategy that fails a stage
    is not discarded: the failure is recorded as knowledge.
    """
    symbol = symbol or (strategy.asset_universe[0] if strategy.asset_universe else "BTCUSD")
    _, series = await market_data.get_or_load_series(session, symbol, interval, limit)

    backtest = await create_experiment(
        session,
        title=f"{strategy.name} — backtest",
        description=f"In-sample backtest of {symbol} over {limit} bars.",
        strategy=strategy,
        version=version,
        agent_id=agent_id,
        generation_number=generation_number,
        stage=ExperimentStage.BACKTEST,
        spec={"symbol": symbol, "interval": interval, "limit": limit},
    )
    in_sample = await run_experiment(session, backtest, series)

    summary: dict = {
        "strategy_id": str(strategy.id),
        "symbol": symbol,
        "in_sample_score": in_sample.score,
        "in_sample_passed": in_sample.passed,
        "stages": {"backtest": {"score": in_sample.score, "passed": in_sample.passed}},
    }
    if not in_sample.passed:
        summary["verdict"] = "REJECTED_IN_SAMPLE"
        return summary

    # Out-of-sample: the last 30% of bars, which the in-sample run never saw.
    split = int(len(series.bars) * 0.7)
    oos_series = series.slice(split, len(series.bars))
    oos_experiment = await create_experiment(
        session,
        title=f"{strategy.name} — out-of-sample",
        description=f"Out-of-sample window: {len(oos_series.bars)} bars.",
        strategy=strategy,
        version=version,
        agent_id=agent_id,
        generation_number=generation_number,
        stage=ExperimentStage.OUT_OF_SAMPLE,
        spec={"symbol": symbol, "interval": interval, "limit": limit},
    )
    oos = await run_experiment(session, oos_experiment, oos_series)
    oos_passed, degradation = out_of_sample_verdict(in_sample.score, oos.score)
    summary["stages"]["out_of_sample"] = {
        "score": oos.score,
        "passed": oos_passed,
        "degradation": round(degradation, 6),
    }
    summary["out_of_sample_passed"] = oos_passed
    if not oos_passed:
        summary["verdict"] = "REJECTED_OUT_OF_SAMPLE"
        await strategies.set_stage(
            session, strategy, StrategyStage.REJECTED,
            reason=f"Degraded {degradation:.0%} out of sample.",
        )
        return summary

    # Robustness: scale the lookback periods up and down and re-run. A fragile
    # edge that only works at one exact parameter value is not a real edge.
    perturbation_scales = [0.8, 1.2, 1.0]
    perturbed_scores: list[float] = []
    for index, scale in enumerate(perturbation_scales):
        params = {**version.parameters, "period_scale": scale}
        perturbed_version = await strategies.mutate_strategy(
            session,
            strategy=strategy,
            parent_version=version,
            parameters=params,
            entry_rules=_perturb_rules(version.entry_rules, scale),
            exit_rules=_perturb_rules(version.exit_rules, scale),
            mutation_reason=f"Robustness perturbation {index + 1} (period scale {scale}).",
        )
        experiment = await create_experiment(
            session,
            title=f"{strategy.name} — robustness {index + 1}",
            description=f"Perturbed run {index + 1} of {len(perturbation_scales)}.",
            strategy=strategy,
            version=perturbed_version,
            agent_id=agent_id,
            generation_number=generation_number,
            stage=ExperimentStage.ROBUSTNESS,
            spec={"symbol": symbol, "interval": interval, "limit": limit},
        )
        perturbed_result = await run_experiment(session, experiment, series)
        perturbed_scores.append(perturbed_result.score)
        # Perturbation versions are throwaway probes, not lineage. The base
        # version stays the active one.
        perturbed_version.is_active = False
    await strategies.set_stage(
        session, strategy, StrategyStage.ROBUSTNESS,
        reason="Robustness probes completed.",
    )
    version.is_active = True
    strategy.current_version = version.version

    robust_passed, worst_drop, notes = robustness_verdict(in_sample.score, perturbed_scores)
    summary["stages"]["robustness"] = {
        "scores": perturbed_scores,
        "passed": robust_passed,
        "worst_drop": round(worst_drop, 6),
        "notes": notes,
    }
    summary["robustness_passed"] = robust_passed

    if robust_passed:
        await strategies.set_stage(
            session, strategy, StrategyStage.PAPER_TRADING,
            reason="Survived backtest, out-of-sample and robustness checks.",
        )
        summary["verdict"] = "ELIGIBLE_FOR_PAPER_TRADING"
    else:
        await strategies.set_stage(
            session, strategy, StrategyStage.REJECTED,
            reason=f"Fragile under perturbation ({worst_drop:.0%} score drop).",
        )
        summary["verdict"] = "REJECTED_ROBUSTNESS"

    return summary


async def _advance_stage(
    session: AsyncSession,
    strategy: Strategy,
    stage: ExperimentStage,
    passed: bool,
) -> None:
    """Move the strategy to the stage its evidence supports."""
    if not passed:
        if strategy.stage in (StrategyStage.IDEA, StrategyStage.HYPOTHESIS):
            await strategies.set_stage(
                session, strategy, StrategyStage.REJECTED,
                reason=f"Failed {stage.value}.",
            )
        return
    progression = {
        ExperimentStage.BACKTEST: StrategyStage.OUT_OF_SAMPLE,
        ExperimentStage.OUT_OF_SAMPLE: StrategyStage.ROBUSTNESS,
        ExperimentStage.ROBUSTNESS: StrategyStage.PAPER_TRADING,
        ExperimentStage.PAPER_TRADING: StrategyStage.EVALUATION,
    }
    target = progression.get(stage)
    if target is not None and strategy.stage in (
        StrategyStage.IDEA,
        StrategyStage.HYPOTHESIS,
        StrategyStage.IMPLEMENTATION,
        StrategyStage.BACKTEST,
        StrategyStage.OUT_OF_SAMPLE,
        StrategyStage.ROBUSTNESS,
        StrategyStage.PAPER_TRADING,
    ):
        await strategies.set_stage(session, strategy, target)


async def _record_memory(
    session: AsyncSession,
    experiment: Experiment,
    result: ExperimentResult,
    evaluation,
) -> None:
    if experiment.agent_id is None:
        return
    kind = MemoryKind.DISCOVERY if evaluation.passed else MemoryKind.FAILURE
    title = (
        f"{'Validated' if evaluation.passed else 'Failed'} {experiment.stage.value} "
        f"experiment"
    )
    content = (
        f"Experiment '{experiment.title}' scored {evaluation.score:.3f}. "
        f"Return {result.metrics.get('total_return', 0):.2%}, "
        f"drawdown {result.metrics.get('max_drawdown', 0):.2%}, "
        f"{result.trades_count} trades. "
        + ("; ".join(evaluation.notes) if evaluation.notes else "")
    )
    await memory.remember(
        session,
        agent_id=experiment.agent_id,
        kind=kind,
        title=title,
        content=content,
        importance=0.7 if evaluation.passed else 0.65,
        tags=["experiment", experiment.stage.value.lower()],
        context={"experiment_id": str(experiment.id)},
        source_type="experiment_result",
        source_id=result.id,
        generation_number=experiment.generation_number,
    )


async def _benchmark_return(
    session: AsyncSession, symbol: str, interval: str, limit: int
) -> float:
    """Buy-and-hold return for the same window, used as the benchmark."""
    try:
        _, series = await market_data.get_or_load_series(session, symbol, interval, limit)
    except Exception:
        return 0.0
    if len(series.bars) < 2 or series.bars[0].close <= 0:
        return 0.0
    return series.bars[-1].close / series.bars[0].close - 1.0


def _perturb_rules(rules: list[dict], scale: float) -> list[dict]:
    """Scale every lookback period in a rule set by `scale`."""
    shifted = []
    for rule in rules:
        new_rule = dict(rule)
        if "period" in new_rule and isinstance(new_rule["period"], (int, float)):
            new_rule["period"] = max(2, int(round(new_rule["period"] * scale)))
        shifted.append(new_rule)
    return shifted
