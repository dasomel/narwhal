import { beforeEach, describe, expect, it, vi } from "vitest"
import type { ArgoApp } from "@/lib/argocd"
import type { NamespaceInfo } from "@/lib/k8s-client"

vi.mock("@/lib/auth", () => ({ auth: vi.fn() }))
vi.mock("@/lib/argocd", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/argocd")>()
  return { ...actual, getArgoApps: vi.fn() }
})
vi.mock("@/lib/gitea", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/gitea")>()
  return { ...actual, getCommitTimestamp: vi.fn() }
})
vi.mock("@/lib/valkey", () => ({ cacheGet: vi.fn(), cacheSet: vi.fn() }))
vi.mock("@/lib/k8s-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/k8s-client")>()
  return { ...actual, getNamespaces: vi.fn() }
})

const { auth } = await import("@/lib/auth")
const { getArgoApps } = await import("@/lib/argocd")
const { getCommitTimestamp } = await import("@/lib/gitea")
const { cacheGet, cacheSet } = await import("@/lib/valkey")
const { getNamespaces } = await import("@/lib/k8s-client")
const { GET } = await import("./route")

const platformTeamSession = { groups: ["developer"], teams: ["platform-team"], user: { role: "developer" } }
const frontendTeamSession = { groups: ["developer"], teams: ["frontend-team"], user: { role: "developer" } }

const namespaces: NamespaceInfo[] = [
  { name: "platform-system", status: "Active", labels: {}, createdAt: "2026-01-01T00:00:00Z" },
  { name: "frontend-app", status: "Active", labels: {}, createdAt: "2026-01-01T00:00:00Z" },
]

function fakeApp(name: string, project: string, namespace: string, revision: string): ArgoApp {
  return {
    metadata: { name },
    spec: { project, destination: { namespace } },
    status: {
      sync: { status: "Synced" },
      health: { status: "Healthy" },
      history: [{ id: 1, revision, deployedAt: new Date(Date.now() - 60 * 60 * 1000).toISOString() }],
    },
  }
}

const platformApp = fakeApp("platform-app", "platform", "platform-system", "aaaaaaa1111")
const frontendApp = fakeApp("frontend-app", "apps", "frontend-app", "bbbbbbb2222")

beforeEach(() => {
  const cache = new Map<string, unknown>()
  vi.mocked(getNamespaces).mockResolvedValue(namespaces)
  vi.mocked(getArgoApps).mockResolvedValue([platformApp, frontendApp])
  vi.mocked(getCommitTimestamp).mockResolvedValue(new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString())
  vi.mocked(cacheGet).mockImplementation(async (key) => cache.get(key) ?? null)
  vi.mocked(cacheSet).mockImplementation(async (key, value) => {
    cache.set(key, value)
  })
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ data: { result: [] } }) }))
})

describe("GET /api/governance/dora — scope enforcement", () => {
  it("reduces per-app, recent, and deployment totals to the frontend team's app", async () => {
    vi.mocked(auth).mockResolvedValue(frontendTeamSession as never)

    const res = await GET()
    const body = await res.json()

    expect(res.status).toBe(200)
    expect(body.totalDeploys).toBe(1)
    expect(body.perApp.map((app: { app: string }) => app.app)).toEqual(["frontend-app"])
    expect(body.recent.map((deployment: { app: string }) => deployment.app)).toEqual(["frontend-app"])
  })

  it("returns the platform team's own app as a positive control", async () => {
    vi.mocked(auth).mockResolvedValue(platformTeamSession as never)

    const res = await GET()
    const body = await res.json()

    expect(res.status).toBe(200)
    expect(body.totalDeploys).toBe(1)
    expect(body.perApp.map((app: { app: string }) => app.app)).toEqual(["platform-app"])
    expect(body.recent.map((deployment: { app: string }) => deployment.app)).toEqual(["platform-app"])
  })

  it("keeps sequential platform and frontend responses in separate scope cache entries", async () => {
    vi.mocked(auth).mockResolvedValue(platformTeamSession as never)
    const platformResponse = await GET()
    vi.mocked(auth).mockResolvedValue(frontendTeamSession as never)
    const frontendResponse = await GET()

    expect((await platformResponse.json()).perApp.map((app: { app: string }) => app.app)).toEqual(["platform-app"])
    expect((await frontendResponse.json()).perApp.map((app: { app: string }) => app.app)).toEqual(["frontend-app"])
    const doraCacheKeys = vi.mocked(cacheSet).mock.calls.map(([key]) => key).filter((key) => key.startsWith("governance:dora:"))
    expect(new Set(doraCacheKeys).size).toBe(2)
  })

  it("401s an unauthenticated caller", async () => {
    vi.mocked(auth).mockResolvedValue(null as never)

    const res = await GET()

    expect(res.status).toBe(401)
  })
})

