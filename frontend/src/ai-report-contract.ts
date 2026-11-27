// Mirrors app/schemas/ai_report.py. Report text and categories come from the server.
import type { Category } from './contract'
import { isRecord } from './contract'

export type ReportSource = {
  number: number; title: string; version: string; location: string
  quote: string; url: string; copy_url: string | null
}
export type ReportStatement = { text: string; sources: ReportSource[] }
export type ReportItem = {
  number: number; name: string; diagnosis: string; category: string; evidence: string; comment: string
  established: ReportStatement[]; uncertain: ReportStatement[]
  questions: (ReportStatement & { why: string })[]
  actions: string[]; sources: ReportSource[]
}
export type AIReportCheck = {
  number: number; item_id: string; diagnosis_id: string; row_number: number
  backend_rule: {
    category: Category | null; source: string | null; reference_version: string | null
    comment: string | null; needs_review: boolean; review_reason: string | null
  }
  ai_rule_category: Category | null; ai_proposed_category: Category | null
  classification_basis: 'APPROVED_RULE' | 'ML2_PROPOSAL' | 'CONFLICT' | 'NONE' | null
  conflict: boolean; processed: boolean; generation_mode: 'OFFLINE' | 'MODEL' | 'FALLBACK' | null
  form_status: 'MATCH' | 'MISMATCH' | 'UNKNOWN' | null; reasons: string[]
}
export type AIReport = {
  schema_version: 'ai-report.1'; application_id: string; saved_at: string
  backend_revision: string; ai_revision: string; completeness: 'COMPLETE' | 'PARTIAL'
  human_report: {
    schema_version: 'human-report.1'; title: string; notice: string; summary: string
    recommendation_note: string; review_note: string; items: ReportItem[]
    costs: { amount: string; currency: string; currency_label: string; unknown_amount_count: number }[]
    purchases_without_amount_count: number
  }
  checks: AIReportCheck[]
}
export type AIReportState =
  | { kind: 'loading' | 'empty' }
  | { kind: 'error'; message: string }
  | { kind: 'report'; report: AIReport }

export const AI_REPORT_ERROR = 'Не удалось показать разбор: формат ответа не поддерживается.'
const string = (v: unknown): v is string => typeof v === 'string'
const count = (v: unknown): v is number => Number.isSafeInteger(v) && (v as number) >= 0
const positive = (v: unknown): v is number => count(v) && v > 0
const nullableString = (v: unknown) => v === null || string(v)
const strings = (v: unknown) => Array.isArray(v) && v.every(string)
const category = (v: unknown) => v === null || ['А', 'Б', 'В'].includes(v as string)
const revision = (v: unknown) => string(v) && /^[0-9a-f]{40}$/.test(v)
const source = (v: unknown) => isRecord(v) && positive(v.number) &&
  ['title', 'version', 'location', 'quote', 'url'].every(k => string(v[k])) && nullableString(v.copy_url)
const statement = (v: unknown) => isRecord(v) && string(v.text) &&
  Array.isArray(v.sources) && v.sources.every(source)
const item = (v: unknown) => isRecord(v) && positive(v.number) &&
  ['name', 'diagnosis', 'category', 'evidence', 'comment'].every(k => string(v[k])) &&
  ['established', 'uncertain'].every(k => Array.isArray(v[k]) && (v[k] as unknown[]).every(statement)) &&
  Array.isArray(v.questions) && v.questions.every(q => statement(q) && isRecord(q) && string(q.why)) &&
  strings(v.actions) && Array.isArray(v.sources) && v.sources.every(source)
const check = (v: unknown) => isRecord(v) && positive(v.number) && positive(v.row_number) &&
  string(v.item_id) && v.item_id.length > 0 && string(v.diagnosis_id) && v.diagnosis_id.length > 0 &&
  isRecord(v.backend_rule) && category(v.backend_rule.category) &&
  ['source', 'reference_version', 'comment', 'review_reason']
    .every(k => nullableString((v.backend_rule as Record<string, unknown>)[k])) &&
  typeof v.backend_rule.needs_review === 'boolean' && category(v.ai_rule_category) && category(v.ai_proposed_category) &&
  [null, 'APPROVED_RULE', 'ML2_PROPOSAL', 'CONFLICT', 'NONE'].includes(v.classification_basis as string | null) &&
  typeof v.conflict === 'boolean' && typeof v.processed === 'boolean' &&
  [null, 'OFFLINE', 'MODEL', 'FALLBACK'].includes(v.generation_mode as string | null) &&
  [null, 'MATCH', 'MISMATCH', 'UNKNOWN'].includes(v.form_status as string | null) && strings(v.reasons)

export function parseAIReport(value: unknown, applicationId: string): AIReport {
  const fail = (): never => { throw new Error(AI_REPORT_ERROR) }
  if (!isRecord(value) || value.schema_version !== 'ai-report.1' || value.application_id !== applicationId ||
      !string(value.saved_at) || !Number.isFinite(Date.parse(value.saved_at)) ||
      !revision(value.backend_revision) || !revision(value.ai_revision) ||
      !['COMPLETE', 'PARTIAL'].includes(value.completeness as string) || !isRecord(value.human_report)) return fail()
  const report = value.human_report
  if (report.schema_version !== 'human-report.1' ||
      !['title', 'notice', 'summary', 'recommendation_note', 'review_note'].every(k => string(report[k])) ||
      !Array.isArray(report.items) || !report.items.every(item) ||
      !Array.isArray(report.costs) || !count(report.purchases_without_amount_count) ||
      !report.costs.every(c => isRecord(c) && string(c.amount) && /^\d+(?:\.\d+)?$/.test(c.amount) &&
        string(c.currency) && string(c.currency_label) && count(c.unknown_amount_count)) ||
      !Array.isArray(value.checks) || !value.checks.every(check) || value.checks.length !== report.items.length) return fail()
  const pairs = new Set<string>()
  for (let i = 0; i < value.checks.length; i++) {
    const current = value.checks[i] as AIReportCheck
    const row = report.items[i] as ReportItem
    const pair = JSON.stringify([current.item_id, current.diagnosis_id])
    if (current.number !== i + 1 || row.number !== current.number || pairs.has(pair)) return fail()
    pairs.add(pair)
    const sources = new Map(row.sources.map(s => [s.number, s]))
    if (sources.size !== row.sources.length) return fail()
    for (const entry of [...row.established, ...row.uncertain, ...row.questions]) {
      for (const reference of entry.sources) {
        const original = sources.get(reference.number)
        if (!original || (Object.keys(original) as (keyof ReportSource)[])
          .some(key => original[key] !== reference[key])) return fail()
      }
    }
  }
  return value as AIReport
}

export function safeSourceURL(value: string | null): string | undefined {
  if (!value) return undefined
  try {
    const url = new URL(value)
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : undefined
  } catch { return undefined }
}
