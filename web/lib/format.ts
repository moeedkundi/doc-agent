export function usd(value: number | string | null | undefined, digits = 6): string {
  const n = typeof value === 'string' ? Number(value) : (value ?? 0)
  if (!Number.isFinite(n) || n === 0) return '$0'
  return `$${n.toFixed(digits)}`
}

export function int(value: number | null | undefined): string {
  return (value ?? 0).toLocaleString('en-US')
}

export function ms(value: number | null | undefined): string {
  if (value == null) return '—'
  return value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${value} ms`
}

export function amount(value: string | number | null | undefined): string {
  if (value == null || value === '') return '—'
  const n = Number(value)
  if (!Number.isFinite(n)) return String(value)
  return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

/** "gemini-3.5-flash-lite" -> "flash-lite": short label for the timeline row. */
export function shortModel(model: string | null | undefined): string | null {
  if (!model) return null
  return model.replace(/^gemini-[\d.]+-/, '').replace(/^gemini-/, '')
}

export const DOC_TYPE_LABEL = { invoice: 'Invoice', purchase_order: 'Purchase order' } as const

export function docTypeLabel(t: string | null | undefined): string {
  return t && t in DOC_TYPE_LABEL ? DOC_TYPE_LABEL[t as keyof typeof DOC_TYPE_LABEL] : 'Document'
}
