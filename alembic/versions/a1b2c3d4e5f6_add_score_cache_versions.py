"""add score cache version columns

Revision ID: a1b2c3d4e5f6
Revises: 5c6a06c70a64
Create Date: 2026-07-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '5c6a06c70a64'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('article_scores', sa.Column('triage_version', sa.Text(), nullable=True))
    op.add_column('article_scores', sa.Column('score_version', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('article_scores', 'score_version')
    op.drop_column('article_scores', 'triage_version')
