"""Синтетические результаты сборки защиты; без SDK, фасада и медицинских данных."""

from app.models import Application
from tests.ai_report_data import integration_result


def defense_result(application: Application, mode: str = "evidence") -> dict:
    from llm_rag.integration_contracts import IntegrationResult

    value = integration_result(application, mode if mode in {"partial", "fallback"} else "evidence")
    value["manifest"]["registry"] = {"version": "synthetic-defense-1", "sha256": "2" * 64}
    if mode == "none":
        for pair in value["pairs"]:
            pair["decision"] = {"classification_basis": "NONE"}
    elif mode == "conflict":
        value["pairs"][0]["decision"] = {
            "upstream_rule_category": "А",
            "proposed_category": "Б",
            "classification_basis": "CONFLICT",
        }
    elif mode == "approved":
        value["pairs"][0]["decision"] = {
            "upstream_rule_category": "В",
            "classification_basis": "APPROVED_RULE",
        }
    elif mode == "generation_error":
        value["pairs"][0]["reasons"] = ["AI_UNAVAILABLE"]
        value["pairs"][0]["generation_mode"] = "FALLBACK"
    value["human_report"] = {}
    return IntegrationResult.model_validate(value).model_dump(mode="json")
