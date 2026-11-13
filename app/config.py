"""Загрузка настроек PostgreSQL из переменных окружения и файла .env."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

DISCLAIMER = "Результат анализа носит рекомендательный характер и требует проверки человеком."


class AccessSettings(BaseSettings):
    """Возможности разработки, выключенные при отсутствии настроек."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    enable_api_docs: bool = False
    enable_application_list: bool = False


def get_access_settings() -> AccessSettings:
    return AccessSettings()


# TODO: уточнить у аналитика названия категорий для отображения.
CATEGORY_LABELS = {
    "A": "Жизненно необходимые",
    "B": "Сопутствующая терапия",
    "V": "Сомнительные/опциональные",
    "undetermined": "Не определена",
}

TRAFFIC_LIGHT_COLORS = {
    "green": "C6EFCE",
    "yellow": "FFEB9C",
    "red": "FFC7CE",
    "undetermined": "D9D9D9",
}

REVIEW_REASON_LABELS = {
    "DRUG_NOT_FOUND": "Препарат не найден в справочнике.",
    "DRUG_AMBIGUOUS": "Препарат сопоставлен неоднозначно.",
    "DIAGNOSIS_NOT_FOUND": "Диагноз не найден в справочнике.",
    "NO_RULE_FOR_PAIR": "Для пары препарат — диагноз нет правила.",
    "LOW_CONFIDENCE": "Недостаточная уверенность сопоставления.",
    "CONFLICTING_CATEGORIES": "Получены разные категории для диагнозов препарата.",
}


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


class ValidationSettings(BaseSettings):
    """Настройки проверки файлов, не зависящие от подключения к БД."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    max_excel_size_bytes: int = Field(default=10 * 1024 * 1024, gt=0)


@lru_cache
def get_validation_settings() -> ValidationSettings:
    """Загрузить и закешировать настройки проверки файлов."""
    return ValidationSettings()


class MatchingSettings(BaseSettings):
    """Пороги нечёткого поиска; значения требуют подтверждения аналитика."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # TODO: уточнить у аналитика пороги автоматического принятия и кандидатов.
    drug_auto_accept_threshold: float = Field(default=92, ge=0, le=100)
    drug_candidate_threshold: float = Field(default=75, ge=0, le=100)
    diagnosis_auto_accept_threshold: float = Field(default=92, ge=0, le=100)
    diagnosis_candidate_threshold: float = Field(default=75, ge=0, le=100)


@lru_cache
def get_matching_settings() -> MatchingSettings:
    return MatchingSettings()


class TrafficLightSettings(BaseSettings):
    """Пороги светофора; значения требуют подтверждения аналитика."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # TODO: уточнить у аналитика остальные правила жёлтого и красного.
    green_category_a_threshold: float = Field(default=80.0, ge=0, le=100)
    undetermined_share_threshold: float = Field(default=50.0, ge=0, le=100)


@lru_cache
def get_traffic_light_settings() -> TrafficLightSettings:
    return TrafficLightSettings()


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    provider: str = "template"


@lru_cache
def get_llm_settings() -> LLMSettings:
    return LLMSettings()
