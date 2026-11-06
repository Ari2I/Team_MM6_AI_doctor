# Демо ИИ-ассистента «Врач»

Веб-приложение проверяет покупки из Excel по каждому диагнозу, сохраняет заявку в PostgreSQL и формирует Excel и HTML. Используется демонстрационный набор правил. Импорт читает первый лист в пределах 2 200 строк и 64 столбцов. Клинические фрагменты помогают сверить основания. Решение о выплате принимает комиссия.

## Запуск с Docker Compose

Откройте PowerShell в checkout ветки `demo_07.11`. Нужен запущенный Docker Desktop с Compose.

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
docker compose up --build -d
docker compose exec api alembic upgrade head
docker compose exec api python -m app.services.reference_seed
Invoke-RestMethod http://127.0.0.1:8000/api/ready
Start-Process 'http://127.0.0.1:8000/'
```

Если порт 8000 занят, задайте `API_PORT` в `.env` и используйте этот порт в адресах. Для остановки выполните `docker compose down`. База останется в локальном томе Compose.

## Запуск без Docker

Нужны Python 3.11, Node.js 24 с npm и PostgreSQL 16. Создайте пустую БД, укажите её хост, порт, имя, пользователя и пароль в отдельном `.env` этого checkout. Переменные можно задать в PowerShell вместо файла.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
npm --prefix frontend ci
npm --prefix frontend run build
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m app.services.reference_seed
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Откройте [веб-интерфейс](http://127.0.0.1:8000/) и [документацию API](http://127.0.0.1:8000/docs). Для разработки интерфейса запустите `npm --prefix frontend run dev` при работающем API на порту 8000. Vite проксирует запросы к API.

## Сценарий защиты на 3–4 минуты

1. Скачайте «Демо Excel» и загрузите его в режиме «Локальный». На экране появятся 4 покупки, 2 диагноза, 8 пар, 3 подтверждённых основания и известная сумма 1 000 ₽. Повтор столбца «Сумма» виден в замечаниях.
2. Откройте диагноз «Артериальная гипертензия» и разверните источник первой покупки «Норваск». Видны категория А, фрагмент, версия и ссылка. Для той же покупки по диагнозу «Сахарный диабет 2 типа» основание требует сверки. Сверьте неизвестный препарат и серый индикатор.
3. Скачайте Excel и HTML. Обновите страницу. Тот же результат загружается из PostgreSQL по адресу с `?application=<uuid>`.
4. Сверьте пары с `examples/expected.json`. Совпадение 8 из 8 относится к этому примеру. Три пары из восьми имеют подтверждённое основание. Методика оценки 95% на эталонном наборе требует согласования.

Категория А в этом сценарии означает совпадение по правилу демо. Фрагмент о сочетанной терапии для второго диагноза требует медицинских сведений о схеме, которых в Excel нет. Дозировка, упаковки и длительность курса остаются вне проверки. Источники и версии зафиксированы в `data/demo/reference.json`.

## GigaChat и поиск

Для живого режима задайте в окружении API или его `.env` ровно одну переменную, `GIGACHAT_CREDENTIALS` либо `GIGACHAT_ACCESS_TOKEN`. Для настройки см. [документацию GigaChat о сертификатах](https://developers.sber.ru/docs/ru/gigachat/certificates). При необходимости укажите `GIGACHAT_CA_BUNDLE` как путь к доверенному PEM, доступный процессу API. При запуске в контейнере подключите PEM только для чтения по указанному пути. Проверка TLS включена. После обновления окружения пересоздайте контейнер API командой `docker compose up -d --force-recreate api`.

Выберите GigaChat в форме и загрузите исходный `examples/demo.xlsx`. Хэш проверяется перед обработкой. Передаются только контекст подтверждённой пары и разрешённый фрагмент. При ошибке провайдера, цитаты или бюджета времени интерфейс показывает сохранённый локальный результат и причину. Если запустить `examples/make_demo.py`, ZIP-метаданные новой копии изменят хэш. Для живого режима используйте файл из ветки.

По умолчанию используется точная выборка по МНН и диагнозу с лексическим ранжированием. Для локального векторного режима установите `.[vector]` и задайте `DEMO_RETRIEVAL=vector`. Модель должна быть в кэше процесса. Её можно загрузить заранее командой `python run.py --retrieval vector --download-embeddings --output .local/vector.json`. При недоступной модели API использует лексический поиск и отображает фактический метод.

## Проверки и прежний CLI

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe run.py --output .local/cli-result.json
npm --prefix frontend run build
```

Интеграционные тесты используют отдельную схему в PostgreSQL. Перед `pytest` задайте `DEMO_TEST_DATABASE_URL` в формате `postgresql+psycopg://user:password@127.0.0.1:5432/test_db` для тестовой БД с правом создания схем. Тест локальной модели запускается при `LLM_RAG_TEST_LOCAL_EMBEDDINGS=1` и наличии кэша.

`GET /health` проверяет процесс. `GET /api/ready` проверяет хранилище, миграцию и активную версию справочника. `POST /api/applications` принимает `file` и `mode` как multipart. По UUID доступны сохранённый результат и выгрузки по путям `/api/applications/{id}`, `/api/applications/{id}/result.xlsx` и `/api/applications/{id}/report.html`.
