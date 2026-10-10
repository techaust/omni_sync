import { useMutation } from '@tanstack/react-query'
import { LayoutDashboardIcon, LogOutIcon, MenuIcon, UserIcon, UsersIcon, type LucideIcon } from 'lucide-react'
import { useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router'
import { toast } from 'sonner'
import { cn } from 'cn'

import { api } from '@/api/client'
import { useSession } from '@/auth/session'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheet'

interface NavItem {
  to: string
  label: string
  icon: LucideIcon
  permission?: string
}

/** Only sections that exist are listed; later milestones add Inbox, Runs, Catalog, etc. */
export const NAV_ITEMS: NavItem[] = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboardIcon },
  { to: '/settings/users', label: 'Users', icon: UsersIcon, permission: 'users.manage' },
]

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const { can } = useSession()
  return (
    <ul className="flex flex-col gap-1">
      {NAV_ITEMS.filter((item) => !item.permission || can(item.permission)).map(({ to, label, icon: Icon }) => (
        <li key={to}>
          <NavLink
            to={to}
            end={to === '/'}
            onClick={onNavigate}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium',
                isActive
                  ? 'bg-accent text-accent-foreground'
                  : 'text-muted-foreground hover:bg-accent hover:text-foreground',
              )
            }
          >
            <Icon className="size-4" aria-hidden="true" />
            {label}
          </NavLink>
        </li>
      ))}
    </ul>
  )
}

export function AppShell() {
  const { user, signedOut } = useSession()
  const navigate = useNavigate()
  const [menuOpen, setMenuOpen] = useState(false)
  const logout = useMutation({
    mutationFn: api.logout,
    onSettled: () => {
      signedOut()
      void navigate('/login', { replace: true })
    },
    onError: () => toast.error('Signed out locally; the server could not be reached.'),
  })

  return (
    <div className="min-h-svh">
      <a
        href="#main"
        className="bg-primary text-primary-foreground sr-only z-50 rounded-md px-3 py-2 focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
      >
        Skip to content
      </a>
      <header className="bg-background/95 sticky top-0 z-40 flex h-14 items-center gap-3 border-b px-4">
        <Sheet open={menuOpen} onOpenChange={setMenuOpen}>
          <SheetTrigger asChild>
            <Button variant="ghost" size="icon" className="md:hidden" aria-label="Open navigation">
              <MenuIcon aria-hidden="true" />
            </Button>
          </SheetTrigger>
          <SheetContent side="left" className="w-64">
            <SheetHeader>
              <SheetTitle>OmniSync</SheetTitle>
              <SheetDescription>{user?.instance.name}</SheetDescription>
            </SheetHeader>
            <nav aria-label="Main" className="px-4">
              <NavLinks onNavigate={() => setMenuOpen(false)} />
            </nav>
          </SheetContent>
        </Sheet>
        <span className="font-semibold">OmniSync</span>
        <span className="text-muted-foreground hidden text-sm sm:inline" title="Instance">
          {user?.instance.name}
        </span>
        <div className="ml-auto">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" aria-label={`Account menu for ${user?.display_name ?? ''}`}>
                <UserIcon aria-hidden="true" />
                <span className="hidden sm:inline">{user?.display_name}</span>
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuLabel>
                {user?.username} · {user?.role}
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={() => void navigate('/account')}>
                <UserIcon aria-hidden="true" /> Account
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => logout.mutate()} disabled={logout.isPending}>
                <LogOutIcon aria-hidden="true" /> Sign out
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </header>
      <div className="flex">
        <nav aria-label="Main" className="hidden w-56 shrink-0 border-r p-3 md:block">
          <NavLinks />
        </nav>
        <main id="main" tabIndex={-1} className="min-w-0 flex-1 p-4 outline-none md:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
