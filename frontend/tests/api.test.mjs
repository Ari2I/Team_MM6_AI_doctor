import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

// Compile the actual boundary modules in memory; no extra test dependencies.
async function moduleUrl(path, replacements = []) {
  let source = await readFile(new URL(path, import.meta.url), 'utf8')
  for (const [from, to] of replacements) source = source.replaceAll(from, to)
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  })
  return 'data:text/javascript;base64,' + Buffer.from(outputText).toString('base64')
}
const contractUrl = await moduleUrl('../src/contract.ts')
const { parseApplicationCard, CATEGORY_LABELS, CONTRACT_ERROR } = await import(contractUrl)
const apiSource = await moduleUrl('../src/api.ts', [
  ["'./contract'", JSON.stringify(contractUrl)],
  ['import.meta.env.VITE_API_URL', "'https://backend.example.test/'"],
])
const { loadApplication, uploadApplication, downloadReport } = await import(apiSource)
const sample = JSON.parse(await readFile(new URL('../../sample_api_response.json', import.meta.url), 'utf8'))

test('accepts actual backend sample and maps Latin categories', () => {
  assert.deepEqual(parseApplicationCard(sample), sample)
  assert.equal(CATEGORY_LABELS.B, 'Б')
  assert.equal(CATEGORY_LABELS.V, 'В')
})
test('rejects old contract, malformed nested checks, invalid categories and amounts', () => {
  for (const bad of [null, {}, { application_id: 'old' },
    { ...sample, diagnoses: [{}] }, { ...sample, issues: [{}] }]) {
    assert.throws(() => parseApplicationCard(bad), { message: CONTRACT_ERROR })
  }
  for (const patch of [{ checks: null }, { amount: 'not money' },
    { checks: [{ ...sample.items[0].checks[0], category: 'Б' }] },
    { checks: [{ ...sample.items[0].checks[0], needs_review: 'yes' }] }]) {
    const bad = structuredClone(sample)
    Object.assign(bad.items[0], patch)
    assert.throws(() => parseApplicationCard(bad), { message: CONTRACT_ERROR })
  }
})
test('accepts null and omitted nullable fields, all lights and error cards', () => {
  for (const light of ['green', 'yellow', 'red', 'undetermined', null]) {
    const card = structuredClone(sample)
    card.application.traffic_light = light
    card.application.status = 'error'
    card.application.category_shares = null
    card.items[0].checks[0].category = null
    delete card.items[0].checks[0].comment
    assert.equal(parseApplicationCard(card).application.traffic_light, light)
  }
})
test('GET and multipart POST use actual paths, base URL, repeated diagnoses', async t => {
  const calls = []
  t.mock.method(globalThis, 'fetch', async (...args) => {
    calls.push(args)
    return Response.json(sample)
  })
  const signal = new AbortController().signal
  await loadApplication('saved id', signal)
  await uploadApplication(new File(['xlsx'], 'demo.xlsx'), ['Диагноз 1', 'Диагноз 2'], signal)
  assert.equal(calls[0][0], 'https://backend.example.test/applications/saved%20id')
  assert.equal(calls[0][1].credentials, 'include')
  assert.equal(calls[1][0], 'https://backend.example.test/applications')
  assert.equal(calls[1][1].method, 'POST')
  assert.deepEqual([...calls[1][1].body.keys()], ['file', 'diagnoses', 'diagnoses'])
  assert.deepEqual(calls[1][1].body.getAll('diagnoses'), ['Диагноз 1', 'Диагноз 2'])
})
test('errors[] preserves messages and coordinates', async t => {
  const errors = [{ level: 'error', code: 'invalid_request', message: 'Не указана сумма.', row: 2, column: 'amount' }]
  t.mock.method(globalThis, 'fetch', async () => Response.json({ errors }, { status: 422 }))
  await assert.rejects(loadApplication('id', new AbortController().signal), error => {
    assert.deepEqual(error.issues, errors)
    return true
  })
})
test('401 works with non-JSON body, including report download', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response('Unauthorized', { status: 401 }))
  for (const operation of [loadApplication, downloadReport]) {
    await assert.rejects(operation('id', new AbortController().signal), /Нужен вход/)
  }
})
test('network failure has a separate message', async t => {
  t.mock.method(globalThis, 'fetch', async () => { throw new TypeError('Failed to fetch') })
  await assert.rejects(loadApplication('id', new AbortController().signal), /Не удалось связаться с сервером/)
})
test('invalid success JSON gives contract error', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response('<html>SPA fallback</html>'))
  await assert.rejects(loadApplication('id', new AbortController().signal), { message: CONTRACT_ERROR })
})
test('report 409 errors are shown and correct endpoint requested', async t => {
  t.mock.method(globalThis, 'fetch', async url => {
    assert.equal(url, 'https://backend.example.test/applications/id/report.xlsx')
    return Response.json({ errors: [{ level: 'critical', code: 'report_not_ready', message: 'Отчёт ещё не готов.' }] }, { status: 409 })
  })
  await assert.rejects(downloadReport('id', new AbortController().signal), error => error.issues[0].code === 'report_not_ready')
})
