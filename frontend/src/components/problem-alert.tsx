import { CircleAlertIcon } from 'lucide-react'

import { ApiError } from '@/api/client'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'

/** Shows an API error with its title, detail, code and request ID (08-UX-DESIGN section 4). */
export function ProblemAlert({ error, className }: { error: unknown; className?: string }) {
  if (!error) return null
  const problem =
    error instanceof ApiError
      ? error
      : new ApiError({ status: 0, code: 'internal', title: 'Something went wrong', detail: String(error) })
  return (
    <Alert variant="destructive" className={className}>
      <CircleAlertIcon aria-hidden="true" />
      <AlertTitle>{problem.title}</AlertTitle>
      <AlertDescription>
        {problem.detail && problem.detail !== problem.title ? <p>{problem.detail}</p> : null}
        <p className="text-muted-foreground font-mono text-xs">
          {problem.code}
          {problem.requestId ? ` · request ${problem.requestId}` : ''}
        </p>
      </AlertDescription>
    </Alert>
  )
}
