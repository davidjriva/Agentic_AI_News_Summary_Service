"""enable row level security

Revision ID: ebfc671a4078
Revises: 4b6e78bf0665
Create Date: 2026-06-29 21:02:51.131960

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'ebfc671a4078'
down_revision: Union[str, Sequence[str], None] = '4b6e78bf0665'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# All application tables. RLS is enabled with NO policies, which blocks the
# anon/authenticated roles used by Supabase's REST API (PostgREST) entirely.
# The app connects directly to Postgres as the table-owning role, which bypasses
# RLS, so application access is unaffected.
_TABLES = (
    "runs",
    "run_articles",
    "failed_articles",
    "filtered_articles",
    "article_scores",
    "seen_articles",
    # Alembic's own bookkeeping table is also exposed to PostgREST; lock it down too.
    "alembic_version",
)


def upgrade() -> None:
    """Enable Row Level Security on all application tables."""
    for table in _TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    """Disable Row Level Security on all application tables."""
    for table in _TABLES:
        op.execute(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY")
