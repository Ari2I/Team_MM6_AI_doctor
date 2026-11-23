import { useState } from 'react'
import type { DragEvent, FormEvent } from 'react'

type Props = {
  file: File | null
  diagnoses: string
  ageGroup: string
  busy: boolean
  onChoose: (file: File | null) => void
  onDiagnoses: (value: string) => void
  onAgeGroup: (value: string) => void
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
}

export function UploadForm({ file, diagnoses, ageGroup, busy, onChoose, onDiagnoses, onAgeGroup, onSubmit }: Props) {
  const [dragging, setDragging] = useState(false)
  const [dropError, setDropError] = useState('')

  function drop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault()
    setDragging(false)
    const dropped = event.dataTransfer.files[0]
    if (!dropped) return
    if (!dropped.name.toLowerCase().endsWith('.xlsx')) {
      setDropError('Нужен файл Excel с расширением .xlsx')
      return
    }
    setDropError('')
    onChoose(dropped)
  }

  return <form className="card upload" onSubmit={onSubmit}>
    <label
      className={'dropzone' + (dragging ? ' dragging' : '') + (file ? ' has-file' : '')}
      onDragOver={event => { event.preventDefault(); setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      onDrop={drop}
    >
      <input className="visually-hidden" type="file" accept=".xlsx"
        onChange={event => { setDropError(''); onChoose(event.target.files?.[0] ?? null) }}/>
      <span className="dropzone-icon" aria-hidden="true">{file ? '✓' : '⇪'}</span>
      {file
        ? <span><strong>{file.name}</strong><small>Нажмите, чтобы выбрать другой файл</small></span>
        : <span><strong>Перетащите файл Excel сюда</strong><small>или нажмите, чтобы выбрать .xlsx на компьютере</small></span>}
    </label>
    {dropError && <p className="field-error" role="alert">{dropError}</p>}
    <p className="hint">
      Читается первый лист. Одна строка — один препарат. Диагноз можно указать в строке препарата
      или добавить к заявке ниже.
    </p>

    <div className="upload-fields">
      <label className="field">Дополнительные диагнозы (необязательно, по одному на строку)
        <textarea rows={3} value={diagnoses} onChange={event => onDiagnoses(event.target.value)}
          placeholder="J45.9 Бронхиальная астма"/>
        <small>Указывайте код МКБ-10 вместе с названием, например «J45.9 Бронхиальная астма».</small>
      </label>
      <label className="field">Возрастная группа для выбора рекомендаций
        <select value={ageGroup} onChange={event => onAgeGroup(event.target.value)}>
          <option value="">Не указана</option>
          <option value="adult">Взрослые</option>
          <option value="child">Дети</option>
        </select>
      </label>
    </div>

    <div className="upload-footer">
      <button className="button primary" type="submit" disabled={!file || busy}>
        {busy ? 'Обработка…' : 'Проверить заявку'}
      </button>
    </div>
  </form>
}
