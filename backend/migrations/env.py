from alembic import context
from pathlib import Path
from dotenv import load_dotenv

# Load local configuration without replacing values supplied by the process
# environment. The file's contents are never logged or echoed.
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

from app.database import create_database_engine, get_migration_database_url
from app.db_models import Base


config = context.config
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = get_migration_database_url()
    if url is None:
        raise RuntimeError("Migration database is not configured. Set SKILLFAIR_MIGRATION_DATABASE_URL before running Alembic.")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = get_migration_database_url()
    if url is None:
        raise RuntimeError("Migration database is not configured. Set SKILLFAIR_MIGRATION_DATABASE_URL before running Alembic.")
    engine = create_database_engine(url)
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                compare_server_default=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
