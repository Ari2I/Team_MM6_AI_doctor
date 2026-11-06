import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { createRoot } from 'react-dom/client'
import type { Check, Citation, Issue, Light, Mode, Purchase, ReferenceProfile, Result } from './contract'
import './style.css'

const money = new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB' })
const percent = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 })
const lights: Record<Light, string> = {
  green: 'Зелёный', yellow: 'Жёлтый', red: 'Красный', undetermined: 'Требуется сверка',
}
const modes: Record<Mode, string> = {
  offline: 'Локальный', gigachat: 'GigaChat', mixed: 'GigaChat и локальный резерв',
}
const evidence: Record<Check['evidence_status'], string> = {
  SUPPORTED: 'Подтверждено для демо', INSUFFICIENT_DATA: 'Недостаточно данных',
  UNCONFIRMED: 'Не подтверждено', SOURCE_CONFLICT: 'Конфликт источников',
  PROCESSING_ERROR: 'Ошибка проверки',
}
const fallbackLabels: Record<string, string> = {
  GIGACHAT_CONFIGURATION: 'Проверьте настройки GigaChat и доверенного сертификата.',
  GIGACHAT_BUDGET_EXCEEDED: 'Превышен лимит времени GigaChat.',
  GIGACHAT_UNVERIFIED_RESPONSE: 'Ответ GigaChat не прошёл проверку цитат.',
  GIGACHAT_PROVIDER_ERROR: 'Сервис GigaChat недоступен.',
  GIGACHAT_TLS_ERROR: 'Не удалось проверить сертификат GigaChat.',
  NO_CONFIRMED_PAIRS: 'Нет пар с проверенным основанием для вызова GigaChat.',
  PROVIDER_BUSY: 'Сервис проверки занят.',
}

function IssueList({ issues }: { issues: Issue[] }) {
  return <ul className="issues">{issues.map((issue, index) =>
    <li key={index}>{issue.row && <strong>Строка {issue.row} · </strong>}{issue.message}</li>
  )}</ul>
}

function Source({ citation }: { citation: Citation }) {
  return <div className="source">
    <blockquote>{citation.quote}</blockquote>
    <div>{citation.url?.startsWith('https://')
      ? <a href={citation.url} target="_blank" rel="noreferrer">{citation.title} ↗</a>
      : citation.title}
    </div>
    <small>{[citation.version, citation.location, citation.fragment_id, citation.publisher,
      citation.retrieved_at && ('получено ' + citation.retrieved_at)].filter(Boolean).join(' · ')}</small>
  </div>
}

function PurchaseRow({ check, item }: { check: Check; item: Purchase }) {
  return <tr>
    <td>{item.row_number}</td>
    <th scope="row">{item.drug_name || 'Название отсутствует'}
      <small>{item.mnn || (item.match_candidates.length
        ? 'Возможные МНН · ' + item.match_candidates.join(', ') : 'МНН не определено')}</small>
      {!!item.official_drug?.registration_count && <details>
        <summary>Реестр и состав · {item.official_drug.registration_count} РУ</summary>
        {item.official_drug.registrations.map(row => <div key={row.source.fragment_id}>
          <small>{row.trade_name} · {row.registration} · {row.form}</small>
          <Source citation={row.source}/>
        </div>)}
        {item.official_drug.compositions.length ? item.official_drug.compositions.map(row =>
          <div key={row.source.fragment_id}><small>Состав · {row.registration} · {row.form}</small>
            <Source citation={row.source}/></div>
        ) : <small>Выдержка о составе из инструкции отсутствует в снимке.</small>}
      </details>}
    </th>
    <td className="money">{item.amount === null ? '—' : money.format(Number(item.amount))}</td>
    <td><span className={check.category ? 'category' : 'muted'}>{check.category || '—'}</span></td>
    <td>
      <div className="evidence">{check.relationship_found ? 'Источник найден · требуется сверка' : evidence[check.evidence_status]}</div>
      <p>{check.explanation}</p>
      {check.citations.length > 0 && <details>
        <summary>Открыть источник</summary>
        {check.citations.map(citation => <Source key={citation.fragment_id + citation.quote} citation={citation}/>)}
      </details>}
      {check.review_sources.length > 0 && <details>
        <summary>Материалы для сверки</summary>
        {check.evidence_conditions.map(condition => <p key={condition}>{condition}</p>)}
        {check.review_sources.map(citation =>
          <Source key={citation.fragment_id + citation.quote} citation={citation}/>)}
      </details>}
      {check.fallback_reason && <small>Локальный резерв · {fallbackLabels[check.fallback_reason] || check.fallback_reason}</small>}
    </td>
  </tr>
}

