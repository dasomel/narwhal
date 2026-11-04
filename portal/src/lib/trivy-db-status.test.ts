import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("server-only", () => ({}))
const mockCache = new Map<string, unknown>()
vi.mock("./valkey", () => ({
  cacheGet: vi.fn(async (key: string) => mockCache.get(key) ?? null),
  cacheSet: vi.fn(async (key: string, val: unknown) => { mockCache.set(key, val) }),
}))
vi.mock("./k8s-client", () => ({ getEvents: vi.fn() }))
vi.mock("./trivy", () => ({
  getSecuritySummary: vi.fn(),
  computeVulnDbFreshness: () => ({ dbRegistry: "harbor.devtools.svc.cluster.local/trivy-db/aquasecurity/trivy-db" }),
}))

import { getEvents } from "./k8s-client"
import { getSecuritySummary } from "./trivy"
import { classifyArtifact, getTrivyDbStatus } from "./trivy-db-status"

const NOW = Date.parse("2026-11-04T00:00:00Z")
const daysAgo = (n: number) => new Date(NOW - n * 86_400_000).toISOString()

function artifact(tag: string, pushedDaysAgo: number, createdDaysAgo?: number) {
  return {
    digest: "sha256:2fd489b1ad2269b3d61b12bf24a91bdd0aec776f715cb77537e05d3124637ddc",
    push_time: daysAgo(pushedDaysAgo),
    tags: [{ name: tag }],
    annotations: createdDaysAgo === undefined ? null : { "org.opencontainers.image.created": daysAgo(createdDaysAgo) },
  }
}

function harbor(byRepo: Record<string, Response | Error>) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const hit = Object.entries(byRepo).find(([repo]) => url.includes(`aquasecurity%252F${repo}/artifacts`))
    const r = hit?.[1] ?? new Response("[]", { status: 404 })
    if (r instanceof Error) throw r
    return r.clone()
  }))
}
const ok = (items: unknown[]) => new Response(JSON.stringify(items), { status: 200 })

describe("classifyArtifact", () => {
  const base = { repository: "aquasecurity/trivy-db", tag: "2" }
  it("is missing when nothing is found", () => {
    expect(classifyArtifact(base, null, NOW).status).toBe("missing")
  })
  it("prefers the OCI created annotation over push_time", () => {
    const a = classifyArtifact(base, artifact("2", 0, 10), NOW)
    expect(a.status).toBe("stale")
    expect(a.ageDays).toBe(10)
  })
  it("treats exactly the SLO as fresh and one day past as stale", () => {
    expect(classifyArtifact(base, artifact("2", 7), NOW).status).toBe("fresh")
    expect(classifyArtifact(base, artifact("2", 8), NOW).status).toBe("stale")
  })
  it("is unknown when no valid timestamp exists", () => {
    expect(classifyArtifact(base, { tags: [{ name: "2" }], push_time: "garbage" }, NOW).status).toBe("unknown")
  })
})

describe("getTrivyDbStatus", () => {
  beforeEach(() => {
    mockCache.clear()
    vi.useFakeTimers({ toFake: ["Date"] })
    vi.setSystemTime(NOW)
    vi.mocked(getSecuritySummary).mockResolvedValue({
      totals: { Critical: 0, High: 0, Medium: 0, Low: 0, Unknown: 0 },
      scannedImages: 4, scannedWorkloads: 7, lastUpdated: daysAgo(0),
    })
    vi.mocked(getEvents).mockResolvedValue([])
  })
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.resetAllMocks() })

  it("reports fresh when all three artifacts are within the SLO", async () => {
    harbor({ "trivy-db": ok([artifact("2", 1, 1)]), "trivy-java-db": ok([artifact("1", 1, 2)]), "trivy-checks": ok([artifact("1", 1)]) })
    const s = await getTrivyDbStatus()
    expect(s.overall).toBe("fresh")
    expect(s.harborReachable).toBe(true)
    expect(s.artifacts.map((a) => a.status)).toEqual(["fresh", "fresh", "fresh"])
    expect(s.scanner.state).toBe("ok")
  })

  it("reports stale when any artifact is older than the SLO", async () => {
    harbor({ "trivy-db": ok([artifact("2", 1, 9)]), "trivy-java-db": ok([artifact("1", 1)]), "trivy-checks": ok([artifact("1", 1)]) })
    const s = await getTrivyDbStatus()
    expect(s.overall).toBe("stale")
    expect(s.artifacts[0].ageDays).toBe(9)
  })

  it("reports missing for a 404 repository and for an artifact without the expected tag", async () => {
    harbor({ "trivy-db": ok([artifact("1", 1)]), "trivy-checks": ok([artifact("1", 1)]) })
    const s = await getTrivyDbStatus()
    expect(s.overall).toBe("missing")
    expect(s.artifacts.map((a) => a.status)).toEqual(["missing", "missing", "fresh"])
  })

  it("flags failing scans when the last failure is newer than the newest report", async () => {
    harbor({})
    vi.mocked(getSecuritySummary).mockResolvedValue({
      totals: { Critical: 0, High: 0, Medium: 0, Low: 0, Unknown: 0 },
      scannedImages: 0, scannedWorkloads: 0, lastUpdated: daysAgo(0),
    })
    vi.mocked(getEvents).mockResolvedValue([
      { type: "Warning", reason: "BackoffLimitExceeded", message: "", namespace: "security-system", involvedObject: { kind: "Job", name: "scan-vulnerabilityreport-abc" }, lastTimestamp: daysAgo(0), firstTimestamp: null },
      { type: "Warning", reason: "BackoffLimitExceeded", message: "", namespace: "security-system", involvedObject: { kind: "Job", name: "other-job" }, lastTimestamp: daysAgo(0), firstTimestamp: null },
    ])
    const s = await getTrivyDbStatus()
    expect(s.scanner).toMatchObject({ state: "failing", reportCount: 0, newestReportAt: null, recentScanFailures: 1 })
  })

  it("reports unknown (not missing) and does not cache when Harbor is unreachable", async () => {
    harbor({ "trivy-db": new Error("ECONNREFUSED"), "trivy-java-db": new Response("no", { status: 503 }), "trivy-checks": new Response("no", { status: 401 }) })
    const s = await getTrivyDbStatus()
    expect(s.harborReachable).toBe(false)
    expect(s.overall).toBe("unknown")
    expect(s.artifacts.every((a) => a.status === "unknown")).toBe(true)
    expect(mockCache.size).toBe(0)
  })
})
