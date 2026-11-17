import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

const sessionState = vi.hoisted(() => ({ role: "cluster-admin" }))

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-query")>()
  return {
    ...actual,
    useQuery: vi.fn(),
    useMutation: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useQueryClient: () => ({ invalidateQueries: vi.fn() }),
  }
})

vi.mock("next-auth/react", () => ({
  useSession: () => ({ data: { user: { role: sessionState.role } } }),
}))

import { QueryClient, QueryObserver, environmentManager, useQuery, type UseQueryOptions } from "@tanstack/react-query"
import { LocaleProvider } from "@/lib/i18n-client"
import type { Certificate } from "@/lib/k8s-client"
import {
  CERT_PENDING_POLL_INTERVAL_MS,
  CERT_PENDING_TIMEOUT_MS,
  CERT_POLL_INTERVAL_MS,
  CERT_POLL_TIMEOUT_MS,
  CERT_RENEW_TIMEOUT_MS,
  bindCertRenewToQueryClient,
} from "./certs-renew"
import {
  CertsTable,
  createCertRenewController,
  formatCertTimestamp,
  hasCertificateRenewed,
  type CertRenewController,
} from "./certs-table"

const T1 = "2026-11-17T00:00:00.000Z"
const T2 = "2026-11-18T00:00:04.000Z"

function makeCert(overrides: Partial<Certificate> = {}): Certificate {
  return {
    name: "openbao-tls",
    namespace: "openbao",
    ready: true,
    issuing: false,
    revision: 1,
    notBefore: T1,
    notAfter: "2027-02-15T00:00:00.000Z",
    dnsNames: ["openbao.narwhal.local"],
    issuer: "narwhal-ca",
    renewalTime: "2027-01-16T00:00:00.000Z",
    ...overrides,
  }
}

function mockCertQuery(certs: Certificate[] | undefined, flags: { isLoading?: boolean; isError?: boolean } = {}) {
  vi.mocked(useQuery).mockReturnValue({
    data: certs,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
    ...flags,
  } as never)
}

function renderTable(
  certs: Certificate[] | undefined,
  locale: "ko" | "en" = "en",
  controller?: CertRenewController,
  flags: { isLoading?: boolean; isError?: boolean } = {},
) {
  mockCertQuery(certs, flags)
  return renderToStaticMarkup(
    <LocaleProvider locale={locale}>
      <CertsTable controller={controller} />
    </LocaleProvider>,
  )
}

function lastRefetchInterval(): unknown {
  const calls = vi.mocked(useQuery).mock.calls
  const lastArg = calls[calls.length - 1]?.[0] as { refetchInterval?: unknown } | undefined
  return lastArg?.refetchInterval
}

function deferred<T = unknown>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function jsonResponse(body: unknown, status = 200, statusText = "OK"): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText,
    json: async () => body,
  } as Response
}

/**
 * Awaits a condition deterministically under fake timers without depending on
 * a fixed microtask round count (which varies across Node/V8 versions).
 */
async function waitUntil(condition: () => boolean, maxTurns = 50): Promise<void> {
  for (let i = 0; i < maxTurns; i++) {
    if (condition()) return
    await vi.advanceTimersByTimeAsync(0)
  }
  throw new Error("Condition was not satisfied within async turn budget")
}

interface FetchCallRecord {
  url: string
  method: string
  signal?: AbortSignal
  body?: unknown
}

/**
 * Drives the real CertsTable query options, QueryClient/QueryObserver,
 * bindCertRenewToQueryClient, and global fetch without mocking out queryFn or reconcile.
 */
