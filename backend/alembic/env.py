from __future__ import annotations

import os

from sqlalchemy import create_engine, pool

import app.models  # noqa: F401 - registers all tables
from alembic import context
from app.db.base import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    # ALEMBIC_DATABASE_URL lets tests migrate a dedicated database without touching DATABASE_URL.
    url = os.environ.get("ALEMBIC_DATABASE_URL")
    if url:
        return url
    from app.core.config import get_settings  # noqa: PLC0415

    return get_settings().database_url.get_secret_value()


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, compare_type=True, transaction_per_migration=True
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
