"""Role -> permission policy. Every protected endpoint checks a permission server-side; data scoping
(which employees an actor may see) is applied separately via ``scope_for``."""

from __future__ import annotations

import enum
from dataclasses import dataclass

from app.models.enums import Role


class Permission(enum.StrEnum):
    SELF_SERVICE = "self_service"
    ATTENDANCE_READ_TEAM = "attendance:read_team"
    ATTENDANCE_READ_ALL = "attendance:read_all"
    CORRECTION_REVIEW = "correction:review"
    CORRECTION_APPLY_DIRECT = "correction:apply_direct"
    # Remove an invalid record (e.g. checked in while not at work). Audited with a full snapshot.
    ATTENDANCE_DELETE = "attendance:delete"
    REPORTS_READ = "reports:read"
    USERS_READ = "users:read"
    USERS_MANAGE = "users:manage"
    SCHEDULES_READ = "schedules:read"
    SCHEDULES_MANAGE = "schedules:manage"
    HOLIDAYS_MANAGE = "holidays:manage"
    BIOMETRICS_MANAGE = "biometrics:manage"
    BIOMETRICS_DELETE_ANY = "biometrics:delete_any"
    VERIFICATIONS_READ = "verifications:read"
    AUDIT_READ = "audit:read"
    SETTINGS_MANAGE = "settings:manage"


# Two roles: employees use self-service only; administrators (who also do the HR and team-lead work)
# hold every permission. Endpoints still check individual permissions, so a role can be split again later.
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.EMPLOYEE: frozenset({Permission.SELF_SERVICE}),
    Role.ADMIN: frozenset(Permission),
}


def has_permission(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]


@dataclass(frozen=True, slots=True)
class DataScope:
    """Which employees' attendance data an actor may read.

    ``all_users`` True means unrestricted; otherwise access is limited to ``self`` plus, for managers,
    their direct reports (resolved by the query layer through ``manager_id``)."""

    all_users: bool
    include_direct_reports: bool


def scope_for(role: Role) -> DataScope:
    if has_permission(role, Permission.ATTENDANCE_READ_ALL):
        return DataScope(all_users=True, include_direct_reports=True)
    if has_permission(role, Permission.ATTENDANCE_READ_TEAM):
        return DataScope(all_users=False, include_direct_reports=True)
    return DataScope(all_users=False, include_direct_reports=False)
