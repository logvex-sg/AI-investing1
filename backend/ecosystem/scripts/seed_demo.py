"""Seed a demonstration world.

    python -m ecosystem.scripts.seed_demo

Creates the admin user, generation 1 with its eight agents, loads synthetic
market data for every supported symbol, and runs one full Overseer research
cycle so the control center has something real to display. Idempotent: running
it twice does not duplicate data.
"""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import func, select

from ecosystem.db.models.enums import UserRole
from ecosystem.db.models.identity import User
from ecosystem.db.session import session_scope
from ecosystem.services import auth, generation, market_data
from ecosystem.services.agents import overseer

DEFAULT_USERNAME = "operator"
DEFAULT_PASSWORD = "ecosystem-demo"


async def main() -> None:
    username = os.environ.get("ECOSYSTEM_DEMO_USER", DEFAULT_USERNAME)
    password = os.environ.get("ECOSYSTEM_DEMO_PASSWORD", DEFAULT_PASSWORD)

    async with session_scope() as session:
        existing = (
            await session.execute(select(func.count()).select_from(User))
        ).scalar_one()
        if not existing:
            await auth.create_user(
                session,
                username=username,
                password=password,
                display_name="Ecosystem Operator",
                role=UserRole.ADMIN,
            )
            print(f"created user {username!r} (password {password!r})")
        else:
            print("user already present; leaving it untouched")

        result = await generation.bootstrap(session)
        print(f"generation: {result}")

        for symbol in market_data.supported_symbols():
            series, bars = await market_data.get_or_load_series(session, symbol, "1d", 500)
            print(f"market data: {symbol} {len(bars.bars)} bars ({series.volatility_regime})")

    async with session_scope() as session:
        report = await overseer.run_cycle(session)
        print(
            "overseer cycle:",
            report.get("objective"),
            "->",
            len(report.get("results", [])),
            "assignments",
        )

    print("demo world ready.")


if __name__ == "__main__":
    asyncio.run(main())
