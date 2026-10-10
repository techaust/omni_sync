/**
 * Typed client for the OmniSync API (docs/05-API-SPEC.md).
 * Types come from src/api/schema.d.ts, generated from the backend's OpenAPI document
 * (`npm run api:generate`); never edit that file by hand.
 */
import type { components } from './schema'

export type Schemas = components['schemas']
export type Me = Schemas['MeOut']
export type User = Schemas['UserOut']
export type Preferences = Schemas['Preferences']
export type HealthDetails = Schemas['HealthDetailsOut']
export type Role = Me['role']

const BASE = '/api/v1'
const SAFE_METHODS = new Set(['GET', 'HEAD'])

export interface FieldError {
  field: string
  message: string
}

/** An RFC 9457 Problem Details response, or a network failure (status 0). */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly title: string
  readonly detail: string | undefined
  readonly errors: FieldError[]
  readonly requestId: string | undefined

  constructor(init: {
    status: number
    code: string
    title: string
    detail?: string | undefined
    errors?: FieldError[] | undefined
    requestId?: string | undefined
  }) {
    super(init.detail ?? init.title)
    this.name = 'ApiError'
    this.status = init.status
    this.code = init.code
    this.title = init.title
    this.detail = init.detail
    this.errors = init.errors ?? []
    this.requestId = init.requestId
  }

  fieldError(field: string): string | undefined {
    return this.errors.find((e) => e.field === field)?.message
  }
}

let csrfToken: string | null = null

/** Store the per-session CSRF token returned by login, setup and `GET /auth/me` (C-02). */
export function setCsrfToken(token: string | null): void {
  csrfToken = token
}

async function toApiError(response: Response): Promise<ApiError> {
  const requestId = response.headers.get('x-request-id') ?? undefined
  try {
    const body = (await response.json()) as Partial<{
      code: string
      title: string
      detail: string
      errors: FieldError[]
      request_id: string
    }>
    return new ApiError({
      status: response.status,
      code: body.code ?? 'internal',
      title: body.title ?? response.statusText,
      detail: body.detail,
      errors: body.errors,
      requestId: body.request_id ?? requestId,
    })
  } catch {
    return new ApiError({ status: response.status, code: 'internal', title: response.statusText, requestId })
  }
}

export async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (!SAFE_METHODS.has(method) && csrfToken) headers['X-CSRF-Token'] = csrfToken
  let response: Response
  try {
    response = await fetch(BASE + path, {
      method,
      headers,
      credentials: 'same-origin',
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError({
      status: 0,
      code: 'network',
      title: 'Cannot reach the OmniSync server',
      detail: 'Check that `omnisync serve` is running on this computer.',
    })
  }
  if (!response.ok) throw await toApiError(response)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const api = {
  setupStatus: () => request<Schemas['SetupStatusOut']>('GET', '/setup/status'),
  setup: (body: Schemas['SetupIn']) => request<Me>('POST', '/setup', body),
  login: (body: Schemas['LoginIn']) => request<Me>('POST', '/auth/login', body),
  logout: () => request<void>('POST', '/auth/logout'),
  me: () => request<Me>('GET', '/auth/me'),
  changePassword: (body: Schemas['PasswordChangeIn']) => request<void>('POST', '/auth/password', body),
  updatePreferences: (body: Schemas['PreferencesPatch']) => request<Me>('PATCH', '/auth/preferences', body),
  healthDetails: () => request<HealthDetails>('GET', '/health/details'),
  listUsers: () => request<Schemas['UserList']>('GET', '/users'),
  createUser: (body: Schemas['UserCreateIn']) => request<User>('POST', '/users', body),
  updateUser: (id: number, body: Schemas['UserPatchIn']) => request<User>('PATCH', `/users/${id}`, body),
}
