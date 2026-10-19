"""Operational commands: ``python -m app.cli <command>``.

create-admin     create the first administrator (prompts for a password; never a default)
seed-dev         development-only sample workplace, shift and users (refused unless APP_ENV=development)
dev-set-login    development-only: give an existing user a username and a password that skips the policy
check-models     load every configured model and report availability (exit 1 if verification unavailable)
purge            apply data-retention rules
rotate-template-keys   re-encrypt all templates with the active key
"""

from __future__ import annotations

import argparse
import getpass
import sys
from datetime import date, time

from sqlalchemy import select

from app.biometrics.crypto import SealedTemplate
from app.biometrics.provider import OnnxVisionProvider
from app.biometrics.runtime import build_runtime
from app.core.config import get_settings
from app.core.security import hash_password, password_policy_errors
from app.db.session import init_engine, session_scope
from app.models import BiometricTemplate, Shift, ShiftAssignment, User, Workplace
from app.models.enums import AuditCategory, Role
from app.services import audit, retention
from app.services.context import SYSTEM_CONTEXT


def _password(prompt: str, email: str, provided: str | None) -> str:
    pw = provided or getpass.getpass(prompt)
    errors = password_policy_errors(pw, email=email)
    if errors:
        sys.exit("password rejected: " + "; ".join(errors))
    return pw


def create_admin(email: str, name: str, password: str | None) -> None:
    email = email.strip().lower()
    pw = _password("Administrator password: ", email, password)
    with session_scope() as db:
        if db.scalar(select(User).where(User.email == email)):
            sys.exit("a user with this email already exists")
        user = User(email=email, full_name=name, role=Role.ADMIN, password_hash=hash_password(pw))
        db.add(user)
        db.flush()
        audit.add(
            db,
            category=AuditCategory.ADMIN,
            action="user.created",
            ctx=SYSTEM_CONTEXT,
            target_type="user",
            target_id=user.id,
            details={"via": "cli", "role": "admin"},
        )
    print(f"administrator {email} created")


def seed_dev() -> None:
    settings = get_settings()
    if settings.app_env != "development":
        sys.exit("seed-dev is only allowed when APP_ENV=development")
    if not settings.dev_seed_admin_email or settings.dev_seed_admin_password is None:
        sys.exit("set DEV_SEED_ADMIN_EMAIL and DEV_SEED_ADMIN_PASSWORD (a strong password of your choice)")
    pw = settings.dev_seed_admin_password.get_secret_value()
    base_email = settings.dev_seed_admin_email.lower()
    if password_policy_errors(pw, email=base_email):
        sys.exit("DEV_SEED_ADMIN_PASSWORD does not meet the password policy")
    local, _, domain = base_email.partition("@")
    with session_scope() as db:
        if db.scalar(select(Workplace).where(Workplace.name == "Head Office (dev)")):
            print("development data already present")
            return
        wp = Workplace(name="Head Office (dev)", timezone="Asia/Jakarta", address="Development sample")
        db.add(wp)
        db.flush()
        shift = Shift(
            workplace_id=wp.id,
            name="Office hours",
            start_time=time(8, 0),
            end_time=time(17, 0),
            check_in_opens_before_min=60,
            check_in_closes_after_min=60,
            grace_min=15,
            check_out_opens_before_min=60,
            check_out_closes_after_min=120,
            break_min=60,
            workdays_mask=0b0011111,
        )
        db.add(shift)
        db.flush()
        people = [
            ("admin", "Dev Administrator", Role.ADMIN),
            ("employee", "Dev Employee", Role.EMPLOYEE),
        ]
        created: dict[str, User] = {}
        for suffix, name, role in people:
            email = base_email if suffix == "admin" else f"{local}+{suffix}@{domain}"
            u = User(
                email=email,
                full_name=name,
                role=role,
                password_hash=hash_password(pw),
                workplace_id=wp.id,
                employee_code=f"DEV-{suffix.upper()}",
            )
            db.add(u)
            created[suffix] = u
        db.flush()
        created["employee"].manager_id = created["admin"].id
        for u in created.values():
            db.add(ShiftAssignment(user_id=u.id, shift_id=shift.id, effective_from=date(2026, 1, 1)))
        audit.add(db, category=AuditCategory.ADMIN, action="dev.seeded", ctx=SYSTEM_CONTEXT)
    print("seeded development workplace, shift and users:")
    for suffix, _, role in people:
        print(f"  {role.value:9s} {base_email if suffix == 'admin' else f'{local}+{suffix}@{domain}'}")
    print("all use DEV_SEED_ADMIN_PASSWORD")


