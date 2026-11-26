# Интеграция LLM/RAG с бэкендом и фронтендом

## Бэкенд

### Вход в LLM/RAG

После разбора XLSX соберите JSON `integration-request.1`. Одна запись в `links`
задаёт проверку одной покупки по одному диагнозу.

```json
{
  "schema_version": "integration-request.1",
  "request_id": "R1",
  "diagnoses": [{"diagnosis_id": "D1", "name": "Тестовый диагноз", "conditions": {"age_group": "adult"}}],
  "purchases": [{"item_id": "P1", "name": "Тестовый препарат", "amount": "100.10", "currency": "RUB"}],
  "links": [{"item_id": "P1", "diagnosis_id": "D1"}]
}
```

| Поле | Формат |
| --- | --- |
| `request_id` | Непустая строка с ID заявки. |
| `diagnoses[]` | Уникальный в заявке `diagnosis_id` и непустой `name`. Опционально `recommendation_edition` (ID редакции КР), `icd10` (например, `J45.0`) и объект `conditions` с `age_group` (`adult` или `child`), `age_years` (неотрицательная десятичная строка) и `pregnant` (булево значение). |
| `purchases[]` | Уникальный в заявке `item_id` и непустой `name`. Опционально `inn` (МНН), `dosage` (дозировка), `quantity` (положительная десятичная строка), `amount` (неотрицательная десятичная строка за всю покупку) и `currency` (три заглавные латинские буквы). |
| `links[]` | Уникальная пара `item_id` и `diagnosis_id` из этой заявки. Для проверки одной покупки по двум диагнозам передайте две связи. |
| `versions` | Опциональный объект с закреплёнными версиями `registry`, `clinical`, `corpus`, `policy` (строки). |

Все три списка обязательны и содержат минимум один элемент. Пропустите неизвестное
опциональное значение или передайте `null` для отдельного поля. `conditions` и
`versions` можно опустить целиком, при передаче это объекты. Для десятичных строк
используйте точку. Валидация отклоняет лишние поля, повторные связи и неизвестные ID.
Сумма одной покупки в JSON и XLSX лежит в диапазоне от 0 до 999999999999.999
включительно. В JSON укажите до 12 целых и до 3 дробных цифр без экспоненты.

Если бэкенд уже хранит `adult` или `child`, передайте это значение в
`conditions.age_group`. `age_years` передавайте при известном точном возрасте.
При заполнении обоих полей валидатор сверяет их по границе 18 лет. По группе
фасад выбирает редакцию КР и проверяет применимость фрагментов. Числовые
ограничения, требующие точного возраста, остаются на сверке при его отсутствии.

### Вызов и выход

Установите пакет `llm-rag` (Python 3.11+). Пример адаптера для JSON в виде строки или байтов.

```python
from llm_rag.integration import analyze_request
from llm_rag.integration_contracts import IntegrationRequest


def analyze_payload(payload: str | bytes):
    request = IntegrationRequest.model_validate_json(payload)
    result = analyze_request(request)
    return result.model_dump(mode="json"), result.human_report
```

Фасад сам получает реестр и клинические рекомендации. Локально тот же вызов выполняет `check_local.py`. Ошибки входа возвращает
`IntegrationRequest.model_validate_json`.

| Поле результата `integration-result.1` | Что делать |
| --- | --- |
| `request` | Исходная заявка. |
| `pairs[]`, `unprocessed_pairs[]` | Сопоставляйте по `request_id`, `item_id`, `diagnosis_id`. Каждая входная связь представлена ровно один раз в этих двух списках. У обработанной пары есть `decision`, `evidence_status`, `reasons`. У необработанной пары есть `reason`. |
| `completeness` | `COMPLETE` означает, что обработаны все связи. Проверяйте ошибки пары отдельно, например `SOURCE_ERROR` в `reasons`. При `PARTIAL` сохраните полученный результат и проверьте `unprocessed_pairs` и причины по парам. |
| `costs` | `totals` содержит десятичные строки по валютам. Сумма из каждой строки покупки учитывается один раз. `unknown_amount_item_ids` и `unknown_currency_item_ids` отмечают пропуски. |
| `human_report` | Верните этот объект как данные отчёта для фронтенда. Полный сериализованный результат храните на сервере. |

В `decision` категории `upstream_rule_category` и `proposed_category` имеют значения
`А`, `Б`, `В` или `null`. При `classification_basis="CONFLICT"` сохраните обе категории.
`requires_review` всегда равен `true`.

## Фронтенд

### Что отправлять

Загрузка XLSX остаётся запросом `multipart/form-data` на маршрут бэкенда. В текущей
ветке `backend` это `POST /applications` с полем `file` и, при необходимости,
повторяемым полем `diagnoses` (одна строка на диагноз). Браузерная ветка `front`
обращается к `/api/applications` через прокси. Преобразование XLSX в
`integration-request.1` выполняет бэкенд.

### Что получить и показать

Для нового экрана формат тела ответа с отчётом — объект `human-report.1` в корне
JSON. Текущий `Result` в `front/src/contract.ts` описывает прежний ответ.
Подключение `human-report.1` требует адаптации HTTP-обработчика и экрана.

Пример отчёта по тестовой заявке выше при недоступном реестре.

```json
{
  "schema_version": "human-report.1",
  "title": "Проверка заявки: R1",
  "notice": "Решение о выплате принимает специалист. Назначение лечения не выполняется.",
  "summary": "Обработано пар: 1 из 1. Покупок без указанной валюты: 0.",
  "recommendation_note": "Часть рекомендаций подготовлена в резервном режиме; требуется сверка.",
  "review_note": "Финальная сверка специалистом обязательна; нерешённые основания отмечены отдельно.",
  "items": [{
    "number": 1,
    "name": "Тестовый препарат",
    "diagnosis": "Тестовый диагноз",
    "category": "не установлена",
    "evidence": "проверку не удалось завершить",
    "comment": "Проверку не удалось завершить; требуется ручная сверка. Форма не сопоставлена.",
    "established": [],
    "uncertain": [],
    "questions": [],
    "actions": ["Передайте результат специалисту для сверки."],
    "sources": []
  }],
  "costs": [{"amount": "100.10", "currency": "RUB", "currency_label": "RUB", "unknown_amount_count": 0}],
  "purchases_without_amount_count": 0
}
```

- Показывайте `title`, `summary`, `notice`, `recommendation_note` и `review_note`
  как готовый текст. Одна строка `items` соответствует паре покупки и диагноза,
  поэтому название покупки может повторяться. `number` — номер строки отчёта.
- `category`, `evidence` и `comment` — готовый текст. `actions` — массив строк.
  Элементы `established` и `uncertain` содержат `text` и `sources`, а элементы
  `questions` дополнительно содержат `why`. Пустые списки можно скрыть.
- Элемент `sources` содержит `number` (число), `title`, `version`, `location`,
  `quote`, `url` (строки) и `copy_url` (строка или `null`). Текст цитаты показывайте
  дословно, ссылки открывайте для `http` и `https`.
- Итоги берите из `costs[]` по валютам. `amount` — строка, `currency_label` —
  подпись валюты. Число покупок без суммы берите из `purchases_without_amount_count`.
