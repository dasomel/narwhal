import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"

vi.mock("server-only", () => ({}))
vi.mock("./config", () => ({
  getK8sApiServer: () => "http://k8s.mock",
}))

const mockCache = new Map<string, unknown>()
vi.mock("./valkey", () => ({
  cacheGet: vi.fn(async (key: string) => mockCache.get(key) ?? null),
  cacheSet: vi.fn(async (key: string, val: unknown) => {
    mockCache.set(key, val)
  }),
}))

import {
  computeVulnDbFreshness,
  getSecuritySummary,
  getWorkloadVulnerabilities,
  getWorkloadVulnerabilitiesWithMeta,
  getImageVulnReport,
} from "./trivy"

interface MakeReportOptions {
  name: string
  namespace?: string
  kind?: string
  workloadName?: string
  repository: string
  tag?: string
  digest?: string
  creationTimestamp?: string
  summary?: {
    criticalCount?: number
    highCount?: number
    mediumCount?: number
    lowCount?: number
    unknownCount?: number
  }
  vulnerabilities?: Array<{
    vulnerabilityID: string
    severity: string
    score?: number
    title: string
    primaryLink?: string
    fixedVersion?: string
    installedVersion?: string
    target?: string
    publishedDate?: string
  }>
}

function makeReport(opts: MakeReportOptions) {
  return {
    metadata: {
      name: opts.name,
      namespace: opts.namespace ?? "default",
      creationTimestamp: opts.creationTimestamp ?? "2026-10-27T00:00:00Z",
      labels: {
        "trivy-operator.resource.kind": opts.kind ?? "ReplicaSet",
        "trivy-operator.resource.name": opts.workloadName ?? opts.name,
      },
    },
    report: {
      artifact: {
        repository: opts.repository,
        tag: opts.tag ?? "1.0.0",
        digest: opts.digest ?? "sha256:deadbeef",
      },
      registry: { server: "harbor.local.narwhal.internal" },
      summary: {
        criticalCount: opts.summary?.criticalCount ?? 0,
        highCount: opts.summary?.highCount ?? 0,
        mediumCount: opts.summary?.mediumCount ?? 0,
        lowCount: opts.summary?.lowCount ?? 0,
        unknownCount: opts.summary?.unknownCount ?? 0,
      },
      vulnerabilities: opts.vulnerabilities ?? [],
    },
  }
}

function reportPageResponse(items: ReturnType<typeof makeReport>[], continueToken?: string) {
  return Response.json({
    metadata: continueToken ? { continue: continueToken } : {},
    items,
  })
}

describe("Vulnerability DB Freshness (Issue #75)", () => {
  it("marks a fresh timestamp (< 14 days) as fresh", () => {
    const recent = new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString()
    const freshness = computeVulnDbFreshness(recent)
    expect(freshness.status).toBe("fresh")
    expect(freshness.dbAgeDays).toBe(2)
  })

  it("marks a stale timestamp (14-29 days) as stale", () => {
    const stale = new Date(Date.now() - 18 * 24 * 60 * 60 * 1000).toISOString()
    const freshness = computeVulnDbFreshness(stale)
    expect(freshness.status).toBe("stale")
    expect(freshness.dbAgeDays).toBe(18)
  })

  it("marks an outdated timestamp (>= 30 days) as critical", () => {
    const critical = new Date(Date.now() - 35 * 24 * 60 * 60 * 1000).toISOString()
    const freshness = computeVulnDbFreshness(critical)
    expect(freshness.status).toBe("critical")
    expect(freshness.dbAgeDays).toBe(35)
  })
})