function mountCertsFlow(
  handler: (call: FetchCallRecord) => Promise<Response> | Response,
  locale: "ko" | "en" = "en",
) {
  const calls: FetchCallRecord[] = []
  const fetchSpy = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url
    const method = init?.method ?? "GET"
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined
    const record: FetchCallRecord = { url, method, signal: init?.signal ?? undefined, body }
    calls.push(record)
    return handler(record)
  })
  vi.stubGlobal("fetch", fetchSpy)

  const prevIsServer = environmentManager.isServer
  environmentManager.setIsServer(() => false)

  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  })
  const controller = createCertRenewController()
  const unbind = bindCertRenewToQueryClient(controller, queryClient)

  let capturedOptions: UseQueryOptions<Certificate[]> | null = null
  let observer: QueryObserver<Certificate[]> | null = null

  const renderWithResult = (result: {
    data?: Certificate[]
    isLoading?: boolean
    isError?: boolean
    refetch?: () => Promise<unknown>
  }) => {
    vi.mocked(useQuery).mockImplementation((opts) => {
      capturedOptions = opts as UseQueryOptions<Certificate[]>
      return {
        data: result.data,
        isLoading: result.isLoading ?? false,
        isError: result.isError ?? false,
        refetch: result.refetch ?? (async () => observer?.refetch()),
      } as never
    })
    return renderToStaticMarkup(
      <LocaleProvider locale={locale}>
        <CertsTable controller={controller} />
      </LocaleProvider>,
    )
  }

  renderWithResult({ isLoading: true })
  if (!capturedOptions) {
    throw new Error("CertsTable did not pass options to useQuery")
  }

  observer = new QueryObserver<Certificate[]>(queryClient, capturedOptions)
  const unsubObserver = observer.subscribe(() => {})
  const unsubController = controller.subscribe(() => {
    renderWithResult(observer!.getCurrentResult())
    if (capturedOptions) {
      observer!.setOptions(capturedOptions)
    }
  })

  const html = () => renderWithResult(observer!.getCurrentResult())
  const getCalls = () => calls.filter((c) => c.method === "GET" && c.url === "/api/settings/certs")
  const postCalls = () => calls.filter((c) => c.method === "POST" && c.url === "/api/settings/certs/renew")

  const dispose = () => {
    unsubController()
    unsubObserver()
    unbind()
    queryClient.clear()
    environmentManager.setIsServer(prevIsServer)
  }

  return {
    controller,
    queryClient,
    observer,
    calls,
    getCalls,
    postCalls,
    html,
    dispose,
  }
}

describe("hasCertificateRenewed", () => {
  it("returns false while issuing is true even when notBefore and revision changed", () => {
    expect(
      hasCertificateRenewed(
        { issuing: true, notBefore: T2, revision: 2 },
        { notBefore: T1, revision: 1 },
      ),
    ).toBe(false)
  })

  it("returns false when both sides are null, undefined, or empty", () => {
    expect(
      hasCertificateRenewed(
        { issuing: false, notBefore: null, revision: null },
        { notBefore: null, revision: null },
      ),
    ).toBe(false)
    expect(
      hasCertificateRenewed(
        { issuing: false, notBefore: null, revision: null },
        { notBefore: undefined, revision: undefined },
      ),
    ).toBe(false)
    expect(
      hasCertificateRenewed(
        { issuing: false, notBefore: "", revision: null },
        { notBefore: null, revision: null },
      ),
    ).toBe(false)
  })

  it("returns false when values are unchanged or regressed", () => {
    expect(
      hasCertificateRenewed(
        { issuing: false, notBefore: T1, revision: 1 },
        { notBefore: T1, revision: 1 },
      ),
    ).toBe(false)
    expect(
      hasCertificateRenewed(
        { issuing: false, notBefore: T1, revision: 1 },
        { notBefore: T1, revision: 2 },
      ),
    ).toBe(false)
    expect(
      hasCertificateRenewed(
        { issuing: false, notBefore: null, revision: 1 },
        { notBefore: T1, revision: 1 },
      ),
    ).toBe(false)
    expect(
      hasCertificateRenewed(
        { issuing: false, notBefore: null, revision: 1 },
        { notBefore: null, revision: undefined },
      ),
    ).toBe(false)
    expect(
      hasCertificateRenewed(
        { issuing: false, notBefore: T1, revision: null },
        { notBefore: T1, revision: 1 },
      ),
    ).toBe(false)
  })

  it("returns true on a non-null notBefore change or strictly greater revision", () => {
    expect(
      hasCertificateRenewed(
        { issuing: false, notBefore: T2, revision: 1 },
        { notBefore: T1, revision: 1 },
      ),
    ).toBe(true)
    expect(
      hasCertificateRenewed(
        { issuing: false, notBefore: T2, revision: null },
        { notBefore: null, revision: null },
      ),
    ).toBe(true)
    expect(
      hasCertificateRenewed(
        { issuing: false, notBefore: T1, revision: 2 },
        { notBefore: T1, revision: 1 },
      ),
    ).toBe(true)
    expect(
      hasCertificateRenewed(
        { issuing: false, notBefore: null, revision: 1 },
        { notBefore: null, revision: 0 },
      ),
    ).toBe(true)
  })
})

