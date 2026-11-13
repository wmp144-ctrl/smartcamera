"""track trajectory samples

Adds ``tracks.path`` — the sampled movement trajectory used by the object replay
page. Stored as JSON so it works on SQLite and PostgreSQL alike.

Revision ID: 0003_track_path
Revises: 0002_admin_auth
Create Date: 2026-11-13 10:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_track_path"
down_revision: Union[str, None] = "0002_admin_auth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tracks") as batch:
        batch.add_column(sa.Column("path", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tracks") as batch:
        batch.drop_column("path")
