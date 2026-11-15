import Script from 'next/script'

import { GA_ID } from '@/lib/analytics'

/** GA4 page views (and history changes, via GA's enhanced measurement). Same property as
 * meetmoeed.com, so a visitor who comes from the portfolio counts as one user. */
export function Analytics() {
  if (!GA_ID) return null
  return (
    <>
      <Script src={`https://www.googletagmanager.com/gtag/js?id=${GA_ID}`} strategy="afterInteractive" />
      <Script id="ga-init" strategy="afterInteractive">
        {`window.dataLayer = window.dataLayer || [];
function gtag(){dataLayer.push(arguments);}
gtag('js', new Date());
gtag('config', '${GA_ID}');`}
      </Script>
    </>
  )
}
