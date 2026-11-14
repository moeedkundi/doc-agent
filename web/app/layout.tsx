import type { Metadata } from 'next'
import { Inter, JetBrains_Mono } from 'next/font/google'

import { SiteHeader } from '@/components/site-header'
import { themeScript } from '@/components/theme-toggle'

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
      </head>
      <body className="min-h-screen font-sans antialiased">
        <div className="flex min-h-screen flex-col">
          <SiteHeader />
          <main className="flex-1">{children}</main>
          <footer className="container py-6 text-xs text-muted-foreground">
            Synthetic data only · LLM spend is capped daily ·{' '}
            <a className="font-mono underline-offset-2 hover:underline" href="https://meetmoeed.com">
              meetmoeed.com
            </a>
          </footer>
        </div>
      </body>
    </html>
  )
}
