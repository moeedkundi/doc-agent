import Link from 'next/link'

import { SiteNav } from '@/components/site-nav'
import { ThemeToggle } from '@/components/theme-toggle'

export function SiteHeader() {
  return (
    <header className="border-b">
      <div className="container flex h-14 items-center justify-between gap-3">
        <Link href="/" className="shrink-0 text-base font-bold">
          <span className="gradient-text">DocAgent</span>{' '}
          <span className="text-[13px] font-normal text-muted-foreground max-sm:hidden">by Moeed</span>
        </Link>
        <nav aria-label="Main" className="flex items-center gap-3.5 text-sm sm:gap-5">
          <SiteNav />
          <ThemeToggle />
        </nav>
      </div>
    </header>
  )
}
