from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, engine_from_config, pool, text

from app.config import settings
from app.database import BaseDbModel

config = context.config
config.set_main_option("sqlalchemy.url", settings.db_uri)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = BaseDbModel.metadata

# `None` keeps alembic's default - an unqualified `alembic_version` - so a
# `public` install migrates exactly as upstream does. Otherwise the version table
# lives in the schema too, and never in whatever `public` holds.
SCHEMA = settings.db_schema
VERSION_TABLE_SCHEMA = None if SCHEMA == "public" else SCHEMA


def ensure_schema(connection: Connection) -> None:
    """Create the schema if it is missing, and only then.

    `CREATE SCHEMA IF NOT EXISTS` checks CREATE on the database before it checks
    whether the schema exists, so a role that owns a pre-created schema - and is
    allowed to create nothing else - would fail it on every run.
    """
    if VERSION_TABLE_SCHEMA is None:
        return
    exists = connection.execute(text("SELECT 1 FROM pg_namespace WHERE nspname = :s"), {"s": SCHEMA}).scalar()
    if not exists:
        connection.execute(text(f'CREATE SCHEMA "{SCHEMA}"'))
    connection.commit()


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema=VERSION_TABLE_SCHEMA,
    )

    with context.begin_transaction():
        if VERSION_TABLE_SCHEMA is not None:
            context.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')
            context.execute(f'SET search_path TO "{SCHEMA}"')
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=settings.db_connect_args,
    )

    with connectable.connect() as connection:
        ensure_schema(connection)
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=VERSION_TABLE_SCHEMA,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
