"""Создание FastAPI-приложения и подключение HTTP-роутеров."""

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from app.api.applications import router as applications_router
from app.api.errors import http_error, request_validation_error
from app.api.health import router as health_router
from app.config import get_access_settings


def create_app() -> FastAPI:
    settings = get_access_settings()
    application = FastAPI(
        title="Врач — backend",
        version="0.1.0",
        docs_url="/docs" if settings.enable_api_docs else None,
        redoc_url="/redoc" if settings.enable_api_docs else None,
        openapi_url="/openapi.json" if settings.enable_api_docs else None,
    )
    application.include_router(health_router)
    application.include_router(applications_router)
    application.add_exception_handler(RequestValidationError, request_validation_error)
    application.add_exception_handler(HTTPException, http_error)
    return application


app = create_app()
