"""Загрузка настроек PostgreSQL из переменных окружения и файла .env."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    """Параметры подключения к PostgreSQL без хранения секретов в коде."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    postgres_host: str = "localhost"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = "vrach"
    postgres_user: str = "vrach"
    postgres_password: SecretStr

    @property
    def database_url(self) -> URL:
        """Собрать URL с корректной обработкой специальных символов в пароле."""
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    """Загрузить и закешировать настройки процесса."""
    return Settings()
