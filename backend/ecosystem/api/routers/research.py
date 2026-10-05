"""Research questions, hypotheses and the Overseer control loop."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ecosystem.api.deps import current_user, get_session, require_operator
from ecosystem.api.schemas import OverseerCycleRequest
from ecosystem.db.models.identity import User
from ecosystem.db.models.research import Experiment, Hypothesis, ResearchQuestion
from ecosystem.services import experiments
from ecosystem.services.agents import overseer

router = APIRouter(prefix="/research", tags=["research"])


def _question_row(q: ResearchQuestion) -> dict:
    return {
        "id": str(q.id),
        "question": q.question,
        "rationale": q.rationale,
        "status": q.status,
        "priority": float(q.priority),
        "generation_number": q.generation_number,
        "created_by": q.created_by,
        "created_at": q.created_at.isoformat() if q.created_at else None,
        "closed_at": q.closed_at.isoformat() if q.closed_at else None,
        "conclusion": q.conclusion,
    }


@router.get("/questions")
async def list_questions(
    status: str | None = None,
    generation_number: int | None = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    stmt = select(ResearchQuestion).order_by(ResearchQuestion.created_at.desc())
    if status:
        stmt = stmt.where(ResearchQuestion.status == status)
    if generation_number is not None:
        stmt = stmt.where(ResearchQuestion.generation_number == generation_number)
    rows = list((await session.execute(stmt)).scalars().all())
    return {"questions": [_question_row(q) for q in rows]}


@router.get("/questions/{question_id}")
async def question_detail(
    question_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    question = await session.get(ResearchQuestion, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="Research question not found.")

    hypotheses = (
        await session.execute(
            select(Hypothesis)
            .where(Hypothesis.question_id == question_id)
            .order_by(Hypothesis.created_at)
        )
    ).scalars().all()
    experiment_rows = (
        await session.execute(
            select(Experiment)
            .where(Experiment.hypothesis_id.in_([h.id for h in hypotheses] or [uuid.uuid4()]))
            .order_by(Experiment.created_at.desc())
        )
    ).scalars().all()

    return {
        "question": _question_row(question),
        "hypotheses": [
            {
                "id": str(h.id),
                "statement": h.statement,
                "rationale": h.rationale,
                "status": h.status,
                "prior_confidence": float(h.prior_confidence),
                "posterior_confidence": float(h.posterior_confidence)
                if h.posterior_confidence is not None
                else None,
                "agent_id": str(h.agent_id) if h.agent_id else None,
                "generation_number": h.generation_number,
            }
            for h in hypotheses
        ],
        "experiments": [
            {
                "id": str(e.id),
                "title": e.title,
                "stage": e.stage.value,
                "status": e.status.value,
                "agent_id": str(e.agent_id) if e.agent_id else None,
                "score": None,
            }
            for e in experiment_rows
        ],
    }


@router.get("/experiments")
async def list_experiments(
    agent_id: uuid.UUID | None = None,
    strategy_id: uuid.UUID | None = None,
    generation_number: int | None = None,
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    return {
        "experiments": await experiments.list_experiments(
            session,
            agent_id=agent_id,
            strategy_id=strategy_id,
            generation_number=generation_number,
            limit=limit,
        )
    }


@router.get("/experiments/{experiment_id}")
async def experiment_detail(
    experiment_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    experiment = await session.get(Experiment, experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail="Experiment not found.")
    rows = await experiments.list_experiments(session, limit=1)
    for row in rows:
        if row["id"] == str(experiment_id):
            return row
    # Fall back to a direct lookup if it was outside the recent window.
    from ecosystem.db.models.research import ExperimentResult

    result = (
        await session.execute(
            select(ExperimentResult)
            .where(ExperimentResult.experiment_id == experiment_id)
            .order_by(ExperimentResult.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return experiments._experiment_dict(experiment, result)


@router.get("/observation")
async def observation(
    generation_number: int | None = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    return (await overseer.observe(session, generation_number)).as_dict()


@router.post("/cycle")
async def run_cycle(
    body: OverseerCycleRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_operator),
) -> dict:
    """Run one full Overseer loop: observe, plan, assign, evaluate, learn."""
    return await overseer.run_cycle(
        session, objective=body.objective, run_assignments=body.run_assignments
    )


@router.post("/characteristics")
async def characteristics(
    generation_number: int | None = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_operator),
) -> dict:
    number = generation_number or await overseer.current_generation_number(session)
    return await overseer.identify_useful_characteristics(session, number)
