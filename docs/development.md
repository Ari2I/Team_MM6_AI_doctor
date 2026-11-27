# Разработка и проверки

На хосте нужен Python **3.11.x**. Для Docker-запуска Python и Node устанавливаются
внутри образов. Код backend находится в `app/`, миграции — в `alembic/`, тесты —
в `tests/`; `frontend/` и `ai/` принадлежат коллегам, их изменения согласуются.
Установите Python 3.11 через [официальные установщики](https://www.python.org/downloads/)
или менеджер версий для своей ОС. На Linux используйте пакет/менеджер своей
системы либо Docker; не заменяйте системный Python.

## Python

macOS/Linux:

```bash
python3.11 --version
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

Windows PowerShell:

```powershell
py -3.11 --version
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
```

В PowerShell далее используйте `.\.venv\Scripts\python.exe` вместо `python`:
активация venv не обязательна. При разрешённой политике можно выполнить
`.\.venv\Scripts\Activate.ps1`; менять ExecutionPolicy всего компьютера не нужно.

Для тестов SDK добавьте extra `ai-network`:

```text
python -m pip install -e '.[dev,ai-network]'
```

Он ставит `gigachat==0.2.0`, httpx и jsonschema. Torch, transformers и веса
моделей не требуются. `template` запускается без AI-библиотеки.

## Проверки backend и AI

```text
python -m pytest -q -rs
python -m ruff check app tests alembic scripts
python -m ruff format --check app tests alembic scripts
```

Общий `ruff check .` не используйте: он включает исторические замечания в чужой
`ai/`. AI-тесты запускайте отдельно из каталога `ai/`:

```text
cd ai
python -m pytest -q
cd ..
```

Локальные тесты запрещают сетевые вызовы в `template`/`offline`, включая импорт,
создание провайдеров и дочерний worker. PostgreSQL-тесты требуют отдельной БД;
без неё они пропускаются. Тесты рабочего RAG-кэша требуют явного свежего
`RAG_TEST_DATA_DIR` и не должны считаться пройденными при пропуске.
Python-настройки приложения могут читать локальный `.env`. Для тестов используйте
изолированное окружение без рабочего `.env` и реальных ключей; Compose-обёртка
из установки не управляет настройками Python-процесса на хосте.

## Frontend

Установите [Node.js](https://nodejs.org/en/download) **22.12+ в ветке 22**;
это соответствует требованиям установленных Vite/plugin-react и Docker-стадии.
Из `frontend/`:

```text
npm ci
npm test
npx tsc --noEmit
npm run build
```

`npm run build` также запускает TypeScript-проверку. Не переносите `node_modules`
между ОС и не храните серверные секреты в frontend `.env*`.
Тесты прежнего контракта используют корневой `sample_api_response.json`.
Обычный Vite-build читает frontend `.env*`; Dockerfile копирует только явно
перечисленные файлы и не включает эти локальные настройки.

## PostgreSQL в контейнере

Используйте отдельный Compose-проект и сначала примените миграции:

```text
dc_demo cp sample_api_response.json api:/app/sample_api_response.json
dc_demo run --rm --no-deps api alembic upgrade head
dc_demo exec -T api alembic check
dc_demo exec -T -e RUN_POSTGRES_TESTS=1 -e ENABLE_APPLICATION_LIST=true api python -c 'import os,pytest; [os.environ.pop(k,None) for k in ("DOCS_ENABLED","LIST_ENDPOINT_ENABLED","ENABLE_API_DOCS","GIGACHAT_CREDENTIALS","GIGACHAT_ACCESS_TOKEN")]; raise SystemExit(pytest.main(["-q","-r","fEs","--tb=no"]))'
dc_demo exec -T api ruff check app tests alembic scripts
dc_demo exec -T api ruff format --check app tests alembic scripts
```

`dc_demo` определена в [installation.md](installation.md). Для сетевого образа
используйте `dc_ai` из [network-setup.md](network-setup.md) во всех командах.
В PowerShell нужен PowerShell 7.3+ с современным режимом передачи native-аргументов;
в Windows PowerShell 5.1 кавычки внутри `python -c` могут потеряться — выполняйте
блок из Bash/WSL. Команда убирает ключи и конфликтующие псевдонимы флагов только
из процесса pytest; работающий API не меняется. Значения не печатаются.

Не запускайте эти тесты против рабочей БД. В template-образе ожидаемы пропуски
тестов, которым нужна библиотека AI. Для старого RAG-моста добавьте
`-e RAG_TEST_DATA_DIR=/run/rag` перед `api` и смонтируйте свежую поставку по
[rag-local.md](rag-local.md). В Dockerfile корневой `sample_api_response.json`
сейчас не копируется автоматически, поэтому явная `cp` выше необходима.

## Smoke и golden

`scripts/smoke_e2e.sh` всегда принудительно выбирает `template`, создаёт отдельный
проект, проверяет оба синтетических справочника и удаляет только свой тестовый том.
Порт должен быть свободен. Bash нужен на macOS/Linux; Windows используйте WSL или
Git Bash:

В этом же Bash-терминале должны быть заданы `POSTGRES_DB`, `POSTGRES_USER`,
`POSTGRES_PASSWORD` и оба `WEB_USER`/`WEB_PASSWORD` для защищённого smoke, как в
установке. Окружение ранее открытого Git Bash не обновляется из PowerShell.

```bash
COMPOSE_PROJECT_NAME="vrach-smoke-$(date +%Y%m%d-%H%M%S)-$$" \
  WEB_PORT=18083 RUN_POSTGRES_TESTS=0 bash scripts/smoke_e2e.sh
```

`RUN_POSTGRES_TESTS=1` добавит PostgreSQL-проверки внутри smoke. Это template-образ;
для полной проверки AI-интеграции нужен отдельный прогон сетевого образа.
При ошибках `bash\r` на Windows проверьте LF у shell-файлов либо используйте
checkout внутри WSL; системные Git-настройки менять ради этого необязательно.

`capture_golden.py` и `compare_golden.py` применяйте только к `template`-стеку с
активным `demo-v2`. Network-результаты с golden не сравниваются и снимки не
перезаписываются.
Оба скрипта делают новые POST четырёх файлов и берут `WEB_URL`, `WEB_USER`,
`WEB_PASSWORD` из окружения хоста. Снимки пользователя `tests/golden/*.json`
не публикуются без отдельного решения; приватные отчёты хранятся в `.local/`.

Сетевая приёмка, сохранённый AI-отчёт, цитаты и повторное чтение без сети описаны
в [category-fix-acceptance.md](category-fix-acceptance.md).