describe("GET /api/governance/dora — deployment outcome semantics (#32)", () => {
  it("marks a historical (non-latest) deployment Unknown, never Succeeded, when ArgoCD gives no per-entry outcome evidence", async () => {
    vi.mocked(auth).mockResolvedValue(platformTeamSession as never)
    const appWithHistory: ArgoApp = {
      metadata: { name: "platform-app" },
      spec: { project: "platform", destination: { namespace: "platform-system" } },
      status: {
        sync: { status: "Synced" },
        health: { status: "Healthy" },
        // operationState.phase is absent -> even the latest entry has no
        // definite outcome evidence.
        history: [
          { id: 1, revision: "1111111aaaa", deployedAt: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString() },
          { id: 2, revision: "2222222bbbb", deployedAt: new Date(Date.now() - 60 * 60 * 1000).toISOString() },
        ],
      },
    }
    vi.mocked(getArgoApps).mockResolvedValue([appWithHistory])

    const res = await GET()
    const body = await res.json()

    expect(res.status).toBe(200)
    expect(body.totalDeploys).toBe(2)
    expect(body.recent.every((d: { status: string }) => d.status === "Unknown")).toBe(true)
    expect(body.knownOutcomeDeploys).toBe(0)
    expect(body.unknownOutcomeDeploys).toBe(2)
    // No known-outcome deploys -> rate is unknown, not a false-healthy 0%.
    expect(body.changeFailureRate).toBeNull()
  })

  it("counts a Failed latest operation phase as a known Failed outcome and computes a rate over known outcomes only", async () => {
    vi.mocked(auth).mockResolvedValue(platformTeamSession as never)
    const failedLatestApp: ArgoApp = {
      metadata: { name: "platform-app" },
      spec: { project: "platform", destination: { namespace: "platform-system" } },
      status: {
        sync: { status: "Synced" },
        health: { status: "Degraded" },
        operationState: { phase: "Failed" },
        history: [
          { id: 1, revision: "1111111aaaa", deployedAt: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString() },
          { id: 2, revision: "2222222bbbb", deployedAt: new Date(Date.now() - 60 * 60 * 1000).toISOString() },
        ],
      },
    }
    vi.mocked(getArgoApps).mockResolvedValue([failedLatestApp])

    const res = await GET()
    const body = await res.json()

    expect(res.status).toBe(200)
    expect(body.totalDeploys).toBe(2)
    expect(body.knownOutcomeDeploys).toBe(1)
    expect(body.unknownOutcomeDeploys).toBe(1)
    // 1 known outcome, 1 failed -> 100%, not diluted by the unknown entry.
    expect(body.changeFailureRate).toBe(100)
    const latest = body.recent.find((d: { revision: string }) => d.revision === "2222222")
    expect(latest).toBeTruthy()
    expect(latest.status).toBe("Failed")
  })

  it("exposes metric definition version, mttr source, and evidence window", async () => {
    vi.mocked(auth).mockResolvedValue(platformTeamSession as never)

    const res = await GET()
    const body = await res.json()

    expect(res.status).toBe(200)
    expect(typeof body.metricDefinitionVersion).toBe("string")
    expect(body.metricDefinitionVersion.length).toBeGreaterThan(0)
    expect(body.mttrSource).toBe("alert-episode-duration")
    expect(body.evidenceWindow.start).toBeTruthy()
    expect(body.evidenceWindow.end).toBeTruthy()
  })
})

