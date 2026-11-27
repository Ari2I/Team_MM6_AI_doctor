"""Единое представление ошибок HTTP без исходных значений запроса."""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException


async def request_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = []
    for error in exc.errors():
        location = error.get("loc", ())
        column = str(location[-1]) if location else None
        errors.append(
            {
                "level": "critical",
                "code": "invalid_request",
                "message": "Обязательное поле отсутствует или имеет недопустимое значение.",
                "row": None,
                "column": column,
            }
        )
    return JSONResponse(status_code=422, content={"errors": errors})


async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    message = (
        "Запрошенный ресурс не найден."
        if exc.status_code == 404
        else "Не удалось обработать запрос."
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "errors": [
                {
                    "level": "critical",
                    "code": "http_error",
                    "message": message,
                    "row": None,
                    "column": None,
                }
            ]
        },
        headers=exc.headers,
    )