describe("CertsTable", () => {
  beforeEach(() => {
    sessionState.role = "cluster-admin"
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-11-18T00:00:00.000Z"))
    vi.mocked(useQuery).mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it("renders loading, error, and empty states deterministically", () => {
    expect(renderTable(undefined, "en", undefined, { isLoading: true })).toContain("Loading data.")
    expect(renderTable(undefined, "en", undefined, { isError: true })).toContain("Could not load data.")
    expect(renderTable([], "en")).toContain("No data to display.")
  })

  it.each(["en", "ko"] as const)("renders issued timestamp, revision, and role-gated action in %s", (locale) => {
    const html = renderTable([makeCert({ notBefore: T1, revision: 1 })], locale)
    expect(html).toContain(locale === "ko" ? "발급일" : "Issued")
    expect(html).toContain(formatCertTimestamp(T1, locale))
    expect(html).toContain("rev 1")
    expect(html).toContain(locale === "ko" ? "갱신" : "Renew")

    sessionState.role = "viewer"
    const viewerHtml = renderTable([makeCert({ notBefore: T1, revision: 1 })], locale)
    expect(viewerHtml).not.toContain(locale === "ko" ? "갱신" : "Renew")
  })

  it("(a)(b) drives POST -> GET /api/settings/certs refetches and flips to renewed only after GET returns updated notBefore/revision", async () => {
    const certV1 = makeCert({ notBefore: T1, revision: 1, issuing: false })
    const certIssuing = makeCert({ notBefore: T1, revision: 2, issuing: true })
    const certV2 = makeCert({ notBefore: T2, revision: 2, issuing: false })
    let serverCert = certV1

    const flow = mountCertsFlow(async ({ url, method }) => {
      if (method === "GET" && url === "/api/settings/certs") {
        return jsonResponse([serverCert])
      }
      if (method === "POST" && url === "/api/settings/certs/renew") {
        return jsonResponse({ success: true, pending: true })
      }
      throw new Error(`Unexpected fetch ${method} ${url}`)
    })

    await waitUntil(() => flow.getCalls().length === 1 && flow.observer.getCurrentResult().isSuccess)
    expect(flow.html()).toContain("Renew")
    expect(lastRefetchInterval()).toBe(false)

    await flow.controller.renew(certV1)
    expect(flow.postCalls()).toHaveLength(1)

    // (a) Invalidation triggers at least one GET /api/settings/certs after POST.
    await waitUntil(() => flow.getCalls().length >= 2)
    expect(flow.html()).toContain("Issuing…")
    expect(flow.html()).not.toContain("Renewed at")
    expect(lastRefetchInterval()).toBe(CERT_POLL_INTERVAL_MS)

    // (b) A poll where issuing=true (even with bumped revision) must NOT flip to renewed.
    serverCert = certIssuing
    const beforeIssuingPoll = flow.getCalls().length
    await vi.advanceTimersByTimeAsync(CERT_POLL_INTERVAL_MS)
    await waitUntil(() => flow.getCalls().length > beforeIssuingPoll)
    expect(flow.html()).toContain("Issuing…")
    expect(flow.html()).not.toContain("Renewed at")

    // Once GET returns issuing=false with new notBefore/revision, row flips to renewed and polling stops.
    serverCert = certV2
    const beforeRenewedPoll = flow.getCalls().length
    await vi.advanceTimersByTimeAsync(CERT_POLL_INTERVAL_MS)
    await waitUntil(() => flow.getCalls().length > beforeRenewedPoll && flow.html().includes("Renewed at"))

    const renewedHtml = flow.html()
    const formattedT2 = formatCertTimestamp(T2, "en")
    expect(renewedHtml).toContain(`Renewed at ${formattedT2}`)
    expect(renewedHtml).toContain("rev 2")
    expect(renewedHtml).not.toContain("Issuing…")
    expect(lastRefetchInterval()).toBe(false)

    const callsAfterRenewed = flow.getCalls().length
    await vi.advanceTimersByTimeAsync(CERT_POLL_INTERVAL_MS * 5)
    expect(flow.getCalls().length).toBe(callsAfterRenewed)

    flow.dispose()
  })

  it("(c) aborts a stalled POST after 15s timeout and transitions to the existing error state", async () => {
    const certV1 = makeCert({ notBefore: T1, revision: 1 })
    let postSignal: AbortSignal | undefined

    const flow = mountCertsFlow(async ({ url, method, signal }) => {
      if (method === "GET" && url === "/api/settings/certs") {
        return jsonResponse([certV1])
      }
      if (method === "POST" && url === "/api/settings/certs/renew") {
        postSignal = signal
        return new Promise<Response>((_resolve, reject) => {
          signal?.addEventListener(
            "abort",
            () => {
              const err = new Error("Aborted")
              err.name = "AbortError"
              reject(err)
            },
            { once: true },
          )
        })
      }
      throw new Error(`Unexpected fetch ${method} ${url}`)
    })

    await waitUntil(() => flow.getCalls().length === 1 && flow.observer.getCurrentResult().isSuccess)
    const getsBeforePost = flow.getCalls().length

    const inflight = flow.controller.renew(certV1)
    expect(postSignal).toBeDefined()
    expect(postSignal?.aborted).toBe(false)

    const submittingHtml = flow.html()
    expect(submittingHtml).toContain("Renewing...")
    expect(submittingHtml).toContain("disabled")

    await vi.advanceTimersByTimeAsync(CERT_RENEW_TIMEOUT_MS - 1)
    expect(postSignal?.aborted).toBe(false)
    expect(flow.html()).toContain("Renewing...")

    await vi.advanceTimersByTimeAsync(1)
    await inflight

    expect(postSignal?.aborted).toBe(true)
    const errorHtml = flow.html()
    expect(errorHtml).toContain("Renewal failed")
    expect(errorHtml).toContain('role="alert"')
    expect(flow.getCalls().length).toBe(getsBeforePost)
    expect(lastRefetchInterval()).toBe(false)
    expect(vi.getTimerCount()).toBe(0)

    flow.dispose()
  })

  it("(c-dispose) aborts an in-flight POST on dispose/unmount without updating state or scheduling timers", async () => {
    const certV1 = makeCert({ notBefore: T1, revision: 1 })
    let postSignal: AbortSignal | undefined

    const flow = mountCertsFlow(async ({ url, method, signal }) => {
      if (method === "GET" && url === "/api/settings/certs") {
        return jsonResponse([certV1])
      }
      if (method === "POST" && url === "/api/settings/certs/renew") {
        postSignal = signal
        return new Promise<Response>(() => {})
      }
      throw new Error(`Unexpected fetch ${method} ${url}`)
    })

    await waitUntil(() => flow.getCalls().length === 1 && flow.observer.getCurrentResult().isSuccess)
    const listener = vi.fn()
    flow.controller.subscribe(listener)

    const inflight = flow.controller.renew(certV1)
    listener.mockClear()
    expect(postSignal?.aborted).toBe(false)

    flow.dispose()
    await inflight

    expect(postSignal?.aborted).toBe(true)
    expect(listener).not.toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(0)
  })

  it("(d) transitions issuing -> still pending at 30s, polls at 10s up to 5m, flips pending -> renewed on late GET, and still recovers on manual refetch after 5m", async () => {
    const certV1 = makeCert({ notBefore: T1, revision: 1, issuing: false })
    const certV2 = makeCert({ notBefore: T2, revision: 2, issuing: false })
    let serverCert = certV1

    // Scenario 1: late issuance during the bounded slow-polling window (e.g. ~42s)
    const flow1 = mountCertsFlow(async ({ url, method }) => {
      if (method === "GET" && url === "/api/settings/certs") {
        return jsonResponse([serverCert])
      }
      if (method === "POST" && url === "/api/settings/certs/renew") {
        return jsonResponse({ success: true, pending: true })
      }
      throw new Error(`Unexpected fetch ${method} ${url}`)
    })

    await waitUntil(() => flow1.getCalls().length === 1 && flow1.observer.getCurrentResult().isSuccess)
    await flow1.controller.renew(certV1)
    await waitUntil(() => flow1.getCalls().length >= 2)

    // Advance through the 30s fast window while server still returns certV1.
    await vi.advanceTimersByTimeAsync(CERT_POLL_TIMEOUT_MS)
    await waitUntil(() => flow1.html().includes("Still pending — check again later"))
    expect(lastRefetchInterval()).toBe(CERT_PENDING_POLL_INTERVAL_MS)

    // Fast 2s interval must not fire while pending; slow 10s interval does.
    const callsAtPending = flow1.getCalls().length
    await vi.advanceTimersByTimeAsync(CERT_POLL_INTERVAL_MS)
    expect(flow1.getCalls().length).toBe(callsAtPending)

    // Now the cert finishes issuing at ~40s; the next 10s poll observes it and flips pending -> renewed.
    serverCert = certV2
    await vi.advanceTimersByTimeAsync(CERT_PENDING_POLL_INTERVAL_MS - CERT_POLL_INTERVAL_MS)
    await waitUntil(() => flow1.html().includes("Renewed at"))
    expect(flow1.html()).toContain(`Renewed at ${formatCertTimestamp(T2, "en")}`)
    expect(lastRefetchInterval()).toBe(false)
    flow1.dispose()

    // Scenario 2: 5-minute ceiling stops background polling, but manual/refocus refetch still recovers.
    serverCert = certV1
    const flow2 = mountCertsFlow(async ({ url, method }) => {
      if (method === "GET" && url === "/api/settings/certs") {
        return jsonResponse([serverCert])
      }
      if (method === "POST" && url === "/api/settings/certs/renew") {
        return jsonResponse({ success: true, pending: true })
      }
      throw new Error(`Unexpected fetch ${method} ${url}`)
    })

    await waitUntil(() => flow2.getCalls().length === 1 && flow2.observer.getCurrentResult().isSuccess)
    await flow2.controller.renew(certV1)
    await waitUntil(() => flow2.getCalls().length >= 2)

    await vi.advanceTimersByTimeAsync(CERT_PENDING_TIMEOUT_MS)
    await waitUntil(() => lastRefetchInterval() === false)
    expect(flow2.html()).toContain("Still pending — check again later")
    expect(vi.getTimerCount()).toBe(0)

    const callsAtFiveMin = flow2.getCalls().length
    await vi.advanceTimersByTimeAsync(60_000)
    expect(flow2.getCalls().length).toBe(callsAtFiveMin)

    // Later manual/refocus refetch after 5m observes certV2 -> flips pending -> renewed.
    serverCert = certV2
    await flow2.observer.refetch()
    await waitUntil(() => flow2.html().includes("Renewed at"))
    expect(flow2.html()).toContain(`Renewed at ${formatCertTimestamp(T2, "en")}`)
    flow2.dispose()
  })

  it("(e) continues polling after a failed GET in the window and flips to renewed on a subsequent healthy GET", async () => {
    const certV1 = makeCert({ notBefore: T1, revision: 1, issuing: false })
    const certV2 = makeCert({ notBefore: T2, revision: 2, issuing: false })
    let getAttempt = 0

    const flow = mountCertsFlow(async ({ url, method }) => {
      if (method === "GET" && url === "/api/settings/certs") {
        getAttempt += 1
        if (getAttempt === 1) return jsonResponse([certV1])
        if (getAttempt === 2) {
          return jsonResponse({ error: "transient" }, 500, "Internal Server Error")
        }
        if (getAttempt === 3) {
          throw new TypeError("network error")
        }
        return jsonResponse([certV2])
      }
      if (method === "POST" && url === "/api/settings/certs/renew") {
        return jsonResponse({ success: true, pending: true })
      }
      throw new Error(`Unexpected fetch ${method} ${url}`)
    })

    await waitUntil(() => flow.getCalls().length === 1 && flow.observer.getCurrentResult().isSuccess)
    await flow.controller.renew(certV1)

    // Immediate invalidation GET fails with 500; table must remain visible in Issuing… and keep polling.
    await waitUntil(() => flow.getCalls().length === 2 && flow.observer.getCurrentResult().isError)
    const duringFirstError = flow.html()
    expect(duringFirstError).toContain("Issuing…")
    expect(duringFirstError).not.toContain("Could not load data.")
    expect(lastRefetchInterval()).toBe(CERT_POLL_INTERVAL_MS)

    // Next 2s poll also fails with 500; polling must still continue.
    await vi.advanceTimersByTimeAsync(CERT_POLL_INTERVAL_MS)
    await waitUntil(() => flow.getCalls().length === 3)
    expect(flow.html()).toContain("Issuing…")
    expect(lastRefetchInterval()).toBe(CERT_POLL_INTERVAL_MS)

    // Subsequent 2s poll succeeds with certV2 and flips to renewed.
    await vi.advanceTimersByTimeAsync(CERT_POLL_INTERVAL_MS)
    await waitUntil(() => flow.getCalls().length === 4 && flow.html().includes("Renewed at"))
    expect(flow.html()).toContain(`Renewed at ${formatCertTimestamp(T2, "en")}`)
    expect(lastRefetchInterval()).toBe(false)

    flow.dispose()
  })

  it("shows error badge and leaves no active timers when renew POST fails", async () => {
    const certV1 = makeCert({ notBefore: T1, revision: 1 })
    const flow = mountCertsFlow(async ({ url, method }) => {
      if (method === "GET" && url === "/api/settings/certs") return jsonResponse([certV1])
      if (method === "POST" && url === "/api/settings/certs/renew") {
        return jsonResponse({ error: "boom" }, 500, "Internal Server Error")
      }
      throw new Error(`Unexpected fetch ${method} ${url}`)
    })

    await waitUntil(() => flow.getCalls().length === 1 && flow.observer.getCurrentResult().isSuccess)
    await flow.controller.renew(certV1)

    const errorHtml = flow.html()
    expect(errorHtml).toContain("Renewal failed")
    expect(errorHtml).toContain('role="alert"')
    expect(lastRefetchInterval()).toBe(false)
    expect(vi.getTimerCount()).toBe(0)

    flow.dispose()
  })

  it("keeps two concurrent row renewals independent with no cross-talk", async () => {
    const certA = makeCert({ name: "openbao-tls", namespace: "openbao", notBefore: T1, revision: 1 })
    const certB = makeCert({ name: "argocd-tls", namespace: "argocd", notBefore: T1, revision: 1 })
    let serverCerts = [certA, certB]

    const flow = mountCertsFlow(async ({ url, method }) => {
      if (method === "GET" && url === "/api/settings/certs") return jsonResponse(serverCerts)
      if (method === "POST" && url === "/api/settings/certs/renew") {
        return jsonResponse({ success: true, pending: true })
      }
      throw new Error(`Unexpected fetch ${method} ${url}`)
    })

    await waitUntil(() => flow.getCalls().length === 1 && flow.observer.getCurrentResult().isSuccess)

    await flow.controller.renew(certA)
    await vi.advanceTimersByTimeAsync(5_000)
    await flow.controller.renew(certB)

    expect(flow.html().split("Issuing…")).toHaveLength(3)

    const certARenewed = { ...certA, notBefore: T2, revision: 2, issuing: false }
    serverCerts = [certARenewed, { ...certB, issuing: true }]
    await vi.advanceTimersByTimeAsync(CERT_POLL_INTERVAL_MS)
    await waitUntil(() => flow.html().includes("Renewed at"))

    const partialHtml = flow.html()
    expect(partialHtml).toContain(`Renewed at ${formatCertTimestamp(T2, "en")}`)
    expect(partialHtml).toContain("Issuing…")
    expect(lastRefetchInterval()).toBe(CERT_POLL_INTERVAL_MS)

    serverCerts = [certARenewed, certB]
    await vi.advanceTimersByTimeAsync(CERT_POLL_TIMEOUT_MS)
    await waitUntil(() => flow.html().includes("Still pending — check again later"))

    const finalHtml = flow.html()
    expect(finalHtml).toContain(`Renewed at ${formatCertTimestamp(T2, "en")}`)
    expect(finalHtml).toContain("Still pending — check again later")
    expect(lastRefetchInterval()).toBe(CERT_PENDING_POLL_INTERVAL_MS)

    flow.dispose()
  })

  it("disables the row button and ignores second clicks while POST or issuing poll is in flight", async () => {
    const certV1 = makeCert({ notBefore: T1, revision: 1 })
    const postGate = deferred<Response>()

    const flow = mountCertsFlow(async ({ url, method }) => {
      if (method === "GET" && url === "/api/settings/certs") return jsonResponse([certV1])
      if (method === "POST" && url === "/api/settings/certs/renew") return postGate.promise
      throw new Error(`Unexpected fetch ${method} ${url}`)
    })

    await waitUntil(() => flow.getCalls().length === 1 && flow.observer.getCurrentResult().isSuccess)

    const firstAttempt = flow.controller.renew(certV1)
    const submittingHtml = flow.html()
    expect(submittingHtml).toContain("disabled")
    expect(submittingHtml).toContain("Renewing...")

    // Second click while POST is in flight must be ignored.
    await flow.controller.renew(certV1)
    expect(flow.postCalls()).toHaveLength(1)

    postGate.resolve(jsonResponse({ success: true, pending: true }))
    await firstAttempt
    await waitUntil(() => flow.getCalls().length >= 2)

    // Second invocation while issuing is in flight must not POST again.
    await flow.controller.renew(certV1)
    expect(flow.postCalls()).toHaveLength(1)

    flow.dispose()
  })
})
