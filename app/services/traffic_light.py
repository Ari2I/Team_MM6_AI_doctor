"""Совместимый модуль расчёта индикатора."""

from app.services.summary import TrafficLightDecision, compute_traffic_light

__all__ = ["TrafficLightDecision", "compute_traffic_light"]
