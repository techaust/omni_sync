import { Navigate, Outlet, createBrowserRouter, useLocation, type RouteObject } from 'react-router'

import { useSession } from '@/auth/session'
import { AppShell } from '@/components/app-shell'
import { ProblemAlert } from '@/components/problem-alert'
import { Skeleton } from '@/components/ui/skeleton'
import { AccountPage } from '@/pages/account'
import { DashboardPage } from '@/pages/dashboard'
import { LoginPage } from '@/pages/login'
import { SetupPage } from '@/pages/setup'
import { UsersPage } from '@/pages/users'

function FullPageLoading() {
  return (
    <main id="main" className="grid min-h-svh place-items-center" aria-busy="true">
      <Skeleton className="h-8 w-48" />
      <span className="sr-only">Loading OmniSync…</span>
    </main>
  )
}

/** Sends visitors to setup, sign-in, or the requested page. */
export function RequireAuth() {
  const { user, isLoading, setupRequired } = useSession()
  const location = useLocation()
  if (isLoading) return <FullPageLoading />
  if (setupRequired) return <Navigate to="/setup" replace />
  if (setupRequired === undefined)
    return (
      <main id="main" className="mx-auto max-w-lg p-6">
        <ProblemAlert
          error={new Error('The OmniSync server did not answer. Check that `omnisync serve` is running, then reload.')}
        />
      </main>
    )
  if (!user) return <Navigate to={`/login?next=${encodeURIComponent(location.pathname)}`} replace />
  return <Outlet />
}

export function RequirePermission({ permission }: { permission: string }) {
  const { can } = useSession()
  if (!can(permission))
    return (
      <div className="grid gap-2">
        <h1 className="text-2xl font-semibold">Not available</h1>
        <p className="text-muted-foreground text-sm">Your role does not include access to this page.</p>
      </div>
    )
  return <Outlet />
}

function NotFoundPage() {
  return (
    <div className="grid gap-2">
      <h1 className="text-2xl font-semibold">Page not found</h1>
      <p className="text-muted-foreground text-sm">Use the navigation to go to an existing section.</p>
    </div>
  )
}

export const routes: RouteObject[] = [
  { path: '/setup', element: <SetupPage /> },
  { path: '/login', element: <LoginPage /> },
  {
    element: <RequireAuth />,
    children: [
      {
        element: <AppShell />,
        children: [
          { index: true, element: <DashboardPage /> },
          { path: 'account', element: <AccountPage /> },
          {
            element: <RequirePermission permission="users.manage" />,
            children: [{ path: 'settings/users', element: <UsersPage /> }],
          },
          { path: '*', element: <NotFoundPage /> },
        ],
      },
    ],
  },
]

export const createRouter = () => createBrowserRouter(routes)
