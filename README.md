# Демо ИИ-ассистента «Врач»

Веб-приложение проверяет покупки из Excel по каждому диагнозу, сохраняет заявку в PostgreSQL и формирует Excel и HTML. Покупки проверяются по диагнозу своего блока. Предел для листа — 2 200 строк и 64 столбца. Клинические фрагменты помогают сверить основания. Решение о выплате принимает комиссия.

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

## Проверка заявки

Откройте страницу по адресу http://127.0.0.1:8000/. Для подготовленного на этой машине checkout можно выполнить в PowerShell `.\.local\start-demo.ps1`. Загружайте XLSX со столбцами «Торговое наименование», «Дозировка», «Количество», «Сумма», «Диагноз (из заключения)». Порядок столбцов свободный. При нескольких подходящих листах или двух колонках с одним назначением интерфейс запросит выбор. Непустой диагноз открывает блок на той же строке. Каждая покупка проверяется в своём блоке, в том числе при повторе названия в другом блоке.

На странице и в выгрузках XLSX и HTML отображаются исходные строки, нормализованные препараты, основания и точные причины сверки, суммы А/Б/В и позиций на сверке, рекомендация секретарю и источники. Известная сумма равна сумме заполненных строк, принятых к денежному расчёту. Категорию присваивает утверждённое правило. Клиническое упоминание без правила отображается отдельно. Решение о выплате принимает комиссия.

### Обновление официальных данных

Установите поддержку разбора PDF командой `python -m pip install -e '.[sources]'`. Получите датированный архив ГРЛС на официальном сайте. Каталог и PDF загружаются независимо от файлов заявок.

```powershell
.\.venv\Scripts\python.exe -m app.services.official_catalog --cache .local/official-inputs/rubricator --download-pdfs --pdf-limit 60
.\.venv\Scripts\python.exe -m app.services.official_import `
  --grls .local/official-inputs/grls-2026-11-06.zip --as-of 2026-11-06 `
  --evidence data/official/evidence.csv --compositions data/official/compositions.csv `
  --rubricator .local/official-inputs/rubricator --output .local/official-reference.json
```

Для следующей партии PDF используйте `--reuse-catalog --download-pdfs --pdf-limit 60`. Без `--pdf-limit` загрузчик запрашивает все ещё отсутствующие PDF. Сканы без текстового слоя, отсутствующие PDF и сбои получения имеют отдельные состояния. Поиск находит упоминания МНН в тексте доступных PDF с ссылкой на страницу. Условия назначения и категории подтверждаются проверенными выдержками и правилами. [ГРЛС](https://grls.minzdrav.gov.ru/grls.aspx) · [Рубрикатор Минздрава](https://cr.minzdrav.gov.ru/)

Снимок можно подключить через `OFFICIAL_REFERENCE_PATH`. Для Docker смонтируйте файл и укажите путь внутри контейнера. `GET /api/references` показывает объём и версии данных, в том числе число документов с доступным текстом и число правил. При импорте можно добавлять независимо подготовленные CSV кодов (`--diagnoses`), каталога (`--catalog`), выдержек (`--evidence`) и составов (`--compositions`).

### Согласованный свод А/Б/В

Укажите при импорте `--rules path/to/approved-rules.json`. Свод содержит JSON-массив. Каждая запись задаёт `rule_id`, `revision`, `evidence_id`, `category` (латинские A, B или V) и `approval_reference`. Чтобы получить ID выдержки для свода, прочитайте `entry_id` соответствующей записи `EvidenceEntry` из снимка. В CSV выдержек поле `polarity` принимает `supports` для А/Б, `excludes` для В или `context` для информационных фрагментов. Связь без согласованного правила остаётся медицинским основанием для сверки. Поставка по умолчанию содержит официальные данные и выдержки. Утверждённый свод подключается отдельным импортом.

У правила можно указать `required_purchases` со списком МНН покупок из того же блока и `manual_condition` для факта, требующего подтверждения. Присутствие покупки в блоке отражает содержимое XLSX. Для условий, требующих подтверждения назначения или схемы, задавайте `manual_condition`.

### GigaChat B2B

При наличии найденных фрагментов GigaChat выбирает ключевые цитаты для построчных пояснений и краткой сводки. Scope `GIGACHAT_API_B2B` задаётся сервером. Настройте в окружении API или его `.env` одну переменную `GIGACHAT_CREDENTIALS` либо `GIGACHAT_ACCESS_TOKEN`. Для существующего отдельного файла задайте `GIGACHAT_ENV_FILE`. При необходимости укажите `GIGACHAT_CA_BUNDLE` для доверенного PEM. Передаются публичные фрагменты и порядковые номера, а ответ принимается только для соответствующих фрагментов. Факт вызова и ошибки модели видны в отчёте. При отказе GigaChat отчёт сохраняет основания и суммы. [Авторизация](https://developers.sber.ru/docs/ru/gigachat/api/reference/rest/gigachat-api) · [Сертификаты](https://developers.sber.ru/docs/ru/gigachat/certificates)

### Независимые синтетические заявки

```powershell
.\.venv\Scripts\python.exe examples/generate_independent_applications.py `
  --seed 20261106 --count 6 --output .local/independent-applications
```

Генератор создаёт блоки из отдельного пула без чтения корпуса и результатов. Файл из раздела `examples/demo.xlsx` относится к прежнему API-сценарию. Для рабочего интерфейса подходят самостоятельно подготовленные XLSX согласованной структуры.

## Проверки и прежний CLI

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe run.py --output .local/cli-result.json
npm --prefix frontend run build
```

Интеграционные тесты используют отдельную схему в PostgreSQL. Перед `pytest` задайте `DEMO_TEST_DATABASE_URL` в формате `postgresql+psycopg://user:password@127.0.0.1:5432/test_db` для тестовой БД с правом создания схем. Тест локальной модели запускается при `LLM_RAG_TEST_LOCAL_EMBEDDINGS=1` и наличии кэша.

`GET /health` проверяет процесс. `GET /api/ready` проверяет хранилище, миграцию и активную версию справочника. `POST /api/applications` принимает `file` и `mode` как multipart. По UUID доступны сохранённый результат и выгрузки по путям `/api/applications/{id}`, `/api/applications/{id}/result.xlsx` и `/api/applications/{id}/report.html`.
