import { cn } from '@/lib/utils'

const TOKEN = /("(?:[^"\\]|\\.)*")(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g
const MAX_CHARS = 6000

/** Pretty-printed JSON with light syntax colouring (keys, strings, numbers, literals). */
export function JsonBlock({ value, className }: { value: unknown; className?: string }) {
  let text = JSON.stringify(value, null, 2) ?? 'null'
  const cut = text.length > MAX_CHARS
  if (cut) text = text.slice(0, MAX_CHARS)
  const parts: React.ReactNode[] = []
  let last = 0
  for (const m of text.matchAll(TOKEN)) {
    const i = m.index ?? 0
    if (i > last) parts.push(text.slice(last, i))
    const [all, str, colon, literal, num] = m
    if (str && colon) {
      parts.push(
        <span key={i} className="text-primary dark:text-violet-300">
          {str}
        </span>,
        colon,
      )
    } else if (str) {
      parts.push(
        <span key={i} className="text-success-foreground">
          {str}
        </span>,
      )
    } else if (literal || num) {
      parts.push(
        <span key={i} className="text-warning-foreground">
          {all}
        </span>,
      )
    }
    last = i + all.length
  }
  parts.push(text.slice(last))
  return (
    <pre
      className={cn(
        'max-h-64 overflow-auto rounded-md bg-secondary px-2.5 py-2 font-mono text-xs leading-relaxed',
        className,
      )}
      tabIndex={0}
    >
      {parts}
      {cut && <span className="text-muted-foreground">{'\n'}… truncated</span>}
    </pre>
  )
}
