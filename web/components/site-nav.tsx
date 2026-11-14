'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'

import { cn } from '@/lib/utils'

const NAV = [
  { href: '/', label: 'Demo', match: (p: string) => p === '/' || p.startsWith('/runs') },
  { href: '/ask', label: 'Ask', match: (p: string) => p.startsWith('/ask') },
  { href: '/evals', label: 'Evals', match: (p: string) => p.startsWith('/evals') },
  { href: '/architecture', label: 'Architecture', match: (p: string) => p.startsWith('/architecture') },
]

export function SiteNav() {
  const path = usePathname()
  return (
    <>
      {NAV.map((item) => {
        const active = item.match(path)
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? 'page' : undefined}
            className={cn(
              'rounded-sm transition-colors hover:text-foreground',
              active ? 'font-medium text-foreground' : 'text-muted-foreground',
            )}
          >
            {item.label}
          </Link>
        )
      })}
    </>
  )
}
