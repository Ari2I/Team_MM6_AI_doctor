"""Локальная имитация генератора для проверки конвейера без API."""


class DemoProvider:
    """Локальная имитация генератора для демо; не заменяет LLM при сбое API."""

    name = "offline-extractive-demo"

    def generate(self, payload: dict) -> dict:
        return {"item_id": payload["item"]["item_id"], "claims": [
            {key: evidence[key] for key in ("claim_id", "text", "fragment_id", "quote")}
            for evidence in payload["evidence"]
        ]}
