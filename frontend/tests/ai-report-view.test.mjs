import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { importSource } from './helpers/modules.mjs'
import { savedReport } from './helpers/ai-report-data.mjs'

const { AIReportView } = await importSource('../../src/components/AIReportPanel.tsx')
const { ResultView } = await importSource('../../src/components/ResultView.tsx')
const render = state => renderToStaticMarkup(createElement(AIReportView, { state, onRetry() {} }))
const show = report => render({ kind: 'report', report })

test('loading, absent and error reports have distinct states and a read retry button', () => {
  assert.match(render({ kind: 'loading' }), /role="status".*Загружаем сохранённый разбор/s)
  const absent = render({ kind: 'empty' })
  assert.match(absent, /Сохранённый разбор ИИ отсутствует/)
  assert.match(absent, /Карточка и Excel доступны/)
  const error = render({ kind: 'error', message: 'Нужен вход в систему.' })
  assert.match(error, /role="alert"/)
  assert.match(error, /Нужен вход в систему/)
  assert.match(error, /Повторить загрузку отчёта/)
  assert.doesNotMatch(error, /Повторить анализ|Проверить заново/)
})

test('full report shows saved text, questions, exact quotes and source links', () => {
  const report = savedReport()
  const html = show(report)
  for (const text of [report.human_report.summary, report.human_report.notice,
    report.human_report.review_note, 'На каких сведениях основан разбор', 'Что не подтверждено',
    'Что уточнить у специалиста', 'Для сверки основания.', 'Источники и цитаты',
    'TEST_EVIDENCE.', '1000,10 RUB']) assert.ok(html.includes(text), text)
  assert.match(html, /Дословная синтетическая цитата\.\nВторая строка: TEST_EVIDENCE\./)
  assert.match(html, /href="https:\/\/example.test\/source" target="_blank" rel="noopener noreferrer"/)
  assert.match(html, /href="#ai-source-1-1"/)
  assert.match(html, /id="ai-source-1-1"/)
  assert.match(html, /Все пары обработаны/)
  assert.match(html, /Подготовлен моделью/)
  assert.match(html, /Б \(предварительное предложение модели\)/)
})

test('disabled backend reference is not shown as a second category source', () => {
  const report = savedReport()
  report.checks[0].backend_rule.category = 'А'
  report.checks[0].backend_rule.source = 'SQL-REFERENCE-MARKER'
  const html = show(report)
  assert.doesNotMatch(html, /SQL-REFERENCE-MARKER|Правило справочника|Правило корпуса ИИ/)
  assert.match(html, /Б \(предварительное предложение модели\)/)
})

test('partial result, no evidence, fallback, conflict and form warning remain explicit', () => {
  const report = savedReport()
  report.completeness = 'PARTIAL'
  Object.assign(report.checks[0], { processed: false, ai_proposed_category: null,
    generation_mode: null, reasons: ['BUDGET_EXHAUSTED', 'NO_EVIDENCE', 'constructor', 'UNRECOGNIZED_REASON'] })
  Object.assign(report.human_report.items[0], { category: 'не установлена',
    established: [], uncertain: [], questions: [], sources: [] })
  let html = show(report)
  assert.match(html, /Частичный результат/)
  assert.match(html, /Проверка не завершена/)
  assert.match(html, /Пояснения не получены/)
  assert.doesNotMatch(html, /Локальный разбор|Подготовлен моделью/)
  assert.match(html, /не установлена/)
  assert.match(html, /Подтверждённые цитаты для этой пары отсутствуют/)
  assert.match(html, /Не хватило ресурса для завершения проверки/)
  assert.doesNotMatch(html, /Источни[кк] 1|UNRECOGNIZED_REASON|BUDGET_EXHAUSTED|constructor/)
  assert.doesNotMatch(html, /Что не подтверждено|Что уточнить у специалиста/)
  report.completeness = 'COMPLETE'
  Object.assign(report.checks[0], { processed: true, generation_mode: 'FALLBACK', form_status: 'MISMATCH' })
  html = show(report)
  assert.match(html, /Все пары обработаны/)
  assert.match(html, /Часть пояснений подготовлена в резервном режиме/)
  assert.match(html, /Сверьте форму и концентрацию/)
  assert.doesNotMatch(html, /Подготовлен моделью/)
  Object.assign(report.checks[0], { conflict: true, classification_basis: 'CONFLICT',
    ai_rule_category: 'А', ai_proposed_category: 'В' })
  report.human_report.items[0].category = 'конфликт: по правилу А; предложение модели В'
  html = show(report)
  assert.match(html, /Категории расходятся/)
  assert.match(html, /По правилу корпуса/)
  assert.match(html, /Предложение ИИ/)
  assert.match(html, /Категория А/)
  assert.match(html, /Категория В/)
})

test('same purchase with two diagnoses has separate source anchors and one saved total', () => {
  const report = savedReport()
  report.human_report.items.push({ ...structuredClone(report.human_report.items[0]),
    number: 2, diagnosis: 'Другой синтетический диагноз' })
  report.checks.push({ ...report.checks[0], number: 2, diagnosis_id: 'another-diagnosis' })
  const html = show(report)
  assert.match(html, /id="ai-source-1-1"/)
  assert.match(html, /id="ai-source-2-1"/)
  assert.match(html, /Другой синтетический диагноз/)
  assert.equal(html.split('1000,10 RUB').length - 1, 1)
})

test('model and source text are escaped and unsafe source addresses are never linked', () => {
  const report = savedReport()
  const row = report.human_report.items[0]
  row.comment = '<img src=x onerror=alert(1)>'
  row.sources[0].quote = '<script>unsafe()</script>'
  row.sources[0].url = 'javascript:alert(1)'
  row.sources[0].copy_url = 'data:text/html,unsafe'
  const html = show(report)
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/)
  assert.match(html, /&lt;script&gt;unsafe\(\)&lt;\/script&gt;/)
  assert.doesNotMatch(html, /<img|<script|href="javascript:|href="data:/)
})

test('unknown amounts and absent costs are shown without inventing zero totals', () => {
  const report = savedReport()
  report.human_report.costs = []
  report.human_report.purchases_without_amount_count = 2
  const html = show(report)
  assert.match(html, /Итоги по суммам отсутствуют/)
  assert.match(html, /Покупок без суммы: 2/)
  assert.doesNotMatch(html, /0 RUB|0,00/)
})

test('result page keeps Excel and mounts the AI section after purchases only for analyzed cards', async () => {
  const card = JSON.parse(await readFile(new URL('../../sample_api_response.json', import.meta.url), 'utf8'))
  let html = renderToStaticMarkup(createElement(ResultView, { card, busy: false, onDownload() {}, onReset() {} }))
  assert.match(html, /Скачать отчёт Excel/)
  assert.ok(html.indexOf('Позиции заявки') < html.indexOf('aria-label="Разбор ИИ"'))
  assert.ok(html.indexOf('aria-label="Разбор ИИ"') < html.indexOf('Справочник и версия данных'))
  for (const status of ['error', 'parsed', 'uploaded']) {
    card.application.status = status
    html = renderToStaticMarkup(createElement(ResultView, { card, busy: false, onDownload() {}, onReset() {} }))
    assert.doesNotMatch(html, /aria-label="Разбор ИИ"/)
  }
})
