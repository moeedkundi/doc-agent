import type { Metadata } from 'next'

import { AskChatClient } from '@/components/ask/ask-chat-client'

export const metadata: Metadata = { title: 'Ask · DocAgent' }

export default function AskPage() {
  return (
    <div className="container max-w-4xl pb-10 pt-8">
      <h1 className="text-2xl font-bold tracking-tight">Ask your data</h1>
      <p className="mt-1.5 text-sm text-muted-foreground">
        Questions about the purchase orders and invoices processed in this browser session. The
        agent writes one SQL <code className="font-mono text-xs">SELECT</code>; it runs through
        sqlstore&apos;s read-only pipeline (reader role, row-level security, 3 s timeout) and you
        see the query, the rows and a one-line answer.
      </p>
      <AskChatClient />
    </div>
  )
}
