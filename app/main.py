"""Создание FastAPI-приложения и подключение HTTP-роутеров."""

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from app.api.applications import router as applications_router
from app.api.errors import http_error, request_validation_error
from app.api.health import router as health_router

app = FastAPI(title="Врач — backend", version="0.1.0")
app.include_router(health_router)
app.include_router(applications_router)
app.add_exception_handler(RequestValidationError, request_validation_error)
app.add_exception_handler(HTTPException, http_error)
