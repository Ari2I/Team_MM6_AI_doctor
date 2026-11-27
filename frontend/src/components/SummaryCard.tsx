import type { ApplicationCard } from '../contract'
import { categoryInfo, categoryKeys, lights, money, percent } from '../lib/labels'

// Главный блок результата: индикатор, доли сумм по категориям и резюме от backend.
// Проценты и суммы приходят готовыми (category_shares), здесь они только показываются.
export function SummaryCard({ card }: { card: ApplicationCard }) {
  const app = card.application
  const shares = app.category_shares

  return <section className="card summary" aria-label="Итог проверки">
    <div className="eyebrow">Итог проверки</div>

    <div className="light-line">
      <span className={'badge light-' + (app.traffic_light ?? 'none')}>
        {app.traffic_light ? lights[app.traffic_light] : 'Индикатор ещё не рассчитан'}
      </span>
      {app.requires_review && <strong className="flag">Требует проверки человеком</strong>}
    </div>
    <p className="muted">Индикатор — подсказка для комиссии, а не решение об одобрении или отказе.</p>

    {shares ? <div className="breakdown">
      <div className="bar" role="img" aria-label="Распределение сумм по категориям">
        {categoryKeys.filter(key => (shares[key] ?? 0) > 0).map(key =>
          <span key={key} className={'bar-part ' + categoryInfo[key].css}
            style={{ width: shares[key] + '%' }} title={categoryInfo[key].label}/>)}
      </div>
      <div className="amounts">
        {categoryKeys.map(key => <div className="amount" key={key} title={categoryInfo[key].hint}>
          <span className="amount-name"><span className={'dot ' + categoryInfo[key].css}/>{categoryInfo[key].label}</span>
          <strong>{shares[key] == null ? 'Нет данных' : percent.format(shares[key]) + '%'}</strong>
          {shares[key + '_amount'] != null && <small>{money.format(shares[key + '_amount'])}</small>}
        </div>)}
      </div>
    </div> : <p className="muted">Доли сумм ещё не рассчитаны.</p>}

    {app.summary && <p className="summary-text">{app.summary}</p>}
  </section>
}
