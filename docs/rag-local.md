# Локальные справочники и полный путь обработки

> Это инструкция прежнего попарного моста и исторический отчёт о поставке кэша.
> Для новой установки используйте [installation.md](installation.md) или
> [network-setup.md](network-setup.md). Размеры/версии и состояние кэша ниже
> описывают тогдашнюю проверку, а не текущую доступность источников.

Рабочая ветка: `integration/techno-razbor`. Проверено на исходниках этой сборки,
без запуска старого demo_07.11 и без автономного `excel_scenario.process_card`.

## Данные и настройки

Предоставленная папка уже распакована. Все четыре файла данных проверены
по размерам и SHA256 из `manifest.json` до копирования и после него.
Установлена только эта подборка в `C:/PP/Proekt_Prac_2/.local/rag-data`:

- `grls/registry.json`: 72 525 862 байта,
  `ml2-grls-2026-11-11-9b33724162ab2b44`, 28 023 записи;
- `clinicalrecs/catalog-ru.json`: каталог КР;
- одна HTML-копия и парные метаданные: бронхиальная астма, редакция `359_3`;
- `README.txt`, исходный `manifest.json`.

Каталог исключён из Git и Docker-контекста. Реестр монтируется read-only, кэш КР —
с правом записи. `manifest.json` описывает исходную поставку: после обновления кэша
его содержимое закономерно может отличаться; не меняйте время проверки в метаданных
ради успешного теста. Новые страницы загружает штатный загрузчик.

Настройки `AISettings` и примеры `.env.example`:

| Настройка | Назначение |
| --- | --- |
| `AI_REGISTRY_PATH` | JSON-файл ГРЛС, путь в процессе API |
| `AI_CLINICAL_CACHE_DIR` | Доступный для записи каталог кэша КР |
| `AI_LIVE_SOURCES` | Разрешает получение КР через кэш и сеть; default `false` |
| `AI_MODE` | `template`: только backend-правила/шаблоны, без AI-импортов; `offline`: локальный мост без сети; `network`: фасад целой заявки |
| `AI_PAIR_TIMEOUT_SECONDS` | Бюджет на одну пару, для локального RAG рекомендовано 120 |
| `AI_APPLICATION_TIMEOUT_SECONDS` | Бюджет заявки, для локального RAG рекомендовано 240 |

`docker-compose.rag.yml` задаёт пути `/run/rag/grls/registry.json` и
`/run/rag/clinicalrecs`, `AI_LIVE_SOURCES=true`, пустой `AI_CORPUS_PATH` и образ
`ai-network` по умолчанию. Для другого расположения файлов на хосте существуют
`RAG_REGISTRY_SOURCE` и `RAG_CACHE_SOURCE`. Секреты не включены в эти файлы.

## Реальные точки вызова

`POST /applications` → `create_application` → Excel parser/validator →
`analyze_application` → SQL-правила → `AIAdapter.analyze` → отдельный
`app.services.ai_worker` → `app.services.ml2_analysis.analyze_pair` →
`RegistrySnapshot.load(Path(...))`, `ClinicalSources(cache=Path(...))`,
`ML2Pipeline(..., complete_json=...).run(request, live_sources=...)` →
проверка цитат по сохранённому текстовому снимку → шаблон пояснения и проверяемое
оформление моделью → `PairCheck` → `item_checks` → карточка backend и XLSX-отчёт.

SQL-категория имеет прежний приоритет. Результаты ML-2 требуют проверки человеком;
неопределённость не превращается в категорию В. Суммы и индикатор считает backend.
МНН, найденное ГРЛС, сохраняется в строке. `source` и `reference_version` содержат
ГРЛС и использованную редакцию КР, проверенные цитаты находятся в комментарии.

Полей `--registry`, `--cache`, `--live-sources` на веб-сервере нет. Это флаги CLI
ML-2. Новый мост передаёт явные настройки из `AISettings` библиотечному вызову.
Для совместимости обязательных полей прежнего AI-контракта используются подклассы
на backend: реальные входы имеют `is_demo=false`, отсутствующие дозы/количество/
суммы остаются отсутствующими. В ML-2 сохраняется тип входного контракта; старый
контракт и медицинская политика не меняются.

В API добавлены необязательный multipart `age_group=adult|child` и поле карточки
с тем же именем. Значение хранится миграцией `b8c9d0e1f2a3`. В интерфейсе есть
выбор группы, по умолчанию не указан. Диагноз должен включать МКБ-10, например
`J45.9 Бронхиальная астма`; код и группу не угадываем. Для нескольких применимых
редакций результат остаётся ручной проверкой. Настоящий возраст/ФИО не требуются.

## Источники и модель

ГРЛС: `https://grls.rosminzdrav.ru/GRLS.aspx`; он используется для происхождения
снимка, но доступность сайта не обязательна для чтения локального JSON.
КР: `https://КлиническиеРекомендации.рф/` (код использует IDNA-адрес
`https://xn--80ahcaaarabfanddhyjjq1br6ji.xn--p1ai`). HTML-копии проверяются
по паспорту, возрастной группе, актуальному статусу и ссылке на редакцию
`https://cr.minzdrav.gov.ru/preview-cr/...`. Это не массовая загрузка PDF Минздрава.
При `live_sources=true` свежий кэш используется без загрузки; отсутствующие страницы
загружаются, старые условно перепроверяются. Неудача перепроверки оставляет
устаревший кэш и ручную проверку; актуальность не подделывается.

