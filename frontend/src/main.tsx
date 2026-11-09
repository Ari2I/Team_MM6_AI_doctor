import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { createRoot } from 'react-dom/client'
import type { Check, Citation, Issue, Light, Mode, Purchase, Result } from './contract'
import './style.css'

const money = new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB' })
const percent = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 })
const lights: Record<Light, string> = {
  green: 'Зелёный', yellow: 'Жёлтый', red: 'Красный', undetermined: 'Требуется сверка',
}
const modes: Record<Mode, string> = {
  offline: 'По источникам', gigachat: 'GigaChat', mixed: 'GigaChat и результаты по источникам',
}
const evidence: Record<Check['evidence_status'], string> = {
  SUPPORTED: 'Подтверждено для демо', INSUFFICIENT_DATA: 'Недостаточно данных',
  UNCONFIRMED: 'Не подтверждено', SOURCE_CONFLICT: 'Конфликт источников',
  PROCESSING_ERROR: 'Ошибка проверки',
}
const ml2Assessments: Record<string, string> = {
  CORE_TREATMENT: 'Основное лечение', SUPPORTIVE_TREATMENT: 'Поддерживающее лечение',
  EXPLICITLY_NOT_RECOMMENDED: 'Прямо не рекомендовано', MENTIONED_ONLY: 'Только упомянуто',
  NOT_FOUND: 'Не найдено во фрагментах', INSUFFICIENT_CONTEXT: 'Недостаточно контекста',
  CONFLICTING_EVIDENCE: 'Противоречивые сведения',
}
const fallbackLabels: Record<string, string> = {
  GIGACHAT_CONFIGURATION: 'Проверьте настройки GigaChat и доверенного сертификата.',
  GIGACHAT_BUDGET_EXCEEDED: 'Превышен лимит времени GigaChat.',
  GIGACHAT_UNVERIFIED_RESPONSE: 'Ответ GigaChat не прошёл проверку цитат.',
  GIGACHAT_PROVIDER_ERROR: 'Сервис GigaChat недоступен.',
  GIGACHAT_TLS_ERROR: 'Не удалось проверить сертификат GigaChat.',
  GIGACHAT_AUTH_FAILED: 'GigaChat отклонил авторизацию B2B.',
  GIGACHAT_FORBIDDEN: 'Ключ не даёт доступа к запрошенной модели GigaChat.',
  GIGACHAT_RATE_LIMIT: 'Достигнут лимит запросов GigaChat.',
  GIGACHAT_TIMEOUT: 'GigaChat не ответил вовремя.',
  GIGACHAT_UNAVAILABLE: 'Сервис GigaChat недоступен.',
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
    {citation.origin === 'published_copy' && citation.copy_url?.startsWith('https://clinreky.ru/') &&
      <div><a href={citation.copy_url} target="_blank" rel="noreferrer">Опубликованная копия ↗</a> · для сверки с оригиналом</div>}
    <small>{[citation.version, citation.location, citation.fragment_id, citation.publisher,
      citation.retrieved_at && ('получено ' + citation.retrieved_at)].filter(Boolean).join(' · ')}</small>
  </div>
}

