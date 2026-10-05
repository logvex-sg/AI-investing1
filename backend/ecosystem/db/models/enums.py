"""Enumerations shared across the persistence and domain layers.

Stored as native PostgreSQL enums so the database itself rejects invalid
states rather than trusting application code alone.
"""

from __future__ import annotations

import enum


class AgentStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    STOPPED = "STOPPED"
    ARCHIVED = "ARCHIVED"
    QUARANTINED = "QUARANTINED"


class GenerationStatus(str, enum.Enum):
    PLANNED = "PLANNED"
    ACTIVE = "ACTIVE"
    EVALUATING = "EVALUATING"
    COMPLETED = "COMPLETED"
    ARCHIVED = "ARCHIVED"


class StrategyStage(str, enum.Enum):
    IDEA = "IDEA"
    HYPOTHESIS = "HYPOTHESIS"
    IMPLEMENTATION = "IMPLEMENTATION"
    BACKTEST = "BACKTEST"
    OUT_OF_SAMPLE = "OUT_OF_SAMPLE"
    ROBUSTNESS = "ROBUSTNESS"
    PAPER_TRADING = "PAPER_TRADING"
    EVALUATION = "EVALUATION"
    RETIRED = "RETIRED"
    REJECTED = "REJECTED"


class ExperimentStatus(str, enum.Enum):
    PROPOSED = "PROPOSED"
    ASSIGNED = "ASSIGNED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ExperimentStage(str, enum.Enum):
    BACKTEST = "BACKTEST"
    OUT_OF_SAMPLE = "OUT_OF_SAMPLE"
    ROBUSTNESS = "ROBUSTNESS"
    PAPER_TRADING = "PAPER_TRADING"


class MemoryKind(str, enum.Enum):
    OBSERVATION = "OBSERVATION"
    RESEARCH = "RESEARCH"
    HYPOTHESIS = "HYPOTHESIS"
    DECISION = "DECISION"
    EXPERIMENT_RESULT = "EXPERIMENT_RESULT"
    STRATEGY_VERSION = "STRATEGY_VERSION"
    DISCOVERY = "DISCOVERY"
    FAILURE = "FAILURE"
    LESSON = "LESSON"
    MARKET_CONDITION = "MARKET_CONDITION"
    RELATIONSHIP = "RELATIONSHIP"


class AccountKind(str, enum.Enum):
    TREASURY_PROTECTED = "TREASURY_PROTECTED"
    TREASURY_TRADING = "TREASURY_TRADING"
    TREASURY_OPERATING = "TREASURY_OPERATING"
    TREASURY_PROFIT = "TREASURY_PROFIT"
    AGENT = "AGENT"


class ProposalAction(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    TRANSFER = "TRANSFER"


class ProposalKind(str, enum.Enum):
    TRADE = "TRADE"
    TRANSFER = "TRANSFER"


class ProposalStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PENDING_RISK = "PENDING_RISK"
    RISK_REJECTED = "RISK_REJECTED"
    PENDING_ACCOUNTING = "PENDING_ACCOUNTING"
    ACCOUNTING_REJECTED = "ACCOUNTING_REJECTED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    CANCELLED = "CANCELLED"
    BLOCKED_EMERGENCY = "BLOCKED_EMERGENCY"


class RiskDecision(str, enum.Enum):
    PASS = "PASS"
    WARN = "WARN"
    REJECT = "REJECT"


class RiskSeverity(str, enum.Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class ExecutionStatus(str, enum.Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    REJECTED = "REJECTED"


class AdapterKind(str, enum.Enum):
    SIMULATED = "SIMULATED"
    PAPER_TRADING = "PAPER_TRADING"
    TESTNET = "TESTNET"
    PRODUCTION = "PRODUCTION"


class EventCategory(str, enum.Enum):
    AI = "AI"
    AGENT = "AGENT"
    RESEARCH = "RESEARCH"
    TRADE = "TRADE"
    RISK = "RISK"
    ACCOUNT = "ACCOUNT"
    SYSTEM = "SYSTEM"
    SECURITY = "SECURITY"


class UserRole(str, enum.Enum):
    ADMIN = "ADMIN"
    OPERATOR = "OPERATOR"
    OBSERVER = "OBSERVER"


class AuditOutcome(str, enum.Enum):
    SUCCESS = "SUCCESS"
    DENIED = "DENIED"
    ERROR = "ERROR"