GigaChat подключён через тот же SDK 0.2.0, `GIGACHAT_CREDENTIALS`, `GIGACHAT_SCOPE`,
`GIGACHAT_CA_BUNDLE_FILE` и официальные API-адреса, что и раньше. ML-2 использует
`ML2GigaChatProvider`/`gigachat_completer`; последующая генерация использует
существующий `AllowlistedProvider`. Во внешний запрос допускаются только МНН,
диагноз, выбранная возрастная группа и публичные фрагменты КР. Исходный файл,
торговое название, суммы, дозы, количество, реальные ID заявки не отправляются.
TLS не отключается. `jsonschema` добавлен в extra `ai-network`, поскольку его
использует имеющийся провайдер ML-2.

## Запуск текущей локальной сборки

Из корня проекта, при работающем PostgreSQL. Ключ/параметры БД остаются в вашем
локальном окружении или `.env`; эта инструкция их не выводит. Чтение `.env` агентом
запрещено, его содержимое не проверялось.

```powershell
Set-Location C:/PP/Proekt_Prac_2
$env:AI_MODE = 'network'
$env:API_BUILD_TARGET = 'ai-network'
$env:AI_LIVE_SOURCES = 'true'
$env:AI_CORPUS_PATH = ''
$env:AI_PAIR_TIMEOUT_SECONDS = '120'
$env:AI_APPLICATION_TIMEOUT_SECONDS = '240'

docker compose -f docker-compose.yml -f docker-compose.certs.yml -f docker-compose.rag.yml build api web
docker compose -f docker-compose.yml -f docker-compose.certs.yml -f docker-compose.rag.yml stop api web
docker compose -f docker-compose.yml -f docker-compose.certs.yml -f docker-compose.rag.yml run --rm --no-deps api alembic upgrade head
docker compose -f docker-compose.yml -f docker-compose.certs.yml -f docker-compose.rag.yml up -d --no-deps --force-recreate --wait api web
```

Следующую команду выполнять только после успеха предыдущей. Откройте прежний
адрес интерфейса, выберите возрастную группу и загрузите XLSX с кодом МКБ-10 в
диагнозе. Остальные заголовки Excel не менялись. В текущем `template` адаптер сразу
возвращает backend-правила и не читает эти AI-источники, независимо от флага
`AI_LIVE_SOURCES`. Старый локальный мост проверяется в `offline` без сети.

Поэтапная проверка из API-контейнера, без вывода секретов или заявок:

```powershell
docker compose -f docker-compose.yml -f docker-compose.certs.yml -f docker-compose.rag.yml exec -T api python scripts/rag_check.py --network-sources --model
docker compose -f docker-compose.yml -f docker-compose.certs.yml -f docker-compose.rag.yml exec -T api python scripts/rag_check.py --api-url http://127.0.0.1:8000
```

Первая команда отдельно проверяет источники, реальную загрузку отсутствующей КР,
worker, число вызовов/ответов модели, проверку цитат и оформление. Вторая посылает
вымышленную XLSX-покупку HTTP-роутеру и проверяет сохранённую карточку и отчёт.
`model_calls` без `model_responses` не доказывает успешный доступ к GigaChat.
`status=analyzed` с `AI_UNAVAILABLE` означает сохранённый результат ручной проверки.

## Что проверено в текущем окружении

- Все четыре исходных файла: SHA256 и размеры совпали до/после копирования.
- ГРЛС: 28 023 записи; «Сальбутамол» → МНН «Сальбутамол».
- `J45.9`, взрослые: единственная КР `359_3`, свежий кэш, 3 фрагмента препарата.
- Настоящий worker вызван backend-адаптером, без вымышленных purchase-полей;
  отключённый `live_sources` действительно не запускает поиск КР.
- XLSX → парсер → оркестратор → ML-2 → карточка → XLSX-отчёт проверены
  на предоставленном наборе справочников; отдельные тесты также проверяют
  отсутствие обращения к источникам при `live_sources=false`.
- Миграции применены к тестовой PostgreSQL через `alembic upgrade head`; итоговая
  версия — `b8c9d0e1f2a3`.
- Backend: 277 тестов прошли; тесты с PostgreSQL и три проверки локального RAG
  запускались с явными `RUN_POSTGRES_TESTS=1` и `RAG_TEST_DATA_DIR`.
- Frontend: 13 тестов прошли. Ruff для backend, тестов, миграций и скриптов
  проходит; общий Ruff для каталога `ai/` содержит отдельные прежние замечания.
- Настоящий вызов GigaChat не является частью этого результата: для него нужны
  ключ, scope и доступный сертификат в окружении оператора.

## Изменённые файлы этой задачи

Backend и запуск:
`.gitignore`, `.dockerignore`, `.env.example`, `pyproject.toml`, `README.md`,
`docker-compose.rag.yml`, `app/config.py`, `app/services/ai_adapter.py`,
`app/services/ai_worker.py`, `app/services/ml2_analysis.py`, `app/services/analysis.py`,
`app/services/report.py`, `app/models/application.py`, `app/schemas/application_api.py`,
`app/api/applications.py`, `alembic/versions/b8c9d0e1f2a3_application_age_group.py`,
`scripts/rag_check.py`, `tests/test_ml2_bridge.py`, `tests/test_category_migration.py`,
`tests/test_api_applications.py`, `docs/rag-local.md`.

Части других участников:

- `ai/src/llm_rag/ml2/pipeline.py`: сохранение подкласса входного контракта
  и его признака происхождения, без изменения медицинской классификации.
- `frontend/src/api.ts`, `frontend/src/main.tsx`, `frontend/src/contract.ts`,
  `frontend/tests/api.test.mjs`: передача, выбор, отображение и проверка возрастной группы.

Логи проверки содержат этапы, версии, числа и фиксированные коды ошибок.
Ни ключи, ни содержимое загруженной заявки в них не печатаются.
