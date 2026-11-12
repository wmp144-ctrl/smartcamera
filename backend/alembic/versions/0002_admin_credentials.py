"""single admin credential

Revision ID: 0002_admin_auth
Revises: 0001_initial
Create Date: 2026-11-12 21:10:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.models.base import UTCDateTime

revision: str = "0002_admin_auth"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("salt", sa.String(length=128), nullable=False),
        sa.Column(
            "params",
            sa.String(length=64),
            nullable=False,
            server_default="scrypt:16384:8:1",
        ),
        sa.Column("session_secret", sa.String(length=128), nullable=False),
        sa.Column("password_changed_at", UTCDateTime(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_credentials")),
    )


def downgrade() -> None:
    op.drop_table("admin_credentials")