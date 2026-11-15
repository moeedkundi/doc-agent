// Google Analytics (GA4). Loaded only when NEXT_PUBLIC_GA_ID is set (production on Vercel),
// so local dev, CI and preview builds send nothing. Events carry no personal data and no
// document or question content: just what kind of thing happened.

export const GA_ID = process.env.NEXT_PUBLIC_GA_ID ?? ''

type Params = Record<string, string | number | boolean | undefined>

declare global {
  interface Window {
    gtag?: (...args: unknown[]) => void
  }
}

export function track(event: string, params: Params = {}): void {
  if (!GA_ID || typeof window === 'undefined' || !window.gtag) return
  window.gtag('event', event, params)
}
