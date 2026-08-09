"""dedupe active sessions and enforce one active session per refresh token hash

Revision ID: 0029_unique_active_refresh_hash
Revises: 0028_add_projects_and_project_id
Create Date: 2026-08-09

Concurrent ``POST /api/v1/auth/refresh`` calls with the same refresh token used
to all pass validation before any rotation committed, then generate the same
JWT (identical exp/sub/type within the same second) and insert multiple ACTIVE
rows sharing one ``refresh_token_hash``. The next lookup by hash then raised
``MultipleResultsFound`` → HTTP 500, and the loser 401s cascaded into a logout
loop on the frontend.

This revision collapses existing duplicates (keeping the newest active row per
hash) and adds a partial unique index so only one active session can ever hold
a given refresh token hash again.
"""

import sqlalchemy as sa

from alembic import op

revision = "0029_unique_active_refresh_hash"
down_revision = "0028_add_projects_and_project_id"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_sessions_active_refresh_token_hash"


def upgrade() -> None:
    # Collapse any pre-existing duplicate active rows sharing a refresh token
    # hash (produced by the pre-fix rotation race): keep the newest row per
    # hash, deactivate the rest, so the partial unique index can be created.
    op.execute(
        """
        UPDATE sessions AS s
        SET is_active = false
        WHERE s.is_active
          AND s.id NOT IN (
              SELECT DISTINCT ON (refresh_token_hash) id
              FROM sessions
              WHERE is_active
              ORDER BY refresh_token_hash, created_at DESC, id DESC
          )
        """
    )
    op.create_index(
        INDEX_NAME,
        "sessions",
        ["refresh_token_hash"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="sessions")
