"""The Overseer.

The Overseer runs the loop:

    OBSERVE -> ANALYZE -> IDENTIFY PROBLEM -> FORM HYPOTHESIS ->
    ASSIGN EXPERIMENT -> RECEIVE RESULTS -> EVALUATE -> LEARN -> PLAN NEXT

What it can do: inspect and compare agents, assign research, create
experiments, request backtests, inspect failures, propose mutations, manage
generations, set research priorities.

What it cannot do, and how that is enforced rather than merely stated:

* disable risk            -> it has no handle on the risk service's limits
* modify evaluation       -> scoring lives in `ecosystem.domain.evaluation`
* approve its own actions -> approvals require a `User` row (see approval.decide)
* erase history           -> audit/system/transaction tables are append-only
* change accounting       -> balances change only via `accounting.apply_*`
* grant credentials       -> it holds no credentials and no user session

The Overseer is a coordinator object, not a privileged actor.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ecosystem.config import get_settings
from ecosystem.db.models.agents import Agent, Generation
from ecosystem.db.models.enums import (
    AgentStatus,
    EventCategory,
    ExperimentStage,
    GenerationStatus,
    MemoryKind,
    StrategyStage,
)
from ecosystem.db.models.research import (
    Experiment,
    ExperimentResult,
    ResearchQuestion,
)
from ecosystem.db.models.strategies import Strategy
from ecosystem.services import events, memory, risk, strategies
from ecosystem.services.agents import investor, prompts
from ecosystem.services.llm import LLMRequest, OverseerPlan, get_scheduler


@dataclass
class Observation:
    generation_number: int
    agents: list[dict] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    recent_events: list[str] = field(default_factory=list)
    performance: dict = field(default_factory=dict)
    emergency_stop: bool = False

    def as_dict(self) -> dict:
        return {
            "generation_number": self.generation_number,
            "agents": self.agents,
            "open_questions": self.open_questions,
            "recent_events": self.recent_events,
            "performance": self.performance,
            "emergency_stop": self.emergency_stop,
        }


async def observe(session: AsyncSession, generation_number: int | None = None) -> Observation:
    """OBSERVE: build a bounded picture of the ecosystem."""
    if generation_number is None:
        generation_number = await current_generation_number(session)

    roster = await investor.active_agents(session, generation_number)
    agent_rows = []
    performance: dict[str, dict] = {}
    for agent in roster:
        snap = await investor.performance_snapshot(session, agent)
        performance[agent.codename] = snap
        agent_rows.append(
            {
                "codename": agent.codename,
                "specialization": agent.specialization,
                "status": agent.status.value,
                "objective": agent.current_objective,
                "score": snap.get("score", 0.0),
                "experiments": snap.get("experiments", 0),
            }
        )

    questions_result = await session.execute(
        select(ResearchQuestion)
        .where(ResearchQuestion.status == "OPEN")
        .order_by(ResearchQuestion.priority.desc())
        .limit(10)
    )
    open_questions = [q.question for q in questions_result.scalars().all()]

    from ecosystem.db.models.identity import SystemEvent

    events_result = await session.execute(
        select(SystemEvent).order_by(SystemEvent.created_at.desc()).limit(12)
    )
    recent_events = [
        f"[{e.category.value}] {e.message}" for e in events_result.scalars().all()
    ]

    return Observation(
        generation_number=generation_number,
        agents=agent_rows,
        open_questions=open_questions,
        recent_events=recent_events,
        performance=performance,
        emergency_stop=risk.emergency_stop_active(),
    )


async def current_generation_number(session: AsyncSession) -> int:
    result = await session.execute(select(func.max(Generation.number)))
    value = result.scalar()
    return int(value) if value is not None else get_settings().initial_generation


async def plan(
    session: AsyncSession, observation: Observation, objective: str | None = None
) -> tuple[ResearchQuestion, OverseerPlan]:
    """ANALYZE + IDENTIFY PROBLEM + FORM HYPOTHESIS: ask for a plan."""
    objective = objective or (
        f"Find a robust, cost-adjusted edge for generation {observation.generation_number}."
    )
    roster = await investor.active_agents(session, observation.generation_number)
    prompt = prompts.overseer_prompt(
        objective=objective,
        agents=roster,
        performance=observation.performance,
        open_questions=observation.open_questions,
        recent_events=observation.recent_events,
    )
    scheduler = get_scheduler()
    response = await scheduler.submit(
        LLMRequest(
            system=prompts.SYSTEM_OVERSEER,
            prompt=scheduler.bound_context(prompt),
            temperature=0.6,
            json_mode=True,
        ),
        priority=1,  # the Overseer is served first
    )
    plan_obj = _parse_plan(response.text, roster, objective)

    question = ResearchQuestion(
        question=plan_obj.research_question.question,
        rationale=plan_obj.research_question.rationale,
        status="OPEN",
        priority=plan_obj.research_question.priority,
        generation_number=observation.generation_number,
        created_by="OVERSEER",
    )
    session.add(question)
    await session.flush()

    await events.emit(
        session,
        EventCategory.RESEARCH,
        "overseer_planned",
        f"Overseer opened research question: {question.question}",
        source="overseer",
        payload={"question_id": str(question.id), "objective": plan_obj.objective},
    )
    await events.audit(
        session,
        action="overseer.plan",
        resource_type="research_question",
        resource_id=str(question.id),
        outcome="SUCCESS",
        actor_type="OVERSEER",
        detail={"objective": plan_obj.objective},
    )
    return question, plan_obj


def _parse_plan(text: str, roster: list[Agent], objective: str) -> OverseerPlan:
    """Validate the plan. If the model is unusable, fall back to a deterministic
    plan derived from the roster, so the loop always makes progress."""
    try:
        data = json.loads(text)
        plan = OverseerPlan.model_validate(data)
        if plan.research_question.question.strip():
            return plan
    except Exception:
        pass

    return OverseerPlan(
        objective=objective,
        research_question={
            "question": (
                f"Which measurable signal on "
                f"{', '.join(sorted({a.specialization for a in roster}))} "
                f"survives backtest, out-of-sample and robustness testing?"
            ),
            "rationale": "Deterministic fallback plan; the model output was unusable.",
            "priority": 0.6,
        },
        assignments=[
            {
                "codename": a.codename,
                "specialization": a.specialization,
                "task": f"Investigate a {a.specialization} signal.",
                "priority": 0.6,
            }
            for a in roster
        ],
        notes="Fallback plan generated because the model response failed validation.",
    )


async def assign_research(
    session: AsyncSession,
    *,
    question: ResearchQuestion,
    assignments: list[dict] | None = None,
) -> list[dict]:
    """ASSIGN EXPERIMENT: hand the question to the roster and run the cycles.

    Disagreement is preserved: every active agent gets a turn and no consensus
    is forced.
    """
    roster = await investor.active_agents(session, question.generation_number)
    by_codename = {a.codename: a for a in roster}
    assignment_list = assignments or [
        {"codename": a.codename, "task": f"Investigate a {a.specialization} signal."}
        for a in roster
    ]

    results: list[dict] = []
    for assignment in assignment_list[: get_settings().max_active_agents]:
        agent = by_codename.get(assignment.get("codename"))
        if agent is None:
            continue
        agent.research_priorities = sorted(
            set(list(agent.research_priorities) + [assignment.get("task", question.question)])
        )[-5:]
        await session.flush()
        try:
            result = await investor.run_research_cycle(
                session,
                agent,
                question=question,
                objective=assignment.get("task") or question.question,
            )
        except Exception as exc:  # one agent failing must not stop the cycle
            await events.emit(
                session,
                EventCategory.AGENT,
                "agent_cycle_failed",
                f"{agent.codename} cycle failed: {exc}",
                source="overseer",
                severity="ERROR",
                agent_id=agent.id,
            )
            result = {"agent": agent.codename, "verdict": "ERROR", "error": str(exc)}
        results.append(result)
    return results


async def run_cycle(
    session: AsyncSession, *, objective: str | None = None, run_assignments: bool = True
) -> dict:
    """One full Overseer loop. Returns a report for the API and the UI."""
    observation = await observe(session)
    question, plan_obj = await plan(session, observation, objective)

    results: list[dict] = []
    if run_assignments:
        results = await assign_research(
            session, question=question, assignments=plan_obj.assignments
        )

    evaluation = await evaluate_generation(session, observation.generation_number)

    # LEARN: record what the cycle concluded, including disagreement.
    verdicts = [r.get("verdict") for r in results]
    summary_text = (
        f"Cycle complete. {verdicts.count('ELIGIBLE_FOR_PAPER_TRADING')} strategies "
        f"eligible for paper trading out of {len(results)} agents. "
        f"Question: {question.question}"
    )
    await events.emit(
        session,
        EventCategory.RESEARCH,
        "overseer_cycle_complete",
        summary_text,
        source="overseer",
        payload={"question_id": str(question.id), "results": results},
    )

    return {
        "observation": observation.as_dict(),
        "objective": plan_obj.objective,
        "question": {
            "id": str(question.id),
            "question": question.question,
            "priority": float(question.priority),
        },
        "assignments": plan_obj.assignments,
        "results": results,
        "evaluation": evaluation,
        "notes": plan_obj.notes,
    }


async def evaluate_generation(
    session: AsyncSession, generation_number: int
) -> dict:
    """EVALUATE: rank the roster by graded evidence."""
    roster = await investor.active_agents(session, generation_number)
    snapshots: dict[str, dict] = {}
    scores: list[float] = []
    for agent in roster:
        snap = await investor.performance_snapshot(session, agent)
        snapshots[agent.codename] = snap
        scores.append(snap.get("score", 0.0))

    median = _median(scores) if scores else 0.0
    ranking = sorted(
        (
            {
                "codename": agent.codename,
                "specialization": agent.specialization,
                "score": snapshots[agent.codename].get("score", 0.0),
                "experiments": snapshots[agent.codename].get("experiments", 0),
                "reliability": snapshots[agent.codename].get("reliability", 0.0),
                "relative": snapshots[agent.codename].get("score", 0.0) - median,
            }
            for agent in roster
        ),
        key=lambda row: row["score"],
        reverse=True,
    )
    return {
        "generation_number": generation_number,
        "median_score": round(median, 6),
        "ranking": ranking,
        "leader": ranking[0]["codename"] if ranking else None,
    }


async def compare_agents(
    session: AsyncSession, generation_number: int | None = None
) -> dict:
    """Compare agents on the evidence they have generated so far."""
    generation_number = generation_number or await current_generation_number(session)
    evaluation = await evaluate_generation(session, generation_number)
    roster = await investor.active_agents(session, generation_number)

    details: list[dict] = []
    for agent in roster:
        result = await session.execute(
            select(ExperimentResult)
            .join(Experiment, Experiment.id == ExperimentResult.experiment_id)
            .where(Experiment.agent_id == agent.id)
            .order_by(ExperimentResult.score.desc())
            .limit(1)
        )
        best = result.scalar_one_or_none()
        failures_result = await session.execute(
            select(func.count())
            .select_from(ExperimentResult)
            .join(Experiment, Experiment.id == ExperimentResult.experiment_id)
            .where(Experiment.agent_id == agent.id, ExperimentResult.passed.is_(False))
        )
        details.append(
            {
                "codename": agent.codename,
                "specialization": agent.specialization,
                "best_score": float(best.score) if best else 0.0,
                "failed_experiments": int(failures_result.scalar() or 0),
                "strategy_count": await _strategy_count(session, agent.id),
            }
        )
    evaluation["details"] = details
    return evaluation


async def _strategy_count(session: AsyncSession, agent_id: uuid.UUID) -> int:
    result = await session.execute(
        select(func.count()).select_from(Strategy).where(Strategy.agent_id == agent_id)
    )
    return int(result.scalar() or 0)


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


async def propose_mutation(
    session: AsyncSession,
    *,
    strategy: Strategy,
    reason: str,
) -> dict:
    """Propose (not perform) a mutation. The mutation is created, but the
    resulting strategy still has to pass the deterministic pipeline again."""
    version = await strategies.active_version(session, strategy)
    if version is None:
        return {"verdict": "NO_ACTIVE_VERSION"}

    # A bounded, structured mutation: shift the lookback periods outward. The
    # Overseer proposes the direction; the engine decides whether it helped.
    mutated_rules = []
    for rule in version.entry_rules:
        new_rule = dict(rule)
        if isinstance(new_rule.get("period"), (int, float)):
            new_rule["period"] = max(2, int(round(new_rule["period"] * 1.15)))
        mutated_rules.append(new_rule)

    new_version = await strategies.mutate_strategy(
        session,
        strategy=strategy,
        parent_version=version,
        parameters={**version.parameters, "mutated_by": "OVERSEER"},
        entry_rules=mutated_rules,
        exit_rules=version.exit_rules,
        mutation_reason=f"Overseer mutation: {reason}",
    )
    await events.emit(
        session,
        EventCategory.RESEARCH,
        "mutation_proposed",
        f"Overseer proposed mutation of '{strategy.name}' v{version.version}.",
        source="overseer",
        payload={"strategy_id": str(strategy.id), "version_id": str(new_version.id)},
    )
    return {
        "verdict": "MUTATION_CREATED",
        "strategy_id": str(strategy.id),
        "version_id": str(new_version.id),
        "version": new_version.version,
    }


async def identify_useful_characteristics(
    session: AsyncSession, generation_number: int | None = None
) -> dict:
    """Extract what the successful strategies have in common.

    This feeds trait inheritance in the Generation Manager.
    """
    generation_number = generation_number or await current_generation_number(session)
    result = await session.execute(
        select(Strategy).where(Strategy.generation_number == generation_number)
    )
    all_strategies = list(result.scalars().all())

    successful: list[dict] = []
    failed: list[dict] = []
    for strategy in all_strategies:
        best = await strategies.best_result(session, strategy.id)
        record = {
            "strategy_id": str(strategy.id),
            "name": strategy.name,
            "agent_id": str(strategy.agent_id) if strategy.agent_id else None,
            "stage": strategy.stage.value,
            "score": float(best.score) if best else 0.0,
        }
        if strategy.stage in (
            StrategyStage.PAPER_TRADING,
            StrategyStage.EVALUATION,
        ):
            successful.append(record)
        elif strategy.stage in (StrategyStage.REJECTED, StrategyStage.RETIRED):
            failed.append(record)

    return {
        "generation_number": generation_number,
        "successful": sorted(successful, key=lambda r: r["score"], reverse=True),
        "failed": sorted(failed, key=lambda r: r["score"], reverse=True),
        "characteristics": _derive_characteristics(successful, failed),
    }


def _derive_characteristics(successful: list[dict], failed: list[dict]) -> list[str]:
    """Turn raw counts into short, actionable observations."""
    characteristics: list[str] = []
    if successful:
        characteristics.append(
            f"{len(successful)} strategies reached paper-trading eligibility; "
            f"best score {successful[0]['score']:.3f}."
        )
    if failed:
        characteristics.append(
            f"{len(failed)} strategies were rejected; their rule sets are retained "
            f"as negative knowledge."
        )
    if not successful and failed:
        characteristics.append(
            "No strategy survived this generation. Broaden the search rather than "
            "tuning the same rule set."
        )
    return characteristics


async def register_relationships(session: AsyncSession, generation_number: int) -> int:
    """Record disagreement and specialisation overlap for the Network view.

    Agents that share an asset universe are related as 'peers'; agents whose
    scores diverge sharply are related as 'disagrees'.
    """
    roster = await investor.active_agents(session, generation_number)
    created = 0
    for i, a in enumerate(roster):
        for b in roster[i + 1 :]:
            shared = set(a.asset_preferences) & set(b.asset_preferences)
            if shared:
                await investor.relate(
                    session,
                    source_agent_id=a.id,
                    target_agent_id=b.id,
                    relation="peers",
                    weight=min(1.0, len(shared) / 4.0),
                    note=f"Shared assets: {', '.join(sorted(shared))}",
                )
                created += 1
    return created
