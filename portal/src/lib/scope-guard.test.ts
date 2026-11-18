import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { QueryClient, QueryObserver, environmentManager } from "@tanstack/react-query"
import {
  ACCESS_NOT_GRANTED_CODE,
  ApiHttpError,
  SCOPE_FINGERPRINT_HEADER,
  ScopeStaleResponseError,
  createScopeGuard,
  createScopedState,
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

async function waitUntil(condition: () => boolean, maxTurns = 50): Promise<void> {
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
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    environmentManager.setIsServer(prevIsServer)
  })

  it("evicts cached active and inactive queries and resets scoped state when X-Narwhal-Scope changes", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity } },
    })
    const guard = createScopeGuard(queryClient)
    const selected = createScopedState<string | null>(null, guard)
    const unsubSelected = selected.mount()

    let alertsScope = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    let eventsScope = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url === "/api/alerts") {
          return makeResponse(
            alertsScope === "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
              ? [{ alertname: "TeamBAlert" }]
              : [{ alertname: "TeamAAlert" }],
            { scope: alertsScope },
          )
        }
        if (url === "/api/events") {
          return makeResponse(
            eventsScope === "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
              ? [{ id: "e-team-b" }]
              : [{ id: "e-team-a" }],
            { scope: eventsScope },
          )
        }
        return makeResponse([], { scope: alertsScope })
      }),
    )

    // Populate an inactive query (e.g. from a previously visited page)
    await queryClient.fetchQuery({
      queryKey: ["catalog"],
      queryFn: () => guard.fetchJson("/api/catalog"),
    })
    expect(queryClient.getQueryData(["catalog"])).toEqual([])

    const alertsObs = new QueryObserver(queryClient, {
      queryKey: ["alerts"],
      queryFn: () => guard.fetchJson("/api/alerts"),
    })
    const eventsObs = new QueryObserver(queryClient, {
      queryKey: ["events"],
      queryFn: () => guard.fetchJson("/api/events"),
    })
    const unsubAlerts = alertsObs.subscribe(() => {})
    const unsubEvents = eventsObs.subscribe(() => {})

    await waitUntil(
      () =>
        alertsObs.getCurrentResult().status === "success" &&
        eventsObs.getCurrentResult().status === "success",
    )
    expect(alertsObs.getCurrentResult().data).toEqual([{ alertname: "TeamBAlert" }])
    expect(eventsObs.getCurrentResult().data).toEqual([{ id: "e-team-b" }])

    selected.set("TeamBAlert")
    expect(selected.get()).toBe("TeamBAlert")

    // Server reduces access to team-a only -> fingerprint changes to bbbbb...
    alertsScope = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    eventsScope = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

    await alertsObs.refetch()

    // Inactive cache is removed, selection is reset, and active alerts query holds new data.
    expect(queryClient.getQueryCache().find({ queryKey: ["catalog"] })).toBeUndefined()
    expect(selected.get()).toBeNull()
    expect(alertsObs.getCurrentResult().data).toEqual([{ alertname: "TeamAAlert" }])

    // Active events query was cleared and refetched under the new scope.
    await waitUntil(
      () =>
        JSON.stringify(eventsObs.getCurrentResult().data) ===
        JSON.stringify([{ id: "e-team-a" }]),
    )
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data))).not.toContain(
      "team-b",
    )

    unsubAlerts()
    unsubEvents()
    unsubSelected()
    queryClient.clear()
  })

  it("clears all queries and resets scoped state on 403 ACCESS_NOT_GRANTED and 401", async () => {
    for (const status of [403, 401] as const) {
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false, gcTime: Infinity } },
      })
      const guard = createScopeGuard(queryClient)
      const selected = createScopedState<string | null>(null, guard)
      const unsubSelected = selected.mount()

      let failAuth = false
      vi.stubGlobal(
        "fetch",
        vi.fn(async (input: RequestInfo | URL) => {
          if (failAuth) {
            return status === 403
              ? makeResponse({ error: "Forbidden", code: ACCESS_NOT_GRANTED_CODE }, { status: 403 })
              : makeResponse({ error: "Unauthorized" }, { status: 401 })
          }
          const url = String(input)
          return makeResponse(url === "/api/events" ? [{ id: "secret-event" }] : [{ id: "secret-alert" }], {
            scope: "11111111111111111111111111111111",
          })
        }),
      )

      const alertsObs = new QueryObserver(queryClient, {
        queryKey: ["alerts"],
        queryFn: () => guard.fetchJson("/api/alerts"),
      })
      const eventsObs = new QueryObserver(queryClient, {
        queryKey: ["events"],
        queryFn: () => guard.fetchJson("/api/events"),
      })
      const unsubAlerts = alertsObs.subscribe(() => {})
      const unsubEvents = eventsObs.subscribe(() => {})

      await waitUntil(
        () =>
          alertsObs.getCurrentResult().status === "success" &&
          eventsObs.getCurrentResult().status === "success",
      )
      selected.set("secret-alert")

      failAuth = true
      const refetchResult = await alertsObs.refetch()
      expect(refetchResult.error).toBeInstanceOf(ApiHttpError)
      expect((refetchResult.error as ApiHttpError).status).toBe(status)
      if (status === 403) {
        expect((refetchResult.error as ApiHttpError).code).toBe(ACCESS_NOT_GRANTED_CODE)
      }

      expect(guard.getLastFingerprint()).toBeNull()
      expect(selected.get()).toBeNull()
      expect(alertsObs.getCurrentResult().data).toBeUndefined()
      expect(eventsObs.getCurrentResult().data).toBeUndefined()
      expect(queryClient.getQueryData(["alerts"])).toBeUndefined()
      expect(queryClient.getQueryData(["events"])).toBeUndefined()

      unsubAlerts()
      unsubEvents()
      unsubSelected()
      queryClient.clear()
    }
  })

  it("does not clear unrelated cached queries on per-resource 403 with unchanged fingerprint", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity } },
    })
    const guard = createScopeGuard(queryClient)
    const fp = "22222222222222222222222222222222"

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url === "/api/alerts") {
          return makeResponse([{ id: "team-a-alert" }], { scope: fp })
        }
        return makeResponse({ error: "Forbidden" }, { status: 403, scope: fp })
      }),
    )

    const alertsObs = new QueryObserver(queryClient, {
      queryKey: ["alerts"],
      queryFn: () => guard.fetchJson("/api/alerts"),
    })
    const unsub = alertsObs.subscribe(() => {})
    await waitUntil(() => alertsObs.getCurrentResult().status === "success")

    await expect(guard.fetchJson("/api/k8s/pods?namespace=other-team")).rejects.toMatchObject({
      status: 403,
    })

    expect(queryClient.getQueryData(["alerts"])).toEqual([{ id: "team-a-alert" }])
    expect(guard.getLastFingerprint()).toBe(fp)

    unsub()
    queryClient.clear()
  })

  it("rejects stale pre-transition in-flight responses after a concurrent fingerprint change", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity } },
    })
    const guard = createScopeGuard(queryClient)
    const fp1 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    const fp2 = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

    let releaseSlowEvents!: (res: Response) => void
    const slowEventsPromise = new Promise<Response>((resolve) => {
      releaseSlowEvents = resolve
    })

    let callCount = 0
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url === "/api/alerts") {
          callCount += 1
          return makeResponse([{ id: callCount === 1 ? "old" : "new" }], {
            scope: callCount === 1 ? fp1 : fp2,
          })
        }
        return slowEventsPromise
      }),
    )

    // Warm initial scope fp1
    await guard.fetchJson("/api/alerts")
    expect(guard.getLastFingerprint()).toBe(fp1)

    // Start slow /api/events under fp1, then /api/alerts returns fp2
    const staleEventsFetch = guard.fetchJson("/api/events")
    await guard.fetchJson("/api/alerts")
    expect(guard.getLastFingerprint()).toBe(fp2)

    // Slow /api/events resolves with fp1 -> must be rejected as stale, leaving fp2 intact
    releaseSlowEvents(makeResponse([{ id: "stale-team-b" }], { scope: fp1 }))
    await expect(staleEventsFetch).rejects.toBeInstanceOf(ScopeStaleResponseError)
    expect(guard.getLastFingerprint()).toBe(fp2)

    queryClient.clear()
  })

  it("never retries 401/403 or ScopeStaleResponseError, and retries transient errors twice", () => {
    expect(shouldRetryQuery(0, new ApiHttpError(401, "Unauthorized"))).toBe(false)
    expect(shouldRetryQuery(0, new ApiHttpError(403, "Forbidden", ACCESS_NOT_GRANTED_CODE))).toBe(false)
    expect(shouldRetryQuery(0, new ScopeStaleResponseError())).toBe(false)
    expect(shouldRetryQuery(1, new ScopeStaleResponseError())).toBe(false)
    expect(shouldRetryQuery(0, new Error("network"))).toBe(true)
    expect(shouldRetryQuery(1, new Error("network"))).toBe(true)
    expect(shouldRetryQuery(2, new Error("network"))).toBe(false)
  })

  it("discards response body when scope eviction occurs while reading the body (real QueryClient)", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: shouldRetryQuery, retryDelay: 0, gcTime: Infinity } },
    })
    const guard = createScopeGuard(queryClient)
    const fp1 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    let releaseBody!: (val: unknown) => void
    let bodyReadStarted = false
    let alertsCalls = 0
    let revokeEvents = false

    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === "/api/alerts") {
        alertsCalls += 1
        if (alertsCalls === 1) {
          return makeResponse([{ id: "initial-alert" }], { scope: fp1 })
        }
        // Second call: header passes under fp1, body resolution pauses until after eviction.
        const headers = new Headers({ [SCOPE_FINGERPRINT_HEADER]: fp1 })
        return {
          ok: true,
          status: 200,
          statusText: "OK",
          headers,
          json: () => {
            bodyReadStarted = true
            return new Promise((resolve) => {
              releaseBody = resolve
            })
          },
        } as unknown as Response
      }
      if (revokeEvents) {
        return makeResponse({ error: "Forbidden", code: ACCESS_NOT_GRANTED_CODE }, { status: 403 })
      }
      return makeResponse([{ id: "ok" }], { scope: fp1 })
    })
    vi.stubGlobal("fetch", fetchMock)

    const alertsObs = new QueryObserver(queryClient, {
      queryKey: ["alerts"],
      queryFn: () => guard.fetchJson("/api/alerts"),
    })
    const unsubAlerts = alertsObs.subscribe(() => {})
    await waitUntil(() => alertsObs.getCurrentResult().status === "success")
    expect(queryClient.getQueryData(["alerts"])).toEqual([{ id: "initial-alert" }])

    // Start refetch: header is checked under fp1, then pauses inside res.json()
    const refetchPromise = alertsObs.refetch()
    await waitUntil(() => bodyReadStarted)

    // Meanwhile, /api/events gets 403 ACCESS_NOT_GRANTED and evicts cache (epoch bumps, fp -> null)
    revokeEvents = true
    await expect(guard.fetchJson("/api/events")).rejects.toBeInstanceOf(ApiHttpError)
    expect(queryClient.getQueryData(["alerts"])).toBeUndefined()

    // Now the stale body finishes reading -> must be discarded, NOT stored in QueryClient, and NOT retried
    releaseBody([{ id: "REVOKED-SECRET" }])
    const refetchResult = await refetchPromise
    expect(refetchResult.error).toBeInstanceOf(ScopeStaleResponseError)
    expect(queryClient.getQueryData(["alerts"])).toBeUndefined()
    expect(alertsObs.getCurrentResult().data).toBeUndefined()
    expect(alertsCalls).toBe(2)

    unsubAlerts()
    queryClient.clear()
  })

  it("discards response body via both fetchJson and raw fetch().json() when scope changes to fp2 during body read", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: shouldRetryQuery, retryDelay: 0, gcTime: Infinity } },
    })
    const guard = createScopeGuard(queryClient)
    const fp1 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    const fp2 = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

    let releaseBody!: (val: unknown) => void
    let bodyReadStarted = false
    let alertsCalls = 0
    let serverScope = fp1

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url === "/api/alerts") {
          alertsCalls += 1
          if (alertsCalls === 1) {
            return makeResponse([{ id: "initial-alert" }], { scope: fp1 })
          }
          return {
            ok: true,
            status: 200,
            statusText: "OK",
            headers: new Headers({ [SCOPE_FINGERPRINT_HEADER]: fp1 }),
            json: () => {
              bodyReadStarted = true
              return new Promise((resolve) => {
                releaseBody = resolve
              })
            },
          } as unknown as Response
        }
        return makeResponse([{ id: "new-event" }], { scope: serverScope })
      }),
    )

    const alertsObs = new QueryObserver(queryClient, {
      queryKey: ["alerts"],
      queryFn: () => guard.fetchJson("/api/alerts"),
    })
    const unsubAlerts = alertsObs.subscribe(() => {})
    await waitUntil(() => alertsObs.getCurrentResult().status === "success")

    const refetchPromise = alertsObs.refetch()
    await waitUntil(() => bodyReadStarted)

    // Scope rotates to fp2 while alerts is awaiting res.json()
    serverScope = fp2
    await guard.fetchJson("/api/events")
    expect(guard.getLastFingerprint()).toBe(fp2)

    releaseBody([{ id: "OLD-SCOPE-DATA" }])
    const refetchResult = await refetchPromise
    expect(refetchResult.error).toBeInstanceOf(ScopeStaleResponseError)
    expect(queryClient.getQueryData(["alerts"])).toBeUndefined()

    // Also verify raw guard.fetch().then(r => r.json()) rejects when scope changes during body read
    let releaseRawBody!: (val: unknown) => void
    let rawBodyStarted = false
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input) === "/api/traces") {
          const res = new Response(null, { status: 200, headers: { [SCOPE_FINGERPRINT_HEADER]: fp2 } })
          res.json = () => {
            rawBodyStarted = true
            return new Promise((resolve) => {
              releaseRawBody = resolve
            })
          }
          return res
        }
        return makeResponse({ error: "Forbidden", code: ACCESS_NOT_GRANTED_CODE }, { status: 403 })
      }),
    )
    const rawJsonPromise = guard.fetch("/api/traces").then((r) => r.json())
    await waitUntil(() => rawBodyStarted)
    await expect(guard.fetchJson("/api/events")).rejects.toBeInstanceOf(ApiHttpError)
    releaseRawBody([{ id: "STALE-TRACE" }])
    await expect(rawJsonPromise).rejects.toBeInstanceOf(ScopeStaleResponseError)

    unsubAlerts()
    queryClient.clear()
  })

  it("does not roll fingerprint back when an older concurrent first request resolves late (real QueryClient)", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: shouldRetryQuery, retryDelay: 0, gcTime: Infinity } },
    })
    const guard = createScopeGuard(queryClient)
    const fpOld = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    const fpNew = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

    let releaseOldAlerts!: (res: Response) => void
    const oldAlertsPromise = new Promise<Response>((resolve) => {
      releaseOldAlerts = resolve
    })

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url === "/api/alerts") {
          return oldAlertsPromise
        }
        return makeResponse([{ id: "new-scope-event" }], { scope: fpNew })
      }),
    )

    // Both queries start concurrently before any fingerprint has been established.
    const alertsObs = new QueryObserver(queryClient, {
      queryKey: ["alerts"],
      queryFn: () => guard.fetchJson("/api/alerts"),
    })
    const eventsObs = new QueryObserver(queryClient, {
      queryKey: ["events"],
      queryFn: () => guard.fetchJson("/api/events"),
    })
    const unsubAlerts = alertsObs.subscribe(() => {})
    const unsubEvents = eventsObs.subscribe(() => {})

    // New scope response arrives first and establishes fpNew.
    await waitUntil(() => eventsObs.getCurrentResult().status === "success")
    expect(guard.getLastFingerprint()).toBe(fpNew)
    expect(queryClient.getQueryData(["events"])).toEqual([{ id: "new-scope-event" }])

    // Late first response from old scope resolves -> must be discarded, never rolling back to fpOld.
    releaseOldAlerts(makeResponse([{ id: "REVOKED-OLD-ALERT" }], { scope: fpOld }))
    await waitUntil(() => alertsObs.getCurrentResult().isError)

    expect(guard.getLastFingerprint()).toBe(fpNew)
    expect(queryClient.getQueryData(["events"])).toEqual([{ id: "new-scope-event" }])
    expect(queryClient.getQueryData(["alerts"])).toBeUndefined()
    expect(alertsObs.getCurrentResult().error).toBeInstanceOf(ScopeStaleResponseError)

    unsubAlerts()
    unsubEvents()
    queryClient.clear()
  })

  it("evicts only tenant-scoped queries and leaves settings and non-tenant queries intact (real QueryClient)", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity } },
    })
    const guard = createScopeGuard(queryClient)
    let currentScope = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    let settingsUsersFetches = 0

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url === "/api/settings/users") {
          settingsUsersFetches += 1
          return makeResponse([{ username: "alice" }])
        }
        if (url === "/api/settings/groups") {
          return makeResponse([{ name: "admins" }])
        }
        if (url === "/api/alerts") {
          return makeResponse(
            currentScope === "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" ? [{ id: "team-b-alert" }] : [{ id: "team-a-alert" }],
            { scope: currentScope },
          )
        }
        return makeResponse([{ id: "tenant-item" }], { scope: currentScope })
      }),
    )

    // Populate non-tenant inactive & active settings queries, plus explicit opt-out query
    await queryClient.fetchQuery({
      queryKey: ["settings-groups"],
      queryFn: () => fetch("/api/settings/groups").then((r) => r.json()),
    })
    await queryClient.fetchQuery({
      queryKey: ["custom-global"],
      meta: { tenantScoped: false },
      queryFn: () => fetch("/api/settings/groups").then((r) => r.json()),
    })
    const settingsUsersObs = new QueryObserver(queryClient, {
      queryKey: ["settings-users"],
      queryFn: () => fetch("/api/settings/users").then((r) => r.json()),
    })
    const unsubSettings = settingsUsersObs.subscribe(() => {})

    // Populate tenant-scoped queries (default list, meta opt-in, prefix)
    await queryClient.fetchQuery({
      queryKey: ["catalog"],
      queryFn: () => guard.fetchJson("/api/catalog"),
    })
    await queryClient.fetchQuery({
      queryKey: ["custom-widget"],
      meta: { tenantScoped: true },
      queryFn: () => guard.fetchJson("/api/catalog"),
    })
    await queryClient.fetchQuery({
      queryKey: ["tenant", "summary"],
      queryFn: () => guard.fetchJson("/api/catalog"),
    })
    const alertsObs = new QueryObserver(queryClient, {
      queryKey: ["alerts"],
      queryFn: () => guard.fetchJson("/api/alerts"),
    })
    const unsubAlerts = alertsObs.subscribe(() => {})

    await waitUntil(
      () =>
        settingsUsersObs.getCurrentResult().status === "success" &&
        alertsObs.getCurrentResult().status === "success",
    )
    expect(settingsUsersFetches).toBe(1)

    // Scope rotates -> only tenant-scoped queries are evicted/refetched
    currentScope = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    await alertsObs.refetch()

    expect(queryClient.getQueryData(["settings-users"])).toEqual([{ username: "alice" }])
    expect(queryClient.getQueryData(["settings-groups"])).toEqual([{ name: "admins" }])
    expect(queryClient.getQueryData(["custom-global"])).toEqual([{ name: "admins" }])
    expect(settingsUsersFetches).toBe(1)

    expect(queryClient.getQueryCache().find({ queryKey: ["catalog"] })).toBeUndefined()
    expect(queryClient.getQueryCache().find({ queryKey: ["custom-widget"] })).toBeUndefined()
    expect(queryClient.getQueryCache().find({ queryKey: ["tenant", "summary"] })).toBeUndefined()
    expect(alertsObs.getCurrentResult().data).toEqual([{ id: "team-a-alert" }])

    unsubSettings()
    unsubAlerts()
    queryClient.clear()
  })
})
