"use client"

import type { QueryClient } from "@tanstack/react-query"
import { ACCESS_NOT_GRANTED_CODE, SCOPE_FINGERPRINT_HEADER } from "./scope-constants"

export { ACCESS_NOT_GRANTED_CODE, SCOPE_FINGERPRINT_HEADER }

const SCOPE_RELOAD_STORAGE_KEY = "narwhal.scope.lastReloadAt"
const SCOPE_RELOAD_COOLDOWN_MS = 30_000

/**
 * Typed HTTP error thrown by {@link apiFetchJson} (and by {@link apiFetch} on
 * 401 / 403 ACCESS_NOT_GRANTED) so React Query retry policies and UI error
 * boundaries can distinguish auth/scope failures from transient network errors.
 */
export class ApiHttpError extends Error {
  readonly status: number
  readonly code?: string

  constructor(status: number, message: string, code?: string) {
    super(message)
    this.name = "ApiHttpError"
    this.status = status
    if (code !== undefined) this.code = code
  }
}

/**
 * Thrown when a server scope change or in-progress scope/auth navigation
 * prevents returning or caching response data.
 */
export class ScopeChangeError extends Error {
  constructor(message = "Server visibility scope changed") {
    super(message)
    this.name = "ScopeChangeError"
  }
}

export interface ScopeGuard {
  bindQueryClient: (client: QueryClient) => () => void
  getLastFingerprint: () => string | null
  isNavigating: () => boolean
  reset: () => void
  fetch: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>
  fetchJson: <T = unknown>(input: RequestInfo | URL, init?: RequestInit) => Promise<T>
}

async function readErrorCode(res: Response): Promise<string | undefined> {
  try {
    const readable = typeof res.clone === "function" ? res.clone() : res
    const body = (await readable.json()) as { code?: unknown } | null
    return body && typeof body.code === "string" ? body.code : undefined
  } catch {
    return undefined
  }
}

function getSessionStorage(): Storage | undefined {
  try {
    if (typeof window !== "undefined" && window.sessionStorage) {
      return window.sessionStorage
    }
    if (typeof sessionStorage !== "undefined") {
      return sessionStorage
    }
  } catch {
    return undefined
  }
  return undefined
}

function tryClaimScopeReload(): boolean {
  try {
    const storage = getSessionStorage()
    // Fail closed: without storage we cannot bound reloads, so do not reload.
    if (!storage) return false
    const now = Date.now()
    const raw = storage.getItem(SCOPE_RELOAD_STORAGE_KEY)
    const lastReloadAt = raw && raw.length > 0 ? Number(raw) : NaN
    if (Number.isFinite(lastReloadAt) && now - lastReloadAt < SCOPE_RELOAD_COOLDOWN_MS) {
      return false
    }
    storage.setItem(SCOPE_RELOAD_STORAGE_KEY, String(now))
    return true
  } catch {
    return false
  }
}

function getSignInUrl(): string {
  if (typeof window === "undefined" || !window.location) return "/login"
  const pathname = typeof window.location.pathname === "string" ? window.location.pathname : ""
  const search = typeof window.location.search === "string" ? window.location.search : ""
  if (!pathname || pathname.startsWith("/login")) return "/login"
  return `/login?callbackUrl=${encodeURIComponent(`${pathname}${search}`)}`
}

function navigateTo(url: string): void {
  if (typeof window === "undefined" || !window.location) return
  if (typeof window.location.assign === "function") {
    window.location.assign(url)
  } else {
    window.location.href = url
  }
}

function reloadPage(): void {
  if (typeof window === "undefined" || !window.location) return
  if (typeof window.location.reload === "function") {
    window.location.reload()
  }
}

function wrapResponseBodyMethods(res: Response, assertFresh: () => void): Response {
  const wrapMethod = (method: "json" | "text" | "arrayBuffer" | "blob") => {
    const original = res[method]
    if (typeof original !== "function") return
    try {
      Object.defineProperty(res, method, {
        configurable: true,
        value: async () => {
          const value = await (original as () => Promise<unknown>).call(res)
          assertFresh()
          return value
        },
      })
    } catch {
      // Non-extensible response mock; guardedFetchJson still validates after await res.json().
    }
  }
  wrapMethod("json")
  wrapMethod("text")
  wrapMethod("arrayBuffer")
  wrapMethod("blob")
  return res
}

/**
 * Tracks the server's `X-Narwhal-Scope` fingerprint across API responses.
 *
 * On a scope fingerprint change, 401, or 403 `ACCESS_NOT_GRANTED`, synchronously
 * clears the bound `QueryClient`, marks navigation in progress so no concurrent
 * or late response can store stale data, and performs a full document navigation
 * (subject to a 30 s `sessionStorage` reload limit to prevent replica-mismatch loops).
 */
