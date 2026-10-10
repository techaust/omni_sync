import { useMutation } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Navigate, useNavigate } from 'react-router'

import { ApiError, api } from '@/api/client'
import { useSession } from '@/auth/session'
import { FormField } from '@/components/form-field'
import { ProblemAlert } from '@/components/problem-alert'
import { Button } from '@/components/ui/button'
import { AuthLayout } from '@/pages/auth-layout'

export const MIN_PASSWORD = 12

/** First-run setup (FR-AUTH-01): needs the token printed by `omnisync serve`. */
export function SetupPage() {
  const { setupRequired, signedIn } = useSession()
  const navigate = useNavigate()
  const [form, setForm] = useState({ setup_token: '', username: '', display_name: '', password: '', confirm: '' })
  const [localError, setLocalError] = useState<string | undefined>()
  const setup = useMutation({
    mutationFn: api.setup,
    onSuccess: (me) => {
      signedIn(me)
      void navigate('/', { replace: true })
    },
  })

  if (setupRequired === false) return <Navigate to="/login" replace />

  const apiError = setup.error instanceof ApiError ? setup.error : undefined
  const update = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }))

  function submit(event: FormEvent) {
    event.preventDefault()
    if (form.password.length < MIN_PASSWORD) return setLocalError(`Use at least ${MIN_PASSWORD} characters.`)
    if (form.password !== form.confirm) return setLocalError('The passwords do not match.')
    setLocalError(undefined)
    const { confirm: _confirm, ...body } = form
    setup.mutate(body)
  }

  return (
    <AuthLayout
      title="Set up OmniSync"
      description="Create the first administrator account. The setup token is shown in the window where the server started, and saved in setup-token.txt in the data folder."
    >
      <form onSubmit={submit} className="grid gap-4" noValidate>
        {setup.error && !apiError?.errors.length ? <ProblemAlert error={setup.error} /> : null}
        <FormField
          label="Setup token"
          value={form.setup_token}
          onChange={update('setup_token')}
          autoComplete="off"
          spellCheck={false}
          className="font-mono"
          required
          error={
            apiError?.fieldError('setup_token')
              ? 'This token is not valid. Copy it again from the server window or file.'
              : undefined
          }
        />
        <FormField
          label="Username"
          value={form.username}
          onChange={update('username')}
          autoComplete="username"
          hint="3–32 characters: lowercase letters, digits, '.', '_' or '-'."
          required
          error={apiError?.fieldError('username')}
        />
        <FormField
          label="Display name"
          value={form.display_name}
          onChange={update('display_name')}
          autoComplete="name"
          required
        />
        <FormField
          label="Password"
          type="password"
          value={form.password}
          onChange={update('password')}
          autoComplete="new-password"
          hint={`At least ${MIN_PASSWORD} characters. A short sentence works well.`}
          required
          error={apiError?.fieldError('password')}
        />
        <FormField
          label="Repeat password"
          type="password"
          value={form.confirm}
          onChange={update('confirm')}
          autoComplete="new-password"
          required
          error={localError}
        />
        <Button type="submit" disabled={setup.isPending}>
          {setup.isPending ? 'Creating account…' : 'Create administrator'}
        </Button>
      </form>
    </AuthLayout>
  )
}
