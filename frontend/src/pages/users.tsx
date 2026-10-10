import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { PlusIcon } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { toast } from 'sonner'

import { ApiError, api, type Role, type User } from '@/api/client'
import { useSession } from '@/auth/session'
import { FormField } from '@/components/form-field'
import { ProblemAlert } from '@/components/problem-alert'
import { StatusBadge } from '@/components/status-badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { MIN_PASSWORD } from '@/pages/setup'

const USERS_KEY = ['users'] as const
const ROLES: { value: Role; label: string; description: string }[] = [
  { value: 'admin', label: 'Admin', description: 'Everything, including settings, secrets, users and S3 cleanup.' },
  { value: 'operator', label: 'Operator', description: 'Upload, plan and run, resolve decisions, edit rules.' },
  { value: 'viewer', label: 'Viewer', description: 'Read-only.' },
]

function RoleSelect({ id, value, onChange }: { id: string; value: Role; onChange: (role: Role) => void }) {
  return (
    <Select value={value} onValueChange={(v) => onChange(v as Role)}>
      <SelectTrigger id={id} className="w-full">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {ROLES.map((r) => (
          <SelectItem key={r.value} value={r.value}>
            {r.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function CreateUserDialog() {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState({ username: '', display_name: '', role: 'operator' as Role, password: '' })
  const create = useMutation({
    mutationFn: api.createUser,
    onSuccess: (user) => {
      void queryClient.invalidateQueries({ queryKey: USERS_KEY })
      toast.success(`Created ${user.username}`)
      setOpen(false)
      setForm({ username: '', display_name: '', role: 'operator', password: '' })
    },
  })
  const apiError = create.error instanceof ApiError ? create.error : undefined
  const passwordTooShort = form.password.length > 0 && form.password.length < MIN_PASSWORD

  function submit(event: FormEvent) {
    event.preventDefault()
    if (passwordTooShort) return
    create.mutate(form)
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <PlusIcon aria-hidden="true" /> Add user
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={submit} className="grid gap-4" noValidate>
          <DialogHeader>
            <DialogTitle>Add user</DialogTitle>
            <DialogDescription>They can change their password after signing in.</DialogDescription>
          </DialogHeader>
          {create.error && !apiError?.errors.length ? <ProblemAlert error={create.error} /> : null}
          <FormField
            label="Username"
            value={form.username}
            onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))}
            autoComplete="off"
            error={apiError?.fieldError('username')}
          />
          <FormField
            label="Display name"
            value={form.display_name}
            onChange={(e) => setForm((f) => ({ ...f, display_name: e.target.value }))}
          />
          <div className="grid gap-1.5">
            <Label htmlFor="new-user-role">Role</Label>
            <RoleSelect id="new-user-role" value={form.role} onChange={(role) => setForm((f) => ({ ...f, role }))} />
            <p className="text-muted-foreground text-xs">{ROLES.find((r) => r.value === form.role)?.description}</p>
          </div>
          <FormField
            label="Initial password"
            type="password"
            autoComplete="new-password"
            value={form.password}
            onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))}
            hint={`At least ${MIN_PASSWORD} characters.`}
            error={passwordTooShort ? `Use at least ${MIN_PASSWORD} characters.` : apiError?.fieldError('password')}
          />
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={create.isPending}>
              Create user
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function EditUserDialog({ user }: { user: User }) {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [role, setRole] = useState<Role>(user.role)
  const [newPassword, setNewPassword] = useState('')
  const update = useMutation({
    mutationFn: (body: Parameters<typeof api.updateUser>[1]) => api.updateUser(user.id, body),
    onSuccess: (updated) => {
      void queryClient.invalidateQueries({ queryKey: USERS_KEY })
      toast.success(`Updated ${updated.username}`)
      setOpen(false)
      setNewPassword('')
    },
  })
  const passwordTooShort = newPassword.length > 0 && newPassword.length < MIN_PASSWORD

  function submit(event: FormEvent) {
    event.preventDefault()
    if (passwordTooShort) return
    update.mutate({ role: role === user.role ? undefined : role, new_password: newPassword || undefined })
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (next) {
          setRole(user.role)
          update.reset()
        }
      }}
    >
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" aria-label={`Edit ${user.username}`}>
          Edit
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={submit} className="grid gap-4" noValidate>
          <DialogHeader>
            <DialogTitle>Edit {user.username}</DialogTitle>
            <DialogDescription>Resetting the password signs the user out everywhere.</DialogDescription>
          </DialogHeader>
          {update.error ? <ProblemAlert error={update.error} /> : null}
          <div className="grid gap-1.5">
            <Label htmlFor={`role-${user.id}`}>Role</Label>
            <RoleSelect id={`role-${user.id}`} value={role} onChange={setRole} />
          </div>
          <FormField
            label="New password (optional)"
            type="password"
            autoComplete="new-password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            error={passwordTooShort ? `Use at least ${MIN_PASSWORD} characters.` : undefined}
          />
          <DialogFooter className="sm:justify-between">
            <Button
              type="button"
              variant="destructive"
              onClick={() => update.mutate({ disabled: !user.disabled })}
              disabled={update.isPending}
            >
              {user.disabled ? 'Enable account' : 'Disable account'}
            </Button>
            <Button type="submit" disabled={update.isPending}>
              Save
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export function UsersPage() {
  const { user: me } = useSession()
  const users = useQuery({ queryKey: USERS_KEY, queryFn: api.listUsers })

  return (
    <div className="grid gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Users</h1>
          <p className="text-muted-foreground text-sm">People who can sign in to this instance.</p>
        </div>
        <CreateUserDialog />
      </div>
      {users.isPending ? (
        <div className="grid gap-2" aria-busy="true" aria-label="Loading users">
          <Skeleton className="h-9" />
          <Skeleton className="h-9" />
        </div>
      ) : users.isError ? (
        <ProblemAlert error={users.error} />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">Username</TableHead>
              <TableHead scope="col">Name</TableHead>
              <TableHead scope="col">Role</TableHead>
              <TableHead scope="col">Status</TableHead>
              <TableHead scope="col">
                <span className="sr-only">Actions</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {users.data.items.map((u) => (
              <TableRow key={u.id}>
                <TableCell className="font-mono">
                  {u.username}
                  {u.id === me?.id ? <span className="text-muted-foreground font-sans"> (you)</span> : null}
                </TableCell>
                <TableCell>{u.display_name}</TableCell>
                <TableCell className="capitalize">{u.role}</TableCell>
                <TableCell>
                  {u.disabled ? (
                    <StatusBadge status="danger">Disabled</StatusBadge>
                  ) : u.locked ? (
                    <StatusBadge status="warning">Locked</StatusBadge>
                  ) : (
                    <StatusBadge status="success">Active</StatusBadge>
                  )}
                </TableCell>
                <TableCell className="text-right">
                  <EditUserDialog user={u} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  )
}