const officialStatuses: Record<string, string> = {
  OFFICIAL_RULE_APPLIED: 'Правило применено',
  OFFICIAL_RULE_CONDITION_MISSING: 'Уточните условия правила',
  OFFICIAL_RULE_CONFLICT: 'Противоречие правил',
  OFFICIAL_SOURCE_CONFLICT: 'Противоречие источников',
  OFFICIAL_DIAGNOSIS_MISSING: 'Диагноз не указан',
  OFFICIAL_DIAGNOSIS_NOT_INDEXED: 'Диагноз вне клинического корпуса',
  OFFICIAL_DIAGNOSIS_CONFLICT: 'Код расходится с названием',
  OFFICIAL_DIAGNOSIS_UNRESOLVED: 'Уточните диагноз',
  OFFICIAL_DIAGNOSIS_UNKNOWN: 'Код отсутствует в МКБ-10',
  OFFICIAL_DRUG_NOT_FOUND: 'Препарат не найден в реестре',
  OFFICIAL_DRUG_UNRESOLVED: 'Уточните препарат',
  OFFICIAL_MNN_NOT_AVAILABLE: 'МНН отсутствует в реестре',
  OFFICIAL_REGISTRATION_UNRESOLVED: 'Уточните форму препарата и РУ',
  OFFICIAL_LINK_NOT_FOUND: 'Связь в проверенных фрагментах не найдена',
  OFFICIAL_DOCUMENT_UNAVAILABLE: 'Документ недоступен',
  OFFICIAL_DOCUMENT_PENDING: 'Текст рекомендации отсутствует в индексе',
  OFFICIAL_DOCUMENT_NO_TEXT: 'Из PDF не извлечён текст',
  OFFICIAL_DOCUMENT_MENTION: 'МНН упомянуто в документе',
  OFFICIAL_COPY_MENTION: 'МНН упомянуто в опубликованной копии',
  OFFICIAL_COPY_LINK_NOT_FOUND: 'Упоминание МНН в разделах лечения не найдено',
  OFFICIAL_LINK_REQUIRES_REVIEW: 'Основание найдено',
  OFFICIAL_GROUP_LINK_REQUIRES_REVIEW: 'Найден документ для группы диагнозов',
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
      <div className="evidence">{officialStatuses[check.reason_code] || evidence[check.evidence_status]}</div>
      <p>{check.explanation}</p>
      {check.ml2_classification && <details className="ml2-result">
        <summary>Предварительная оценка ML-2 · {ml2Assessments[check.ml2_classification.assessment || ''] || check.ml2_classification.status}
          {check.ml2_classification.proposed_category
            ? ' · категория ' + check.ml2_classification.proposed_category : ' · категория не определена'}</summary>
        {check.ml2_classification.reason && <p>{check.ml2_classification.reason}</p>}
        {check.ml2_classification.conditions.length > 0 && <p>Условия: {check.ml2_classification.conditions.join('; ')}</p>}
        {check.ml2_classification.missing_context.length > 0 &&
          <p>Нужно уточнить: {check.ml2_classification.missing_context.join('; ')}</p>}
        {check.ml2_classification.dosage_check && <p><strong>Дозировка · {check.ml2_classification.dosage_check.status}.</strong>{' '}
          {check.ml2_classification.dosage_check.reason}
          {check.ml2_classification.dosage_check.missing_data.length > 0 &&
            ' Не хватает: ' + check.ml2_classification.dosage_check.missing_data.join(', ')}</p>}
        {check.ml2_classification.citations.map(citation => <Source key={citation.fragment_id + citation.quote} citation={citation}/>) }
      </details>}
      {check.evidence_conditions.length > 0 && <ul className="issues">
        {check.evidence_conditions.map(condition => <li key={condition}>{condition}</li>)}
      </ul>}
      {check.model_review_action && <p><strong>Шаг сверки по выбору GigaChat.</strong> {check.model_review_action}</p>}
      {check.rule_ids?.length > 0 && <small>Правила {check.rule_ids.join(', ')}</small>}
      {check.model_highlight && <details>
        <summary>Фрагмент, выделенный GigaChat</summary>
        <Source citation={check.model_highlight}/>
      </details>}
      {check.citations.length > 0 && <details>
        <summary>Открыть источник</summary>
        {check.citations.map(citation => <Source key={citation.fragment_id + citation.quote} citation={citation}/>)}
      </details>}
      {check.catalog_sources?.length > 0 && <details>
        <summary>Рекомендации в каталоге без доступного текста</summary>
        {check.catalog_sources.map(citation => <Source key={citation.fragment_id} citation={citation}/>)}
      </details>}
      {check.searched_sources?.length > 0 && <details>
        <summary>Документы, по которым выполнен поиск</summary>
        {check.searched_sources.map(citation => <Source key={citation.fragment_id} citation={citation}/>)}
      </details>}
      {check.review_sources.length > 0 && <details>
        <summary>Материалы для сверки</summary>
        {check.review_sources.map(citation =>
          <Source key={citation.fragment_id + citation.quote} citation={citation}/>)}
      </details>}
      {check.fallback_reason && <small>Локальный резерв · {fallbackLabels[check.fallback_reason] || check.fallback_reason}</small>}
    </td>
  </tr>
}

const categoryLabels = { 'А': 'Категория А', 'Б': 'Категория Б', 'В': 'Категория В', undetermined: 'На сверке' }
type Amounts = Result['amounts_total']

