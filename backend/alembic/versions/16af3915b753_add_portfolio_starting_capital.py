"""add portfolio starting_capital and the agents.current_strategy_id FK

Revision ID: 16af3915b753
Revises: 396c81863418
Create Date: 2026-10-03 17:06:54.034177

Two changes:

* `portfolios.starting_capital` records the capital a portfolio began with, so
  original capital is always distinguishable from profit.
* `agents.current_strategy_id` gains the foreign key that the initial migration
  declared with `use_alter=True` but could not emit (the constraint would have
  closed the agents <-> strategies reference cycle). It is added here, after
  both tables exist.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

import ecosystem.db.models.types

revision = '16af3915b753'
down_revision = '396c81863418'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'portfolios',
        sa.Column(
            'starting_capital',
            ecosystem.db.models.types.NumericAsDecimal(precision=24, scale=8),
            nullable=False,
            server_default='0',
        ),
    )
    op.create_foreign_key(
        'fk_agents_current_strategy_id_strategies',
        'agents',
        'strategies',
        ['current_strategy_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint(
        'fk_agents_current_strategy_id_strategies', 'agents', type_='foreignkey'
    )
    op.drop_column('portfolios', 'starting_capital')
