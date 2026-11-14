'use client'

import { Moon, Sun } from 'lucide-react'

import { Button } from '@/components/ui/button'

/** Same behaviour as meetmoeed.com: follows the system until the visitor picks a theme. */
export function ThemeToggle() {
  const toggle = () => {
    const dark = !document.documentElement.classList.contains('dark')
    document.documentElement.classList.toggle('dark', dark)
    try {
      localStorage.setItem('theme', dark ? 'dark' : 'light')
    } catch {
      // storage blocked: the choice lasts for this page only
    }
  }
  return (
    <Button variant="ghost" size="icon" onClick={toggle} className="relative" aria-label="Toggle theme">
      <Sun className="size-5 rotate-0 scale-100 transition-all dark:-rotate-90 dark:scale-0" />
      <Moon className="absolute size-5 rotate-90 scale-0 transition-all dark:rotate-0 dark:scale-100" />
    </Button>
  )
}

/** Runs before paint so the page never flashes the wrong theme. */
export const themeScript = `(function(){try{var t=localStorage.getItem('theme');var d=t?t==='dark':matchMedia('(prefers-color-scheme: dark)').matches;document.documentElement.classList.toggle('dark',d)}catch(e){}})()`