function AmountCards({ amounts }: { amounts: Amounts }) {
  return <div className="shares">{(Object.keys(categoryLabels) as (keyof Amounts)[]).map(key =>
    <div key={key}><small>{categoryLabels[key]}</small>
      <strong>{money.format(Number(amounts[key] ?? 0))}</strong>
    </div>)}</div>
}

function ApplicationView({ result }: { result: Result }) {
  const [selected, setSelected] = useState(result.diagnoses[0]?.diagnosis_id)
  const summary = result.summary_by_diagnosis.find(row => row.diagnosis_id === selected)
  const checks = result.checks.filter(row => row.diagnosis_id === selected)
  const items = new Map(result.items.map(row => [row.item_id, row]))
  const route = '/api/applications/' + result.application_id
  const official = result.reference_profile === 'official'
  const supported = result.checks.filter(row => official ? row.relationship_found : row.evidence_status === 'SUPPORTED').length
  const searched = result.checks.filter(row => row.searched_sources?.length || row.review_sources.length || row.citations.length).length
  const methods = [...new Set(result.checks.map(check =>
    check.retrieval === 'vector' ? 'векторный' : 'лексический'))].join(' / ')
  return <section className="result" aria-label="Результат">
    <div className="result-top">
      <div>
        <div className="eyebrow">Результат проверки</div>
        <h2>{result.filename}</h2>
        <small className="application-id">Заявка {result.application_id}{result.sheet_name && " · Лист " + result.sheet_name}</small>
      </div>
      <div className="actions">
        <a className="button secondary" href={route + '/result.xlsx'}>Скачать Excel</a>
        <a className="button secondary" href={route + '/report.html'}>Скачать отчёт HTML</a>
      </div>
    </div>
    <div className="metrics">
      <div><small>Известная сумма</small><strong>{money.format(Number(result.known_total))}</strong></div>
      <div><small>Покупки / диагнозы</small><strong>{result.items.length} / {result.diagnoses.length}</strong></div>
      <div><small>{official ? 'Пары со связью в справочнике' : 'Подтверждённые пары'}</small><strong>{supported} из {result.checks.length}</strong>
        {official && <small>Тексты доступны для {searched} из {result.checks.length}</small>}
      </div>
      <div><small>Фактический режим</small><strong>{modes[result.effective_mode]}</strong><small>Поиск · {methods}</small>
        {result.model_tokens != null && <small>Токены GigaChat · {result.model_tokens.toLocaleString('ru-RU')}</small>}
        {result.model_tokens == null && result.model_attempted && <small>Запрос в GigaChat отправлен</small>}
      </div>
    </div>
    <p className="version">{official ? 'Официальный снимок' : 'Справочник'} {result.reference_version}
      {official && result.official_as_of ? ' · ГРЛС на ' + result.official_as_of : ''}
      {result.official_coverage === 'historical' ? ' · исторические данные' : ''}</p>
    {result.recommendation && <div className="notice">
      <strong>Рекомендация секретарю</strong><p>{result.recommendation}</p>
      <AmountCards amounts={result.amounts_total}/>
      {result.model_review_priorities?.length > 0 && <div>
        <strong>Что сверить в первую очередь</strong>
        <ul className="issues">{result.model_review_priorities.map(action => <li key={action}>{action}</li>)}</ul>
      </div>}
      {result.model_summary?.length > 0 && <details><summary>Ключевые основания по выбору GigaChat</summary>
        {result.model_summary.map((citation, index) => <Source key={index} citation={citation}/>)}
      </details>}
    </div>}
    {result.fallback_reason && <div className="notice">Локальный результат сохранён. {fallbackLabels[result.fallback_reason] || result.fallback_reason}</div>}
    {result.issues.length > 0 && <details className="notice" open>
      <summary>Замечания к Excel · {result.issues.length}</summary>
      <IssueList issues={result.issues}/>
    </details>}
    <div className="tabs" aria-label="Выбор диагноза">
      {result.diagnoses.map(diag =>
        <button key={diag.diagnosis_id} aria-pressed={selected === diag.diagnosis_id}
          onClick={() => setSelected(diag.diagnosis_id)}>{diag.name}{diag.source_row ? ' · строка ' + diag.source_row : ''}</button>)}
    </div>
    {summary && <div className="diagnosis">
      {!official && <div className="light-line"><span className={'badge ' + summary.traffic_light}>{lights[summary.traffic_light]}</span>
        <span>{summary.reason}</span></div>}
      {official && <AmountCards amounts={summary.amounts}/>}
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
    <footer>{result.disclaimer}.</footer>
  </section>
}

type Layout = {
  sheets: { name: string; header_row: number; headers: string[] }[]
  columns: Record<string, { index: number; header: string; sample: string[] }[]>
  sheet: string | null
}

class LayoutRequired extends Error {
  constructor(readonly layout: Layout) { super('Уточните таблицу заявки') }
}

async function readResponse(response: Response): Promise<Result> {
  const json = await response.json()
  if (response.ok) return json as Result
  if (response.status === 409 && json.detail?.layout) throw new LayoutRequired(json.detail.layout)
  const issues = json.detail?.issues
  throw Array.isArray(issues) ? issues : new Error('Не удалось выполнить запрос.')
}

function App() {
  const [file, setFile] = useState<File | null>(null)
  const [layout, setLayout] = useState<Layout | null>(null)
  const [sheet, setSheet] = useState('')
  const [columns, setColumns] = useState<Record<string, number>>({})
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
      setLayout(null)
      history.replaceState(null, '', '?application=' + encodeURIComponent(data.application_id))
    } catch (error) {
      if (controller.signal.aborted) return
      if (error instanceof LayoutRequired) {
        setLayout(error.layout)
        setSheet(error.layout.sheet ?? '')
        setColumns({})
      } else if (Array.isArray(error)) setIssues(error)
      else setMessage('Не удалось получить результат. Проверьте соединение с сервером.')
    } finally {
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  useEffect(() => {
    const saved = new URLSearchParams(location.search).get('application')
    if (saved) void request('/api/applications/' + encodeURIComponent(saved))
    return () => current.current?.abort()
  }, [])

  function choose(value: File | null) {
    current.current?.abort()
    setFile(value)
    setLayout(null)
    setSheet('')
    setColumns({})
    setResult(null)
    setIssues([])
    setMessage('')
    setBusy(false)
    history.replaceState(null, '', location.pathname)
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!file) return
    const form = new FormData()
    form.set('file', file)
    form.set('reference', 'official')
    form.set('mode', 'gigachat')
    if (sheet) form.set('sheet', sheet)
    if (Object.keys(columns).length) form.set('columns', JSON.stringify(columns))
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
        <p>Загрузите Excel с торговыми наименованиями, дозировками, количеством, суммами и диагнозами из заключения. Строка с диагнозом начинает новый блок покупок.</p>
      </section>
      <form className="upload" onSubmit={submit}>
        <div className="upload-title"><h2>Загрузить заявку</h2></div>
        <div className="upload-fields">
          <label className="file-label">Файл заявки (.xlsx)
            <input type="file" accept=".xlsx" required onChange={event => choose(event.target.files?.[0] ?? null)}/>
          </label>
          {layout?.sheets.length ? <label>Лист с покупками
            <select required value={sheet} onChange={event => setSheet(event.target.value)}>
              <option value="">Выберите лист</option>
              {layout.sheets.map(row => <option key={row.name} value={row.name}>
                {row.name} · строка {row.header_row} · {row.headers.filter(Boolean).join(' / ')}
              </option>)}
            </select>
          </label> : null}
          {layout && Object.entries(layout.columns).map(([field, options]) => <label key={field}>
            Уточните столбец «{options[0].header}»
            <select required value={columns[field] ?? ''} onChange={event =>
              setColumns({ ...columns, [field]: Number(event.target.value) })}>
              <option value="">Выберите столбец</option>
              {options.map(option => <option key={option.index} value={option.index}>
                Столбец {option.index + 1} · {option.header} · {option.sample.filter(Boolean).join(' / ')}
              </option>)}
            </select>
          </label>)}
          <button className="button primary" type="submit" disabled={!file || busy}>{busy ? 'Обработка…' : 'Проверить заявку'}</button>
        </div>
        <p className="hint">Сверка лекарств с клиническими рекомендациями выполняется через GigaChat. Загрузите только синтетические данные для демонстрации.</p>
        {layout && <p className="hint">В книге несколько подходящих вариантов. Выберите данные заявки.</p>}
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
