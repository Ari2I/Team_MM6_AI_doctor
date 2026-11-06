"""Точка входа FastAPI и собранного веб-интерфейса."""

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.applications import router as applications_router
from app.api.health import router as health_router

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

app = FastAPI(title="Врач — демо", version="0.1.0")
app.include_router(health_router)
app.include_router(applications_router)

frontend = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if frontend.is_dir():
    app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
