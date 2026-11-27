import { useEffect, useState } from 'react'
import { loadAIReport } from '../api'
import type { AIReportCheck, AIReportState, ReportItem, ReportSource, ReportStatement } from '../ai-report-contract'
import { safeSourceURL } from '../ai-report-contract'
import { CategoryBadge } from './CategoryBadge'
import '../ai-report.css'

const reasonLabels: Record<string, string> = {
  NEEDS_REVIEW: 'Требуется сверка специалистом', NO_EVIDENCE: 'Основание для этой пары не найдено',
  NEEDS_CONTEXT: 'Недостаточно сведений для проверки', INN_UNRESOLVED: 'МНН не удалось определить',
  AI_UNAVAILABLE: 'Модель недоступна', AI_TIMEOUT: 'Время обработки истекло',
  AI_INVALID_RESPONSE: 'Ответ модели не прошёл проверку', SOURCE_ERROR: 'Источник недоступен',
  BUDGET_EXHAUSTED: 'Не хватило ресурса для завершения проверки', CONTEXT_LIMIT: 'Превышен допустимый объём материалов',
  DIAGNOSIS_MISMATCH: 'Название диагноза не согласовано с выбранной рекомендацией',
  NETWORK_POLICY_DENIED: 'Запрос не соответствует ограничениям передачи данных',
  FORM_MISMATCH: 'Есть расхождение формы или концентрации', MODEL_DISABLED: 'Модельный режим выключен',
}

function Source({ source, pair }: { source: ReportSource; pair: number }) {
  const primary = safeSourceURL(source.url)
  const copy = safeSourceURL(source.copy_url)
  return <details className="ai-source" id={`ai-source-${pair}-${source.number}`} open>
    <summary><span>Источник {source.number} · {source.title}</span><small>{source.version} · {source.location}</small></summary>
    <blockquote className="quote">{source.quote}</blockquote>
    <div className="ai-source-links">
      {primary && <a href={primary} target="_blank" rel="noopener noreferrer">Открыть источник ↗</a>}
      {copy && copy !== primary && <a href={copy} target="_blank" rel="noopener noreferrer">Копия документа ↗</a>}
    </div>
  </details>
}

function Statements({ title, values, pair }: { title: string; values: ReportStatement[]; pair: number }) {
  if (!values.length) return null
  return <section className="ai-section"><h4>{title}</h4><ul>{values.map((statement, index) => <li key={index}>
    <p>{statement.text}</p>
    {'why' in statement && typeof statement.why === 'string' &&
      <p className="ai-why"><strong>Почему это важно: </strong>{statement.why}</p>}
    {statement.sources.length > 0 && <div className="ai-inline-sources">{statement.sources.map(source =>
      <a key={source.number} href={`#ai-source-${pair}-${source.number}`}>Источник {source.number}</a>)}</div>}
  </li>)}</ul></section>
}

function Pair({ row, check, first }: { row: ReportItem; check: AIReportCheck; first: boolean }) {
  return <details className="ai-pair" open={first}>
    <summary className="ai-pair-summary">
      <div className="ai-pair-title"><strong>{row.name}</strong><span className="muted">{row.diagnosis} · Строка {check.row_number}</span></div>
      <div className="ai-pair-badges">
        <span className={check.processed ? 'flag' : 'flag flag-strong'}>
          {check.processed ? 'Требует проверки' : 'Проверка не завершена'}
        </span>
        <span className="ai-disclosure"><span className="when-closed">Подробнее ＋</span><span className="when-open">Свернуть −</span></span>
      </div>
    </summary>
    <div className="ai-pair-body">
      {check.conflict && <div className="notice error"><strong>Категории расходятся</strong>
        <p>В сохранённом отчёте отмечено расхождение категорий. Специалисту нужно сверить основания.</p>
      </div>}
      {!check.processed && <div className="notice warn"><strong>Для этой пары разбор не завершён</strong>
        <p>Доступные результаты остальных пар сохранены.</p>
      </div>}
      <div className="ai-category"><small>Категория</small><strong>{row.category}</strong>
        <small>{check.generation_mode === 'MODEL' ? 'Пояснение подготовлено моделью'
          : check.generation_mode === 'FALLBACK' ? 'Пояснение подготовлено в резервном режиме'
          : check.generation_mode === 'OFFLINE' ? 'Локальное пояснение' : 'Категория требует сверки'}</small>
      </div>
      {check.classification_basis === 'CONFLICT' && <div className="ai-category-grid">
        <div className="ai-category"><small>По правилу корпуса</small><CategoryBadge category={check.ai_rule_category}/></div>
        <div className="ai-category"><small>Предложение ИИ</small><CategoryBadge category={check.ai_proposed_category}/></div>
      </div>}
      {check.form_status === 'MISMATCH' && <div className="notice warn"><strong>Сверьте форму и концентрацию</strong>
        <p>Обнаружено расхождение с источником. Это отдельное предупреждение; оно не меняет категорию.</p>
      </div>}
      <div className="ai-pair-explanation"><p>{row.comment}</p><p className="muted">{row.evidence}</p></div>
      <div className="ai-text-grid">
        <Statements title="На каких сведениях основан разбор" values={row.established} pair={row.number}/>
        <Statements title="Что не подтверждено" values={row.uncertain} pair={row.number}/>
      </div>
      <Statements title="Что уточнить у специалиста" values={row.questions} pair={row.number}/>
      {row.actions.length > 0 && <section className="ai-section"><h4>Дальнейшие действия</h4>
        <ol>{row.actions.map((action, index) => <li key={index}>{action}</li>)}</ol>
      </section>}
      <section className="ai-section"><h4>Источники и цитаты</h4>
        {row.sources.length ? <div className="ai-sources">{row.sources.map(source =>
          <Source key={source.number} source={source} pair={row.number}/>)}</div>
          : <p className="muted">Подтверждённые цитаты для этой пары отсутствуют.</p>}
      </section>
      {check.reasons.length > 0 && <div className="ai-reasons">{[...new Set(check.reasons)].map(reason =>
        <span key={reason} className="chip">{Object.hasOwn(reasonLabels, reason)
          ? reasonLabels[reason] : 'Требуется дополнительная сверка'}</span>)}</div>}
    </div>
  </details>
}

