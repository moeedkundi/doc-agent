import { cn } from '@/lib/utils'

const KEYWORDS = new Set(
  (
    'select from where join left right inner outer full cross on and or not in is null as group by ' +
    'order having limit offset distinct case when then else end with union all exists between like ' +
    'ilike asc desc coalesce sum count avg min max round cast interval true false over partition ' +
    'insert update delete drop alter create truncate grant into values set returning filter lateral'
  ).split(' '),
)
const TOKEN = /(--[^\n]*)|('(?:[^']|'')*')|\b(\d+(?:\.\d+)?)\b|\b([A-Za-z_][A-Za-z0-9_]*)\b/g

/** Lightly formatted, syntax-coloured SQL (keywords, strings, numbers, comments). */
export function SqlBlock({ sql, className }: { sql: string; className?: string }) {
  const text = format(sql)
  const parts: React.ReactNode[] = []
  let last = 0
  for (const m of text.matchAll(TOKEN)) {
    const i = m.index ?? 0
    if (i > last) parts.push(text.slice(last, i))
    const [all, comment, str, num, word] = m
    if (comment) parts.push(<span key={i} className="text-muted-foreground italic">{comment}</span>)
    else if (str) parts.push(<span key={i} className="text-success-foreground">{str}</span>)
    else if (num) parts.push(<span key={i} className="text-warning-foreground">{num}</span>)
    else if (word && KEYWORDS.has(word.toLowerCase()))
      parts.push(
        <span key={i} className="font-semibold text-primary dark:text-violet-300">
          {word.toUpperCase()}
        </span>,
      )
    else parts.push(all)
    last = i + all.length
  }
  parts.push(text.slice(last))
  return (
    <pre
      tabIndex={0}
      className={cn(
        'overflow-x-auto rounded-md bg-secondary px-3 py-2.5 font-mono text-xs leading-relaxed whitespace-pre-wrap',
        className,
      )}
    >
      {parts}
    </pre>
  )
}

/** Break the main clauses onto their own lines (strings are left alone). */
function format(sql: string): string {
  if (sql.includes('\n')) return sql.trim()
  const clauses = /\s+(?=(?:FROM|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|(?:LEFT |RIGHT |INNER |FULL )?JOIN|UNION)\b)/gi
  return sql
    .split(/('(?:[^']|'')*')/)
    .map((chunk, i) => (i % 2 ? chunk : chunk.replace(clauses, '\n')))
    .join('')
    .trim()
}
