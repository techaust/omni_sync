import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

import { ApiError } from '@/api/client'
import { SessionProvider, useSession } from '@/auth/session'
import { Toaster } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useResolvedTheme } from '@/lib/theme'

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Do not retry client errors (401/403/404/422); retry transient failures once.
        retry: (count, error) => !(error instanceof ApiError && error.status >= 400 && error.status < 500) && count < 1,
        refetchOnWindowFocus: false,
      },
      mutations: { retry: false },
    },
  })
}

function ThemedToaster() {
  const { user } = useSession()
  const theme = useResolvedTheme(user?.preferences.color_mode ?? 'system')
  return <Toaster theme={theme} position="bottom-right" closeButton />
}

export function AppProviders({ client, children }: { client: QueryClient; children: ReactNode }) {
  return (
    <QueryClientProvider client={client}>
      <SessionProvider>
        <TooltipProvider>
          {children}
          <ThemedToaster />
        </TooltipProvider>
      </SessionProvider>
    </QueryClientProvider>
  )
}
