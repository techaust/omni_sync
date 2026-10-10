import { CircleCheckIcon, CircleXIcon, InfoIcon, LoaderIcon, TriangleAlertIcon, type LucideIcon } from 'lucide-react'
import { cn } from 'cn'

export type Status = 'success' | 'warning' | 'danger' | 'info' | 'running'

const STYLES: Record<Status, { icon: LucideIcon; className: string }> = {
  success: { icon: CircleCheckIcon, className: 'text-success' },
  warning: { icon: TriangleAlertIcon, className: 'text-warning' },
  danger: { icon: CircleXIcon, className: 'text-danger' },
  info: { icon: InfoIcon, className: 'text-info' },
  running: { icon: LoaderIcon, className: 'text-info [&_svg]:animate-spin' },
}

/**
 * Status = icon + text label + colour token on the `muted` surface. Colour is never the only
 * signal (08-UX-DESIGN section 5.2); every pair is in the measured contrast table.
 */
export function StatusBadge({ status, children, className }: { status: Status; children: string; className?: string }) {
  const { icon: Icon, className: tone } = STYLES[status]
  return (
    <span
      className={cn(
        'bg-muted inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs font-medium',
        tone,
        className,
      )}
    >
      <Icon className="size-3.5" aria-hidden="true" />
      {children}
    </span>
  )
}
