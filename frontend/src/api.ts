import { CONTRACT_ERROR, isIssue, isRecord, parseApplicationCard } from './contract'
import type { Issue } from './contract'

const baseUrl = (import.meta.env.VITE_API_URL ?? '').replace(/\/+$/, '')
export const apiUrl = (path: string) => baseUrl + path
export class RequestError extends Error {
  constructor(message: string, readonly issues: Issue[] = []) { super(message) }
}
async function fetchApi(path: string, options: RequestInit): Promise<Response> {
  let response: Response
  try {
    response = await fetch(apiUrl(path), { credentials: 'include', ...options })
  } catch (error) {
    if (options.signal?.aborted) throw error
    throw new RequestError('Не удалось связаться с сервером. Проверьте соединение и повторите попытку.')
  }
  if (response.status === 401) throw new RequestError('Нужен вход в систему. Выполните вход и повторите запрос.')
  if (!response.ok) {
    const json: unknown = await response.json().catch(() => null)
    const issues = isRecord(json) && Array.isArray(json.errors) ? json.errors.filter(isIssue) : []
    throw new RequestError(issues.length ? 'Сервер не смог выполнить запрос:' : 'Не удалось выполнить запрос. Повторите попытку позднее.', issues)
  }
  return response
}
async function readCard(response: Response) {
  const json: unknown = await response.json().catch(() => { throw new RequestError(CONTRACT_ERROR) })
  return parseApplicationCard(json)
}
export async function loadApplication(id: string, signal: AbortSignal) {
  return readCard(await fetchApi('/applications/' + encodeURIComponent(id), { signal }))
}
export async function uploadApplication(file: File, diagnoses: string[], signal: AbortSignal) {
  const body = new FormData()
  body.set('file', file)
  diagnoses.forEach(diagnosis => body.append('diagnoses', diagnosis))
  return readCard(await fetchApi('/applications', { method: 'POST', body, signal }))
}
export async function downloadReport(id: string, signal: AbortSignal): Promise<void> {
  const response = await fetchApi('/applications/' + encodeURIComponent(id) + '/report.xlsx', { signal })
  const blob = await response.blob()
  if (signal.aborted) return
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = 'application-' + id + '.xlsx'
  document.body.append(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
