import { useQuery } from '@tanstack/react-query'

import { api } from '@/api/client'
import { useSession } from '@/auth/session'
import { ProblemAlert } from '@/components/problem-alert'
import { StatusBadge } from '@/components/status-badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'

const CHECK_LABELS: Record<string, string> = {
  database: 'Database',
  schema: 'Database schema',
  data_directory: 'Data folder',
}

export function DashboardPage() {
  const { user } = useSession()
  const health = useQuery({ queryKey: ['health', 'details'], queryFn: api.healthDetails })

  return (
    <div className="grid gap-6">
      <div>
        <h1 className="text-2xl font-semibold">Dashboard</h1>
        <p className="text-muted-foreground text-sm">Signed in as {user?.display_name}.</p>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>
              <h2>Health</h2>
            </CardTitle>
            <CardDescription>Checks of this OmniSync instance.</CardDescription>
          </CardHeader>
          <CardContent>
            {health.isPending ? (
              <div className="grid gap-2" aria-busy="true" aria-label="Loading health checks">
                <Skeleton className="h-6" />
                <Skeleton className="h-6" />
                <Skeleton className="h-6" />
              </div>
            ) : health.isError ? (
              <ProblemAlert error={health.error} />
            ) : (
              <ul className="grid gap-3">
                {health.data.checks.map((check) => (
                  <li key={check.name} className="flex flex-wrap items-center justify-between gap-2">
                    <div className="min-w-0">
                      <p className="text-sm font-medium">{CHECK_LABELS[check.name] ?? check.name}</p>
                      <p className="text-muted-foreground truncate font-mono text-xs" title={check.detail}>
                        {check.detail}
                      </p>
                    </div>
                    <StatusBadge status={check.ok ? 'success' : 'danger'}>{check.ok ? 'OK' : 'Problem'}</StatusBadge>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>
              <h2>This instance</h2>
            </CardTitle>
            <CardDescription>Identity used for coordination and the audit log.</CardDescription>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
              <dt className="text-muted-foreground">Name</dt>
              <dd>{user?.instance.name}</dd>
              <dt className="text-muted-foreground">Instance ID</dt>
              <dd className="font-mono text-xs break-all">{user?.instance.id}</dd>
              <dt className="text-muted-foreground">Version</dt>
              <dd className="font-mono text-xs">{health.data?.version ?? '…'}</dd>
              <dt className="text-muted-foreground">Your role</dt>
              <dd className="capitalize">{user?.role}</dd>
            </dl>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
