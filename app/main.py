"""Создание FastAPI-приложения и подключение HTTP-роутеров."""

from fastapi import FastAPI

from app.api.health import router as health_router

app = FastAPI(title="Врач — backend", version="0.1.0")
app.include_router(health_router)
