import { ArrowUpRight } from 'lucide-react'
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
          <a
            href="https://meetmoeed.com"
            className="hidden items-center gap-0.5 border-l pl-4 text-muted-foreground hover:text-foreground md:inline-flex"
          >
            meetmoeed.com <ArrowUpRight className="size-3.5" aria-hidden />
          </a>
          <ThemeToggle />
        </nav>
      </div>
    </header>
  )
}
