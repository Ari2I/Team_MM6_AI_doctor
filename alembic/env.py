"""Запуск миграций с настройками PostgreSQL из app.config."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.config import get_settings
from app.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Когда появятся модели, импортировать их здесь для регистрации метаданных.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Сгенерировать SQL миграций без соединения с PostgreSQL."""
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Применить миграции через подключение с общими настройками приложения."""
    connectable = create_engine(
        get_settings().database_url,
        poolclass=pool.NullPool,
        hide_parameters=True,
    )
    try:
        with connectable.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)

            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
