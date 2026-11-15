import type { Metadata } from 'next'
import { Inter, JetBrains_Mono } from 'next/font/google'

import { Analytics } from '@/components/analytics'
import { SiteHeader } from '@/components/site-header'
import { themeScript } from '@/components/theme-toggle'
import { API_URL } from '@/lib/api'

import './globals.css'

const inter = Inter({ subsets: ['latin'], variable: '--font-inter' })
const mono = JetBrains_Mono({ subsets: ['latin'], variable: '--font-jetbrains-mono' })

export const metadata: Metadata = {
  title: 'DocAgent — invoices and POs to checked, queryable data',
  description:
    'An agentic document-to-data demo: MCP tools, LangGraph, human review, live traces and cost control. Synthetic documents only.',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={`${inter.variable} ${mono.variable}`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
        <link rel="preconnect" href={API_URL} crossOrigin="use-credentials" />
      </head>
      <body className="min-h-screen font-sans antialiased">
        <Analytics />
        <div className="flex min-h-screen flex-col">
          <SiteHeader />
          <main className="flex-1">{children}</main>
          <footer className="container flex flex-wrap items-center justify-between gap-3 py-6 text-xs text-muted-foreground">
            <span>Synthetic data only · LLM spend is capped daily</span>
            <a
              className="inline-flex items-center gap-1 rounded-md border px-2.5 py-1 font-medium text-foreground hover:bg-accent"
              href="https://meetmoeed.com"
            >
              ← Back to meetmoeed.com
            </a>
          </footer>
        </div>
      </body>
    </html>
  )
}
