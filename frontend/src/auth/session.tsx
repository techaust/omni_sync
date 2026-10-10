import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useEffect, useMemo, type ReactNode } from 'react'

import { ApiError, api, setCsrfToken, type Me } from '@/api/client'
import { applyColorMode, applyDensity } from '@/lib/theme'

export const ME_KEY = ['auth', 'me'] as const
export const SETUP_KEY = ['setup', 'status'] as const

interface SessionValue {
  user: Me | null
  isLoading: boolean
  setupRequired: boolean | undefined
  can: (permission: string) => boolean
  signedIn: (me: Me) => void
  signedOut: () => void
}

const SessionContext = createContext<SessionValue | null>(null)

async function fetchMe(): Promise<Me | null> {
  try {
    return await api.me()
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null
    throw error
  }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const setup = useQuery({ queryKey: SETUP_KEY, queryFn: api.setupStatus, staleTime: Infinity })
  const me = useQuery({
    queryKey: ME_KEY,
    queryFn: fetchMe,
    enabled: setup.data?.setup_required === false,
    staleTime: 60_000,
  })
  const user = me.data ?? null

  useEffect(() => {
    setCsrfToken(user?.csrf_token ?? null)
    if (user) {
      applyColorMode(user.preferences.color_mode)
      applyDensity(user.preferences.density)
    }
  }, [user])

  const signedIn = useCallback(
    (next: Me) => {
      setCsrfToken(next.csrf_token)
      queryClient.setQueryData(ME_KEY, next)
      queryClient.setQueryData(SETUP_KEY, { setup_required: false })
    },
    [queryClient],
  )

  const signedOut = useCallback(() => {
    setCsrfToken(null)
    queryClient.setQueryData(ME_KEY, null)
    queryClient.removeQueries({ predicate: (q) => q.queryKey[0] !== 'auth' && q.queryKey[0] !== 'setup' })
  }, [queryClient])

  const value = useMemo<SessionValue>(
    () => ({
      user,
      isLoading: setup.isPending || (setup.data?.setup_required === false && me.isPending),
      setupRequired: setup.data?.setup_required,
      can: (permission) => user?.permissions.includes(permission) ?? false,
      signedIn,
      signedOut,
    }),
    [user, setup.isPending, setup.data, me.isPending, signedIn, signedOut],
  )

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext)
  if (!value) throw new Error('useSession must be used inside <SessionProvider>')
  return value
}
