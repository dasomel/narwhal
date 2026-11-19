import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { QueryClient, QueryObserver, environmentManager } from "@tanstack/react-query"
import {
  ACCESS_NOT_GRANTED_CODE,
  ApiHttpError,
  SCOPE_FINGERPRINT_HEADER,
  ScopeChangeError,
  apiFetch,
  apiFetchJson,
  bindScopeQueryClient,
  createScopeGuard,
  resetScopeGuard,
  shouldRetryQuery,
} from "./scope-guard"

function makeResponse(
  body: unknown,
  options: { status?: number; statusText?: string; scope?: string | null } = {},
): Response {
  const status = options.status ?? 200
  const headers = new Headers()
  if (options.scope) {
    headers.set(SCOPE_FINGERPRINT_HEADER, options.scope)
  }
  return new Response(JSON.stringify(body), {
    status,
    statusText: options.statusText ?? (status === 200 ? "OK" : "Error"),
    headers,
  })
}

function createMemoryStorage(): Storage {
  const store = new Map<string, string>()
  return {
    get length() {
      return store.size
    },
    clear() {
      store.clear()
    },
    getItem(key: string) {
      return store.has(key) ? store.get(key)! : null
    },
    key(index: number) {
      return Array.from(store.keys())[index] ?? null
    },
    removeItem(key: string) {
      store.delete(key)
    },
    setItem(key: string, value: string) {
      store.set(key, String(value))
    },
  }
}

function stubBrowserEnv(pathname = "/governance", search = "?tab=alerts") {
  const assign = vi.fn()
  const reload = vi.fn()
  const storage = createMemoryStorage()
  const location = { assign, reload, pathname, search, href: `${pathname}${search}` }
  vi.stubGlobal("window", { location, sessionStorage: storage })
  vi.stubGlobal("sessionStorage", storage)
  return { assign, reload, storage, location }
}

async function waitUntil(condition: () => boolean, maxTurns = 100): Promise<void> {
  for (let i = 0; i < maxTurns; i++) {
    if (condition()) return
    await new Promise((resolve) => setTimeout(resolve, 0))
  }
  throw new Error("Condition was not satisfied within async turn budget")
}

