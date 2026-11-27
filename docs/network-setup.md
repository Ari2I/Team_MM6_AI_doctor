# Сетевая сборка с GigaChat

Сборка защиты использует `AI_MODE=network` и `AI_USE_BACKEND_REFERENCE=false`.
Один worker вызывает `analyze_request()` на всю заявку; внутри фасада возможны
запросы GigaChat и Embeddings. Backend-справочник `demo-api-2` в этом потоке
не подставляет категории.

## Требования

- Инструменты установлены по [инструкции для ОС](installation.md#инструменты).
- Выполнен блок [окружения Compose](installation.md#окружение-compose) для своей
  ОС: заданы `POSTGRES_*`, `WEB_*` и функция скрытого ввода. Команды сборки и
  импорта demo-справочника из той инструкции для сетевого варианта не нужны.
- Есть корпоративный ключ GigaChat с подтверждённым scope. В проверенной сборке
  использовался `GIGACHAT_API_B2B`; `GIGACHAT_API_CORP` задают только при таком
  значении в кабинете. Scope не подбирают по ответам сервера.
- Есть согласие на передачу минимального набора данных внешней модели.
- CA bundle Минцифры получен по [официальной инструкции GigaChat](https://developers.sber.ru/docs/ru/gigachat/certificates).

Старый `/run/rag`-кэш из `docker-compose.rag.yml` этой сборке не нужен. Фасад
хранит данные в постоянном Docker-томе `rag_runtime_data`.
Уже работающий стек с `dc_defense` можно оставить на прежних обёртках и томах.
Пример нового имени проекта ниже не заменяет имя существующей БД.

## CA и секреты

Положите PEM bundle в `certs/gigachat-ca.pem`. Если нужны два корневых CA,
поместите оба полных PEM-блока подряд. Приватный ключ в этот файл не добавляется.
`certs/` исключён из Git и Docker-контекста; Compose подключает файл read-only.

Создайте каталог (`mkdir -p certs` в Bash/zsh или
`New-Item -ItemType Directory -Force certs | Out-Null` в PowerShell) и скопируйте
туда свой bundle под указанным именем. Если формируете bundle редактором,
сохраните обычный PEM-текст с переводом строки между блоками, не UTF-16.

Не записывайте ключ в `.env`, исходники, образ или отчёт. Вводите его скрыто:

```bash
printf 'GigaChat credentials: '
IFS= read -r -s GIGACHAT_CREDENTIALS
printf '\n'
export GIGACHAT_CREDENTIALS GIGACHAT_SCOPE=GIGACHAT_API_B2B
```

В PowerShell используйте `Set-PrivateEnv GIGACHAT_CREDENTIALS` из
[инструкции установки](installation.md#окружение-compose), затем:

```powershell
$env:GIGACHAT_SCOPE = 'GIGACHAT_API_B2B'
```

Проверка TLS всегда включена. Ошибку CA исправляют заменой bundle/путей, а не
`verify=False`.

## Compose-обёртка

### macOS/Linux

```bash
export VrachProject=vrach-network
export AI_MODE=network API_BUILD_TARGET=ai-network AI_CORPUS_PATH=
export AI_APPLICATION_TIMEOUT_SECONDS=240
export AI_BACKEND_REVISION="$(git rev-parse HEAD)"
export AI_LIBRARY_REVISION="$(git log -1 --format=%H -- ai/src/llm_rag)"
export DOCS_ENABLED=false LIST_ENDPOINT_ENABLED=false

dc_ai() {
  docker compose --env-file /dev/null -p "$VrachProject" \
    -f docker-compose.yml -f docker-compose.network.yml \
    -f docker-compose.certs.yml -f docker-compose.defense.yml "$@"
}
```

### Windows PowerShell

```powershell
$script:VrachProject = 'vrach-network'
$env:AI_MODE = 'network'
$env:API_BUILD_TARGET = 'ai-network'
$env:AI_CORPUS_PATH = ''
$env:AI_APPLICATION_TIMEOUT_SECONDS = '240'
$env:AI_BACKEND_REVISION = (git rev-parse HEAD).Trim()
$env:AI_LIBRARY_REVISION = (git log -1 --format=%H -- ai/src/llm_rag).Trim()
$env:DOCS_ENABLED = 'false'
$env:LIST_ENDPOINT_ENABLED = 'false'

function dc_ai {
    & docker compose --env-file NUL -p $script:VrachProject `
        -f docker-compose.yml -f docker-compose.network.yml `
        -f docker-compose.certs.yml -f docker-compose.defense.yml @args
}
```

240 секунд — существующий бюджет прежнего `docker-compose.rag.yml`, заданный
здесь явно без его старых bind mounts. Контроль двух пар занял около 111 секунд;
базовых 90 секунд для этого прогона недостаточно. Другие заявки могут не уложиться
в этот срок. Лимиты числа вызовов/токенов, TLS и принудительное завершение worker
не отключаются. Бюджет пары старого адаптера не ограничивает каждый внутренний
вызов фасада отдельно.

## Сборка и первый запуск

```text
dc_ai build api web
dc_ai up -d --wait --wait-timeout 180 db
dc_ai run --rm --no-deps --user root api python scripts/check_network_build.py --prepare-volume
dc_ai run --rm --no-deps api alembic upgrade head
dc_ai up -d --no-build --wait --wait-timeout 180 api web
dc_ai exec -T api python scripts/check_network_build.py --write-probe
dc_ai exec -T api python scripts/check_ai_worker_source.py
```

После первого запуска замените `--write-probe` на `--check-probe`: проверочный
файл и постоянный RAG-том должны сохраняться после пересоздания API. Эти проверки
не создают провайдеров и не вызывают модель.

Откройте `http://localhost:<WEB_PORT>/` и загрузите согласованную синтетическую
AI-книгу. В карточке и «Разборе ИИ» проверьте категории, редакцию, дословные
цитаты и ссылки; затем скачайте Excel. `MODEL` относится к пояснению и сам по
себе не доказывает наличие категории. `COMPLETE` означает обработку всех пар.

Повторное открытие без внешних запросов:

```text
dc_ai -f docker-compose.no-egress.yml up -d --no-build --force-recreate --wait --wait-timeout 180 api web
dc_ai -f docker-compose.no-egress.yml exec -T api python scripts/verify_network_acceptance.py --check-egress-blocked
```

В этом режиме только перечитайте существующую карточку, AI-отчёт и Excel. Для
новых заявок верните обычную сеть:

```text
dc_ai up -d --no-build --force-recreate --wait --wait-timeout 180 api web
dc_ai exec -T api python scripts/check_network_build.py --check-probe
```

## Данные и сеть

- PostgreSQL хранит заявку, полный `IntegrationResult`, снимок оснований и историю.
- `rag_runtime_data` хранит загруженные ресурсы ГРЛС и векторный индекс
  `/var/lib/vrach/rag/indexes/official-embeddings.json`.
- Во внешние запросы проходят только разрешённые МНН/диагнозы, необходимые
  публичные фрагменты, цитаты и метаданные источников. XLSX, имя файла, исходные
  ID, дозировка, количество, суммы, валюта, точный возраст, `age_group` и
  произвольный клинический контекст не передаются.
- Возрастная группа и условия используются локально для выбора редакции КР и
  проверки применимости; сама группа не подтверждает числовой возрастной порог.
- Исходящие соединения используют TLS-проверку; браузер → nginx в текущей сборке
  использует HTTP и требует отдельной защищённой среды для реальных данных.

Для строгой приёмки категорий используйте [category-fix-acceptance.md](category-fix-acceptance.md).
В командах той инструкции `dc_defense` обозначает прежнюю обёртку. Для этой
самостоятельной установки замените её на `dc_ai`. Три теста старого моста и
`RAG_TEST_DATA_DIR=/run/rag` требуют дополнительного старого bind-кэша; новый фасад
в нём не нуждается. Нельзя считать эти тесты пройденными по одному успеху фасада.
Не сравнивайте network-результаты с template golden и не удаляйте RAG-тома ради
повторной проверки.
