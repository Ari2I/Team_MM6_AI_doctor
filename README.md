# Врач — отдельный фронтенд

React 19 + TypeScript + Vite. Ветка `front` содержит интерфейс из
[`demo_07.11`, `6e61dc8`](https://github.com/Ari2I/Team_MM6_AI_doctor/tree/6e61dc822b999d08fea480065a008537f9f360ab/frontend).
История изменений папки `frontend/` сохранена; её содержимое перенесено в корень.
Исходники интерфейса и lock-файл перенесены без изменений.

## Быстрый запуск

Требуется Node.js 22.12+ (или 24 LTS) и npm. Python, БД и ключи GigaChat
для установки, запуска интерфейса и сборки не нужны.

```sh
git clone --branch front --single-branch https://github.com/Ari2I/Team_MM6_AI_doctor.git doctor-front
cd doctor-front
npm ci
npm run dev
```

Откройте адрес из вывода Vite (обычно http://127.0.0.1:5173).
Без backend доступна форма загрузки; обработка Excel и получение результатов
требуют совместимого API. Ветка `llm_rag` — автономная библиотека, а не этот API.
Совместимый backend есть в исходной ветке `demo_07.11`; его настройка описана там.

## Подключение backend

Интерфейс обращается к `/api` на своём домене. При `npm run dev` Vite
проксирует эти запросы на `http://127.0.0.1:8000`. Чтобы подключить другой
сервер, измените адрес `server.proxy['/api']` в `vite.config.ts` и перезапустите Vite.
Путь `/api` должен сохраняться при проксировании, в том числе для скачивания файлов.
Секреты и настройки модели остаются на backend.

Используемый контракт ответа описан в `src/contract.ts`:

| Запрос | Назначение |
| --- | --- |
| `POST /api/applications` | Загрузка XLSX как `multipart/form-data`: `file`, `reference=official`, при уточнении `sheet` и `columns` (JSON с индексами столбцов от нуля) |
| `GET /api/applications/{id}` | Открытие сохранённого результата, в том числе через `?application={id}` |
| `GET /api/applications/{id}/result.xlsx` | Скачивание Excel |
| `GET /api/applications/{id}/report.html` | Скачивание HTML-отчёта |

При успешной загрузке API возвращает JSON `Result` (HTTP 201); при чтении — тот же
контракт (HTTP 200). HTTP 409 с `detail.layout` запрашивает выбор листа/столбцов;
`detail.issues` содержит сообщения об ошибках. Существующий интерфейс использует
контракт демо; совместимость с другими backend нужно проверять отдельно.

## Сборка и размещение

```sh
npm run build
npx vite preview --host 127.0.0.1
```

Сборка проверяет TypeScript и создаёт статические файлы в `dist/`. Их можно
разместить отдельно от Python-приложения. Пример конфигурации nginx:

```nginx
server {
    listen 80;
    root /srv/doctor-front/dist;
    index index.html;

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_read_timeout 300s;
        client_max_body_size 10m;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

Адрес backend и лимит загрузки настройте под своё окружение. `vite preview`
служит для локального просмотра сборки; для рабочего размещения используйте
статический сервер с прокси `/api`. Dev-прокси не встраивается в `dist/`.
Backend, данные, справочники и результаты заявок в этой ветке отсутствуют.
