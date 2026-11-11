# ML-2 в ветке llm_rag

Перенос личного блока из демо `0996cc1` на восстановленную командную ветку
`origin/llm_rag` (`3477f59`). Все изменения — новые файлы пакета `llm_rag.ml2`,
свои примеры в `examples/ml2` и свои проверки в `tests/ml2`.
Существующие файлы LLM/RAG не изменены.

Пакет сопоставляет торговое название с МНН, выбирает применимую клиническую
рекомендацию, извлекает фрагменты лечения и проверяет дословность цитат GigaChat.
Результат передаётся коллеге как существующий `AnalysisRequest` версии `1.0`.
Полные цитаты с URL, версией, локатором и статусом хранятся также в отдельном JSON.

## Установка и локальный пример

Нужны Python >=3.11 и зависимости основного проекта. Дополнение для импорта
официальных ZIP/XLSX ГРЛС (для готового JSON оно не требуется):

```powershell
python -m pip install -e .
python -m pip install -r src/llm_rag/ml2/requirements.txt
```

Пример полностью синтетический. `examples/ml2/registry.synthetic.json` служит
для проверки структуры обмена, а не заменяет настоящий ГРЛС.
`examples/corpus.json` — демонстрационные правила, не утверждённая медицинская база.

```powershell
python -m llm_rag.ml2 --request examples/request.json --registry examples/ml2/registry.synthetic.json --approved-corpus examples/corpus.json --output .local/ml2/upstream.json --details .local/ml2/details.json
python -m llm_rag --request .local/ml2/upstream.json --corpus examples/corpus.json --output .local/ml2/rag-result.json
```

Первый запуск выдаёт результат ML-2; второй вызывает существующий RAG коллеги.
Внешних запросов в этом примере нет. Суммы строк сохраняются и повторно на
количество не умножаются. Дозировка имеет статус `INSUFFICIENT_DATA`, поскольку
полная схема приёма отсутствует. Поэтому даже совпавшее правило может дать
`INSUFFICIENT_DATA` на стадии объяснений; подтверждение категории и дозировки
в контракте RAG проверяется раздельно.

## Реальный справочник и публичные источники

Получение актуального датированного ZIP ГРЛС и создание собственного JSON:

```powershell
python -m llm_rag.ml2.grls --output .local/grls/registry.json
```

Повторный импорт из уже скачанного ZIP:

```powershell
python -m llm_rag.ml2.grls --archive PATH_TO_GRLS_ZIP --output .local/grls/registry.json
```

Старый JSON-снимок демо поддерживается на входе только как источник
`registrations`, `version`, `as_of`. Модели БД, клинические страницы и правила
из него не импортируются. Справочник сопоставляется по нормализованному названию;
содержание в форме выпуска помогает различать записи. Неизвестное или
неоднозначное название не разрешается автоматически по похожему написанию.
Состав с несколькими МНН возвращается на сверку.

Пример с синтетической покупкой сальбутамола и существующим ключом:

```powershell
python -m llm_rag.ml2 --request examples/ml2/request.synthetic.json --registry .local/grls/registry.json --live-sources --enable-api --env-file PATH_TO_ENV --output .local/ml2/upstream.json --details .local/ml2/details.json
```

`--live-sources` отдельно разрешает публичный каталог/карточки КР;
`--enable-api` отдельно разрешает GigaChat. По умолчанию оба отключены.
Данные заявки должны иметь `is_demo: true`. Настройки берутся из окружения
или явно указанного `.env`; секреты не копируются в исходники и результаты.
Поддержаны `GIGACHAT_CREDENTIALS` или `GIGACHAT_ACCESS_TOKEN`, `GIGACHAT_MODEL`,
`GIGACHAT_SCOPE`, `GIGACHAT_CA_BUNDLE_FILE` (также прежнее `GIGACHAT_CA_BUNDLE`).
По умолчанию этот ML-2 запуск сохраняет B2B scope из демо. Другой тип доступа
задаётся явно через `GIGACHAT_SCOPE`.

Код МКБ-10 читается из `--icd10`, `context.icd10` или начала `diagnosis`.
Возраст: `context.age_group` (`Взрослые`/`Дети`, также adults/children)
либо `context.age` в полных годах. Выбор редакции задаётся через
`context.recommendation_edition`. Возрастные ограничения всё равно проверяются.
Если несколько редакций остаются применимыми, результат — `NEEDS_CONTEXT`.
Источник — выбранный пользователем каталог КлиническиеРекомендации.рф;
проверяются паспорт, статус, принадлежность Минздраву и ссылка редакции.
Кэш хранится сутки, при обновлении используются ETag/Last-Modified.
Устаревший fallback даёт `NEEDS_REVIEW`, без назначения категории.
Проверяется опубликованная копия; это не независимая сверка полного оригинального PDF.