function ApplicationView({ result }: { result: Result }) {
  const [selected, setSelected] = useState(result.diagnoses[0]?.diagnosis_id)
  const summary = result.summary_by_diagnosis.find(row => row.diagnosis_id === selected)
  const checks = result.checks.filter(row => row.diagnosis_id === selected)
  const items = new Map(result.items.map(row => [row.item_id, row]))
  const route = '/api/applications/' + result.application_id
  const official = result.reference_profile === 'official'
  const supported = result.checks.filter(row => official ? row.relationship_found : row.evidence_status === 'SUPPORTED').length
  const methods = [...new Set(result.checks.map(check =>
    check.retrieval === 'vector' ? 'векторный' : 'лексический'))].join(' / ')
  return <section className="result" aria-label="Результат">
    <div className="result-top">
      <div>
        <div className="eyebrow">Результат проверки</div>
        <h2>{result.filename}</h2>
        <small className="application-id">Заявка {result.application_id}</small>
      </div>
      <div className="actions">
        <a className="button secondary" href={route + '/result.xlsx'}>Скачать Excel</a>
        <a className="button secondary" href={route + '/report.html'}>Скачать отчёт HTML</a>
      </div>
    </div>
    <div className="metrics">
      <div><small>Известная сумма</small><strong>{money.format(Number(result.known_total))}</strong></div>
      <div><small>Покупки / диагнозы</small><strong>{result.items.length} / {result.diagnoses.length}</strong></div>
      <div><small>{official ? 'Пары с найденными источниками' : 'Подтверждённые пары'}</small><strong>{supported} из {result.checks.length}</strong></div>
      <div><small>Фактический режим</small><strong>{modes[result.effective_mode]}</strong><small>Поиск · {methods}</small></div>
    </div>
    <p className="version">{official ? 'Официальный снимок' : 'Справочник'} {result.reference_version}
      {official && result.official_as_of ? ' · ГРЛС на ' + result.official_as_of : ''}</p>
    {official && <p className="notice">Категории А/Б/В определяются правилами комиссии. В отчёте показаны найденные документы для сверки.</p>}
    {result.fallback_reason && <div className="notice">Локальный результат сохранён. {fallbackLabels[result.fallback_reason] || result.fallback_reason}</div>}
    {result.issues.length > 0 && <details className="notice" open>
      <summary>Замечания к Excel · {result.issues.length}</summary>
      <IssueList issues={result.issues}/>
    </details>}
    <div className="tabs" aria-label="Выбор диагноза">
      {result.diagnoses.map(diag =>
        <button key={diag.diagnosis_id} aria-pressed={selected === diag.diagnosis_id}
          onClick={() => setSelected(diag.diagnosis_id)}>{diag.name}</button>)}
    </div>
    {summary && <div className="diagnosis">
      <div className="light-line"><span className={'badge ' + summary.traffic_light}>{lights[summary.traffic_light]}</span>
        <span>{summary.reason}</span></div>
      {!official && <div className="shares">{(['А', 'Б', 'В', 'undetermined'] as const).map(key =>
        <div key={key}><small>{key === 'undetermined' ? 'Не определено' : 'Категория ' + key}</small>
          <strong>{money.format(Number(summary.amounts[key]))}</strong>
          <small>{percent.format(Number(summary.shares[key]))}%</small></div>
      )}</div>}
      <div className="table-scroll">
        <table><thead><tr><th>Строка</th><th>Препарат</th><th>Сумма</th><th>Категория</th><th>Основание</th></tr></thead>
          <tbody>{checks.map(check =>
            <PurchaseRow key={check.item_id} check={check} item={items.get(check.item_id)!}/>
          )}</tbody></table>
      </div>
    </div>}
    <footer><strong>Все пары передаются на ручную проверку.</strong> {result.disclaimer}.</footer>
  </section>
}

async function readResponse(response: Response): Promise<Result> {
  const json = await response.json()
  if (response.ok) return json as Result
  const issues = json.detail?.issues
  throw Array.isArray(issues) ? issues : new Error('Не удалось выполнить запрос.')
}

