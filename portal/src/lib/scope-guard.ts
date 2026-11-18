"use client"

import { useCallback, useEffect, useState, useSyncExternalStore } from "react"
import type { QueryClient } from "@tanstack/react-query"
import { ACCESS_NOT_GRANTED_CODE, SCOPE_FINGERPRINT_HEADER } from "./scope-constants"

export { ACCESS_NOT_GRANTED_CODE, SCOPE_FINGERPRINT_HEADER }

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
 * Thrown when an in-flight request resolves with a pre-transition scope
 * fingerprint after a concurrent request has already moved the session to a
 * newer scope, preventing stale responses from overwriting the cleared cache.
 */
export class ScopeStaleResponseError extends Error {
  constructor() {
    super("Response belonged to a superseded visibility scope")
    this.name = "ScopeStaleResponseError"
  }
}

export interface ScopedState<T> {
  get: () => T
  set: (next: T | ((prev: T) => T)) => void
  subscribe: (listener: () => void) => () => void
  mount: () => () => void
}

export interface ScopeGuard {
  bindQueryClient: (client: QueryClient) => () => void
  getQueryClient: () => QueryClient | null
  getEpoch: () => number
  getLastFingerprint: () => string | null
  subscribe: (listener: () => void) => () => void
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

/**
 * Creates a client-side scope tracker bound to a single React Query `QueryClient`.
 *
 * The server's `X-Narwhal-Scope` response header is the only source of truth for
 * the caller's visibility scope: whenever a response reports a different
 * fingerprint, or returns 401 / 403 `ACCESS_NOT_GRANTED`, cached queries are
 * cleared immediately and subscribed detail views reset to their initial state.
 */
export function createScopeGuard(initialClient: QueryClient | null = null): ScopeGuard {
  let queryClient: QueryClient | null = initialClient
  let lastFingerprint: string | null = null
  let epoch = 0
  const listeners = new Set<() => void>()

  const notifyListeners = () => {
    for (const listener of listeners) {
      listener()
    }
  }

  const evictCache = (refetchActive: boolean) => {
    epoch += 1
    notifyListeners()
    if (!queryClient) return

    const activeKeys: Array<readonly unknown[]> = []
    for (const query of queryClient.getQueryCache().getAll()) {
      if (query.getObserversCount() === 0) {
        queryClient.removeQueries({ queryKey: query.queryKey, exact: true })
      } else if (query.state.fetchStatus === "fetching") {
        // Clear any previously cached data on the in-flight query without
        // cancelling the currently settling fetch; the caller either returns
        // the new-scope payload or throws into error state.
        query.setState({ data: undefined, dataUpdatedAt: 0, status: "pending" })
      } else {
        query.reset()
        if (refetchActive && query.isActive()) {
          activeKeys.push(query.queryKey)
        }
      }
    }

    if (refetchActive) {
      for (const queryKey of activeKeys) {
        void queryClient.refetchQueries({ queryKey, exact: true })
      }
    }
  }

  const handleRevoked = () => {
    lastFingerprint = null
    evictCache(false)
  }

  const handleScopeChanged = (nextFingerprint: string) => {
    lastFingerprint = nextFingerprint
    evictCache(true)
  }

  const reconcileFingerprint = (
    serverFingerprint: string | null,
    requestEpoch: number,
    requestFingerprint: string | null,
  ) => {
    if (!serverFingerprint) return

    if (epoch !== requestEpoch) {
      if (lastFingerprint === null) {
        throw new ApiHttpError(403, "Forbidden", ACCESS_NOT_GRANTED_CODE)
      }
      if (serverFingerprint !== lastFingerprint) {
        if (serverFingerprint === requestFingerprint) {
          throw new ScopeStaleResponseError()
        }
        handleScopeChanged(serverFingerprint)
      }
      return
    }

    if (lastFingerprint === null) {
      lastFingerprint = serverFingerprint
    } else if (serverFingerprint !== lastFingerprint) {
      handleScopeChanged(serverFingerprint)
    }
  }

  const bindQueryClient = (client: QueryClient) => {
    queryClient = client
    return () => {
      if (queryClient === client) {
        queryClient = null
      }
    }
  }

  const subscribe = (listener: () => void) => {
    listeners.add(listener)
    return () => {
      listeners.delete(listener)
    }
  }

  const reset = () => {
    lastFingerprint = null
    epoch = 0
    listeners.clear()
    queryClient = null
  }

  const guardedFetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const requestEpoch = epoch
    const requestFingerprint = lastFingerprint
    const res = await fetch(input, init)
    const serverFingerprint = res.headers?.get?.(SCOPE_FINGERPRINT_HEADER) || null

    if (res.status === 401) {
      handleRevoked()
      throw new ApiHttpError(401, res.statusText || "Unauthorized")
    }

    if (res.status === 403) {
      const code = await readErrorCode(res)
      if (code === ACCESS_NOT_GRANTED_CODE) {
        handleRevoked()
        throw new ApiHttpError(403, res.statusText || "Forbidden", ACCESS_NOT_GRANTED_CODE)
      }
      reconcileFingerprint(serverFingerprint, requestEpoch, requestFingerprint)
      return res
    }

    reconcileFingerprint(serverFingerprint, requestEpoch, requestFingerprint)
    return res
  }

