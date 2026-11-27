# Положительный и отрицательный контроль с ГРЛС

Сценарий выполняет пользователь в прежнем терминале с `dc_acceptance`,
подключённым CA, ключом и настройками Basic Auth. Каждая загрузка — новая
синтетическая заявка через API; её разбирает штатный network-worker.
Библиотечный алгоритм, API и БД этим сценарием не меняются.

Положительный контроль подбирается из актуального ГРЛС по полному МНН, явно
встречающемуся в выбранной действующей КР. Для узкого теста каждый подобранный
фрагмент и каждая итоговая цитата должны содержать полное МНН. Это сознательно
строгий контроль: цепочки «группа → принадлежность МНН» могут быть допустимы в
библиотеке, но требуют отдельной проверки. Они не объявляются ошибкой AI только
из-за отсутствия полного названия в одном предложении.

Отрицательный контроль — тоже реальная запись ГРЛС, но в выбранной КР нет полного
МНН и библиотека не находит для него фрагментов. Ожидается `NO_EVIDENCE`, категория
не установлена, цитат нет. Это не медицинский вывод о неприменимости препарата.
GigaChat может подготовить текстовое объяснение ограничения, сохранив пустые
цитаты; успешная модельная генерация проверяется отдельно от классификации.

Числа 1 и 1000 — явные синтетические значения. Название и форма копируются из
ГРЛС, диагноз — из выбранной КР с явным МКБ. Точный возраст не выдумывается.
Ни категория, ни положительная клиническая применимость не задаются заранее.

## 1. Собрать инструменты и подготовить обе заявки

Каталоги `acceptance-controls-inn` и `.local/network-controls-inn` должны быть
новыми. Скрипт останавливается при существующем каталоге; прежние `acceptance`,
`state.json`, baseline и индекс не перезаписываются. Если новое имя уже занято,
сначала остановиться и согласовать другой каталог во всех командах.

```bash
export AI_MODE=template
export AI_BACKEND_REVISION="$(git rev-parse HEAD)"
export AI_LIBRARY_REVISION="$(git log -1 --format=%H -- ai/src/llm_rag)"
export GIGACHAT_SCOPE=GIGACHAT_API_B2B
export RAG_ACCEPTANCE_TERMS=/var/lib/vrach/rag/acceptance-controls-inn/network-terms.json

dc_acceptance build api
dc_acceptance up -d --no-build --force-recreate --wait --wait-timeout 180 db api web

dc_acceptance exec -T api python scripts/prepare_network_case.py \
  --icd10 J45.9 --age-group adult --with-negative \
  --case-dir /var/lib/vrach/rag/acceptance-controls-inn

dc_acceptance exec -T api python scripts/verify_network_acceptance.py \
  --case-dir /var/lib/vrach/rag/acceptance-controls-inn --baseline

mkdir -p .local
test ! -e .local/network-controls-inn && \
  dc_acceptance cp api:/var/lib/vrach/rag/acceptance-controls-inn .local/network-controls-inn
```

Следующий блок выполнять после успеха предыдущего. Подготовка получает публичные
источники с проверкой TLS, GigaChat/Embeddings не вызывает. Если ни одной пары
нет, не подбирать диагноз, ключ, синоним или категорию наугад.

## 2. Загрузить положительный контроль и проверить цитаты

```bash
export AI_MODE=network
dc_acceptance up -d --no-build --force-recreate --wait --wait-timeout 180 api web

export WEB_URL="http://127.0.0.1:${WEB_PORT}"
.venv/bin/python scripts/run_network_acceptance.py --case-dir .local/network-controls-inn

dc_acceptance exec -T api python scripts/verify_network_acceptance.py \
  --case-dir /var/lib/vrach/rag/acceptance-controls-inn --state-stdin \
  < .local/network-controls-inn/state.json
```

Нужны COMPLETE, MODEL, сохранённые цитаты, новые проверенные векторы контрольной
пары, версии ресурсов и SHA. HTTP 200, наличие зарегистрированного препарата или
запись `MODEL` по отдельности недостаточны. Категория может остаться неопределённой
из-за неизвестных условий: это не заменяет проверку текстовой связи цитат с МНН.

Цитаты доступны при локальном открытии сохранённого
`/applications/{application_id}/ai-report` в `human_report.items[].sources`.
ID находится в локальном `state.json`. Смотреть результат можно локально;
не копировать весь отчёт в терминальные логи или Git.

## 3. Загрузить отрицательный контроль

Выполнять после успешного положительного контроля:

```bash
.venv/bin/python scripts/run_network_acceptance.py \
  --case-dir .local/network-controls-inn/negative

dc_acceptance exec -T api python scripts/verify_network_acceptance.py \
  --case-dir /var/lib/vrach/rag/acceptance-controls-inn/negative --state-stdin \
  < .local/network-controls-inn/negative/state.json
```

Нужны определённое МНН, NO_EVIDENCE, отсутствие обеих категорий и цитат, MODEL
для объяснения ограничения. Ошибку источника, fallback или выдуманные цитаты
скрипт отклонит. Наличие новых векторов для отрицательного случая не требуется:
текстов для поиска нет. Не требовать от GigaChat цитату там, где её нет.

## 4. Повторное чтение при запрете внешней сети API

```bash
dc_acceptance -f docker-compose.no-egress.yml up -d --no-build \
  --force-recreate --wait --wait-timeout 180 db api web

dc_acceptance -f docker-compose.no-egress.yml exec -T api \
  python scripts/verify_network_acceptance.py \
  --case-dir /var/lib/vrach/rag/acceptance-controls-inn --check-egress-blocked

.venv/bin/python scripts/run_network_acceptance.py \
  --case-dir .local/network-controls-inn --reopen
.venv/bin/python scripts/run_network_acceptance.py \
  --case-dir .local/network-controls-inn/negative --reopen

dc_acceptance -f docker-compose.no-egress.yml exec -T api \
  python scripts/verify_network_acceptance.py \
  --case-dir /var/lib/vrach/rag/acceptance-controls-inn --persistence
dc_acceptance -f docker-compose.no-egress.yml exec -T api \
  python scripts/verify_network_acceptance.py \
  --case-dir /var/lib/vrach/rag/acceptance-controls-inn/negative --persistence
```

Ожидаются недоступное контрольное внешнее соединение, успешные GET карточек,
AI-отчётов и Excel, сохранность результатов и файлов ресурсов. Новый POST при
`--reopen` не выполняется.

## 5. Вернуть template

```bash
export AI_MODE=template
dc_acceptance up -d --no-build --force-recreate --wait --wait-timeout 180 db api web
```

Скрипты печатают технические статусы без текста заявок, цитат и секретов. Если
проверка не проходит, прислать этот безопасный вывод и остановиться. Старые
артефакты, векторы и CA не удалять; TLS и лимиты не отключать.
