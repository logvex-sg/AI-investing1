"""Import every model so Alembic and metadata.create_all see them all."""

from ecosystem.db.models.agents import (
    Agent,
    AgentEvent,
    AgentMemory,
    AgentRelationship,
    Generation,
)
from ecosystem.db.models.governance import (
    ApprovalRequest,
    ExecutionRecord,
    RiskEvent,
    RiskLimit,
    TradeProposal,
    TransferProposal,
)
from ecosystem.db.models.identity import AuditLog, SystemEvent, User
from ecosystem.db.models.market import MarketBar, MarketDataSeries
from ecosystem.db.models.portfolio import (
    Account,
    Balance,
    PerformanceMetric,
    Portfolio,
    Position,
    ProfitRecord,
    Transaction,
)
from ecosystem.db.models.research import (
    Benchmark,
    Experiment,
    ExperimentResult,
    Hypothesis,
    ResearchQuestion,
)
from ecosystem.db.models.strategies import Strategy, StrategyVersion

__all__ = [
    "Account",
    "Agent",
    "AgentEvent",
    "AgentMemory",
    "AgentRelationship",
    "ApprovalRequest",
    "AuditLog",
    "Balance",
    "Benchmark",
    "ExecutionRecord",
    "Experiment",
    "ExperimentResult",
    "Generation",
    "Hypothesis",
    "MarketBar",
    "MarketDataSeries",
    "PerformanceMetric",
    "Portfolio",
    "Position",
    "ProfitRecord",
    "ResearchQuestion",
    "RiskEvent",
    "RiskLimit",
    "Strategy",
    "StrategyVersion",
    "SystemEvent",
    "TradeProposal",
    "Transaction",
    "TransferProposal",
    "User",
]
