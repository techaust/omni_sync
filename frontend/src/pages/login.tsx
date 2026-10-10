import { useMutation } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Navigate, useNavigate, useSearchParams } from 'react-router'

import { ApiError, api } from '@/api/client'
import { useSession } from '@/auth/session'
import { FormField } from '@/components/form-field'
import { ProblemAlert } from '@/components/problem-alert'
import { Button } from '@/components/ui/button'
import { AuthLayout } from '@/pages/auth-layout'

/** Only same-app paths are accepted as a redirect target (no open redirects). */
export function safeNext(next: string | null): string {
  return next && next.startsWith('/') && !next.startsWith('//') ? next : '/'
}

export function LoginPage() {
  const { user, setupRequired, signedIn } = useSession()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const login = useMutation({
    mutationFn: api.login,
    onSuccess: (me) => {
      signedIn(me)
      void navigate(safeNext(params.get('next')), { replace: true })
    },
  })

  if (setupRequired) return <Navigate to="/setup" replace />
  if (user) return <Navigate to="/" replace />

  function submit(event: FormEvent) {
    event.preventDefault()
    login.mutate({ username, password })
  }

  const error = login.error
  const message =
    error instanceof ApiError && error.code === 'auth.invalid_credentials'
      ? 'Wrong username or password. After 10 failed attempts the account is locked for 15 minutes.'
      : undefined

  return (
    <AuthLayout title="Sign in" description="Sign in to this OmniSync instance.">
      <form onSubmit={submit} className="grid gap-4">
        {error ? (
          message ? (
            <p role="alert" className="text-danger text-sm font-medium">
              {message}
            </p>
          ) : (
            <ProblemAlert error={error} />
          )
        ) : null}
        <FormField
          label="Username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
          required
        />
        <FormField
          label="Password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          required
        />
        <Button type="submit" disabled={login.isPending}>
          {login.isPending ? 'Signing in…' : 'Sign in'}
        </Button>
      </form>
    </AuthLayout>
  )
}
