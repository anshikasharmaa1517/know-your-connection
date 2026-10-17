export type User = { id: string; name: string; handle: string }
export type Account = { token: string; user: User }

const ACCOUNTS = 'connections.accounts'
const CURRENT = 'connections.current'

function read<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key)
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch {
    return fallback
  }
}

function write(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    /* storage can be unavailable in private windows */
  }
}

let memoryAccounts: Account[] = read<Account[]>(ACCOUNTS, [])
let memoryCurrent: string | null = read<string | null>(CURRENT, null)

export function listAccounts(): Account[] {
  return memoryAccounts
}

export function currentAccount(): Account | null {
  return memoryAccounts.find((a) => a.user.id === memoryCurrent) ?? memoryAccounts[0] ?? null
}

export function switchAccount(id: string) {
  memoryCurrent = id
  write(CURRENT, id)
}

export function addAccount(account: Account) {
  memoryAccounts = [...memoryAccounts.filter((a) => a.user.id !== account.user.id), account]
  write(ACCOUNTS, memoryAccounts)
  switchAccount(account.user.id)
}

export function forgetAccount(id: string) {
  memoryAccounts = memoryAccounts.filter((a) => a.user.id !== id)
  write(ACCOUNTS, memoryAccounts)
  if (memoryCurrent === id) {
    memoryCurrent = memoryAccounts[0]?.user.id ?? null
    write(CURRENT, memoryCurrent)
  }
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

type Options = { method?: string; body?: unknown; auth?: boolean }

export async function api<T = any>(path: string, opts: Options = {}): Promise<T> {
  const headers: Record<string, string> = {}
  const account = currentAccount()
  if (opts.auth !== false && account) headers.Authorization = `Bearer ${account.token}`
  let body: BodyInit | undefined
  if (opts.body instanceof FormData) {
    body = opts.body
  } else if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(opts.body)
  }
  const res = await fetch(path, { method: opts.method ?? (body ? 'POST' : 'GET'), headers, body })
  const text = await res.text()
  let data: any = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = { detail: text }
  }
  if (!res.ok) {
    const detail = typeof data?.detail === 'string' ? data.detail : `Request failed (${res.status})`
    throw new ApiError(res.status, detail)
  }
  return data as T
}

export function formatDate(value?: string | number | null): string {
  if (value === null || value === undefined || value === '') return ''
  const d = typeof value === 'number' ? new Date(value) : new Date(`${value}T00:00:00`)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/)
  return ((parts[0]?.[0] ?? '') + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase()
}

export async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}
