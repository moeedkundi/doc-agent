import Link from 'next/link'

import { ThemeToggle } from '@/components/theme-toggle'

const NAV = [
  { href: '/', label: 'Demo' },
  { href: '/ask', label: 'Ask', soon: true },
  { href: '/evals', label: 'Evals', soon: true },
  { href: '/architecture', label: 'Architecture', soon: true },
]

export function SiteHeader() {
  return (
    <header className="border-b">
      <div className="container flex h-14 items-center justify-between">
        <Link href="/" className="text-base font-bold">
          <span className="gradient-text">DocAgent</span>{' '}
          <span className="text-[13px] font-normal text-muted-foreground">by Moeed</span>
        </Link>
        <nav aria-label="Main" className="flex items-center gap-5 text-sm">
          {NAV.map((item) =>
            item.soon ? (
              <span
                key={item.href}
                className="hidden cursor-default text-muted-foreground/70 sm:inline"
                title="Coming in part B"
              >
                {item.label}
              </span>
            ) : (
              <Link key={item.href} href={item.href} className="font-medium">
                {item.label}
              </Link>
            ),
          )}
          <ThemeToggle />
        </nav>
      </div>
    </header>
  )
}