export function createScopeGuard(initialClient: QueryClient | null = null): ScopeGuard {
  let queryClient: QueryClient | null = initialClient
  let lastFingerprint: string | null = null
  let navigating = false

  const clearForNavigation = () => {
    if (!queryClient) return
    for (const query of queryClient.getQueryCache().getAll()) {
      query.setState({ data: undefined, dataUpdatedAt: 0, status: "pending" })
    }
    queryClient.clear()
  }

  const handleFingerprint = (serverFingerprint: string | null) => {
    if (navigating) {
      throw new ScopeChangeError()
    }
    if (serverFingerprint === null) return
    if (lastFingerprint === null) {
      lastFingerprint = serverFingerprint
      return
    }
    if (serverFingerprint === lastFingerprint) return

    // D-688-nav: every scope change ends in a full document navigation, which discards
    // the JS heap (observers, selected items). In-memory cleanup was dropped after four
    // review rounds of races. When a reload is not allowed (cooldown hit, or storage
    // unavailable so reloads cannot be bounded) go to /access-denied instead of reloading:
    // it renders no tenant data and cannot loop.
    navigating = true
    clearForNavigation()
    if (tryClaimScopeReload()) {
      reloadPage()
    } else {
      navigateTo("/access-denied")
    }
    throw new ScopeChangeError()
  }

  const bindQueryClient = (client: QueryClient) => {
    queryClient = client
    return () => {
      if (queryClient === client) {
        queryClient = null
      }
    }
  }

  const reset = () => {
    lastFingerprint = null
    navigating = false
    queryClient = null
  }

  const guardedFetchInternal = async (
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<{ res: Response; serverFingerprint: string | null }> => {
    if (navigating) {
      throw new ScopeChangeError()
    }
    const res = await fetch(input, init)
    if (navigating) {
      throw new ScopeChangeError()
    }

    if (res.status === 401) {
      navigating = true
      clearForNavigation()
      navigateTo(getSignInUrl())
      throw new ApiHttpError(401, res.statusText || "Unauthorized")
    }

    if (res.status === 403) {
      const code = await readErrorCode(res)
      if (navigating) {
        throw new ScopeChangeError()
      }
      if (code === ACCESS_NOT_GRANTED_CODE) {
        navigating = true
        clearForNavigation()
        navigateTo("/access-denied")
        throw new ApiHttpError(403, res.statusText || "Forbidden", ACCESS_NOT_GRANTED_CODE)
      }
    }

    const serverFingerprint = res.headers?.get?.(SCOPE_FINGERPRINT_HEADER) || null
    handleFingerprint(serverFingerprint)
    return { res, serverFingerprint }
  }

  const guardedFetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const { res, serverFingerprint } = await guardedFetchInternal(input, init)
    return wrapResponseBodyMethods(res, () => handleFingerprint(serverFingerprint))
  }

  const guardedFetchJson = async <T = unknown>(
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<T> => {
    const { res, serverFingerprint } = await guardedFetchInternal(input, init)
    if (!res.ok) {
      const code = res.status === 403 ? await readErrorCode(res) : undefined
      handleFingerprint(serverFingerprint)
      throw new ApiHttpError(res.status, res.statusText || `HTTP ${res.status}`, code)
    }
    const data = (await res.json()) as T
    handleFingerprint(serverFingerprint)
    return data
  }

  return {
    bindQueryClient,
    getLastFingerprint: () => lastFingerprint,
    isNavigating: () => navigating,
    reset,
    fetch: guardedFetch,
    fetchJson: guardedFetchJson,
  }
}

export const defaultScopeGuard = createScopeGuard()

export function bindScopeQueryClient(
  client: QueryClient,
  guard: ScopeGuard = defaultScopeGuard,
): () => void {
  return guard.bindQueryClient(client)
}

export function resetScopeGuard(guard: ScopeGuard = defaultScopeGuard): void {
  guard.reset()
}

export function apiFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
  guard: ScopeGuard = defaultScopeGuard,
): Promise<Response> {
  return guard.fetch(input, init)
}

export function apiFetchJson<T = unknown>(
  input: RequestInfo | URL,
  init?: RequestInit,
  guard: ScopeGuard = defaultScopeGuard,
): Promise<T> {
  return guard.fetchJson<T>(input, init)
}

/**
 * Default React Query retry policy: never retry 401/403 (auth/scope denials) or
 * scope-change errors (`ScopeChangeError`), and retry transient errors up to 2 times.
 */
export function shouldRetryQuery(failureCount: number, error: unknown): boolean {
  if (error instanceof ScopeChangeError) return false
  if (error instanceof ApiHttpError && (error.status === 401 || error.status === 403)) {
    return false
  }
  return failureCount < 2
}
