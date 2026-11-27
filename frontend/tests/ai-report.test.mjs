import assert from 'node:assert/strict'
import test from 'node:test'
import { importSource } from './helpers/modules.mjs'
import { applicationId, savedReport } from './helpers/ai-report-data.mjs'

const { parseAIReport, safeSourceURL, AI_REPORT_ERROR } = await importSource('../../src/ai-report-contract.ts')
const { loadAIReport, loadApplication, downloadReport } = await importSource('../../src/api.ts')

test('saved report preserves Cyrillic categories, exact quotes and decimal cost strings', () => {
  const value = savedReport()
  assert.deepEqual(parseAIReport(value, applicationId), value)
  assert.equal(value.human_report.costs[0].amount, '1000.10')
  assert.match(value.human_report.items[0].sources[0].quote, /\nВторая строка: TEST_EVIDENCE\./)
})

test('unsupported versions, another application and malformed nested data are rejected safely', () => {
  const changes = [
    value => { value.schema_version = 'future-report.2' },
    value => { value.application_id = 'another-application' },
    value => { value.saved_at = 'invalid-date' },
    value => { value.backend_revision = '' },
    value => { value.human_report.schema_version = 'future-human-report.2' },
    value => { value.human_report.items[0].questions[0].why = null },
    value => { value.human_report.items[0].sources[0].quote = {} },
    value => { value.human_report.costs[0].amount = 1000.10 },
    value => { value.human_report.purchases_without_amount_count = -1 },
    value => { value.checks[0].ai_proposed_category = 'B' },
    value => { value.checks[0].generation_mode = 'SUCCESS' },
    value => { value.checks[0].processed = 'true' },
    value => { value.checks[0].row_number = 0 },
  ]
  for (const change of changes) {
    const value = savedReport()
    change(value)
    assert.throws(() => parseAIReport(value, applicationId), { message: AI_REPORT_ERROR })
  }
})

test('row mapping, unique pairs and statement sources must match the saved report', () => {
  for (const change of [
    value => { value.checks = [] },
    value => { value.checks[0].number = 2 },
    value => { value.human_report.items[0].number = 2 },
    value => {
      value.checks.push({ ...value.checks[0], number: 2 })
      value.human_report.items.push({ ...value.human_report.items[0], number: 2 })
    },
    value => { value.human_report.items[0].established[0].sources[0].quote = 'Подменённая цитата' },
    value => { value.human_report.items[0].sources.push(value.human_report.items[0].sources[0]) },
  ]) {
    const value = savedReport()
    change(value)
    assert.throws(() => parseAIReport(value, applicationId), { message: AI_REPORT_ERROR })
  }
})

test('partial, unprocessed, fallback and null category are supported', () => {
  const value = savedReport()
  value.completeness = 'PARTIAL'
  Object.assign(value.checks[0], { processed: false, generation_mode: null, form_status: null,
    ai_proposed_category: null, classification_basis: null, reasons: ['BUDGET_EXHAUSTED'] })
  value.human_report.items[0].category = 'не установлена'
  assert.equal(parseAIReport(value, applicationId), value)
  value.completeness = 'COMPLETE'
  Object.assign(value.checks[0], { processed: true, generation_mode: 'FALLBACK', reasons: ['SOURCE_ERROR'] })
  assert.equal(parseAIReport(value, applicationId), value)
})

test('only absolute http/https source links without embedded credentials are clickable', () => {
  assert.equal(safeSourceURL('https://example.test/source'), 'https://example.test/source')
  assert.equal(safeSourceURL('http://example.test/source'), 'http://example.test/source')
  for (const url of [null, '', '/relative', '//example.test', 'javascript:alert(1)',
    'data:text/html,unsafe', 'file:///tmp/source', 'https://user:password@example.test/']) {
    assert.equal(safeSourceURL(url), undefined)
  }
})

test('opening and retrying a saved AI report uses GET, base URL, auth and abort signal only', async t => {
  const calls = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, options })
    return Response.json(savedReport())
  })
  const signal = new AbortController().signal
  await loadAIReport(applicationId, signal)
  await loadAIReport(applicationId, signal)
  assert.equal(calls.length, 2)
  for (const { url, options } of calls) {
    assert.equal(url, 'https://backend.example.test/applications/' + applicationId + '/ai-report')
    assert.equal(options.method ?? 'GET', 'GET')
    assert.equal(options.credentials, 'include')
    assert.equal(options.signal, signal)
    assert.equal(options.body, undefined)
  }
})

test('AI report 409 means absent, while card and Excel 409 remain errors', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response('Report absent', { status: 409 }))
  assert.equal(await loadAIReport(applicationId, new AbortController().signal), null)
  for (const operation of [loadApplication, downloadReport]) {
    await assert.rejects(operation(applicationId, new AbortController().signal), /Не удалось выполнить запрос/)
  }
})

test('401, missing report, server failure and invalid JSON have safe error messages', async t => {
  let status = 401
  t.mock.method(globalThis, 'fetch', async () => new Response('PRIVATE-RESPONSE-MARKER', { status }))
  await assert.rejects(loadAIReport(applicationId, new AbortController().signal), /Нужен вход/)
  for (status of [404, 500]) {
    await assert.rejects(loadAIReport(applicationId, new AbortController().signal), error =>
      !error.message.includes('PRIVATE-RESPONSE-MARKER') && /Не удалось выполнить запрос/.test(error.message))
  }
  status = 200
  await assert.rejects(loadAIReport(applicationId, new AbortController().signal), { message: AI_REPORT_ERROR })
})

test('network errors are readable and aborted reads preserve cancellation', async t => {
  t.mock.method(globalThis, 'fetch', async () => { throw new TypeError('PRIVATE-NETWORK-MARKER') })
  await assert.rejects(loadAIReport(applicationId, new AbortController().signal), /Не удалось связаться/)
  const controller = new AbortController()
  controller.abort()
  const cancelled = new DOMException('Aborted', 'AbortError')
  t.mock.method(globalThis, 'fetch', async () => { throw cancelled })
  await assert.rejects(loadAIReport(applicationId, controller.signal), error => error === cancelled)
})