describe("GET /api/governance/dora — only git SHAs are looked up (Gitea SSO-redirect fix)", () => {
  it("never sends a Helm chart version to Gitea and marks that app's lead time as not applicable", async () => {
    vi.mocked(auth).mockResolvedValue(platformTeamSession as never)
    vi.mocked(getArgoApps).mockResolvedValue([
      fakeApp("platform-app", "platform", "platform-system", "aaaaaaa1111"),
      fakeApp("chart-app", "platform", "platform-system", "86.2.3"),
      fakeApp("chart-v-app", "platform", "platform-system", "v1.20.2"),
    ])
    vi.mocked(getCommitTimestamp).mockClear()

    const body = await (await GET()).json()

    expect(vi.mocked(getCommitTimestamp).mock.calls.map(([sha]) => sha)).toEqual(["aaaaaaa1111"])
    const byApp = Object.fromEntries(body.perApp.map((a: { app: string }) => [a.app, a]))
    expect(byApp["platform-app"].leadTimeApplicable).toBe(true)
    expect(byApp["platform-app"].leadTimeHours).not.toBeNull()
    expect(byApp["chart-app"].leadTimeApplicable).toBe(false)
    expect(byApp["chart-app"].leadTimeHours).toBeNull()
    expect(byApp["chart-v-app"].leadTimeApplicable).toBe(false)
  })
})

describe("GET /api/governance/dora — minute precision", () => {
  it("preserves fractional minutes and weighted averages through the cache", async () => {
    vi.mocked(auth).mockResolvedValue(platformTeamSession as never)
    const first = fakeApp("first", "platform", "platform-system", "aaaaaaa1111")
    const second = fakeApp("second", "platform", "platform-system", "bbbbbbb2222")
    const deployedAt = first.status.history![0].deployedAt
    second.status.history![0].deployedAt = deployedAt
    first.status.history!.push({ id: 2, revision: "ccccccc3333", deployedAt })
    vi.mocked(getArgoApps).mockResolvedValue([first, second])
    const durations: Record<string, number> = { aaaaaaa1111: 30000, bbbbbbb2222: 150000, ccccccc3333: 90000 }
    vi.mocked(getCommitTimestamp).mockImplementation(async (sha) => new Date(Date.parse(deployedAt) - durations[sha]).toISOString())

    const body = await (await GET()).json()
    expect(body.leadTimeMinutes).toBe(1.5)
    expect(body.leadTimeHours).toBe(0) // legacy rounding retained
    expect(body.perApp.find((a: { app: string }) => a.app === "first").leadTimeMinutes).toBe(1)
    expect(body.perApp.find((a: { app: string }) => a.app === "second").leadTimeMinutes).toBe(2.5)
    expect(await (await GET()).json()).toEqual(body)
  })

  it.each([
    [30000, 0.5],
    [0, 0],
    [-30000, 0],
    [null, null],
    [NaN, null],
  ])("handles elapsed milliseconds %s as minutes %s", async (elapsed, expected) => {
    vi.mocked(auth).mockResolvedValue(platformTeamSession as never)
    const app = fakeApp("platform-app", "platform", "platform-system", "aaaaaaa1111")
    vi.mocked(getArgoApps).mockResolvedValue([app])
    const deployedAtMs = Date.parse(app.status.history![0].deployedAt)
    vi.mocked(getCommitTimestamp).mockResolvedValue(elapsed === null ? null : Number.isNaN(elapsed) ? "invalid" : new Date(deployedAtMs - elapsed).toISOString())
    const body = await (await GET()).json()
    expect(body.leadTimeMinutes).toBe(expected)
    expect(body.perApp[0].leadTimeMinutes).toBe(expected)
    expect(body.perApp[0].leadTimeApplicable).toBe(true)
    expect(body.leadTimeHours).toBe(expected === null ? null : 0)
  })

  it("keeps Helm apps inapplicable with null minutes", async () => {
    vi.mocked(auth).mockResolvedValue(platformTeamSession as never)
    vi.mocked(getArgoApps).mockResolvedValue([fakeApp("chart", "platform", "platform-system", "86.2.3")])
    const body = await (await GET()).json()
    expect(body.leadTimeMinutes).toBeNull()
    expect(body.perApp[0]).toMatchObject({ leadTimeMinutes: null, leadTimeHours: null, leadTimeApplicable: false })
  })
})

