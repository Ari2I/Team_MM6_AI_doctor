import type { ApplicationCard } from '../contract'
import { itemNeedsReview, money } from '../lib/labels'
import { IssueList } from './Notices'
import { PurchaseTable } from './PurchaseTable'
import { SummaryCard } from './SummaryCard'
import { AIReportPanel } from './AIReportPanel'

type Props = {
  card: ApplicationCard
  busy: boolean
  onDownload: () => void
  onReset: () => void
}

// Экран результата. Только показ данных: все расчёты уже сделал backend.
export function ResultView({ card, busy, onDownload, onReset }: Props) {
  const app = card.application
  const total = app.category_shares?.total_amount
  const reviewCount = card.items.filter(itemNeedsReview).length
  const versions = [...new Set(card.items.flatMap(item => item.checks.map(check => check.reference_version)).filter(Boolean))]
  const created = new Date(app.created_at)

  return <section className="result" aria-label="Результат проверки">
    <div className="card result-head">
      <div>
        <div className="eyebrow">Результат проверки</div>
        <h2>{app.filename}</h2>
        <small className="muted">Заявка {app.id}</small>
      </div>
      <div className="actions">
        <button className="button secondary" onClick={onReset}>Новая проверка</button>
        <button className="button secondary" disabled={busy || app.status !== 'analyzed'} onClick={onDownload}>
          {busy ? 'Подготовка…' : 'Скачать отчёт Excel'}
        </button>
      </div>
    </div>

    {app.status === 'error' && <div className="notice error">Анализ завершился с ошибкой. Требуется проверка человеком.</div>}
    {app.status !== 'analyzed' && app.status !== 'error' &&
      <div className="notice warn">Анализ ещё не завершён. Откройте карточку позднее.</div>}

    <div className="kpis">
      <div className="kpi"><small>Общая сумма</small><strong>{total == null ? 'Нет данных' : money.format(total)}</strong></div>
      <div className="kpi"><small>Позиций в заявке</small><strong>{card.items.length}</strong></div>
      <div className="kpi"><small>Требуют проверки</small><strong>{reviewCount} из {card.items.length}</strong></div>
    </div>

    <SummaryCard card={card}/>

    {app.reference_is_synthetic === true && <div className="notice warn">
      Использован синтетический справочник. Результат требует проверки по рабочим источникам.
    </div>}
    {app.warning_message && <div className="notice warn"><strong>Предупреждение</strong><p>{app.warning_message}</p></div>}
    {card.issues.length > 0 && <details className="notice warn" open>
      <summary>Замечания к Excel · {card.issues.length}</summary>
      <IssueList issues={card.issues}/>
    </details>}

    <div className="card table-card">
      <div className="table-head">
        <h3>Позиции заявки</h3>
        <div className="diagnosis-chips" aria-label="Диагнозы заявки">
          {card.diagnoses.length
            ? card.diagnoses.map((diagnosis, index) => <span className="chip" key={index}>{diagnosis}</span>)
            : <span className="muted">Диагнозы не указаны.</span>}
        </div>
      </div>
      {card.items.length ? <PurchaseTable items={card.items}/> : <p className="muted">Позиций нет.</p>}
    </div>

    {app.status === 'analyzed' && <AIReportPanel applicationId={app.id}/>}

    <details className="card tech">
      <summary>Справочник и версия данных</summary>
      <dl>
        {app.reference_name && <div><dt>Справочник</dt><dd>{app.reference_name}</dd></div>}
        {versions.length > 0 && <div><dt>Версия данных</dt><dd>{versions.join(', ')}</dd></div>}
        {app.age_group && <div><dt>Возрастная группа</dt><dd>{app.age_group === 'adult' ? 'Взрослые' : 'Дети'}</dd></div>}
        <div><dt>Проверка создана</dt><dd>{Number.isNaN(created.getTime()) ? app.created_at : created.toLocaleString('ru-RU')}</dd></div>
      </dl>
    </details>

    <footer className="disclaimer">{card.disclaimer}</footer>
  </section>
}
