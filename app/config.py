"""Загрузка настроек PostgreSQL из переменных окружения и файла .env."""

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

DISCLAIMER = "Результат анализа носит рекомендательный характер и требует проверки человеком."


class AccessSettings(BaseSettings):
    """Возможности разработки, выключенные при отсутствии настроек."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    enable_api_docs: bool = Field(
        default=False,
        validation_alias=AliasChoices("DOCS_ENABLED", "ENABLE_API_DOCS", "enable_api_docs"),
    )
    enable_application_list: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "LIST_ENDPOINT_ENABLED", "ENABLE_APPLICATION_LIST", "enable_application_list"
        ),
    )


def get_access_settings() -> AccessSettings:
    return AccessSettings()


# TODO: уточнить у аналитика названия категорий для отображения.
CATEGORY_LABELS = {
    "А": "Жизненно необходимые",
    "Б": "Сопутствующая терапия",
    "В": "Сомнительные/опциональные",
    "undetermined": "Не определена",
}

TRAFFIC_LIGHT_COLORS = {
    "green": "C6EFCE",
    "yellow": "FFEB9C",
    "red": "FFC7CE",
    "undetermined": "D9D9D9",
}

REVIEW_REASON_LABELS = {
    "AI_INPUT_INCOMPLETE": "Для анализа недостаточно исходных данных.",
    "AI_UNAVAILABLE": "ИИ временно недоступен; требуется проверка специалистом.",
    "AI_TIMEOUT": "ИИ не успел завершить обработку; требуется проверка специалистом.",
    "AI_INVALID_RESPONSE": "Не удалось подтвердить результат ИИ; требуется проверка специалистом.",
    "AI_PRELIMINARY": "Категория предложена ИИ и требует подтверждения специалистом.",
    "AI_REVIEW_REQUIRED": "Результат ИИ требует проверки специалистом.",
    "DRUG_NOT_FOUND": "Препарат не найден в справочнике.",
    "DRUG_AMBIGUOUS": "Препарат сопоставлен неоднозначно.",
    "DIAGNOSIS_NOT_FOUND": "Диагноз не найден в справочнике.",
    "NO_RULE_FOR_PAIR": "Для пары препарат — диагноз нет правила.",
    "LOW_CONFIDENCE": "Недостаточная уверенность сопоставления.",
    "CONFLICTING_CATEGORIES": "Получены разные категории для диагнозов препарата.",
}

MODEL_ERROR_LABELS = {
    "MODEL_NOT_CONFIGURED": "Подключение к ИИ не настроено. Обратитесь к администратору.",
    "MODEL_SDK_MISSING": "Компонент ИИ недоступен. Обратитесь к администратору.",
    "MODEL_UNAVAILABLE": "ИИ временно недоступен. Требуется проверка специалистом.",
    "GIGACHAT_TIMEOUT": "ИИ не успел завершить обработку. Требуется проверка специалистом.",
    "GIGACHAT_TLS_ERROR": "Не удалось установить защищённое соединение с ИИ.",
    "GIGACHAT_UNAVAILABLE": "Сервис ИИ временно недоступен.",
    "GIGACHAT_AUTH_FAILED": "Не удалось подключиться к ИИ. Обратитесь к администратору.",
    "GIGACHAT_FORBIDDEN": "Нет доступа к сервису ИИ. Обратитесь к администратору.",
    "GIGACHAT_RATE_LIMIT": "Сервис ИИ перегружен. Повторите проверку позднее.",
    "GIGACHAT_BAD_REQUEST": "Сервис ИИ не смог обработать запрос.",
    "GIGACHAT_SERVER_ERROR": "В сервисе ИИ произошла ошибка. Повторите проверку позднее.",
    "GIGACHAT_API_ERROR": "Не удалось получить результат от ИИ.",
    "GIGACHAT_NOT_FOUND": "Сервис ИИ недоступен. Обратитесь к администратору.",
    "GIGACHAT_INVALID_JSON": "Ответ ИИ не прошёл проверку. Требуется проверка специалистом.",
    "GIGACHAT_INVALID_RESPONSE": "Ответ ИИ не прошёл проверку. Требуется проверка специалистом.",
    "GIGACHAT_INVALID_COMPLETION": "Ответ ИИ не прошёл проверку. Требуется проверка специалистом.",
    "GIGACHAT_INCOMPLETE_COMPLETION": "ИИ вернул неполный ответ. Требуется проверка специалистом.",
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


class AISettings(BaseSettings):
    """Настройки адаптера: только окружение, без чтения .env и секретов."""

    model_config = SettingsConfigDict(env_prefix="AI_", extra="ignore")

    mode: Literal["template", "offline", "network"] = "template"
    pair_timeout_seconds: float = Field(default=20, gt=0, allow_inf_nan=False)
    application_timeout_seconds: float = Field(default=90, gt=0, allow_inf_nan=False)
    # Optional legacy RAG corpus; ordinary analysis uses the active reference.
    corpus_path: str = ""
    # Paths in the API process/container, not CLI arguments. No implicit downloads.
    registry_path: str = ""
    clinical_cache_dir: str = ""
    live_sources: bool = False
    # TODO: уточнить у аналитика валюту; backend пока не хранит валюту позиции.
    currency: str = Field(default="RUB", pattern=r"^[A-Z]{3}$")
    # Метаданные проверенной сборки; пустые значения не разрешают network-запуск.
    backend_revision: str = Field(default="", pattern=r"^(?:[0-9a-f]{40})?$")
    library_revision: str = Field(default="", pattern=r"^(?:[0-9a-f]{40})?$")


class AINetworkSettings(BaseSettings):
    """Создаётся только сетевым worker; секрет остаётся в памяти процесса."""

    model_config = SettingsConfigDict(env_prefix="GIGACHAT_", extra="ignore")

    credentials: SecretStr
    scope: str = ""
    ca_bundle_file: str = ""


@lru_cache
def get_llm_settings() -> LLMSettings:
    return LLMSettings()