describe("scope-guard with real QueryClient", () => {
  let prevIsServer: () => boolean

  beforeEach(() => {
    prevIsServer = environmentManager.isServer
    environmentManager.setIsServer(() => false)
    resetScopeGuard()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    resetScopeGuard()
    environmentManager.setIsServer(prevIsServer)
  })

  it("clears cache and reloads once on fingerprint change without storing the triggering response", async () => {
    const { assign, reload } = stubBrowserEnv()
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: shouldRetryQuery, retryDelay: 0, gcTime: Infinity } },
    })
    const clearSpy = vi.spyOn(queryClient, "clear")
    const guard = createScopeGuard(queryClient)

    let scope = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url === "/api/catalog") {
          return makeResponse([{ name: "team-b-svc" }], { scope })
        }
        return makeResponse(
          scope === "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            ? [{ alertname: "OldScopeAlert" }]
            : [{ alertname: "NewScopeAlertShouldNotCache" }],
          { scope },
        )
      }),
    )

    await queryClient.fetchQuery({
      queryKey: ["catalog"],
      queryFn: ({ signal }) => guard.fetchJson("/api/catalog", { signal }),
    })

    const alertsObs = new QueryObserver(queryClient, {
      queryKey: ["alerts"],
      queryFn: ({ signal }) => guard.fetchJson("/api/alerts", { signal }),
    })
    const unsub = alertsObs.subscribe(() => {})
    await waitUntil(() => alertsObs.getCurrentResult().status === "success")
    expect(queryClient.getQueryData(["alerts"])).toEqual([{ alertname: "OldScopeAlert" }])

    scope = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    await alertsObs.refetch()

    expect(clearSpy).toHaveBeenCalledTimes(1)
    expect(reload).toHaveBeenCalledTimes(1)
    expect(assign).not.toHaveBeenCalled()
    expect(guard.isNavigating()).toBe(true)
    expect(queryClient.getQueryData(["catalog"])).toBeUndefined()
    expect(queryClient.getQueryData(["alerts"])).toBeUndefined()
    expect(alertsObs.getCurrentResult().data).toBeUndefined()

    unsub()
    queryClient.clear()
  })

  it("never stores a slow old response completing after a navigation decision", async () => {
    const { reload } = stubBrowserEnv()
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: shouldRetryQuery, retryDelay: 0, gcTime: Infinity } },
    })
    const guard = createScopeGuard(queryClient)

    let resolveSlowFetch!: (res: Response) => void
    let resolveSlowBody!: (data: unknown) => void
    let callIndex = 0

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input)
        if (url === "/api/alerts") {
          callIndex += 1
          return Promise.resolve(
            makeResponse([{ alertname: callIndex === 1 ? "Initial" : "Rotated" }], {
              scope: callIndex === 1
                ? "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                : "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            }),
          )
        }
        if (url === "/api/events") {
          return new Promise<Response>((resolve) => {
            resolveSlowFetch = resolve
          })
        }
        return Promise.resolve({
          ok: true,
          status: 200,
          statusText: "OK",
          headers: new Headers({ [SCOPE_FINGERPRINT_HEADER]: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }),
          json: () =>
            new Promise((resolve) => {
              resolveSlowBody = resolve
            }),
        } as unknown as Response)
      }),
    )

    // Establish initial fingerprint.
    await queryClient.fetchQuery({
      queryKey: ["alerts"],
      queryFn: () => guard.fetchJson("/api/alerts"),
    })

    // Start two slow old-scope requests (one still awaiting fetch, one awaiting res.json()).
    const slowFetchSettled = queryClient
      .fetchQuery({ queryKey: ["events"], queryFn: () => guard.fetchJson("/api/events") })
      .then(
        () => ({ ok: true as const }),
        (err) => ({ ok: false as const, err }),
      )
    const slowBodySettled = queryClient
      .fetchQuery({ queryKey: ["pods"], queryFn: () => guard.fetchJson("/api/pods") })
      .then(
        () => ({ ok: true as const }),
        (err) => ({ ok: false as const, err }),
      )
    const rawFetchBodySettled = guard
      .fetch("/api/scorecards")
      .then((r) => r.json())
      .then(
        () => ({ ok: true as const }),
        (err) => ({ ok: false as const, err }),
      )
    await Promise.resolve()

    // Concurrent poll triggers scope change -> clear + reload + navigating = true.
    await expect(
      queryClient.fetchQuery({
        queryKey: ["alerts"],
        queryFn: () => guard.fetchJson("/api/alerts"),
      }),
    ).rejects.toThrow()
    expect(reload).toHaveBeenCalledTimes(1)

    // Now the slow responses from the old scope complete.
    resolveSlowFetch(makeResponse([{ id: "stale-secret" }], { scope: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }))
    resolveSlowBody([{ name: "stale-pod" }])

    expect(await slowFetchSettled).toMatchObject({ ok: false })
    expect(await slowBodySettled).toMatchObject({ ok: false })
    const rawRes = await rawFetchBodySettled
    expect(rawRes.ok).toBe(false)
    if (!rawRes.ok) {
      expect(rawRes.err).toBeInstanceOf(ScopeChangeError)
    }

    // Subsequent fetches while navigating throw ScopeChangeError directly without hitting fetch again.
    await expect(guard.fetchJson("/api/catalog")).rejects.toBeInstanceOf(ScopeChangeError)

    expect(reload).toHaveBeenCalledTimes(1)
    expect(queryClient.getQueryData(["events"])).toBeUndefined()
    expect(queryClient.getQueryData(["pods"])).toBeUndefined()
    expect(queryClient.getQueryData(["alerts"])).toBeUndefined()

    queryClient.clear()
  })

  it("clears cache and navigates to /access-denied on 403 ACCESS_NOT_GRANTED", async () => {
    const { assign, reload } = stubBrowserEnv()
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: shouldRetryQuery, retryDelay: 0, gcTime: Infinity } },
    })
    const clearSpy = vi.spyOn(queryClient, "clear")
    const unbind = bindScopeQueryClient(queryClient)

    let revoked = false
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        revoked
          ? makeResponse({ error: "Forbidden", code: ACCESS_NOT_GRANTED_CODE }, { status: 403 })
          : makeResponse([{ alertname: "TeamBAlert" }], { scope: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }),
      ),
    )

    await queryClient.fetchQuery({
      queryKey: ["alerts"],
      queryFn: ({ signal }) => apiFetchJson("/api/alerts", { signal }),
    })
    expect(queryClient.getQueryData(["alerts"])).toEqual([{ alertname: "TeamBAlert" }])

    revoked = true
    let helperErr: unknown
    await expect(
      queryClient.fetchQuery({
        queryKey: ["alerts"],
        queryFn: async ({ signal }) => {
          try {
            return await apiFetchJson("/api/alerts", { signal })
          } catch (err) {
            helperErr = err
            throw err
          }
        },
      }),
    ).rejects.toThrow()

    expect(helperErr).toMatchObject({
      name: "ApiHttpError",
      status: 403,
      code: ACCESS_NOT_GRANTED_CODE,
    })

    expect(clearSpy).toHaveBeenCalledTimes(1)
    expect(assign).toHaveBeenCalledTimes(1)
    expect(assign).toHaveBeenCalledWith("/access-denied")
    expect(reload).not.toHaveBeenCalled()
    expect(queryClient.getQueryData(["alerts"])).toBeUndefined()

    unbind()
    queryClient.clear()
  })

  it("clears cache and navigates to the sign-in flow on 401, but ignores route-level 403 without ACCESS_NOT_GRANTED", async () => {
    const { assign, reload } = stubBrowserEnv("/governance", "?tab=audit")
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: shouldRetryQuery, retryDelay: 0, gcTime: Infinity } },
    })
    const clearSpy = vi.spyOn(queryClient, "clear")
    const guard = createScopeGuard(queryClient)

    let phase: "ok" | "route-forbidden" | "unauthorized" = "ok"
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        if (phase === "route-forbidden") {
          return makeResponse({ error: "Forbidden" }, { status: 403 })
        }
        if (phase === "unauthorized") {
          return makeResponse({ error: "Unauthorized" }, { status: 401 })
        }
        return makeResponse([{ alertname: "TeamA" }], { scope: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" })
      }),
    )

    await queryClient.fetchQuery({
      queryKey: ["alerts"],
      queryFn: () => guard.fetchJson("/api/alerts"),
    })

    // Route-level 403 (e.g., admin-only route for scoped developer) must not navigate or clear cache.
    phase = "route-forbidden"
    await expect(guard.fetchJson("/api/governance/distribution")).rejects.toMatchObject({
      name: "ApiHttpError",
      status: 403,
    })
    expect(assign).not.toHaveBeenCalled()
    expect(clearSpy).not.toHaveBeenCalled()
    expect(queryClient.getQueryData(["alerts"])).toEqual([{ alertname: "TeamA" }])

    // Session 401 must clear cache and redirect to /login?callbackUrl=...
    phase = "unauthorized"
    await expect(guard.fetchJson("/api/alerts")).rejects.toMatchObject({
      name: "ApiHttpError",
      status: 401,
    })
    expect(clearSpy).toHaveBeenCalledTimes(1)
    expect(assign).toHaveBeenCalledTimes(1)
    expect(assign).toHaveBeenCalledWith("/login?callbackUrl=%2Fgovernance%3Ftab%3Daudit")
    expect(reload).not.toHaveBeenCalled()
    expect(queryClient.getQueryData(["alerts"])).toBeUndefined()

    queryClient.clear()
  })

  it("within the 30s reload cooldown navigates to /access-denied instead of reloading, and stores nothing", async () => {
    vi.useFakeTimers({ toFake: ["Date"] })
    vi.setSystemTime(new Date("2026-11-19T00:00:00.000Z"))

    const { reload, assign } = stubBrowserEnv()
    let scope = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url === "/api/catalog") {
          return makeResponse([{ name: "svc" }], { scope })
        }
        return makeResponse([{ alertname: scope }], { scope })
      }),
    )

    // 1st scope change at t=0s -> triggers reload once and records timestamp in sessionStorage.
    const qc1 = new QueryClient({
      defaultOptions: { queries: { retry: shouldRetryQuery, retryDelay: 0, gcTime: Infinity } },
    })
    const guard1 = createScopeGuard(qc1)
    await qc1.fetchQuery({ queryKey: ["alerts"], queryFn: () => guard1.fetchJson("/api/alerts") })
    scope = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    let firstChangeErr: unknown
    await expect(
      qc1.fetchQuery({
        queryKey: ["alerts"],
        queryFn: async () => {
          try {
            return await guard1.fetchJson("/api/alerts")
          } catch (err) {
            firstChangeErr = err
            throw err
          }
        },
      }),
    ).rejects.toThrow()
    expect(firstChangeErr).toBeInstanceOf(ScopeChangeError)
    expect(reload).toHaveBeenCalledTimes(1)

    // 2nd scope change within 30s (t=10s) on the reloaded page -> no second reload, cache cleared, error thrown.
    vi.setSystemTime(new Date("2026-11-19T00:00:10.000Z"))
    const qc2 = new QueryClient({
      defaultOptions: { queries: { retry: shouldRetryQuery, retryDelay: 0, gcTime: Infinity } },
    })
    const guard2 = createScopeGuard(qc2)
    await qc2.fetchQuery({ queryKey: ["catalog"], queryFn: () => guard2.fetchJson("/api/catalog") })

    const alertsObs = new QueryObserver(qc2, {
      queryKey: ["alerts"],
      queryFn: ({ signal }) => guard2.fetchJson("/api/alerts", { signal }),
    })
    const unsub = alertsObs.subscribe(() => {})
    await waitUntil(() => alertsObs.getCurrentResult().status === "success")

    // Disagreed replica returns old fingerprint within 30s.
    scope = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    await alertsObs.refetch()

    expect(reload).toHaveBeenCalledTimes(1)
    expect(assign).toHaveBeenCalledWith("/access-denied")
    expect(guard2.isNavigating()).toBe(true)
    expect(qc2.getQueryData(["catalog"])).toBeUndefined()
    expect(qc2.getQueryData(["alerts"])).toBeUndefined()

    // A late response from the old scope completing afterwards is never stored.
    scope = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    await expect(
      qc2.fetchQuery({ queryKey: ["late"], queryFn: () => guard2.fetchJson("/api/alerts") }),
    ).rejects.toBeInstanceOf(ScopeChangeError)
    expect(qc2.getQueryData(["late"])).toBeUndefined()

    unsub()
    qc1.clear()
    qc2.clear()
  })

  it("without usable sessionStorage navigates to /access-denied instead of reloading (reloads cannot be bounded)", async () => {
    const reload = vi.fn()
    const assign = vi.fn()
    vi.stubGlobal("window", {
      location: { assign, reload, pathname: "/", search: "" },
      get sessionStorage() {
        throw new Error("SecurityError: storage disabled")
      },
    })
    vi.stubGlobal("sessionStorage", {
      getItem() {
        throw new Error("SecurityError")
      },
      setItem() {
        throw new Error("SecurityError")
      },
    })

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: shouldRetryQuery, retryDelay: 0, gcTime: Infinity } },
    })
    const guard = createScopeGuard(queryClient)

    let scope = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    vi.stubGlobal("fetch", vi.fn(async () => makeResponse([{ ok: true }], { scope })))

    await queryClient.fetchQuery({ queryKey: ["alerts"], queryFn: () => guard.fetchJson("/api/alerts") })
    scope = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    let helperErr: unknown
    await expect(
      queryClient.fetchQuery({
        queryKey: ["alerts"],
        queryFn: async () => {
          try {
            return await guard.fetchJson("/api/alerts")
          } catch (err) {
            helperErr = err
            throw err
          }
        },
      }),
    ).rejects.toThrow()
    expect(helperErr).toBeInstanceOf(ScopeChangeError)

    expect(reload).not.toHaveBeenCalled()
    expect(assign).toHaveBeenCalledWith("/access-denied")
    expect(queryClient.getQueryData(["alerts"])).toBeUndefined()
    queryClient.clear()
  })

  it("leaves cache, fingerprint, and navigation untouched on non-scope errors (500 and network)", async () => {
    const { assign, reload } = stubBrowserEnv()
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: shouldRetryQuery, retryDelay: 0, gcTime: Infinity } },
    })
    const clearSpy = vi.spyOn(queryClient, "clear")
    const guard = createScopeGuard(queryClient)

    let mode: "ok" | "500" | "network" = "ok"
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        if (mode === "500") {
          return makeResponse({ error: "Upstream unavailable" }, { status: 500, statusText: "Internal Server Error" })
        }
        if (mode === "network") {
          throw new TypeError("Failed to fetch")
        }
        return makeResponse([{ alertname: "TeamAAlert" }], { scope: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" })
      }),
    )

    await queryClient.fetchQuery({
      queryKey: ["alerts"],
      queryFn: () => guard.fetchJson("/api/alerts"),
    })
    expect(guard.getLastFingerprint()).toBe("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

    mode = "500"
    await expect(guard.fetchJson("/api/alerts")).rejects.toMatchObject({
      name: "ApiHttpError",
      status: 500,
    })

    mode = "network"
    await expect(apiFetch("/api/alerts", undefined, guard)).rejects.toThrow("Failed to fetch")

    expect(clearSpy).not.toHaveBeenCalled()
    expect(reload).not.toHaveBeenCalled()
    expect(assign).not.toHaveBeenCalled()
    expect(guard.isNavigating()).toBe(false)
    expect(guard.getLastFingerprint()).toBe("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    expect(queryClient.getQueryData(["alerts"])).toEqual([{ alertname: "TeamAAlert" }])

    queryClient.clear()
  })

  it("never retries 401/403 or ScopeChangeError, and retries transient errors twice", () => {
    expect(shouldRetryQuery(0, new ApiHttpError(401, "Unauthorized"))).toBe(false)
    expect(shouldRetryQuery(0, new ApiHttpError(403, "Forbidden"))).toBe(false)
    expect(shouldRetryQuery(0, new ScopeChangeError())).toBe(false)
    expect(shouldRetryQuery(1, new ScopeChangeError())).toBe(false)
    expect(shouldRetryQuery(0, new ApiHttpError(500, "Server Error"))).toBe(true)
    expect(shouldRetryQuery(1, new Error("network"))).toBe(true)
    expect(shouldRetryQuery(2, new Error("network"))).toBe(false)
  })
})
