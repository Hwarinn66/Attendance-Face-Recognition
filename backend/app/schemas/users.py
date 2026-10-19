from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import AliasChoices, EmailStr, Field, field_validator

from app.models.enums import BiometricStatus, Role
from app.schemas.common import ORMModel, StrictInput


class UserSummary(ORMModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: Role
    employee_code: str | None
    department: str | None


class UserOut(ORMModel):
    id: uuid.UUID
    email: str
    username: str | None
    full_name: str
    role: Role
    is_active: bool
    must_change_password: bool
    employee_code: str | None
    department: str | None
    position: str | None
    workplace_id: uuid.UUID | None
    manager_id: uuid.UUID | None
    biometric_status: BiometricStatus
    last_login_at: datetime | None
    created_at: datetime


class MeOut(UserOut):
    permissions: list[str]


class LoginIn(StrictInput):
    # Email address or username. Plain string on purpose: login must not act as an email-format oracle.
    # "email" is still accepted as the field name for existing clients.
    login: str = Field(min_length=3, max_length=320, validation_alias=AliasChoices("login", "email"))
    password: str = Field(min_length=1, max_length=256)


class LoginOut(ORMModel):
    user: MeOut
    csrf_token: str


class TokenOut(ORMModel):
    """Sign-in result for native clients: the session token is returned instead of being set as a cookie."""

    access_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth token type name, not a secret
    expires_in: int
    user: MeOut


class ChangePasswordIn(StrictInput):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class SessionOut(ORMModel):
    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    ip_address: str | None
    user_agent: str | None
    current: bool = False


USERNAME_PATTERN = r"^[a-z0-9][a-z0-9._-]{2,63}$"


class _ProfileFields(StrictInput):
    # Sign-in ID (alternative to the email address). Stored lower case; "" clears it.
    username: str | None = Field(default=None, max_length=64)
    employee_code: str | None = Field(default=None, max_length=50)
    department: str | None = Field(default=None, max_length=120)
    position: str | None = Field(default=None, max_length=120)
    workplace_id: uuid.UUID | None = None
    manager_id: uuid.UUID | None = None

    @field_validator("username", mode="before")
    @classmethod
    def _username(cls, v: object) -> object:
        if isinstance(v, str):
            v = v.strip().lower() or None
            if v is not None and not re.fullmatch(USERNAME_PATTERN, v):
                raise ValueError("use 3-64 characters: letters, digits, '.', '_' or '-' (no spaces or '@')")
        return v


class UserCreate(_ProfileFields):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    role: Role = Role.EMPLOYEE
    temporary_password: str = Field(min_length=1, max_length=256)
    # True (default): a temporary password the user must replace at first sign-in.
    must_change_password: bool = True

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.strip().lower()


class UserUpdate(_ProfileFields):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    role: Role | None = None
    is_active: bool | None = None


class PasswordResetIn(StrictInput):
    temporary_password: str = Field(min_length=1, max_length=256)
    # False: the administrator hands over a final password; True: the user must replace it at next sign-in.
    must_change: bool = True
