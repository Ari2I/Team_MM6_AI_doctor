"""HTTP-проверка работоспособности процесса API."""

from fastapi import APIRouter

from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Вернуть состояние API без обращения к базе данных."""
    return HealthResponse()
