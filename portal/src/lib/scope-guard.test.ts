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

  it("never retries 401/403, retries ScopeStaleResponseError once, and retries transient errors twice", () => {
    expect(shouldRetryQuery(0, new ApiHttpError(401, "Unauthorized"))).toBe(false)
    expect(shouldRetryQuery(0, new ApiHttpError(403, "Forbidden", ACCESS_NOT_GRANTED_CODE))).toBe(false)
    expect(shouldRetryQuery(0, new ScopeStaleResponseError())).toBe(true)
    expect(shouldRetryQuery(1, new ScopeStaleResponseError())).toBe(false)
    expect(shouldRetryQuery(0, new Error("network"))).toBe(true)
    expect(shouldRetryQuery(1, new Error("network"))).toBe(true)
    expect(shouldRetryQuery(2, new Error("network"))).toBe(false)
  })
})
