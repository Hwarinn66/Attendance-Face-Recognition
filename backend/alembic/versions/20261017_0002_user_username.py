"""optional username as an alternative sign-in identifier

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-17 14:09:51.945366
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(length=64), nullable=True))
    op.create_unique_constraint(op.f("uq_users_username"), "users", ["username"])
    op.create_check_constraint(
        op.f("ck_users_username_format"),
        "users",
        "username = lower(username) AND position('@' in username) = 0",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_users_username_format"), "users", type_="check")
    op.drop_constraint(op.f("uq_users_username"), "users", type_="unique")
    op.drop_column("users", "username")
