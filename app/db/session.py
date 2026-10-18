"""Создание движка PostgreSQL и фабрики сессий из общих настроек."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings

engine = create_engine(get_settings().database_url, pool_pre_ping=True, hide_parameters=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
