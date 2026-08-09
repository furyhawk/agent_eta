"""add projects, project_members, and project_id columns

Revision ID: 0028_add_projects_and_project_id
Revises: 0027_user_magic_link_epoch
Create Date: 2026-08-09

Adds the multi-user Project/ProjectMember tables (DeepAgents projects) and
wires the ``project_id`` column onto ``conversations``, ``channel_bots`` and
``channel_sessions`` so the SQLAlchemy model layer and the database stay in
sync. The model layer already references these columns; without this revision
the app fails at startup with "column channel_bots.project_id does not exist".
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

revision = "0028_add_projects_and_project_id"
down_revision = "0027_user_magic_link_epoch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- projects ---
    op.create_table(
        "projects",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "owner_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("image", sa.String(255), nullable=False),
        sa.Column("container_name", sa.String(255), nullable=False),
        sa.Column("volume_name", sa.String(255), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("container_name", name="projects_container_name_key"),
        sa.UniqueConstraint("volume_name", name="projects_volume_name_key"),
    )
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])

    # --- project_members (composite PK) ---
    op.create_table(
        "project_members",
        sa.Column(
            "project_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("invited_by", PG_UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    # --- conversations.project_id (CASCADE) ---
    op.add_column(
        "conversations",
        sa.Column("project_id", PG_UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "conversations_project_id_fkey",
        "conversations",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_conversations_project_id", "conversations", ["project_id"])

    # --- channel_bots.project_id (SET NULL) ---
    op.add_column(
        "channel_bots",
        sa.Column("project_id", PG_UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "channel_bots_project_id_fkey",
        "channel_bots",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_channel_bots_project_id", "channel_bots", ["project_id"])

    # --- channel_sessions.project_id (SET NULL) ---
    op.add_column(
        "channel_sessions",
        sa.Column("project_id", PG_UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "channel_sessions_project_id_fkey",
        "channel_sessions",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_channel_sessions_project_id", "channel_sessions", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_channel_sessions_project_id", table_name="channel_sessions")
    op.drop_constraint("channel_sessions_project_id_fkey", "channel_sessions", type_="foreignkey")
    op.drop_column("channel_sessions", "project_id")

    op.drop_index("ix_channel_bots_project_id", table_name="channel_bots")
    op.drop_constraint("channel_bots_project_id_fkey", "channel_bots", type_="foreignkey")
    op.drop_column("channel_bots", "project_id")

    op.drop_index("ix_conversations_project_id", table_name="conversations")
    op.drop_constraint("conversations_project_id_fkey", "conversations", type_="foreignkey")
    op.drop_column("conversations", "project_id")

    op.drop_table("project_members")
    op.drop_table("projects")
