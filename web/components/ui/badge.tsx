import { cva, type VariantProps } from 'class-variance-authority'
import * as React from 'react'

import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center gap-1 rounded-full border px-2 py-px text-[11px] font-semibold leading-5 whitespace-nowrap [&_svg]:size-3',
  {
    variants: {
      variant: {
        outline: 'border-border text-foreground',
        po: 'border-border bg-secondary text-secondary-foreground',
        invoice: 'border-primary/30 bg-primary/10 text-primary dark:text-violet-300',
        warning: 'border-warning/40 bg-warning-soft text-warning-foreground',
        success: 'border-success/40 bg-success-soft text-success-foreground',
        danger: 'border-destructive/50 bg-danger-soft text-danger-foreground',
        strong: 'border-transparent bg-warning text-amber-950',
        primary: 'border-primary/50 text-primary dark:text-violet-300',
      },
    },
    defaultVariants: { variant: 'outline' },
  },
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />
}