describe("VulnerabilityReport bounded pagination", () => {
  const originalFetch = global.fetch
  const mockFetch = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    mockFetch.mockReset()
    mockCache.clear()
    global.fetch = mockFetch
  })

  afterEach(() => {
    global.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it("aggregates totals, scannedImages and workloads across a multi-page list", async () => {
    const queueThreePages = () => {
      mockFetch
        .mockResolvedValueOnce(
          reportPageResponse(
            [
              makeReport({
                name: "rep-1",
                namespace: "ns-a",
                kind: "Deployment",
                workloadName: "api-a",
                repository: "library/app-a",
                tag: "1.0.0",
                creationTimestamp: "2026-10-25T00:00:00Z",
                summary: { criticalCount: 1, highCount: 2, mediumCount: 0, lowCount: 0, unknownCount: 0 },
              }),
            ],
            "tok-1",
          ),
        )
        .mockResolvedValueOnce(
          reportPageResponse(
            [
              makeReport({
                name: "rep-2",
                namespace: "ns-b",
                kind: "StatefulSet",
                workloadName: "db-b",
                repository: "library/db-b",
                tag: "16.2",
                creationTimestamp: "2026-10-27T12:00:00Z",
                summary: { criticalCount: 2, highCount: 1, mediumCount: 3, lowCount: 4, unknownCount: 1 },
              }),
            ],
            "tok-2",
          ),
        )
        .mockResolvedValueOnce(
          reportPageResponse([
            makeReport({
              name: "rep-3",
              namespace: "ns-c",
              kind: "DaemonSet",
              workloadName: "agent-c",
              repository: "library/agent-c",
              tag: "2.1.0",
              creationTimestamp: "2026-10-26T00:00:00Z",
              summary: { criticalCount: 0, highCount: 3, mediumCount: 2, lowCount: 1, unknownCount: 0 },
            }),
          ]),
        )
    }

    queueThreePages()
    const summary = await getSecuritySummary()
    expect(summary.totals).toEqual({ Critical: 3, High: 6, Medium: 5, Low: 5, Unknown: 1 })
    expect(summary.scannedImages).toBe(3)
    expect(summary.scannedWorkloads).toBe(3)
    expect(summary.lastUpdated).toBe("2026-10-27T12:00:00Z")

    queueThreePages()
    const workloads = await getWorkloadVulnerabilities()
    expect(workloads.map((w) => `${w.namespace}/${w.kind}/${w.name}`)).toEqual([
      "ns-a/Deployment/api-a",
      "ns-b/StatefulSet/db-b",
      "ns-c/DaemonSet/agent-c",
    ])
    expect(mockFetch).toHaveBeenCalledTimes(6)
  })

  it("carries limit=20 on every list request and never makes a request without a limit", async () => {
    const queueTwoPages = () => {
      mockFetch
        .mockResolvedValueOnce(
          reportPageResponse([makeReport({ name: "rep-1", repository: "library/app-1" })], "tok-1"),
        )
        .mockResolvedValueOnce(
          reportPageResponse([makeReport({ name: "rep-2", repository: "library/app-2" })]),
        )
    }

    queueTwoPages()
    await getSecuritySummary()

    queueTwoPages()
    await getWorkloadVulnerabilities()

    queueTwoPages()
    await getImageVulnReport("harbor.local.narwhal.internal/library/app-2:1.0.0")

    expect(mockFetch.mock.calls.length).toBeGreaterThan(0)
    for (const [rawUrl] of mockFetch.mock.calls) {
      const url = new URL(rawUrl as string)
      expect(url.pathname).toBe("/apis/aquasecurity.github.io/v1alpha1/vulnerabilityreports")
      expect(url.searchParams.get("limit")).toBe("20")
    }
    expect(mockFetch).toHaveBeenCalledTimes(6)
    expect(new URL(mockFetch.mock.calls[0][0] as string).searchParams.has("continue")).toBe(false)
    expect(new URL(mockFetch.mock.calls[1][0] as string).searchParams.get("continue")).toBe("tok-1")
  })

  it("warns once with the page count on a truncated list and still returns the fetched data", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {})
    let page = 0
    mockFetch.mockImplementation(async () => {
      page++
      return reportPageResponse(
        [
          makeReport({
            name: `rep-${page}`,
            workloadName: `wl-${page}`,
            repository: `library/img-${page}`,
            summary: { criticalCount: 1 },
          }),
        ],
        `cont-${page}`,
      )
    })

    const summary = await getSecuritySummary()
    expect(mockFetch).toHaveBeenCalledTimes(50)
    expect(warnSpy).toHaveBeenCalledTimes(1)
    expect(warnSpy.mock.calls[0][0]).toMatch(/truncated.*50 pages/i)
    expect(summary.totals.Critical).toBe(50)
    expect(summary.scannedImages).toBe(50)
    expect(summary.scannedWorkloads).toBe(50)

    warnSpy.mockClear()
    page = 0
    const rows = await getWorkloadVulnerabilities()
    expect(warnSpy).toHaveBeenCalledTimes(1)
    expect(warnSpy.mock.calls[0][0]).toMatch(/truncated.*50 pages/i)
    expect(rows).toHaveLength(50)
  })

  it("falls back as before when the first page request fails (nothing to return as partial)", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {})
    mockFetch.mockImplementation(async () => new Response(null, { status: 500 }))

    const summary = await getSecuritySummary()
    expect(summary.totals).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 0, Unknown: 0 })
    expect(summary.scannedImages).toBe(0)
    expect(summary.scannedWorkloads).toBe(0)
    expect(summary.partial).toBeUndefined()
    expect(mockCache.size).toBe(0)

    expect(await getWorkloadVulnerabilities()).toEqual([])
    expect(mockCache.size).toBe(0)

    expect(await getImageVulnReport("harbor.local.narwhal.internal/library/app-1:1.0.0")).toBeNull()
    expect(mockCache.size).toBe(0)
    expect(warnSpy).toHaveBeenCalledTimes(3)
  })

  it("finds an image in getImageVulnReport that is only on a later page", async () => {
    mockFetch
      .mockResolvedValueOnce(
        reportPageResponse([makeReport({ name: "rep-1", repository: "library/first", tag: "1.0.0" })], "tok-1"),
      )
      .mockResolvedValueOnce(
        reportPageResponse([makeReport({ name: "rep-2", repository: "library/second", tag: "1.0.0" })], "tok-2"),
      )
      .mockResolvedValueOnce(
        reportPageResponse([
          makeReport({
            name: "rep-3",
            namespace: "database",
            kind: "StatefulSet",
            workloadName: "narwhal-db",
            repository: "library/postgres",
            tag: "16.4",
            digest: "sha256:cafe1234",
            summary: { criticalCount: 1, highCount: 2, mediumCount: 0, lowCount: 0, unknownCount: 0 },
            vulnerabilities: [
              {
                vulnerabilityID: "CVE-2026-0001",
                severity: "CRITICAL",
                score: 9.8,
                title: "test vuln",
              },
            ],
          }),
        ]),
      )

    const report = await getImageVulnReport("harbor.local.narwhal.internal/library/postgres:16.4")
    expect(report).not.toBeNull()
    expect(report?.image).toBe("harbor.local.narwhal.internal/library/postgres:16.4")
    expect(report?.digest).toBe("sha256:cafe1234")
    expect(report?.namespace).toBe("database")
    expect(report?.workload).toEqual({ kind: "StatefulSet", name: "narwhal-db" })
    expect(report?.summary.Critical).toBe(1)
    expect(report?.summary.High).toBe(2)
    expect(report?.vulnerabilities).toHaveLength(1)
    expect(report?.vulnerabilities[0].id).toBe("CVE-2026-0001")
  })
})

