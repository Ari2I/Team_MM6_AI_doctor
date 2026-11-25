# «Врач» — backend

FastAPI/PostgreSQL: приём XLSX, валидация, сопоставление торговых названий с МНН,
проверка каждой пары «препарат + диагноз», расчёт долей сумм и индикатора,
сохранение результатов и Excel-отчёт. Анализ рекомендательный, решение принимает
человек. Дозировку, количество и длительность система медицински не оценивает.

Ветка `backend` обновлена из `integration/techno-razbor`, коммит `0d34e09`.
Backend находится в корневом `app/`. Код `app/`, миграции и тесты перенесены
без изменения контрактов интеграционной версии. Frontend и исходники AI/LLM
не включены. Из `ai/` сохранён только синтетический `examples/corpus.json`,
необходимый существующим backend-тестам и опциональному legacy RAG-пути.

## Запуск API и PostgreSQL

Скопируйте `.env.example` в локальный `.env`, задайте `POSTGRES_DB`,
`POSTGRES_USER`, `POSTGRES_PASSWORD`. Не коммитьте секреты и реальные заявки.
`API_PORT=8000` — порт хоста; API публикуется только на `127.0.0.1`.

```bash
docker compose up --build -d --wait
docker compose exec -T api alembic upgrade head
curl --fail http://127.0.0.1:8000/health
```

Миграции не запускаются автоматически. Перед первым запросом к заявкам выполните
`alembic upgrade head`; для существующей БД предварительно сделайте резервную копию.
В обновление входят миграции кириллических категорий А/Б/В, метаданных рабочего
справочника и возрастной группы. Старый контракт латинских A/B/C заменён
кириллическими А/Б/В ещё в интеграционной версии — клиентам старой ветки нужно
обновить обработку категорий. Новые необязательные поля: `age_group`,
`reference_name`, `reference_is_synthetic`.

По умолчанию `AI_MODE=template`: детерминированные правила и шаблонные тексты,
без AI-библиотеки и внешних вызовов. Без активного справочника заявка всё равно
обрабатывается: категории не определены, `needs_review=true`, индикатор
`undetermined`, версия `unconfigured` и предупреждение человеку.

Для вымышленных демонстрационных заявок можно активировать синтетический справочник:

```bash
docker compose exec -T api python -m app.scripts.import_reference data/reference/demo-v2.json --activate
```

Примеры заявок: `data/demo/*.xlsx`. Справочники `data/reference/demo*.json`
синтетические, не являются клинической истиной и не предназначены для реальных
решений. Рабочий справочник импортируется тем же CLI; формат описан Pydantic-моделью
`ReferenceDocument` в `app/scripts/import_reference.py`. Источник каждого правила
обязателен; отсутствие правила не заменяется придуманным обоснованием.

## HTTP-контракт для frontend

| Метод и путь | Назначение |
| --- | --- |
| `GET /health` | Проверка доступности |
| `POST /applications` | Multipart: `file`, необязательные повторяемые `diagnoses`, `age_group=adult\|child` |
| `GET /applications` | Список, выключен по умолчанию |
| `GET /applications/{id}` | Сохранённая карточка |
| `GET /applications/{id}/report.xlsx` | Excel-отчёт |

Карточка: `application`, `disclaimer`, `diagnoses`, `items`, `issues`.
В `items[].checks[]` хранятся проверки пар с `source`, `reference_version`,
`needs_review`, `review_reason`, `comment`. Ошибки API возвращаются в `errors[]`
с кодом, русским сообщением, строкой, колонкой и уровнем замечания.
Полный пример: `sample_api_response.json`; схемы: `app/schemas/application_api.py`.

`DOCS_ENABLED=true` включает `/docs`, `/redoc`, `/openapi.json`;
`LIST_ENDPOINT_ENABLED=true` включает список заявок. После смены настроек
пересоздайте API: `docker compose up -d --force-recreate api`.
В этой ветке нет nginx и Basic Auth: сетевой доступ и frontend подключаются
в интеграционной сборке. Frontend обращается к API через свой reverse proxy;
прямые браузерные запросы с другого origin потребуют отдельно согласованной CORS-настройки.

## Подключение AI-модуля

