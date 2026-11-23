import { Fragment } from 'react'
import type { CheckDetails } from '../contract'
import { reviewReasonLabel } from '../contract'
import { isAiFailure, splitComment } from '../lib/labels'
import { CategoryBadge } from './CategoryBadge'

// Превращает адреса https://… в тексте в ссылки. Остальные адреса остаются обычным текстом.
function LinkedText({ text }: { text: string }) {
  const parts = text.split(/(https:\/\/[^\s;)]+)/g)
  return <>{parts.map((part, index) => index % 2 === 1
    ? <a key={index} href={part} target="_blank" rel="noreferrer">{part}</a>
    : <Fragment key={index}>{part}</Fragment>)}</>
}

// Короткая строка проверки внутри таблицы: диагноз, категория и отметка о проверке.
export function CheckLine({ check }: { check: CheckDetails }) {
  return <div className="check-line">
    <span className="check-diagnosis">{check.diagnosis}</span>
    <CategoryBadge category={check.category}/>
    {check.needs_review && <span className={isAiFailure(check) ? 'flag flag-strong' : 'flag'}>
      {isAiFailure(check) ? 'ИИ не завершил проверку' : 'Требует проверки'}
    </span>}
  </div>
}

// Полное описание одной проверки: показывается по кнопке «Подробнее».
export function CheckBlock({ check }: { check: CheckDetails }) {
  const { main, quote } = splitComment(check.comment)
  return <section className="check-block">
    <header>
      <strong>{check.diagnosis}</strong>
      <CategoryBadge category={check.category}/>
      {check.category && check.category_label && <small>{check.category_label}</small>}
    </header>

    {check.needs_review && <div className={isAiFailure(check) ? 'notice error' : 'notice warn'}>
      <strong>{check.category ? 'Предварительный результат — требует проверки специалистом' : 'Требует проверки специалистом'}</strong>
      <p>{reviewReasonLabel(check.review_reason)}</p>
    </div>}

    {main ? <p className="comment"><LinkedText text={main}/></p> : <p className="muted">Комментарий не предоставлен.</p>}
    {quote && <blockquote className="quote"><LinkedText text={quote}/></blockquote>}

    <dl className="facts">
      <div><dt>Источник</dt><dd>{check.source ? <LinkedText text={check.source}/> : 'не указан'}</dd></div>
      <div><dt>Версия справочника</dt><dd>{check.reference_version ?? 'не указана'}</dd></div>
    </dl>
  </section>
}
