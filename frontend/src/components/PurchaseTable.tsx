import { Fragment, useState } from 'react'
import type { ItemDetails } from '../contract'
import { itemHasNoCategory, itemNeedsReview, money } from '../lib/labels'
import { CheckBlock, CheckLine } from './CheckBlock'

type Filter = 'all' | 'review' | 'nocategory'

// Таблица покупок. Проверки по диагнозам стоят прямо под своей позицией, категории не пересчитываются.
export function PurchaseTable({ items }: { items: ItemDetails[] }) {
  const [filter, setFilter] = useState<Filter>('all')
  const [openIndex, setOpenIndex] = useState<number | null>(null)

  const filters: { key: Filter; label: string; count: number }[] = [
    { key: 'all', label: 'Все', count: items.length },
    { key: 'review', label: 'Требуют проверки', count: items.filter(itemNeedsReview).length },
    { key: 'nocategory', label: 'Категория не определена', count: items.filter(itemHasNoCategory).length },
  ]
  // Запоминаем исходный номер, чтобы раскрытие строки не «прыгало» после фильтра.
  const visible = items
    .map((item, index) => ({ item, index }))
    .filter(({ item }) => filter === 'all' || (filter === 'review' ? itemNeedsReview(item) : itemHasNoCategory(item)))

  return <div>
    <div className="filters" role="group" aria-label="Фильтр позиций">
      {filters.map(row => <button key={row.key} aria-pressed={filter === row.key} onClick={() => setFilter(row.key)}>
        {row.label} <span className="count">{row.count}</span>
      </button>)}
    </div>

    <div className="table-scroll">
      <table>
        <thead><tr>
          <th className="col-row">Строка</th>
          <th>Препарат</th>
          <th className="col-money">Сумма</th>
          <th>Проверки по диагнозам</th>
          <th><span className="visually-hidden">Подробности</span></th>
        </tr></thead>
        <tbody>
          {visible.length === 0 && <tr><td colSpan={5} className="empty">В этом фильтре позиций нет.</td></tr>}
          {visible.map(({ item, index }) => {
            const open = openIndex === index
            return <Fragment key={index}>
              <tr className={open ? 'row-open' : ''}>
                <td className="col-row">{item.row_number}</td>
                <th scope="row">
                  <span className="drug">{item.drug_name ?? 'Название отсутствует'}</span>
                  <small>{item.mnn ?? 'МНН не определено'}</small>
                </th>
                <td className="col-money">{item.amount == null ? 'Не указана' : money.format(Number(item.amount))}</td>
                <td>{item.checks.length
                  ? item.checks.map((check, checkIndex) => <CheckLine key={checkIndex} check={check}/>)
                  : <span className="muted">Проверок нет</span>}</td>
                <td className="col-toggle">
                  {item.checks.length > 0 && <button className="link-button" aria-expanded={open}
                    onClick={() => setOpenIndex(open ? null : index)}>
                    {open ? 'Скрыть ▲' : 'Подробнее ▼'}
                  </button>}
                </td>
              </tr>
              {open && <tr className="details-row"><td colSpan={5}>
                <div className="details">
                  {item.match_confidence != null &&
                    <small className="muted">Уверенность сопоставления МНН: {Math.round(item.match_confidence * 100)}%</small>}
                  {item.checks.map((check, checkIndex) => <CheckBlock key={checkIndex} check={check}/>)}
                </div>
              </td></tr>}
            </Fragment>
          })}
        </tbody>
      </table>
    </div>
  </div>
}