function App() {
  const [file, setFile] = useState<File | null>(null)
  const [reference, setReference] = useState<ReferenceProfile | null>(null)
  const [officialAvailable, setOfficialAvailable] = useState(false)
  const [mode, setMode] = useState<'offline' | 'gigachat'>('offline')
  const [result, setResult] = useState<Result | null>(null)
  const [issues, setIssues] = useState<Issue[]>([])
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const current = useRef<AbortController | null>(null)

  async function request(path: string, body?: FormData) {
    current.current?.abort()
    const controller = new AbortController()
    current.current = controller
    setBusy(true)
    setIssues([])
    setMessage('')
    try {
      const data = await readResponse(await fetch(path, body
        ? { method: 'POST', body, signal: controller.signal }
        : { signal: controller.signal }))
      if (controller.signal.aborted) return
      setResult(data)
      history.replaceState(null, '', '?application=' + encodeURIComponent(data.application_id))
    } catch (error) {
      if (controller.signal.aborted) return
      if (Array.isArray(error)) setIssues(error)
      else setMessage('Не удалось получить результат. Проверьте соединение с сервером.')
    } finally {
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  useEffect(() => {
    const controller = new AbortController()
    void fetch('/api/references', { signal: controller.signal })
      .then(response => response.json())
      .then(data => {
        setOfficialAvailable(Boolean(data.official))
        setReference(data.default === 'official' ? 'official' : 'demo')
      })
      .catch(() => { if (!controller.signal.aborted) setReference('demo') })
    const saved = new URLSearchParams(location.search).get('application')
    if (saved) void request('/api/applications/' + encodeURIComponent(saved))
    return () => { controller.abort(); current.current?.abort() }
  }, [])

  function choose(value: File | null) {
    current.current?.abort()
    setFile(value)
    setResult(null)
    setIssues([])
    setMessage('')
    setBusy(false)
    history.replaceState(null, '', location.pathname)
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!file || !reference) return
    const form = new FormData()
    form.set('file', file)
    form.set('reference', reference)
    form.set('mode', reference === 'official' ? 'offline' : mode)
    setResult(null)
    void request('/api/applications', form)
  }

  return <>
    <header><div className="header-content"><span className="brand-icon" aria-hidden="true">✚</span>
      <div><strong>Врач</strong><small>Помощник комиссии по ТЖС</small></div>
      <span className="demo-tag">Демонстрация</span></div></header>
    <main>
      <section className="intro"><div className="eyebrow">Материальная помощь</div>
        <h1>Проверка расходов по диагнозам</h1>
        <p>Загрузите список покупок из Excel. Система проверит каждую покупку по каждому диагнозу и покажет источники и позиции для ручной сверки.</p>
      </section>
      <form className="upload" onSubmit={submit}>
        <div className="upload-title"><h2>Загрузить заявку</h2><a href="/api/demo.xlsx">Скачать демо Excel ↓</a></div>
        <div className="upload-fields">
          <label className="file-label">Файл заявки (.xlsx)
            <input type="file" accept=".xlsx" required onChange={event => choose(event.target.files?.[0] ?? null)}/>
          </label>
          <label>Справочник<select value={reference || ''} disabled={!reference} onChange={event => setReference(event.target.value === 'official' ? 'official' : 'demo')}>
            {!reference && <option value="">Загрузка…</option>}
            <option value="official" disabled={!officialAvailable}>Официальные источники</option>
            <option value="demo">Демонстрационный</option>
          </select></label>
          {reference === 'demo' && <label>Режим пояснений<select value={mode} onChange={event => setMode(event.target.value === 'gigachat' ? 'gigachat' : 'offline')}>
            <option value="offline">Локальный</option><option value="gigachat">GigaChat · только демо-файл</option>
          </select></label>}
          <button className="button primary" type="submit" disabled={!file || !reference || busy}>{busy ? 'Обработка…' : 'Проверить заявку'}</button>
        </div>
        {reference === 'demo' && mode === 'gigachat' && <p className="hint">Для вызова GigaChat используется исходный синтетический файл из ссылки выше.</p>}
      </form>
      {busy && <p role="status" className="notice">Проверка выполняется…</p>}
      {(issues.length > 0 || message) && <div role="alert" className="notice error">
        <strong>Заявка не обработана</strong>{message && <p>{message}</p>}{issues.length > 0 && <IssueList issues={issues}/>}
      </div>}
      {result && <ApplicationView key={result.application_id} result={result}/>}
      <p className="bottom-note">Результат проверки передаётся специалисту. Решение о выплате принимает комиссия.</p>
    </main>
  </>
}

createRoot(document.getElementById('root')!).render(<App/>)