  const guardedFetchJson = async <T = unknown>(
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<T> => {
    const res = await guardedFetch(input, init)
    if (!res.ok) {
      const code = res.status === 403 ? await readErrorCode(res) : undefined
      throw new ApiHttpError(res.status, res.statusText || `HTTP ${res.status}`, code)
    }
    return (await res.json()) as T
  }

  return {
    bindQueryClient,
    getQueryClient: () => queryClient,
    getEpoch: () => epoch,
    getLastFingerprint: () => lastFingerprint,
    subscribe,
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
 * Default React Query retry policy: never retry 401/403 (auth/scope denials),
 * retry a superseded in-flight scope response once, and retry transient errors
 * up to 2 times.
 */
export function shouldRetryQuery(failureCount: number, error: unknown): boolean {
  if (error instanceof ScopeStaleResponseError) return failureCount < 1
  if (error instanceof ApiHttpError && (error.status === 401 || error.status === 403)) {
    return false
  }
  return failureCount < 2
}

/**
 * Creates a small external store whose value resets to `initialValue` whenever
 * the bound {@link ScopeGuard} increments its epoch (scope change or 401/403
 * `ACCESS_NOT_GRANTED`). Safe across SSR, `renderToStaticMarkup`, and React 19
 * StrictMode remounts.
 */
export function createScopedState<T>(
  initialValue: T,
  guard: ScopeGuard = defaultScopeGuard,
): ScopedState<T> {
  let value = initialValue
  let valueEpoch = guard.getEpoch()
  const listeners = new Set<() => void>()

  const syncWithEpoch = () => {
    const currentEpoch = guard.getEpoch()
    if (valueEpoch !== currentEpoch) {
      value = initialValue
      valueEpoch = currentEpoch
      return true
    }
    return false
  }

  const notify = () => {
    for (const listener of listeners) {
      listener()
    }
  }

  const get = (): T => {
    syncWithEpoch()
    return value
  }

  const set = (next: T | ((prev: T) => T)) => {
    const current = get()
    const resolved = typeof next === "function" ? (next as (prev: T) => T)(current) : next
    if (Object.is(resolved, current) && valueEpoch === guard.getEpoch()) return
    value = resolved
    valueEpoch = guard.getEpoch()
    notify()
  }

  const subscribe = (listener: () => void) => {
    listeners.add(listener)
    return () => {
      listeners.delete(listener)
    }
  }

  const mount = () => {
    if (syncWithEpoch()) {
      notify()
    }
    return guard.subscribe(() => {
      if (syncWithEpoch()) {
        notify()
      }
    })
  }

  return { get, set, subscribe, mount }
}

/**
 * React state hook backed by {@link createScopedState} so open sheets, drawers,
 * and selected items reset automatically when the server visibility scope
 * changes or access is revoked.
 */
export function useScopedState<T>(
  initialValue: T,
  external?: ScopedState<T>,
  guard: ScopeGuard = defaultScopeGuard,
): [T, (next: T | ((prev: T) => T)) => void] {
  const [internal] = useState(() => createScopedState(initialValue, guard))
  const store = external ?? internal

  useEffect(() => {
    if (external) return
    return internal.mount()
  }, [external, internal])

  const value = useSyncExternalStore(store.subscribe, store.get, store.get)
  const setValue = useCallback(
    (next: T | ((prev: T) => T)) => store.set(next),
    [store],
  )
  return [value, setValue]
}

/**
 * Invokes `onReset` whenever the bound {@link ScopeGuard} detects a scope
 * change or access revocation.
 */
export function useScopeResetEffect(
  onReset: () => void,
  guard: ScopeGuard = defaultScopeGuard,
): void {
  useEffect(() => guard.subscribe(onReset), [guard, onReset])
}
