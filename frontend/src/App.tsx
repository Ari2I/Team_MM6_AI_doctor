import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import type { ApplicationCard, Issue } from './contract'
import { downloadReport, loadApplication, uploadApplication, RequestError } from './api'
import { ErrorBox, Loading } from './components/Notices'
import { ResultView } from './components/ResultView'
import { UploadForm } from './components/UploadForm'

export function App() {
  const [file, setFile] = useState<File | null>(null)
  const [diagnoses, setDiagnoses] = useState('')
  const [ageGroup, setAgeGroup] = useState('')
  const [result, setResult] = useState<ApplicationCard | null>(null)
  const [issues, setIssues] = useState<Issue[]>([])
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const current = useRef<AbortController | null>(null)

  // ---- Запросы к серверу. Логика перенесена из прежнего main.tsx без изменений. ----
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
    void request(signal => uploadApplication(
      file,
      diagnoses.split(/\r?\n/).map(value => value.trim()).filter(Boolean),
      signal,
      ageGroup,
    ))
  }

  // ---- Дальше только то, что видит пользователь. ----
  const hasError = message !== '' || issues.length > 0

  return <>
    <header className="topbar">
      <div className="topbar-inner">
        <span className="brand-icon" aria-hidden="true">✚</span>
        <div className="brand-text"><strong>Врач</strong><small>Помощник комиссии по ТЖС</small></div>
      </div>
    </header>

    <main>
      {!result && !busy && <section className="intro">
        <div className="eyebrow">Материальная помощь</div>
        <h1>Проверка расходов по диагнозам</h1>
        <p>Загрузите Excel-заявку: система сопоставит купленные препараты с диагнозом и подготовит подсказки для комиссии. Анализ носит рекомендательный характер, решение принимает человек.</p>
      </section>}

      {!result && !busy && <UploadForm file={file} diagnoses={diagnoses} ageGroup={ageGroup} busy={busy}
        onChoose={choose} onDiagnoses={setDiagnoses} onAgeGroup={setAgeGroup} onSubmit={submit}/>}

      {busy && !result && <Loading/>}
      {hasError && <ErrorBox message={message} issues={issues}/>}

      {result && <ResultView key={result.application.id} card={result} busy={busy}
        onDownload={() => { void request(signal => downloadReport(result.application.id, signal)) }}
        onReset={() => choose(null)}/>}
    </main>
  </>
}
