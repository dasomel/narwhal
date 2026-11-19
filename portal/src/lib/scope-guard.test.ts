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
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    environmentManager.setIsServer(prevIsServer)
  })

  it("cancels in-flight tenant queries via AbortSignal, evicts cache, resets detail views, and refetches active queries on scope change", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: shouldRetryQuery, retryDelay: 0, gcTime: Infinity } },
    })
    const guard = createScopeGuard(queryClient)
    const selected = createScopedState<string | null>(null, guard)
    const unsubSelected = selected.mount()

    let currentScope = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    const seenSignals: Array<AbortSignal | undefined> = []

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        seenSignals.push(init?.signal ?? undefined)
        const url = String(input)
        if (url === "/api/alerts") {
          return makeResponse(
            currentScope === "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
              ? [{ alertname: "TeamBAlert" }]
              : [{ alertname: "TeamAAlert" }],
            { scope: currentScope },
          )
        }
        if (url === "/api/events") {
          return makeResponse(
            currentScope === "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
              ? [{ id: "e-team-b" }]
              : [{ id: "e-team-a" }],
            { scope: currentScope },
          )
        }
        return makeResponse([], { scope: currentScope })
      }),
    )

    await queryClient.fetchQuery({
      queryKey: ["catalog"],
      queryFn: ({ signal }) => guard.fetchJson("/api/catalog", { signal }),
    })
    expect(queryClient.getQueryData(["catalog"])).toEqual([])

    const alertsObs = new QueryObserver(queryClient, {
      queryKey: ["alerts"],
      queryFn: ({ signal }) => guard.fetchJson("/api/alerts", { signal }),
    })
    const eventsObs = new QueryObserver(queryClient, {
      queryKey: ["events"],
      queryFn: ({ signal }) => guard.fetchJson("/api/events", { signal }),
    })
    const unsubAlerts = alertsObs.subscribe(() => {})
    const unsubEvents = eventsObs.subscribe(() => {})

    await waitUntil(
      () =>
        alertsObs.getCurrentResult().status === "success" &&
        eventsObs.getCurrentResult().status === "success",
    )
    expect(seenSignals.every((s) => s instanceof AbortSignal)).toBe(true)

    selected.set("TeamBAlert")
    expect(selected.get()).toBe("TeamBAlert")

    // Server rotates scope -> refetch detects new fingerprint, evicts, and refetches both active observers.
    currentScope = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    await alertsObs.refetch()

    await waitUntil(
      () =>
        alertsObs.getCurrentResult().status === "success" &&
        eventsObs.getCurrentResult().status === "success" &&
        JSON.stringify(alertsObs.getCurrentResult().data) === JSON.stringify([{ alertname: "TeamAAlert" }]) &&
        JSON.stringify(eventsObs.getCurrentResult().data) === JSON.stringify([{ id: "e-team-a" }]),
    )

    expect(queryClient.getQueryCache().find({ queryKey: ["catalog"] })).toBeUndefined()
    expect(selected.get()).toBeNull()
    expect(guard.getLastFingerprint()).toBe("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data))).not.toContain(
      "team-b",
    )

    unsubAlerts()
    unsubEvents()
    unsubSelected()
    queryClient.clear()
  })

  it("discards a header-checked response whose body completes after a scope change, and the discarded query ends in success after refetch (real QueryClient)", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: shouldRetryQuery, retryDelay: 0, gcTime: Infinity } },
    })
    const guard = createScopeGuard(queryClient)
    const fpOld = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    const fpNew = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

    let releaseBody!: (val: unknown) => void
    let bodyReadStarted = false
    let alertsSignalOnInFlight: AbortSignal | undefined
    let alertsCalls = 0
    let serverScope = fpOld

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url === "/api/alerts") {
          alertsCalls += 1
          if (alertsCalls === 1) {
            return makeResponse([{ id: "initial-old" }], { scope: fpOld })
          }
          if (alertsCalls === 2) {
            alertsSignalOnInFlight = init?.signal ?? undefined
            // Header passes under fpOld, then body resolves AFTER scope changed to fpNew.
            return {
              ok: true,
              status: 200,
              statusText: "OK",
              headers: new Headers({ [SCOPE_FINGERPRINT_HEADER]: fpOld }),
              json: () => {
                bodyReadStarted = true
                return new Promise((resolve) => {
                  releaseBody = resolve
                })
              },
            } as unknown as Response
          }
          return makeResponse([{ id: "fresh-new-alert" }], { scope: fpNew })
        }
        return makeResponse([{ id: "fresh-new-event" }], { scope: serverScope })
      }),
    )

    const alertsObs = new QueryObserver(queryClient, {
      queryKey: ["alerts"],
      queryFn: ({ signal }) => guard.fetchJson("/api/alerts", { signal }),
    })
    const eventsObs = new QueryObserver(queryClient, {
      queryKey: ["events"],
      queryFn: ({ signal }) => guard.fetchJson("/api/events", { signal }),
    })
    const unsubAlerts = alertsObs.subscribe(() => {})
    const unsubEvents = eventsObs.subscribe(() => {})

    await waitUntil(
      () =>
        alertsObs.getCurrentResult().status === "success" &&
        eventsObs.getCurrentResult().status === "success",
    )

    // Start alerts refetch under fpOld; pause while awaiting res.json().
    void alertsObs.refetch()
    await waitUntil(() => bodyReadStarted)

    // Scope changes to fpNew and events refetch detects it -> cancels alerts, resets & invalidates active queries.
    serverScope = fpNew
    await eventsObs.refetch()

    // In-flight alerts fetch's AbortSignal must be aborted by cancelQueries.
    expect(alertsSignalOnInFlight?.aborted).toBe(true)

    // Now the stale header-checked body completes with old-scope secret data.
    releaseBody([{ id: "REVOKED-SECRET" }])

    // Replacement refetch must bring alerts and events to success with fpNew data (not stuck in error).
    await waitUntil(
      () =>
        alertsObs.getCurrentResult().status === "success" &&
        eventsObs.getCurrentResult().status === "success" &&
        JSON.stringify(alertsObs.getCurrentResult().data) === JSON.stringify([{ id: "fresh-new-alert" }]),
    )

    expect(guard.getLastFingerprint()).toBe(fpNew)
    expect(queryClient.getQueryData(["alerts"])).toEqual([{ id: "fresh-new-alert" }])
    expect(queryClient.getQueryData(["events"])).toEqual([{ id: "fresh-new-event" }])
    expect(alertsObs.getCurrentResult().error).toBeNull()
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data))).not.toContain(
      "REVOKED-SECRET",
    )

    // Also verify raw guard.fetch().then(r => r.json()) rejects when scope changes during body read.
    let releaseRawBody!: (val: unknown) => void
    let rawBodyStarted = false
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input) === "/api/traces") {
          const res = new Response(null, { status: 200, headers: { [SCOPE_FINGERPRINT_HEADER]: fpNew } })
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
    unsubEvents()
    queryClient.clear()
  })

  it("converges to new-scope data and new fingerprint when old-scope response arrives first and new-scope arrives second (real QueryClient)", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: shouldRetryQuery, retryDelay: 0, gcTime: Infinity } },
    })
    const guard = createScopeGuard(queryClient)
    const fpOld = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    const fpNew = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

    let releaseOldAlerts!: (res: Response) => void
    let releaseNewEvents!: (res: Response) => void
    const oldAlertsPromise = new Promise<Response>((resolve) => {
      releaseOldAlerts = resolve
    })
    const newEventsPromise = new Promise<Response>((resolve) => {
      releaseNewEvents = resolve
    })

    let alertsCalls = 0
    let eventsCalls = 0
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url === "/api/alerts") {
          alertsCalls += 1
          if (alertsCalls === 1) return oldAlertsPromise
          return makeResponse([{ id: "new-scope-alert" }], { scope: fpNew })
        }
        eventsCalls += 1
        if (eventsCalls === 1) return newEventsPromise
        return makeResponse([{ id: "new-scope-event" }], { scope: fpNew })
      }),
    )

    // Both requests start concurrently before any fingerprint is accepted (epoch = 0, lastFingerprint = null).
    const alertsObs = new QueryObserver(queryClient, {
      queryKey: ["alerts"],
      queryFn: ({ signal }) => guard.fetchJson("/api/alerts", { signal }),
    })
    const eventsObs = new QueryObserver(queryClient, {
      queryKey: ["events"],
      queryFn: ({ signal }) => guard.fetchJson("/api/events", { signal }),
    })
    const unsubAlerts = alertsObs.subscribe(() => {})
    const unsubEvents = eventsObs.subscribe(() => {})

    await waitUntil(() => alertsCalls === 1 && eventsCalls === 1)

    // Old-scope response arrives FIRST -> accepted temporarily as first fingerprint.
    releaseOldAlerts(makeResponse([{ id: "REVOKED-OLD-ALERT" }], { scope: fpOld }))
    await waitUntil(() => guard.getLastFingerprint() === fpOld)

    // New-scope response arrives SECOND -> treated as scope change -> evicts old data and refetches both.
    releaseNewEvents(makeResponse([{ id: "first-new-event" }], { scope: fpNew }))

    await waitUntil(
      () =>
        alertsObs.getCurrentResult().status === "success" &&
        eventsObs.getCurrentResult().status === "success" &&
        JSON.stringify(alertsObs.getCurrentResult().data) === JSON.stringify([{ id: "new-scope-alert" }]),
    )

    expect(guard.getLastFingerprint()).toBe(fpNew)
    expect(queryClient.getQueryData(["alerts"])).toEqual([{ id: "new-scope-alert" }])
    expect(queryClient.getQueryData(["events"])).toEqual([{ id: "new-scope-event" }])
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data))).not.toContain(
      "REVOKED-OLD-ALERT",
    )

    unsubAlerts()
    unsubEvents()
    queryClient.clear()
  })

  it("converges to new-scope data and discards late old-scope response when new-scope arrives first (real QueryClient)", async () => {
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

    let alertsCalls = 0
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url === "/api/alerts") {
          alertsCalls += 1
          if (alertsCalls === 1) return oldAlertsPromise
          return makeResponse([{ id: "new-scope-alert" }], { scope: fpNew })
        }
        return makeResponse([{ id: "new-scope-event" }], { scope: fpNew })
      }),
    )

    const alertsObs = new QueryObserver(queryClient, {
      queryKey: ["alerts"],
      queryFn: ({ signal }) => guard.fetchJson("/api/alerts", { signal }),
    })
    const eventsObs = new QueryObserver(queryClient, {
      queryKey: ["events"],
      queryFn: ({ signal }) => guard.fetchJson("/api/events", { signal }),
    })
    const unsubAlerts = alertsObs.subscribe(() => {})
    const unsubEvents = eventsObs.subscribe(() => {})

    // New-scope response arrives first and establishes fpNew.
    await waitUntil(() => eventsObs.getCurrentResult().status === "success")
    expect(guard.getLastFingerprint()).toBe(fpNew)

    // Late old-scope response resolves -> never stores old data, triggers eviction+refetch, and ends in success with fpNew.
    releaseOldAlerts(makeResponse([{ id: "REVOKED-OLD-ALERT" }], { scope: fpOld }))

    await waitUntil(
      () =>
        alertsObs.getCurrentResult().status === "success" &&
        eventsObs.getCurrentResult().status === "success" &&
        guard.getLastFingerprint() === fpNew &&
        JSON.stringify(alertsObs.getCurrentResult().data) === JSON.stringify([{ id: "new-scope-alert" }]),
    )

    expect(queryClient.getQueryData(["alerts"])).toEqual([{ id: "new-scope-alert" }])
    expect(queryClient.getQueryData(["events"])).toEqual([{ id: "new-scope-event" }])
    expect(alertsObs.getCurrentResult().error).toBeNull()
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data))).not.toContain(
      "REVOKED-OLD-ALERT",
    )

    unsubAlerts()
    unsubEvents()
    queryClient.clear()
  })

  it("evicts only tenant-scoped queries and leaves settings and non-tenant queries intact (real QueryClient)", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: shouldRetryQuery, retryDelay: 0, gcTime: Infinity } },
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
            currentScope === "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
              ? [{ id: "team-b-alert" }]
              : [{ id: "team-a-alert" }],
            { scope: currentScope },
          )
        }
        return makeResponse([{ id: "tenant-item" }], { scope: currentScope })
      }),
    )

    // Populate non-tenant inactive & active settings queries, plus explicit opt-out query.
    await queryClient.fetchQuery({
      queryKey: ["settings-groups"],
      queryFn: ({ signal }) => fetch("/api/settings/groups", { signal }).then((r) => r.json()),
    })
    await queryClient.fetchQuery({
      queryKey: ["custom-global"],
      meta: { tenantScoped: false },
      queryFn: ({ signal }) => fetch("/api/settings/groups", { signal }).then((r) => r.json()),
    })
    const settingsUsersObs = new QueryObserver(queryClient, {
      queryKey: ["settings-users"],
      queryFn: ({ signal }) => fetch("/api/settings/users", { signal }).then((r) => r.json()),
    })
    const unsubSettings = settingsUsersObs.subscribe(() => {})

    // Populate tenant-scoped queries (default list, meta opt-in, prefix, active observer).
    await queryClient.fetchQuery({
      queryKey: ["catalog"],
      queryFn: ({ signal }) => guard.fetchJson("/api/catalog", { signal }),
    })
    await queryClient.fetchQuery({
      queryKey: ["custom-widget"],
      meta: { tenantScoped: true },
      queryFn: ({ signal }) => guard.fetchJson("/api/catalog", { signal }),
    })
    await queryClient.fetchQuery({
      queryKey: ["tenant", "summary"],
      queryFn: ({ signal }) => guard.fetchJson("/api/catalog", { signal }),
    })
    const alertsObs = new QueryObserver(queryClient, {
      queryKey: ["alerts"],
      queryFn: ({ signal }) => guard.fetchJson("/api/alerts", { signal }),
    })
    const unsubAlerts = alertsObs.subscribe(() => {})

    await waitUntil(
      () =>
        settingsUsersObs.getCurrentResult().status === "success" &&
        alertsObs.getCurrentResult().status === "success",
    )
    expect(settingsUsersFetches).toBe(1)

    // Scope rotates -> only tenant-scoped queries are evicted/refetched; settings queries survive untouched.
    currentScope = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    await alertsObs.refetch()

    await waitUntil(
      () =>
        alertsObs.getCurrentResult().status === "success" &&
        JSON.stringify(alertsObs.getCurrentResult().data) === JSON.stringify([{ id: "team-a-alert" }]),
    )

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

  it("clears all tenant queries and resets scoped state on 403 ACCESS_NOT_GRANTED and 401", async () => {
    for (const status of [403, 401] as const) {
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: shouldRetryQuery, retryDelay: 0, gcTime: Infinity } },
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
        queryFn: ({ signal }) => guard.fetchJson("/api/alerts", { signal }),
      })
      const eventsObs = new QueryObserver(queryClient, {
        queryKey: ["events"],
        queryFn: ({ signal }) => guard.fetchJson("/api/events", { signal }),
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

  it("aborts an in-flight body read on 403 ACCESS_NOT_GRANTED and never stores revoked data", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: shouldRetryQuery, retryDelay: 0, gcTime: Infinity } },
    })
    const guard = createScopeGuard(queryClient)
    const fp1 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    let releaseBody!: (val: unknown) => void
    let bodyReadStarted = false
    let alertsSignalOnInFlight: AbortSignal | undefined
    let alertsCalls = 0
    let revokeEvents = false

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url === "/api/alerts") {
          alertsCalls += 1
          if (alertsCalls === 1) {
            return makeResponse([{ id: "initial-alert" }], { scope: fp1 })
          }
          alertsSignalOnInFlight = init?.signal ?? undefined
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
        if (revokeEvents) {
          return makeResponse({ error: "Forbidden", code: ACCESS_NOT_GRANTED_CODE }, { status: 403 })
        }
        return makeResponse([{ id: "ok" }], { scope: fp1 })
      }),
    )

    const alertsObs = new QueryObserver(queryClient, {
      queryKey: ["alerts"],
      queryFn: ({ signal }) => guard.fetchJson("/api/alerts", { signal }),
    })
    const unsubAlerts = alertsObs.subscribe(() => {})
    await waitUntil(() => alertsObs.getCurrentResult().status === "success")

    const refetchPromise = alertsObs.refetch()
    await waitUntil(() => bodyReadStarted)

    revokeEvents = true
    await expect(guard.fetchJson("/api/events")).rejects.toBeInstanceOf(ApiHttpError)
    expect(queryClient.getQueryData(["alerts"])).toBeUndefined()
    await waitUntil(() => Boolean(alertsSignalOnInFlight?.aborted))

    releaseBody([{ id: "REVOKED-SECRET" }])
    await refetchPromise
    expect(queryClient.getQueryData(["alerts"])).toBeUndefined()
    expect(alertsObs.getCurrentResult().data).toBeUndefined()
    expect(alertsCalls).toBe(2)

    unsubAlerts()
    queryClient.clear()
  })

  it("does not clear unrelated cached queries on per-resource 403 or 500 with unchanged/absent fingerprint", async () => {
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
        if (url === "/api/events") {
          return makeResponse({ error: "Internal Server Error" }, { status: 500 })
        }
        return makeResponse({ error: "Forbidden" }, { status: 403, scope: fp })
      }),
    )

    const alertsObs = new QueryObserver(queryClient, {
      queryKey: ["alerts"],
      queryFn: ({ signal }) => guard.fetchJson("/api/alerts", { signal }),
    })
    const unsub = alertsObs.subscribe(() => {})
    await waitUntil(() => alertsObs.getCurrentResult().status === "success")

    await expect(guard.fetchJson("/api/k8s/pods?namespace=other-team")).rejects.toMatchObject({
      status: 403,
    })
    await expect(guard.fetchJson("/api/events")).rejects.toMatchObject({ status: 500 })

    expect(queryClient.getQueryData(["alerts"])).toEqual([{ id: "team-a-alert" }])
    expect(guard.getLastFingerprint()).toBe(fp)

    unsub()
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
})
