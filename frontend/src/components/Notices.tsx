import { useEffect, useState } from 'react'
import type { Issue } from '../contract'
import { columnNames } from '../lib/labels'

export function IssueList({ issues }: { issues: Issue[] }) {
  return <ul className="issue-list">{issues.map((issue, index) =>
    <li key={index}>
      {issue.row != null && <strong>Строка {issue.row} · </strong>}
      {issue.column && <span>Столбец «{columnNames[issue.column] ?? issue.column}» · </span>}
      {issue.message}
    </li>
  )}</ul>
}

export function ErrorBox({ message, issues }: { message: string; issues: Issue[] }) {
  return <div role="alert" className="notice error">
    <strong>Не удалось выполнить запрос</strong>
    {message && <p>{message}</p>}
    {issues.length > 0 && <IssueList issues={issues}/>}
  </div>
}

function formatElapsed(seconds: number) {
  const minutes = Math.floor(seconds / 60)
  return minutes + ':' + String(seconds % 60).padStart(2, '0')
}

// Анализ идёт одним длинным запросом (до нескольких минут), прогресса сервер не присылает.
export function Loading() {
  const [seconds, setSeconds] = useState(0)
  useEffect(() => {
    const timer = setInterval(() => setSeconds(value => value + 1), 1000)
    return () => clearInterval(timer)
  }, [])
  return <section className="card loading" role="status" aria-live="polite">
    <div className="spinner" aria-hidden="true"/>
    <div>
      <strong>Идёт проверка заявки · {formatElapsed(seconds)}</strong>
      <p className="muted">Анализ может занять несколько минут. Не закрывайте и не обновляйте страницу: результат придёт одним ответом.</p>
    </div>
  </section>
}