describe("VulnerabilityReport 429 / expired-continue handling (#505)", () => {
  const originalFetch = global.fetch
  const mockFetch = vi.fn()
  const page = (n: number, token?: string) =>
    reportPageResponse(
      [makeReport({ name: `rep-${n}`, workloadName: `wl-${n}`, repository: `library/img-${n}`, summary: { criticalCount: 1 } })],
      token,
    )

  beforeEach(() => {
    vi.clearAllMocks()
    mockFetch.mockReset()
    mockCache.clear()
    global.fetch = mockFetch
    vi.useFakeTimers()
    vi.spyOn(Math, "random").mockReturnValue(0.5)
    vi.spyOn(console, "warn").mockImplementation(() => {})
  })

  afterEach(() => {
    vi.useRealTimers()
    global.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it("retries a page that gets 429 and returns the full list, not partial, and caches it", async () => {
    mockFetch
      .mockResolvedValueOnce(page(1, "tok-1"))
      .mockResolvedValueOnce(new Response(null, { status: 429 }))
      .mockResolvedValueOnce(page(2))

    const promise = getWorkloadVulnerabilitiesWithMeta()
    await vi.runAllTimersAsync()
    const result = await promise

    expect(result.partial).toBe(false)
    expect(result.rows).toHaveLength(2)
    expect(mockFetch).toHaveBeenCalledTimes(3)
    // the retry re-requests the SAME continue token
    expect(new URL(mockFetch.mock.calls[2][0] as string).searchParams.get("continue")).toBe("tok-1")
    expect(mockCache.size).toBe(1)
  })

  it("flags partial (reason error) and does not cache when 429 persists past the retry budget", async () => {
    mockFetch
      .mockResolvedValueOnce(page(1, "tok-1"))
      .mockImplementation(async () => new Response(null, { status: 429 }))

    const promise = getSecuritySummary()
    await vi.runAllTimersAsync()
    const summary = await promise

    expect(mockFetch).toHaveBeenCalledTimes(1 + 3) // first page + 3 attempts
    expect(summary.partial).toBe(true)
    expect(summary.partialReason).toBe("error")
    expect(summary.totals.Critical).toBe(1)
    expect(mockCache.size).toBe(0)
  })

  it("honours Retry-After on a 429 before retrying the page", async () => {
    mockFetch
      .mockResolvedValueOnce(page(1, "tok-1"))
      .mockResolvedValueOnce(new Response(null, { status: 429, headers: { "Retry-After": "2" } }))
      .mockResolvedValueOnce(page(2))

    const promise = getWorkloadVulnerabilitiesWithMeta()
    await vi.advanceTimersByTimeAsync(1_900)
    expect(mockFetch).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(200)
    expect(mockFetch).toHaveBeenCalledTimes(3)
    expect((await promise).rows).toHaveLength(2)
  })

  it("restarts once on an expired continue token (410) and returns the full list", async () => {
    mockFetch
      .mockResolvedValueOnce(page(1, "tok-1"))
      .mockResolvedValueOnce(new Response(null, { status: 410 }))
      .mockResolvedValueOnce(page(1, "tok-2"))
      .mockResolvedValueOnce(page(2))

    const result = await getWorkloadVulnerabilitiesWithMeta()
    expect(result.partial).toBe(false)
    expect(result.rows).toHaveLength(2)
  })

  it("flags partial (reason expired) and does not cache when the restart also hits 410", async () => {
    mockFetch
      .mockResolvedValueOnce(page(1, "tok-1"))
      .mockResolvedValueOnce(new Response(null, { status: 410 }))
      .mockResolvedValueOnce(page(1, "tok-2"))
      .mockResolvedValueOnce(new Response(null, { status: 410 }))

    const result = await getWorkloadVulnerabilitiesWithMeta()
    expect(result.partial).toBe(true)
    expect(result.partialReason).toBe("expired")
    expect(result.rows).toHaveLength(1)
    expect(mockCache.size).toBe(0)
  })

  it("leaves a normal multi-page walk unflagged", async () => {
    mockFetch.mockResolvedValueOnce(page(1, "tok-1")).mockResolvedValueOnce(page(2))
    const summary = await getSecuritySummary()
    expect(summary.partial).toBeUndefined()
    expect(summary.scannedImages).toBe(2)
    expect(mockFetch).toHaveBeenCalledTimes(2)
  })
})
