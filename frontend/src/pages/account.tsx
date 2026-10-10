import { useMutation } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { toast } from 'sonner'

import { ApiError, api, type Preferences } from '@/api/client'
import { useSession } from '@/auth/session'
import { FormField } from '@/components/form-field'
import { ProblemAlert } from '@/components/problem-alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { applyColorMode, applyDensity } from '@/lib/theme'
import { MIN_PASSWORD } from '@/pages/setup'

const COLOR_MODES: { value: Preferences['color_mode']; label: string }[] = [
  { value: 'system', label: 'Same as Windows' },
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
]

function PreferencesCard() {
  const { user, signedIn } = useSession()
  const save = useMutation({
    mutationFn: api.updatePreferences,
    onSuccess: (me) => {
      signedIn(me)
      applyColorMode(me.preferences.color_mode)
      applyDensity(me.preferences.density)
      toast.success('Preferences saved')
    },
  })
  if (!user) return null
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Appearance</h2>
        </CardTitle>
        <CardDescription>Saved to your account and applied on every device you sign in from.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {save.error ? <ProblemAlert error={save.error} /> : null}
        <div className="grid gap-1.5">
          <Label htmlFor="color-mode">Colour mode</Label>
          <Select
            value={user.preferences.color_mode}
            onValueChange={(value) => save.mutate({ color_mode: value as Preferences['color_mode'] })}
          >
            <SelectTrigger id="color-mode" className="w-56">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {COLOR_MODES.map((m) => (
                <SelectItem key={m.value} value={m.value}>
                  {m.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="density">Density</Label>
          <Select
            value={user.preferences.density}
            onValueChange={(value) => save.mutate({ density: value as Preferences['density'] })}
          >
            <SelectTrigger id="density" className="w-56">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="compact">Compact</SelectItem>
              <SelectItem value="comfortable">Comfortable</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </CardContent>
    </Card>
  )
}

function PasswordCard() {
  const [form, setForm] = useState({ current: '', next: '', confirm: '' })
  const [localError, setLocalError] = useState<string | undefined>()
  const change = useMutation({
    mutationFn: api.changePassword,
    onSuccess: () => {
      setForm({ current: '', next: '', confirm: '' })
      toast.success('Password changed. Your other sessions were signed out.')
    },
  })
  const apiError = change.error instanceof ApiError ? change.error : undefined

  function submit(event: FormEvent) {
    event.preventDefault()
    if (form.next.length < MIN_PASSWORD) return setLocalError(`Use at least ${MIN_PASSWORD} characters.`)
    if (form.next !== form.confirm) return setLocalError('The new passwords do not match.')
    setLocalError(undefined)
    change.mutate({ current_password: form.current, new_password: form.next })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Password</h2>
        </CardTitle>
        <CardDescription>Changing it signs you out everywhere else.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="grid max-w-sm gap-4" noValidate>
          {change.error && !apiError?.errors.length ? <ProblemAlert error={change.error} /> : null}
          <FormField
            label="Current password"
            type="password"
            autoComplete="current-password"
            value={form.current}
            onChange={(e) => setForm((f) => ({ ...f, current: e.target.value }))}
            error={apiError?.fieldError('current_password')}
          />
          <FormField
            label="New password"
            type="password"
            autoComplete="new-password"
            hint={`At least ${MIN_PASSWORD} characters.`}
            value={form.next}
            onChange={(e) => setForm((f) => ({ ...f, next: e.target.value }))}
            error={apiError?.fieldError('password')}
          />
          <FormField
            label="Repeat new password"
            type="password"
            autoComplete="new-password"
            value={form.confirm}
            onChange={(e) => setForm((f) => ({ ...f, confirm: e.target.value }))}
            error={localError}
          />
          <Button type="submit" disabled={change.isPending} className="justify-self-start">
            Change password
          </Button>
        </form>
      </CardContent>
    </Card>
  )
}

export function AccountPage() {
  const { user } = useSession()
  return (
    <div className="grid max-w-3xl gap-6">
      <div>
        <h1 className="text-2xl font-semibold">Account</h1>
        <p className="text-muted-foreground text-sm">
          {user?.display_name} ({user?.username}) · <span className="capitalize">{user?.role}</span>
        </p>
      </div>
      <PreferencesCard />
      <PasswordCard />
    </div>
  )
}
