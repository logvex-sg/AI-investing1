"""Central configuration.

All tunables live here so that deterministic services (risk, accounting,
execution) read their limits from one auditable place rather than from
scattered literals. Secrets are read from the environment only.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ECOSYSTEM_",
        env_file=(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "development"
    database_url: str = "postgresql+asyncpg://ecosystem:ecosystem@127.0.0.1:5432/ecosystem"
    secret_key: str = "insecure-development-key"

    # ---- LLM ----
    llm_provider: str = "mock"  # ollama | mock
    ollama_base_url: str = "http://127.0.0.1:11434"
    overseer_model: str = "qwen2.5:7b-instruct"
    agent_model: str = "qwen2.5:7b-instruct"
    coding_model: str = "qwen2.5-coder:7b-instruct"
    llm_max_context_tokens: int = 4096
    llm_max_concurrency: int = 1
    llm_request_timeout: float = 120.0

    # ---- Execution adapters (deny by default) ----
    enable_testnet_adapter: bool = False
    enable_production_adapter: bool = False

    # ---- Risk limits (EUR / fractions) ----
    risk_max_position_pct: float = 0.25
    risk_max_exposure_pct: float = 0.80
    risk_max_drawdown_pct: float = 0.20
    risk_max_daily_loss_pct: float = 0.05
    risk_max_concentration_pct: float = 0.40
    risk_max_trades_per_day: int = 20

    # ---- Treasury ----
    treasury_total_capital: float = 10_000.0
    treasury_protected_pct: float = 0.40
    treasury_trading_pct: float = 0.40
    treasury_operating_pct: float = 0.15
    treasury_profit_pct: float = 0.05

    # ---- Ecosystem ----
    max_active_agents: int = 8
    initial_generation: int = 1
    base_currency: str = "EUR"

    @field_validator("llm_provider")
    @classmethod
    def _known_provider(cls, v: str) -> str:
        allowed = {"ollama", "mock"}
        if v not in allowed:
            raise ValueError(f"llm_provider must be one of {sorted(allowed)}")
        return v

    @property
    def treasury_splits(self) -> dict[str, float]:
        splits = {
            "protected": self.treasury_protected_pct,
            "trading": self.treasury_trading_pct,
            "operating": self.treasury_operating_pct,
            "profit": self.treasury_profit_pct,
        }
        total = sum(splits.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Treasury splits must sum to 1.0, got {total}")
        return splits


@lru_cache
def get_settings() -> Settings:
    return Settings()
