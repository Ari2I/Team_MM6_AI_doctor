# Установка на Windows, macOS и Linux

Эта инструкция запускает текущую демонстрацию без внешнего AI: `template` с
синтетическим справочником `demo-api-2`. Для GigaChat переходите к
[сетевой сборке](network-setup.md). Работайте из корня репозитория.

## Инструменты

| ОС | Установить | Терминал |
| --- | --- | --- |
| Windows | [Git for Windows](https://git-scm.com/downloads/win), [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/) с Linux-контейнерами и WSL 2, [PowerShell](https://learn.microsoft.com/powershell/scripting/install/installing-powershell-on-windows) | PowerShell 7.3+; Git Bash/WSL для smoke |
| macOS | [Git](https://git-scm.com/downloads/mac), [Docker Desktop](https://docs.docker.com/desktop/setup/install/mac-install/) | Terminal, Bash или zsh |
| Linux | Git из пакетного менеджера, [Docker Engine](https://docs.docker.com/engine/install/) и [Compose plugin](https://docs.docker.com/compose/install/linux/) | Bash |

На macOS выберите вариант Docker Desktop для своей архитектуры. На Windows
включите виртуализацию и WSL 2 по подсказкам Docker Desktop. На Linux настройте
доступ текущего пользователя к Docker; не выдавайте Docker-сокету права `666`.

Windows: установите Git с доступом из командной строки, Docker Desktop и PowerShell;
после установки откройте новое окно PowerShell. В настройках Docker Desktop
выберите WSL 2 backend и Linux containers. Если установщик просит перезагрузку,
выполните её до запуска проекта. PowerShell 7 — отдельная программа `pwsh`,
а не встроенный Windows PowerShell 5.1.

macOS: Git можно установить командой `xcode-select --install` и завершить диалог
установки Command Line Tools. Затем установите Docker Desktop для Apple Silicon
или Intel, откройте приложение и дождитесь готовности Engine.

Linux: сначала установите Git (например, `sudo apt install git` на Debian/Ubuntu
или `sudo dnf install git` на Fedora). Затем выполните официальную инструкцию
Docker Engine **для своего дистрибутива**, включая Compose plugin. После этого
проверьте доступ текущего пользователя к Engine без запуска всего проекта от root.
Установка отдельных локальных Python/PostgreSQL/Node для Docker-пути не нужна.

Проверьте установку:

```text
git --version
docker version
docker compose version
docker compose up --help
```

У `docker compose up --help` должен быть флаг `--wait`. Если Docker Engine не
запущен, сначала запустите Docker Desktop или службу Docker.

Если репозитория ещё нет:

```text
git clone https://github.com/Ari2I/Team_MM6_AI_doctor.git Proekt_Prac_2
cd Proekt_Prac_2
git status --short --branch
```

`clone` получает ветку по умолчанию на сервере. Перед запуском убедитесь, что
в неё уже перенесена итоговая сборка с `app/`, `frontend/` и `ai/`. Иначе получите
опубликованную командой интеграционную ветку/тег (`git clone --branch ИМЯ ...`).
Не используйте устаревшую `demo_07.11`. Инструкция не переключает имеющиеся ветки.

## Окружение Compose

Ниже приведены безопасные имена для новой демо-БД. Пароля по умолчанию нет.
Для существующей БД нужно сохранить прежний проект, пользователя и пароль:
новое значение `POSTGRES_PASSWORD` не меняет пароль в уже созданном томе.

### macOS/Linux (Bash или zsh)

```bash
export VrachProject=vrach-demo
export POSTGRES_DB=vrach POSTGRES_USER=vrach
export WEB_PORT=18081 WEB_USER=demo_check
export AI_MODE=template API_BUILD_TARGET=template AI_CORPUS_PATH=
export DOCS_ENABLED=false LIST_ENDPOINT_ENABLED=false

set_private_env() {
  printf '%s: ' "$1"
  IFS= read -r -s "$1"
  printf '\n'
  export "$1"
}
set_private_env POSTGRES_PASSWORD
set_private_env WEB_PASSWORD

dc_demo() {
  docker compose --env-file /dev/null -p "$VrachProject" -f docker-compose.yml "$@"
}
```

### Windows (PowerShell 7)

```powershell
$script:VrachProject = 'vrach-demo'
$env:POSTGRES_DB = 'vrach'
$env:POSTGRES_USER = 'vrach'
$env:WEB_PORT = '18081'
$env:WEB_USER = 'demo_check'
$env:AI_MODE = 'template'
$env:API_BUILD_TARGET = 'template'
$env:AI_CORPUS_PATH = ''
$env:DOCS_ENABLED = 'false'
$env:LIST_ENDPOINT_ENABLED = 'false'

function Set-PrivateEnv([string]$Name) {
    $secure = Read-Host $Name -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        [Environment]::SetEnvironmentVariable(
            $Name, [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer), 'Process')
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        $secure.Dispose()
    }
}
Set-PrivateEnv POSTGRES_PASSWORD
Set-PrivateEnv WEB_PASSWORD

function dc_demo {
    & docker compose --env-file NUL -p $script:VrachProject -f docker-compose.yml @args
}
```

`/dev/null` и `NUL` не загружают `.env`; Compose получает только переменные
текущего процесса. Не используйте `env`, `printenv`, `Get-ChildItem Env:`,
`docker inspect` или `docker compose config` для диагностики: вывод может содержать
секреты.

Значения доступны только процессу терминала и его дочерним процессам. После
закрытия окна заново задайте те же настройки и функции; не придумывайте новый
пароль для существующей БД. Введите непустые пароли. Если заполнено только одно
из `WEB_USER`/`WEB_PASSWORD`, nginx выключает Basic Auth.

## Сборка и запуск

Выполняйте команды по одной:

```text
dc_demo build api web
dc_demo up -d --wait --wait-timeout 180 db
dc_demo run --rm --no-deps api alembic upgrade head
dc_demo up -d --no-build --wait --wait-timeout 180 api web
dc_demo exec -T api python -m app.scripts.import_reference data/reference/demo-v2.json --activate
dc_demo ps
```

Импорт выполняется один раз на новой демо-БД. Повторный импорт той же версии
отклоняется. Откройте `http://localhost:<WEB_PORT>/` и войдите по Basic Auth.
`/health` открыт, `/docs` и список заявок по умолчанию закрыты и возвращают 404.

Загрузите файлы из `data/demo/`:

| Файл | Ожидание в `template` с `demo-api-2` |
| --- | --- |
| `correct.xlsx` | Категории А/Б/В, green |
| `typo.xlsx` | Проверка человеком, yellow |
| `unknown.xlsx` | Категория не установлена, undetermined |
| `two_diagnoses.xlsx` | Отдельные проверки по диагнозам |

Названия, МНН и правила этого справочника синтетические. В `template` внешний
AI не вызывается; отсутствие AI-отчёта ожидаемо, карточка и Excel работают.
Многолистовые AI-книги требуют сетевого режима.

## Остановка и обновление

```text
dc_demo up -d --no-build --wait
dc_demo stop
```

При обновлении существующего стека с уже работающей БД:

```text
dc_demo build api web
dc_demo stop api web
dc_demo run --rm --no-deps api alembic upgrade head
dc_demo up -d --no-build --force-recreate --wait --wait-timeout 180 api web
```

При ошибке остановитесь; не обслуживайте загрузки до завершения миграции.
Импорт справочника при каждом старте не повторяется. `dc_demo down` сохраняет данные.
Не добавляйте `--volumes`/`-v` к рабочему проекту: это удаляет БД.
Переменные nginx применяются при пересоздании `web`; `restart` их не обновляет.

## Частые проблемы

- **Порт занят:** выберите свободный `WEB_PORT` и пересоздайте `web`.
- **401:** проверьте свои значения `WEB_USER`/`WEB_PASSWORD`; пересоздайте `web`.
- **404 на `/docs` или списке:** это штатные ограничения защищённой сборки.
- **Нет категорий:** проверьте импорт `demo-v2` в БД того же Compose-проекта.
- **Ошибка PostgreSQL password authentication:** сохранённая БД ожидает старый
  пароль; новая переменная его не меняет. Не удаляйте том ради обхода ошибки.
- **Windows и Bash:** для `scripts/smoke_e2e.sh` используйте WSL или Git Bash;
  обычный Compose можно выполнять из PowerShell.
- **Bind mount на Docker Desktop:** разрешите Docker доступ к каталогу проекта
  в настройках файлового доступа; на macOS учитывайте системные разрешения.
  Для `certs/gigachat-ca.pem` нужен существующий файл, а не автоматически созданный каталог.
