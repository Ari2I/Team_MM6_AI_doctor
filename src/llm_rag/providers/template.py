"""Локальная имитация генератора для проверки конвейера без API."""


class TemplateProvider:
    """Локальная имитация генератора для локального резервного режима; не заменяет LLM при сбое API."""

    name = "offline-extractive"

    def generate(self, payload: dict) -> dict:
        return {
            "item_id": payload["item"]["item_id"],
            "claims": [
                {key: evidence[key] for key in ("claim_id", "text", "fragment_id", "quote")}
                for evidence in payload["evidence"]
            ],
        }