def dev_set_login(email: str, username: str, password: str) -> None:
    """Temporary local credentials (e.g. ``Admin123`` / ``Admin``). Never available outside development."""
    settings = get_settings()
    if settings.app_env != "development":
        sys.exit("dev-set-login is only allowed when APP_ENV=development")
    email, username = email.strip().lower(), username.strip().lower()
    if not 3 <= len(username) <= 64 or "@" in username:
        sys.exit("username must be 3-64 characters and must not contain '@'")
    if not password:
        sys.exit("password must not be empty")
    with session_scope() as db:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            sys.exit(f"no user with email {email}")
        taken = db.scalar(select(User).where(User.username == username, User.id != user.id))
        if taken is not None:
            sys.exit(f"username {username} is already used by {taken.email}")
        user.username = username
        # Deliberately bypasses password_policy_errors: weak, temporary development credentials only.
        user.password_hash = hash_password(password)
        user.must_change_password = False
        audit.add(
            db,
            category=AuditCategory.ADMIN,
            action="dev.login_set",
            ctx=SYSTEM_CONTEXT,
            target_type="user",
            target_id=user.id,
            details={"username": username, "password_policy": "bypassed (development only)"},
        )
    print(f"{email}: username {username} set; password policy bypassed (development only)")


def check_models() -> None:
    settings = get_settings()
    provider = OnnxVisionProvider(settings)
    ok = True
    for s in provider.statuses():
        state = "available" if s.available else f"UNAVAILABLE ({s.error})"
        print(f"{s.role:10s} {s.model_id or '-':32s} {state}  license={s.license or '-'}")
        if s.role in ("detector", "recognizer") and not s.available:
            ok = False
        if s.role == "pad" and settings.pad_mode == "required" and not s.available:
            ok = False
    print("verification:", "available" if ok else "UNAVAILABLE (fails closed)")
    sys.exit(0 if ok else 1)


def purge() -> None:
    with session_scope() as db:
        result = retention.purge(db, get_settings())
    for k, v in result.items():
        print(f"{k:32s} {v}")


def rotate_keys() -> None:
    settings = get_settings()
    runtime = build_runtime(settings)
    cipher = runtime.cipher
    n = 0
    with session_scope() as db:
        for t in db.scalars(select(BiometricTemplate).with_for_update()):
            if t.key_id == cipher.active_key_id:
                continue
            emb = cipher.open(
                SealedTemplate(t.key_id, t.nonce, t.ciphertext),
                template_id=t.id,
                user_id=t.user_id,
                model_name=t.model_name,
                dim=t.embedding_dim,
            )
            sealed = cipher.seal(emb, template_id=t.id, user_id=t.user_id, model_name=t.model_name)
            t.key_id, t.nonce, t.ciphertext = sealed.key_id, sealed.nonce, sealed.ciphertext
            n += 1
        audit.add(
            db,
            category=AuditCategory.SECURITY,
            action="biometric.keys_rotated",
            ctx=SYSTEM_CONTEXT,
            details={"templates": n, "active_key_id": cipher.active_key_id},
        )
    runtime.shutdown()
    print(f"re-encrypted {n} template(s) with key '{cipher.active_key_id}'")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-admin")
    p.add_argument("--email", required=True)
    p.add_argument("--name", default="Administrator")
    p.add_argument("--password", help="non-interactive use only; prefer the prompt")
    sub.add_parser("seed-dev")
    p = sub.add_parser("dev-set-login")
    p.add_argument("--email", required=True, help="existing account")
    p.add_argument("--username", required=True)
    p.add_argument("--password", required=True)
    sub.add_parser("check-models")
    sub.add_parser("purge")
    sub.add_parser("rotate-template-keys")
    args = parser.parse_args(argv)
    settings = get_settings()
    settings.validate_for_environment()
    if args.cmd != "check-models":
        init_engine(settings)
    {
        "create-admin": lambda: create_admin(args.email, args.name, args.password),
        "seed-dev": seed_dev,
        "dev-set-login": lambda: dev_set_login(args.email, args.username, args.password),
        "check-models": check_models,
        "purge": purge,
        "rotate-template-keys": rotate_keys,
    }[args.cmd]()


if __name__ == "__main__":
    main()
