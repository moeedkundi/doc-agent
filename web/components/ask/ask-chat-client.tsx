'use client'

import dynamic from 'next/dynamic'

/** Rendered only in the browser: the chat restores itself from sessionStorage. */
export const AskChatClient = dynamic(() => import('./ask-chat').then((m) => m.AskChat), {
  ssr: false,
  loading: () => <div className="mt-6 h-24 animate-pulse rounded-lg bg-secondary/60" />,
})
