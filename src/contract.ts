export type Mode = 'offline' | 'gigachat' | 'mixed'
export type Light = 'green' | 'yellow' | 'red' | 'undetermined'
type Category = 'А' | 'Б' | 'В'

export type Issue = {
  level: 'critical' | 'warning'
  code: string
  message: string
  row: number | null
}

export type Citation = {
  fragment_id: string
  title: string
  version: string
  location: string
  quote: string
  url: string | null
  publisher: string | null
  origin: 'official' | 'synthetic'
  retrieved_at: string | null
}

export type Check = {
  item_id: string
  diagnosis_id: string
  category: Category | null
  evidence_status: 'SUPPORTED' | 'INSUFFICIENT_DATA' | 'UNCONFIRMED' | 'SOURCE_CONFLICT' | 'PROCESSING_ERROR'
  explanation: string
  reason_code: string
  citations: Citation[]
  review_sources: Citation[]
  effective_mode: Mode
  retrieval: 'lexical' | 'vector'
  fallback_reason: string | null
}

export type Purchase = {
  item_id: string
  row_number: number
  drug_name: string | null
  mnn: string | null
  amount: string | null
  match_candidates: string[]
}

export type Result = {
  application_id: string
  filename: string
  diagnoses: { diagnosis_id: string; name: string }[]
  items: Purchase[]
  checks: Check[]
  issues: Issue[]
  summary_by_diagnosis: {
    diagnosis_id: string
    traffic_light: Light
    reason: string
    amounts: Record<Category | 'undetermined', string>
    shares: Record<Category | 'undetermined', string>
    confirmed_count: number
    pair_count: number
  }[]
  known_total: string
  reference_version: string
  corpus_version: string
  effective_mode: Mode
  fallback_reason: string | null
  disclaimer: string
}
