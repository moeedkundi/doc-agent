// Shapes returned by the DocAgent API (SPEC §9). Kept in one place so components agree.

export type DocType = 'invoice' | 'purchase_order'
export type RunStatus = 'queued' | 'running' | 'awaiting_review' | 'completed' | 'failed' | 'rejected'
export type EventKind = 'node_start' | 'tool_end' | 'node_end' | 'error' | 'interrupt'
export type MatchStatus = 'matched' | 'partial' | 'mismatch' | 'po_not_found'

export interface Sample {
  id: string
  doc_type: DocType
  template: string
  scanned: boolean
  number: string
  party: string | null
  currency: string | null
  total: string | null
  lines: number
  references_po: string | null
  expected_match: MatchStatus | null
  replay_available: boolean
}

export interface DocumentMeta {
  document_id: string
  filename: string
  pages: number
  type: string
  sample_id: string | null
}

export interface Usage {
  tier?: 'default' | 'strong'
  model?: string
  calls?: number
  cost_usd?: string
  tokens_in?: number
  tokens_out?: number
}

export interface TraceEvent {
  run_id: string
  seq: number
  ts: string
  node: string
  kind: EventKind
  tool: string | null
  model: string | null
  tokens_in: number | null
  tokens_out: number | null
  latency_ms: number | null
  cost_usd: number | null
  error: string | null
  args: unknown
  payload: unknown
}

export interface ValidationIssue {
  field_path: string
  severity: 'error' | 'review' | 'warning'
  rule: string
  message: string
}

export interface LineItem {
  sku: string | null
  description: string | null
  quantity: string | null
  unit_price: string | null
  line_total: string | null
}

export interface Party {
  name: string | null
  address: string | null
  tax_id?: string | null
}

/** Plain record (no confidences), as saved and as the reviewer edits it. */
export interface PlainRecord {
  invoice_number?: string | null
  invoice_date?: string | null
  due_date?: string | null
  po_number?: string | null
  po_date?: string | null
  currency?: string | null
  payment_terms?: string | null
  seller?: Party
  buyer?: Party
  vendor?: Party
  ship_to?: string | null
  requested_delivery_date?: string | null
  line_items: LineItem[]
  subtotal?: string | null
  tax_amount?: string | null
  total?: string | null
  [key: string]: unknown
}

export interface ReviewPayload {
  run_id: string
  doc_type: DocType
  record: PlainRecord
  issues: ValidationIssue[]
  notes: string[]
}

export interface MatchIssue {
  rule: string
  message: string
  invoice_line: number | null
  po_line: number | null
}

export interface LineMatch {
  invoice_line: number
  po_line: number
  method: string
  score: number
}

export interface MatchResult {
  status: MatchStatus
  issues: MatchIssue[]
  line_matches: LineMatch[]
  po_number?: string
  duplicates?: unknown[]
}

export interface IngestResult {
  doc_type: DocType | 'unknown'
  record?: PlainRecord
  match?: MatchResult | null
  summary?: string
  notes?: string[]
  issues?: ValidationIssue[]
  cost_usd?: string
  escalated?: boolean
  reviewed?: boolean
  record_id?: string
}

export interface RunError {
  code: string | null
  message: string | null
  replay_available: boolean
}

export interface Run {
  id: string
  kind: 'ingest' | 'ask'
  status: RunStatus
  replay: boolean
  document_id: string | null
  question: string | null
  started_at: string | null
  finished_at: string | null
  total_cost_usd: string
  escalated: boolean
  error: RunError | null
  result: IngestResult | null
  review: ReviewPayload | null
}

export interface EndEvent {
  status: RunStatus
  error_code: string | null
  result: IngestResult | null
}

export interface AskPlan {
  action: 'query' | 'followup' | 'refuse'
  reason: string
  sql: string | null
  convert_to: string | null
}

export interface AskResult {
  question: string
  answer: string
  plan: AskPlan
  sql: string | null
  columns: string[] | null
  rows: unknown[][] | null
  truncated: boolean | null
  sql_error: { code: string; message: string } | null
  conversion: Record<string, unknown> | null
  figure_amount: string | null
  figure_currency: string | null
  cost_usd: string
}

export interface EvalDocument {
  doc_id: string
  doc_type: DocType
  scanned: boolean
  status: string
  field_accuracy: number | null
  cost_usd: string
  latency_s: number
  escalated: boolean
  reviewed: boolean
}

export interface EvalMetrics {
  documents: number
  failed_runs: number
  field_accuracy: number | null
  classification_accuracy: number | null
  by_doc_type: Record<string, { documents: number; field_accuracy: number | null; f1: number }>
  clean_vs_scanned: {
    clean: { documents: number; field_accuracy: number | null }
    scanned: { documents: number; field_accuracy: number | null }
    paired: { pairs: number; clean_twins: number | null; scanned_variants: number | null }
  }
  match_status_accuracy: number | null
  mismatch_recall: number | null
  mismatch_recall_by_kind: Record<string, number | null>
  escalation_rate: number | null
  review_rate: number | null
  cost_per_doc_usd: { avg: number | null; max: number | null; total: number }
  latency_s: { p50: number | null; p95: number | null }
  per_field_accuracy: Record<string, number>
}

export interface EvalReport {
  suite: string
  finished_at: string
  models: Record<string, string>
  ci_run_url?: string | null
  metrics: EvalMetrics
  documents: EvalDocument[]
}
