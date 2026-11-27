# Подготовка сетевой сборки без запросов к модели

Этот шаг проверяет network-образ, CA, запись от appuser и сохранность отдельного
RAG-тома после пересоздания API. AI_MODE остаётся template. Клиенты GigaChat не
создаются, ключ не читается проверочным скриптом, источники и модели не скачиваются.
Реальная генерация, embeddings и TLS-соединение проходят отдельную приёмку.

## Состав сборки

- Dockerfile уже содержит ai-network: backend + AI-исходники + gigachat==0.2.0,
  httpx и jsonschema. Torch/transformers для этого пути не добавляются.
- docker-compose.network.yml выбирает эту стадию и монтирует проектный named
  volume rag_runtime_data в /var/lib/vrach/rag. RAG_DATA_DIR передаётся явно.
  Он сохраняет загрузки cache/grls и векторный индекс indexes/official-embeddings.json.
- Это отдельное хранилище от прежних /run/rag/grls и /run/rag/clinicalrecs.
  Прежние bind mounts сохраняются в используемом dc_rag, новый фасад их не читает.
  Установку набора через current.json и копирование старого HTML-кэша не выполняем.
- CA подключается существующим docker-compose.certs.yml, только для чтения.
  Скачанные сертификаты не копируются в образ или named volume.
- SHA и scope обязательны для нового override. Подтверждённый пользователем scope:
  GIGACHAT_API_B2B. Его не подбираем и автоматически не заменяем.

## Команды в прежнем терминале

Предполагается, что dc_rag обращается к текущему проекту Compose, а dc_tls добавляет
docker-compose.certs.yml. Сохраняем эти обёртки и имя проекта, чтобы использовать
существующую БД и опубликованный порт. POSTGRES_*/WEB_* уже заданы; значения не печатать.
Команды выполнять из корня репозитория на проверенных локальных коммитах.

```bash
dc_network() {
  dc_tls -f docker-compose.network.yml "$@"
}

export API_BUILD_TARGET=ai-network AI_MODE=template AI_LIVE_SOURCES=false
export GIGACHAT_SCOPE=GIGACHAT_API_B2B
export AI_BACKEND_REVISION="$(git rev-parse HEAD)"
export AI_LIBRARY_REVISION="$(git rev-parse 8836cdb)"

# Убедиться, что AI-исходники соответствуют указанной ревизии: код возврата 0.
git diff --quiet HEAD:ai 8836cdb

dc_network build api
```

После изменения исходников обновить SHA и пересобрать образ. Сборка скачивает
Python-зависимости; это не вызов GigaChat и не сетевой анализ заявки.

Новый пустой volume принадлежит root. Однократно подготовить только его корень:

```bash
dc_network run --rm --no-deps --user root api \
  python scripts/check_network_build.py --prepare-volume

dc_network up -d --no-build --force-recreate --wait --wait-timeout 180 api web

dc_network exec -T api python scripts/check_network_build.py --write-probe
```

Подготовка меняет владельца только корня отдельного mount на appuser из образа;
рекурсивный chown, права хостового каталога и существующие данные не меняются.
Сам API и последующая проверка работают обычным appuser. Проверка от root отклоняется.

Ожидаются сообщения о корректности SHA/scope/импортов SDK, загрузке CA и записи
в каталоги ГРЛС/индекса. Содержимое настроек, ключи и сторонние исключения скрыты.
Скрипт запрещает сокеты/DNS до импортов SDK/AI и учитывает даже подавленные попытки.
Он не создаёт провайдеры и не вызывает analyze_request().

Проверить сохранность контрольного файла после пересоздания:

```bash
dc_network up -d --no-build --force-recreate --wait --wait-timeout 180 api web
dc_network exec -T api python scripts/check_network_build.py --check-probe
```

Вторая проверка читает существующий контрольный файл и не создаёт его заново.
Том остаётся тем же в пределах текущего проекта Compose; down -v и удаление тома
в этот сценарий не входят. В нём не хранятся заявки/ключи: IntegrationResult
сохраняется в PostgreSQL, как прежде. Фактическую сохранность скачанных ресурсов
и индекса дополнительно проверяем после реального сетевого прогона.

## Проверено и ограничения

Локальные тесты проверяют отказ от root/немонтированного каталога, область chown,
запись/чтение контрольного файла без перезаписи, явные SHA/scope, TLS-контекст,
скрытие исключений и запрет сети. Docker-команды выполняет пользователь.

Не использовать docker compose config/inspect для диагностики: они могут показать
секреты окружения. Проверочный скрипт не подтверждает действительность ключа,
доступ к генерации/embeddings, доступность источников или достаточность таймаута.
Включение AI_MODE=network и тестовая заявка — следующий отдельный шаг.
