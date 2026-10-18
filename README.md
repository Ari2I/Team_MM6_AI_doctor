# Backend ассистента «Врач»

Каркас FastAPI-приложения на Python 3.11. Анализ носит рекомендательный характер;
решение принимает человек. Обработка заявок и интеграции AI/ML пока не реализованы.

## Структура

```text
app/
  main.py, config.py
  api/          HTTP-роутеры (пока только health)
  schemas/      Pydantic-схемы API
  models/       место для будущих моделей
  services/     пустые модули excel_parser, validator, analysis, traffic_light
  db/           декларативная база, движок и фабрика сессий
tests/
  data/         место только для вымышленных Excel-данных
  test_health.py
alembic/
  env.py, script.py.mako, versions/
```

## Запуск в Docker

Нужен Docker с Compose. Создайте `.env` из `.env.example` и задайте свой пароль.
В PowerShell:

```powershell
Copy-Item .env.example .env
docker compose up --build
```

В Linux/macOS вместо `Copy-Item` используйте `cp .env.example .env`.
API: <http://localhost:8000>, Swagger: <http://localhost:8000/docs>.
Порт API задаётся через `API_PORT` в `.env`.
Compose ожидает готовности PostgreSQL перед запуском API; оба сервиса имеют healthcheck.
Для запуска в фоне с ожиданием готовности:

```sh
docker compose up --build -d --wait
docker compose ps
curl http://localhost:8000/health
```

Остановка без удаления данных PostgreSQL:

```sh
docker compose down
```

## Проверки в контейнере (Python 3.11)

Образ для разработки включает pytest и ruff. После сборки образа проверки можно
выполнять и без запущенной БД:

```sh
docker compose run --rm --no-deps api pytest -q
docker compose run --rm --no-deps api ruff check .
docker compose run --rm --no-deps api ruff format --check .
```

Исходники копируются при сборке: после изменений перед контейнерными проверками
выполните `docker compose build api`.

## Локальная разработка

Нужен Python 3.11. Создайте окружение и установите зависимости:

```sh
python -m venv .venv
```

Активируйте его через `.venv\Scripts\Activate.ps1` в PowerShell
или `source .venv/bin/activate` в Linux/macOS, затем:

```sh
python -m pip install -e ".[dev]"
pytest -q
ruff check .
ruff format --check .
uvicorn app.main:app --reload
```

Тест `/health` не требует `.env` и PostgreSQL. Для обращения к БД нужны настройки
из `.env`; в Compose хост автоматически заменяется на `db`, порт — на `5432`.
Порт PostgreSQL на хост не публикуется. Для локального Python подключите собственный
PostgreSQL и укажите его адрес в `.env`.

## Alembic

Общий источник настроек подключения — `app/config.py`. Пароль не хранится в
`alembic.ini`. Доменных моделей и ревизий пока нет; `Base.metadata` пусты.
Миграции не применяются автоматически при запуске API.

```sh
docker compose exec api alembic upgrade head
docker compose exec api alembic current
```

После добавления моделей импортируйте их в `alembic/env.py`, затем создайте ревизию
локально, чтобы файл сохранился в репозитории:

```sh
alembic revision --autogenerate -m "msg"
alembic upgrade head
```

Локальные команды требуют доступной БД с настройками из `.env`.

## Принятые допущения

- PostgreSQL 16 и синхронный SQLAlchemy с драйвером psycopg 3; Docker использует Python 3.11.
- `GET /health` проверяет процесс API, без обращения к БД, и возвращает HTTP 200:
  `{"status":"ok","disclaimer":"Анализ носит рекомендательный характер. Решение принимает человек."}`.
- Дисклеймер включён и в технический ответ согласно требованию `AGENTS.md`.
- Бизнес-контракты, парсер, доменные модели, правила анализа и LLM-адаптер не определяются
  в этом каркасе. Соответствующие модули содержат только однострочные docstring.
- Пример пароля предназначен только для локальной разработки; `.env` исключён из Git
  и контекста сборки. Данные PostgreSQL сохраняются в именованном томе.
- Диапазоны зависимостей заданы в `pyproject.toml`; lock-файл пока не используется.