Для воспроизводимой обработки можно передать `--fragments FILE`: JSON-словарь
`МНН -> список EvidenceFragment` (поля `fragment_id`, `source_id`, `title`,
`version`, `url`, `location`, `text`, необязательный `copy_url`).
Такие фрагменты уже должны быть выбраны вызывающим кодом для нужного диагноза
и клинического контекста. Сам файл фрагментов не доказывает применимость документа.

## Категория и передача коллеге

Критерии и модельная инструкция находятся в отдельных читаемых файлах:

- [`instructions/classification_rules.json`](instructions/classification_rules.json) —
  определения ролей, буквы, причины и проверки цитат;
- [`instructions/classification_policy.md`](instructions/classification_policy.md) —
  определения А/Б/В, типы тезисов и условия неопределённости;
- [`instructions/evidence_extraction.md`](instructions/evidence_extraction.md) —
  инструкция извлечения, формат ответа и требования к цитатам;
- [`instructions/README.md`](instructions/README.md) — версия, порядок загрузки,
  использование на стороне RAG и различие между инструкцией, правилами и КР.

`classifier.py` собирает системный промпт из этих файлов через `policy.py`.
Для чтения тех же критериев в RAG доступна функция
`from llm_rag.ml2 import load_classification_policy`.
Сам конвейер пояснений это изменение не подключает и не меняет.

Рабочая версия `ml2-policy.2` предлагает предварительные А/Б/В по КР без
обязательного Master List. Определения, соответствие буквам и проверки цитат
хранятся в `instructions/classification_rules.json` и включаются в промпт через
`classification_policy.md`. А — лечение заявленного заболевания по КР;
Б — сопутствующая терапия; В — явно опциональное средство или недоказанная
эффективность. Предварительная А не означает экспертного подтверждения
«золотого стандарта». Отсутствие упоминания и противопоказание не дают В.

При недостатке контекста, конфликте, отсутствии основания или технической ошибке
категория остаётся пустой с отдельным статусом. Цитаты проверяются дословно.
В деталях сохраняются `policy_version`, `model_assessment` и итоговый `assessment`.

Применимое правило из переданного `approved_corpus` имеет приоритет. Диагноз,
МНН и `required_context` должны совпасть; причина передаётся дословно как
`upstream_reason`. Модель не создаёт утверждённые правила. Если правила нет,
предложенная по КР категория получает `PROPOSED` и `rule_applied=false`.
Медицинская точность и цель 95% пока не подтверждены.

Программный вызов без изменения конвейера коллеги:

```python
from llm_rag.ml2 import ML2Pipeline, RegistrySnapshot
from llm_rag.pipeline import Pipeline

batch = ML2Pipeline(
    registry=RegistrySnapshot.load(registry_path), approved_corpus=approved_corpus
).run(request)
explanation = Pipeline(explanation_corpus, rag_provider).run(batch.request)
full_ml2_evidence = batch.details()
```

Для модели передайте `complete_json=gigachat_completer(provider)` с новым
`llm_rag.ml2.provider.ML2GigaChatProvider`. Это наследник существующего клиента;
его исходный файл не менялся. В `batch.request` поля строки, МНН, категория,
причина, `source_refs`, дозировка и статусы совместимы с текущим RAG.
Полные ML-2 цитаты доступны в `batch.details()`.

RAG самостоятельно сверяет переданную причину по своему корпусу. Если там нет
соответствующей записи, возможен `UNCONFIRMED`/`INSUFFICIENT_DATA` при сохранённой
предварительной категории ML-2. Подгонка корпуса под свободный ответ модели
не выполняется. Для объяснения по реальной КР коллеге нужен согласованный корпус
с этими источниками и утверждениями.

## Что заменено при переносе

`ml2_classifier` стал `classifier.py`, `ml2_clinicalrecs_source` — `clinicalrecs.py`,
`ml2_grls_source` — `grls.py`. Вместо демо `OfficialIndex` создан собственный
`registry.py`. Вместо обработки SQLAlchemy-заявки и сохранения `ItemCheck`
работает `pipeline.py`, возвращающий `AnalysisRequest` и подробный результат.
Демо-адаптер `ml2_rag_handoff` заменён прямой передачей `batch.request` в `Pipeline.run`.
Сборка общего clinical snapshot для backend заменена независимыми снимком ГРЛС
и кэшем карточек КР. Веб-маршруты, таблицы БД, экспорт и элементы UI пакету не нужны.

Проверки нового блока: `python -m pytest tests/ml2 -q`.
