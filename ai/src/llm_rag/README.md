# Рабочая библиотека llm_rag

| Область | Основные модули |
| --- | --- |
| Встраиваемая многодиагнозная обработка | integration, integration_contracts, integration_policy, integration_budget |
| Excel и публичный отчёт | integration_excel, integration_report, human_report |
| Сеть и данные | runtime, network_policy, embeddings, rag_data, file_lock, paths |
| ML-2 | ml2/registry, pipeline, classifier, official, clinicalrecs, grls, provider, policy, form_matching |
| Основания и рекомендации | evidence_snapshot, explanation_contracts, explanation, explanation_summary, staff_contracts, staff_report |
| Однодиагнозный путь с корпусом | contracts, corpus, retrieval, vector_index, evidence, generation, validation, summary, pipeline |
| Адаптеры API и локальный резерв | providers/gigachat, gateway, base, template |
| Диагностика и точки входа | diagnostics, __main__, excel_scenario |

Новый потребитель вызывает analyze_request. Модули оркестрации используют
общую обработку ML2Pipeline.evaluate_pair; деньги/количество не подставляются
ради старой модели Original. Статусы и происхождение хранятся отдельно от
human_report. Внешние адаптеры не получают произвольный контекст заявки.

Ресурсы [инструкций ML-2](ml2/instructions/README.md) входят в wheel/sdist.
Рабочие данные и векторы сюда не копируются: см. [data/rag](../../data/rag/README.md).
Примеры имеют один исходник в examples и устанавливаются отдельно как данные
пакета. Личные пути, кэши и настройки не должны становиться импортируемыми ресурсами.

См. [корневой README](../../README.md), [контракт входа](../../docs/integration-input.md),
[результат](../../docs/integration-result.md) и [проверки](../../tests/README.md).