Адаптеры backend сохранены: `app/services/ai_adapter.py`, `ai_worker.py`,
`standard_analysis.py`, `ml2_analysis.py`. Режимы актуального кода:
`template` (по умолчанию), `offline`, `network`. Старое значение `module`
не поддерживается. Ошибки опционального AI дают fallback и ручную проверку.

Для AI-образов отдельно подключите совместимую библиотеку `llm_rag` из
интеграционного коммита `0d34e09` в `ai/src/` (без изменения backend-контракта).
Без неё используйте только образ `template`; стадии Docker `ai-offline` и
`ai-network` специально требуют эти исходники. Исходники AI не копируются
в базовую/template стадию, тяжёлые AI-зависимости не устанавливаются.

`AI_MODE=offline` без заданных источников использует активный backend-справочник
и шаблоны. `network` дополнительно требует `API_BUILD_TARGET=ai-network`, ключ
`GIGACHAT_CREDENTIALS` в окружении и, при необходимости, доверенный CA-файл.
Ключи не передаются через аргументы команд и не публикуются.
`docker-compose.certs.yml` подключает локальный `certs/gigachat-ca.pem` read-only.
`scripts/compose_ai.py` позволяет запускать Compose без автоматического чтения `.env`:
все обязательные настройки нужно заранее установить в окружении хоста.

Интеграционная инструкция по ГРЛС/кэшу КР сохранена в `docs/rag-local.md`.
`docker-compose.rag.yml` — опциональный override для подключённого AI-модуля;
он явно включает сетевой режим по умолчанию, обычный Compose его не использует.
`scripts/rag_check.py` и `scripts/ai_network_check.py` запускаются только вручную.

## Проверки и структура

Python 3.11; бизнес-логика — `app/services/`, HTTP — `app/api/`, схемы —
`app/schemas/`, SQLAlchemy — `app/models/`, миграции — `alembic/`.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
pytest -q
ruff check app tests alembic scripts
ruff format --check app tests alembic scripts
```

PostgreSQL-тесты требуют отдельной тестовой БД с применёнными миграциями и
`RUN_POSTGRES_TESTS=1 ENABLE_APPLICATION_LIST=true` (без конфликтующих
`DOCS_ENABLED`/`LIST_ENDPOINT_ENABLED` в окружении). Тесты настоящей AI-библиотеки пропускаются без `ai/src`;
это не проверка offline/network интеграции. Для изолированной Docker-проверки
задайте в окружении `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, свободный
`API_PORT` и выполните `RUN_POSTGRES_TESTS=1 bash scripts/smoke_e2e.sh`.
Smoke создаёт отдельный Compose-проект, проверяет API, отчёты и обе версии
демо-справочника и удаляет только собственные контейнеры и том при завершении.

## Ограничения текущего состояния

Категории и пороги требуют подтверждения аналитиком (TODO в конфиге).
Текущая реализация меняет технические статусы обработки заявки
(`parsed/analyzed/error`); отдельное решение секретаря не хранится. Это
расхождение с формулировкой AGENTS.md сохранено при переносе, бизнес-логика
в этой задаче не переписывалась. Индивидуальные роли, аудит, сроки хранения
и клиническая приёмка остаются отдельными задачами.

Проверка обновления ветки: Python 3.11.18, временный PostgreSQL 17.11 —
269 тестов прошли, 8 пропущены (AI-библиотека/внешние данные отсутствуют).
Все миграции применены; Ruff и проверка форматирования проходят.
HTTP smoke на локальном uvicorn прошёл для обоих демо-справочников:
загрузка, карточка, отчёт, ошибки файла и проверки доступа API.
Docker-проверка также пройдена: собран образ `template`, запущены API и PostgreSQL 16,
применены все миграции; контейнерный pytest — 269 passed, 8 skipped.
Полный `RUN_POSTGRES_TESTS=1 bash scripts/smoke_e2e.sh` прошёл для обоих
демо-справочников, включая опубликованный health endpoint, загрузку XLSX,
сохранённые карточки и Excel-отчёты. Ruff и форматирование внутри образа проходят.
Для запуска существующих тестов Dockerfile копирует `sample_api_response.json`.
Временные контейнеры, сеть и тестовый том удалены после проверки.
AI-образы `ai-offline`/`ai-network` не проверялись: библиотека `ai/src` намеренно
не включена в самостоятельную ветку backend.