describe("GET /api/governance/dora — daily deployment trend bucketing", () => {
  it("buckets deployments into exactly 7 UTC calendar days with MM-DD format", async () => {
    vi.mocked(auth).mockResolvedValue(platformTeamSession as never)
    const now = Date.now()
    const nowDate = new Date(now)
    const appWithMultiDayHistory: ArgoApp = {
      metadata: { name: "platform-app" },
      spec: { project: "platform", destination: { namespace: "platform-system" } },
      status: {
        sync: { status: "Synced" },
        health: { status: "Healthy" },
        history: [
          { id: 1, revision: "aaaaaaa1111", deployedAt: new Date(Date.UTC(nowDate.getUTCFullYear(), nowDate.getUTCMonth(), nowDate.getUTCDate(), 2, 0, 0)).toISOString() },
          { id: 2, revision: "bbbbbbb2222", deployedAt: new Date(Date.UTC(nowDate.getUTCFullYear(), nowDate.getUTCMonth(), nowDate.getUTCDate() - 1, 14, 0, 0)).toISOString() },
          { id: 3, revision: "ccccccc3333", deployedAt: new Date(Date.UTC(nowDate.getUTCFullYear(), nowDate.getUTCMonth(), nowDate.getUTCDate() - 1, 18, 0, 0)).toISOString() },
          { id: 4, revision: "ddddddd4444", deployedAt: new Date(Date.UTC(nowDate.getUTCFullYear(), nowDate.getUTCMonth(), nowDate.getUTCDate() - 3, 10, 0, 0)).toISOString() },
        ],
      },
    }
    vi.mocked(getArgoApps).mockResolvedValue([appWithMultiDayHistory])

    const res = await GET()
    const body = await res.json()

    expect(res.status).toBe(200)
    expect(body.dailyDeploys).toHaveLength(7)
    // Every date has MM-DD format
    for (const d of body.dailyDeploys) {
      expect(d.date).toMatch(/^\d{2}-\d{2}$/)
    }

    const todayStr = `${String(nowDate.getUTCMonth() + 1).padStart(2, "0")}-${String(nowDate.getUTCDate()).padStart(2, "0")}`
    const yesterdayDate = new Date(Date.UTC(nowDate.getUTCFullYear(), nowDate.getUTCMonth(), nowDate.getUTCDate() - 1))
    const yesterdayStr = `${String(yesterdayDate.getUTCMonth() + 1).padStart(2, "0")}-${String(yesterdayDate.getUTCDate()).padStart(2, "0")}`
    const threeDaysAgoDate = new Date(Date.UTC(nowDate.getUTCFullYear(), nowDate.getUTCMonth(), nowDate.getUTCDate() - 3))
    const threeDaysAgoStr = `${String(threeDaysAgoDate.getUTCMonth() + 1).padStart(2, "0")}-${String(threeDaysAgoDate.getUTCDate()).padStart(2, "0")}`

    const todayBucket = body.dailyDeploys.find((b: { date: string }) => b.date === todayStr)
    const yesterdayBucket = body.dailyDeploys.find((b: { date: string }) => b.date === yesterdayStr)
    const threeDaysAgoBucket = body.dailyDeploys.find((b: { date: string }) => b.date === threeDaysAgoStr)

    expect(todayBucket?.count).toBe(1)
    expect(yesterdayBucket?.count).toBe(2)
    expect(threeDaysAgoBucket?.count).toBe(1)
  })
})