// Отображение отделено от загрузки: ошибки отчёта не скрывают карточку/Excel.
export function AIReportView({ state, onRetry }: { state: AIReportState; onRetry?: () => void }) {
  const report = state.kind === 'report' ? state.report : null
  const hasFallback = report?.checks.some(check => check.generation_mode === 'FALLBACK')
  const model = report?.checks.some(check => check.generation_mode === 'MODEL')
  const offline = report?.checks.some(check => check.generation_mode === 'OFFLINE')
  return <section className="card ai-panel" id="ai-analysis" aria-label="Разбор ИИ">
    <div className="ai-title-row"><div><div className="eyebrow">Пояснения и основания</div><h3>Разбор ИИ</h3></div>
      {report && <div className="ai-statuses">
        <span className="badge cat-u">{report.completeness === 'PARTIAL' ? 'Частичный результат' : 'Все пары обработаны'}</span>
        <span className="chip">{hasFallback ? 'Есть резервные пояснения' : model ? 'Подготовлен моделью'
          : offline ? 'Локальный разбор' : 'Пояснения не получены'}</span>
      </div>}
    </div>
    <div aria-live="polite">
      {state.kind === 'loading' && <div className="ai-read-state" role="status">
        <div className="spinner" aria-hidden="true"/><p>Загружаем сохранённый разбор…</p>
      </div>}
      {state.kind === 'empty' && <div className="notice"><strong>Сохранённый разбор ИИ отсутствует</strong>
        <p>Для этой заявки подробный AI-отчёт не сохранён. Карточка и Excel доступны в обычном порядке.</p>
      </div>}
      {state.kind === 'error' && <div className="notice error" role="alert"><strong>Не удалось получить разбор</strong>
        <p>{state.message}</p>{onRetry && <button type="button" className="button secondary" onClick={onRetry}>Повторить загрузку отчёта</button>}
      </div>}
    </div>
    {report && <>
      <div className="ai-report-intro"><p>{report.human_report.summary}</p>
        <small>Сохранено: {new Date(report.saved_at).toLocaleString('ru-RU')}</small>
      </div>
      <div className={'notice ' + (hasFallback ? 'error' : 'warn')}>
        <strong>{hasFallback ? 'Часть пояснений подготовлена в резервном режиме' : 'Результат требует проверки человеком'}</strong>
        <p>{report.human_report.recommendation_note}</p><p>{report.human_report.notice}</p>
      </div>
      {report.completeness === 'PARTIAL' && <div className="notice warn"><strong>Часть пар не обработана</strong>
        <p>Посмотрите причины у соответствующих позиций. Повторное открытие показывает сохранённый результат.</p>
      </div>}
      <div className="ai-pairs">{report.human_report.items.map((row, index) =>
        <Pair key={`${report.checks[index].item_id}:${report.checks[index].diagnosis_id}`}
          row={row} check={report.checks[index]} first={index === 0}/>)}</div>
      <div className="ai-report-footer"><div><small>Суммы покупок из сохранённого отчёта</small>
        <div className="ai-costs">{report.human_report.costs.map(cost =>
          <strong key={cost.currency}>{cost.amount.replace('.', ',')} {cost.currency_label}</strong>)}</div>
        {report.human_report.costs.length === 0 && <p className="muted">Итоги по суммам отсутствуют.</p>}
        {report.human_report.purchases_without_amount_count > 0 &&
          <p className="muted">Покупок без суммы: {report.human_report.purchases_without_amount_count}</p>}
        <small>Покупки учитываются один раз, независимо от числа диагнозов.</small>
      </div><p className="muted">{report.human_report.review_note}</p></div>
    </>}
  </section>
}

function AIReportLoader({ applicationId }: { applicationId: string }) {
  const [state, setState] = useState<AIReportState>({ kind: 'loading' })
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    setState({ kind: 'loading' })
    loadAIReport(applicationId, controller.signal).then(report => {
      if (!controller.signal.aborted) setState(report ? { kind: 'report', report } : { kind: 'empty' })
    }).catch(error => {
      if (!controller.signal.aborted) setState({ kind: 'error', message: error instanceof Error
        ? error.message : 'Не удалось получить сохранённый разбор.' })
    })
    return () => controller.abort()
  }, [applicationId, attempt])
  return <AIReportView state={state} onRetry={() => setAttempt(value => value + 1)}/>
}

export function AIReportPanel({ applicationId }: { applicationId: string }) {
  // При переходе к другой заявке старые тексты не показываются даже на один кадр.
  return <AIReportLoader key={applicationId} applicationId={applicationId}/>
}
