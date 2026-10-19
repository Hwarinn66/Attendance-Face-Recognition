"""merge the hr and manager roles into admin (roles are now employee and admin)

Existing hr/manager accounts become administrators, as the role merge requires; their sessions are
revoked so the new role takes effect on the next sign-in (like any role change). Downgrade widens the
constraint again but cannot tell which administrators used to be hr or manager.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-17 15:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE auth_sessions SET revoked_at = now(), revoked_reason = 'role_changed' "
        "WHERE revoked_at IS NULL AND user_id IN (SELECT id FROM users WHERE role IN ('hr', 'manager'))"
    )
    op.execute("UPDATE users SET role = 'admin' WHERE role IN ('hr', 'manager')")
    op.drop_constraint(op.f("ck_users_role"), "users", type_="check")
    op.create_check_constraint(op.f("ck_users_role"), "users", "role IN ('employee', 'admin')")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_users_role"), "users", type_="check")
    op.create_check_constraint(op.f("ck_users_role"), "users", "role IN ('employee', 'manager', 'hr', 'admin')")
