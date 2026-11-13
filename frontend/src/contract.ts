// Mirrors app/schemas/application_api.py; extra response fields are allowed.
export type Light = 'green' | 'yellow' | 'red' | 'undetermined'
export type Category = 'A' | 'B' | 'V'
export const CATEGORY_LABELS = { A: 'А', B: 'Б', V: 'В', undetermined: 'не определена' } as const
export type Issue = {
  level: string; code: string; message: string; row?: number | null; column?: string | null
}
export type CheckDetails = {
  diagnosis: string
  category?: Category | null
  category_label?: string | null
  source?: string | null
  reference_version?: string | null
  comment?: string | null
  needs_review: boolean
  review_reason?: string | null
}
export type ItemDetails = {
  row_number: number
  drug_name?: string | null
  mnn?: string | null
  match_confidence?: number | null
  amount?: string | null
  checks: CheckDetails[]
}
export type ApplicationCard = {
  application: {
    id: string
    filename: string
    status: 'uploaded' | 'parsed' | 'analyzed' | 'error'
    traffic_light?: Light | null
    requires_review: boolean
    created_at: string
    summary?: string | null
    warning_message?: string | null
    category_shares?: Record<string, number> | null
  }
  disclaimer: string
  diagnoses: string[]
  items: ItemDetails[]
  issues: Issue[]
}
export const CONTRACT_ERROR = 'Сервер вернул ответ в неподдерживаемом формате. Не удалось показать результат. Обратитесь к администратору.'
export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
const isString = (value: unknown): value is string => typeof value === 'string'
const isNumber = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
const optionalString = (value: unknown) => value == null || isString(value)
const optionalNumber = (value: unknown) => value == null || isNumber(value)
const oneOf = (value: unknown, choices: readonly string[]) => isString(value) && choices.includes(value)
const isDecimal = (value: unknown) => value == null ||
  (isString(value) && /^[+-]?\d+(\.\d+)?$/.test(value) && Number.isFinite(Number(value)))
export function isIssue(value: unknown): value is Issue {
  return isRecord(value) && isString(value.level) && isString(value.code) &&
    isString(value.message) && (value.row == null || Number.isInteger(value.row)) && optionalString(value.column)
}
function isCheck(value: unknown): value is CheckDetails {
  return isRecord(value) && isString(value.diagnosis) &&
    (value.category == null || oneOf(value.category, ['A', 'B', 'V'])) &&
    ['category_label', 'source', 'reference_version', 'comment', 'review_reason']
      .every(key => optionalString(value[key])) && typeof value.needs_review === 'boolean'
}
function isItem(value: unknown): value is ItemDetails {
  return isRecord(value) && Number.isInteger(value.row_number) &&
    optionalString(value.drug_name) && optionalString(value.mnn) &&
    optionalNumber(value.match_confidence) && isDecimal(value.amount) &&
    Array.isArray(value.checks) && value.checks.every(isCheck)
}
export function parseApplicationCard(value: unknown): ApplicationCard {
  if (isRecord(value) && isRecord(value.application)) {
    const app = value.application
    if (isString(app.id) && isString(app.filename) && isString(app.created_at) &&
      oneOf(app.status, ['uploaded', 'parsed', 'analyzed', 'error']) &&
      (app.traffic_light == null || oneOf(app.traffic_light, ['green', 'yellow', 'red', 'undetermined'])) &&
      typeof app.requires_review === 'boolean' && optionalString(app.summary) &&
      optionalString(app.warning_message) && (app.category_shares == null ||
        (isRecord(app.category_shares) && Object.values(app.category_shares).every(isNumber))) &&
      isString(value.disclaimer) && Array.isArray(value.diagnoses) && value.diagnoses.every(isString) &&
      Array.isArray(value.items) && value.items.every(isItem) &&
      Array.isArray(value.issues) && value.issues.every(isIssue)) return value as ApplicationCard
  }
  throw new Error(CONTRACT_ERROR)
}
