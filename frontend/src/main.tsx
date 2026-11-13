import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { createRoot } from 'react-dom/client'
import { CATEGORY_LABELS } from './contract'
import type { ApplicationCard, CheckDetails, Issue, Light } from './contract'
import { downloadReport, loadApplication, uploadApplication, RequestError } from './api'
import './style.css'

// API does not specify currency; do not invent a currency symbol.
const money = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const percent = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 })
const lights: Record<Light, string> = {
  green: 'Зелёный', yellow: 'Жёлтый', red: 'Красный', undetermined: 'Не определён — требуется проверка',
}
const reasons: Record<string, string> = {
  DRUG_NOT_FOUND: 'Препарат не найден в справочнике',
  DRUG_AMBIGUOUS: 'Неоднозначное сопоставление препарата',
  DIAGNOSIS_NOT_FOUND: 'Диагноз не найден в справочнике',
  NO_RULE_FOR_PAIR: 'Нет правила для пары препарат — диагноз',
  LOW_CONFIDENCE: 'Недостаточная уверенность сопоставления',
  CONFLICTING_CATEGORIES: 'Категории по диагнозам расходятся',
}
const columns: Record<string, string> = {
  drug_name: 'Препарат', mnn: 'МНН', amount: 'Сумма', diagnosis: 'Диагноз',
  diagnoses: 'Диагнозы', dosage: 'Дозировка', quantity: 'Количество', file: 'Файл',
}
function IssueList({ issues }: { issues: Issue[] }) {
  return <ul className="issues">{issues.map((issue, index) =>
    <li key={index}>{issue.row != null && <strong>Строка {issue.row} · </strong>}
      {issue.column && <span>Столбец «{columns[issue.column] ?? issue.column}» · </span>}{issue.message}</li>
  )}</ul>
}
function CheckView({ check }: { check: CheckDetails }) {
  return <div className="pair-check"><strong>{check.diagnosis}</strong>
    <p>Категория: <span className="category">{CATEGORY_LABELS[check.category ?? 'undetermined']}</span></p>
    <small>Источник: {check.source ?? 'не указан'}</small>
    <small>Версия справочника: {check.reference_version ?? 'не указана'}</small>
    <p>Комментарий: {check.comment ?? 'не предоставлен'}</p>
    {check.needs_review && <div className="notice"><strong>Требует проверки</strong>
      <p>{check.review_reason ? (reasons[check.review_reason] ?? check.review_reason) : 'Причина не указана'}</p></div>}
  </div>
}
function ApplicationView({ result, busy, onDownload }: {
  result: ApplicationCard; busy: boolean; onDownload: () => void
}) {
  const app = result.application
  const shares = app.category_shares
  return <section className="result" aria-label="Результат">
    <div className="result-top"><div><div className="eyebrow">Результат проверки</div><h2>{app.filename}</h2>
      <small className="application-id">Заявка {app.id}</small></div>
      <button className="button secondary" disabled={busy || app.status !== 'analyzed'} onClick={onDownload}>Скачать отчёт Excel</button></div>
    {app.status === 'error' && <div className="notice error">Анализ завершился с ошибкой. Требуется проверка человеком.</div>}
    {app.status !== 'analyzed' && app.status !== 'error' && <p className="notice">Анализ ещё не завершён. Откройте карточку позднее.</p>}
    <div className="light-line"><span className={'badge ' + (app.traffic_light ?? 'undetermined')}>
      {app.traffic_light ? lights[app.traffic_light] : 'Индикатор ещё не рассчитан'}</span>
      {app.requires_review && <strong>Требует проверки человеком</strong>}</div>
    <h3>Доли сумм по категориям</h3>
    {shares ? <div className="shares">{(Object.keys(CATEGORY_LABELS) as (keyof typeof CATEGORY_LABELS)[]).map(key =>
      <div key={key}><small>Категория: {CATEGORY_LABELS[key]}</small>
        <strong>{shares[key] == null ? 'Нет данных' : percent.format(shares[key]) + '%'}</strong>
        {shares[key + '_amount'] != null && <small>Сумма: {money.format(shares[key + '_amount'])}</small>}
      </div>)}</div> : <p>Доли ещё не рассчитаны.</p>}
    {shares?.total_amount != null && <p>Общая сумма: {money.format(shares.total_amount)}</p>}
    <h3>Резюме</h3><p>{app.summary ?? 'Резюме пока не сформировано.'}</p>
    {app.warning_message && <div className="notice"><strong>Предупреждение</strong><p>{app.warning_message}</p></div>}
    <h3>Диагнозы заявки</h3>
    {result.diagnoses.length ? <ul>{result.diagnoses.map((diagnosis, index) => <li key={index}>{diagnosis}</li>)}</ul> : <p>Диагнозы не указаны.</p>}
    {result.issues.length > 0 && <details className="notice" open><summary>Замечания к Excel</summary><IssueList issues={result.issues}/></details>}
    <div className="table-scroll"><table>
      <thead><tr><th>Строка</th><th>Препарат</th><th>МНН</th><th>Сумма</th><th>Проверки по диагнозам</th></tr></thead>
      <tbody>{result.items.map((item, index) => <tr key={index}>
        <td>{item.row_number}</td><th scope="row">{item.drug_name ?? 'Название отсутствует'}</th>
        <td>{item.mnn ?? 'Не определено'}</td><td className="money">{item.amount == null ? 'Не указана' : money.format(Number(item.amount))}</td>
        <td>{item.checks.length ? item.checks.map((check, checkIndex) => <CheckView key={checkIndex} check={check}/>) : 'Проверок нет'}</td>
      </tr>)}</tbody></table></div>
    {!result.items.length && <p>Позиций нет.</p>}<footer>{result.disclaimer}</footer>
  </section>
}
function App() {
  const [file, setFile] = useState<File | null>(null)
  const [diagnoses, setDiagnoses] = useState('')
  const [result, setResult] = useState<ApplicationCard | null>(null)
  const [issues, setIssues] = useState<Issue[]>([])
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const current = useRef<AbortController | null>(null)
  async function request(operation: (signal: AbortSignal) => Promise<ApplicationCard | void>) {
    current.current?.abort()
    const controller = new AbortController()
    current.current = controller
    setBusy(true)
    setIssues([])
    setMessage('')
    try {
      const data = await operation(controller.signal)
      if (controller.signal.aborted) return
      if (data) {
        setResult(data)
        history.replaceState(null, '', '?application=' + encodeURIComponent(data.application.id))
      }
    } catch (error) {
      if (controller.signal.aborted) return
      setMessage(error instanceof Error ? error.message : 'Не удалось выполнить запрос.')
      if (error instanceof RequestError) setIssues(error.issues)
    } finally {
      if (!controller.signal.aborted) setBusy(false)
    }
  }
  useEffect(() => {
    const saved = new URLSearchParams(location.search).get('application')
    if (saved) void request(signal => loadApplication(saved, signal))
    return () => current.current?.abort()
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
    if (!file) return
    setResult(null)
    void request(signal => uploadApplication(file, diagnoses.split(/\r?\n/).map(value => value.trim()).filter(Boolean), signal))
  }
  return <>
    <header><div className="header-content"><span className="brand-icon" aria-hidden="true">✚</span>
      <div><strong>Врач</strong><small>Помощник комиссии по ТЖС</small></div><span className="demo-tag">Демонстрация</span></div></header>
    <main><section className="intro"><div className="eyebrow">Материальная помощь</div><h1>Проверка расходов по диагнозам</h1>
      <p>Загрузите XLSX с препаратами и суммами. Читается первый лист. Диагноз можно указать в строке препарата или добавить к заявке ниже.</p></section>
      <div className="notice"><strong>Справочник демонстрационный (синтетический).</strong>
        <p>Результаты не являются клинической истиной. Анализ носит рекомендательный характер; решение принимает человек.</p></div>
      <form className="upload" onSubmit={submit}><div className="upload-title"><h2>Загрузить заявку</h2></div>
        <div className="upload-fields"><label className="file-label">Файл заявки (.xlsx)
          <input type="file" accept=".xlsx" required onChange={event => choose(event.target.files?.[0] ?? null)}/></label>
          <label>Дополнительные диагнозы (необязательно, по одному на строку)
            <textarea rows={3} value={diagnoses} onChange={event => setDiagnoses(event.target.value)}/></label>
          <button className="button primary" type="submit" disabled={!file || busy}>{busy ? 'Обработка…' : 'Проверить заявку'}</button></div></form>
      {busy && <p role="status" className="notice">Запрос выполняется…</p>}
      {(issues.length > 0 || message) && <div role="alert" className="notice error">
        {message && <p>{message}</p>}{issues.length > 0 && <IssueList issues={issues}/>}</div>}
      {result && <ApplicationView result={result} busy={busy} onDownload={() => {
        void request(signal => downloadReport(result.application.id, signal))
      }}/>}
      <p className="bottom-note">Результат носит рекомендательный характер; решение принимает человек.</p></main>
  </>
}
createRoot(document.getElementById('root')!).render(<App/>)
