import { describe, expect, it, vi, beforeEach } from "vitest"
import type { ArgoApp } from "@/lib/argocd"
import type { NamespaceInfo } from "@/lib/k8s-client"

// portal#14: /api/events applies appVisible/alertVisible/getEffectiveScope and keys
// its cache by scope.fingerprint (the cross-tenant cache-poisoning bug documented in
// docs/lessons-log.md 2026-08-20). The underlying predicates are each unit-tested
// (role-filter.test.ts, event-visibility.test.ts) but the route itself had no
// end-to-end test. Mocking rationale: see src/app/api/catalog/route.test.ts.
vi.mock("@/lib/auth", () => ({ auth: vi.fn() }))
vi.mock("@/lib/argocd", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/argocd")>()
  return { ...actual, getArgoApps: vi.fn() }
})
vi.mock("@/lib/alertmanager", () => ({ getAlerts: vi.fn() }))
vi.mock("@/lib/valkey", () => ({ cacheGet: vi.fn(), cacheSet: vi.fn() }))
vi.mock("@/lib/k8s-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/k8s-client")>()
  return { ...actual, getNamespaces: vi.fn() }
})

const { auth } = await import("@/lib/auth")
const { getArgoApps } = await import("@/lib/argocd")
const { getAlerts } = await import("@/lib/alertmanager")
const { cacheGet, cacheSet } = await import("@/lib/valkey")
const { getNamespaces } = await import("@/lib/k8s-client")
const { GET } = await import("./route")

const platformTeamSession = { groups: ["developer"], teams: ["platform-team"], user: { role: "developer" } }
const frontendTeamSession = { groups: ["developer"], teams: ["frontend-team"], user: { role: "developer" } }
const clusterAdminSession = { groups: ["cluster-admin"], teams: [], user: { role: "cluster-admin" } }
// narwhal#659: a developer with an unmapped team (or no team at all) now fails closed in
// getVisibilityScope() -> hasMapping:false with zero namespaces.
const unscopedSession = { groups: ["unmapped-group"], teams: [], user: { role: "developer" } }
const unmappedDeveloperSession = { groups: ["developer"], teams: ["unmapped-team"], user: { role: "developer" } }
const teamlessDeveloperSession = { groups: ["developer"], teams: [], user: { role: "developer" } }

const namespaces: NamespaceInfo[] = [
  { name: "platform-system", status: "Active", labels: {}, createdAt: "2026-01-01T00:00:00Z" },
  { name: "frontend-app", status: "Active", labels: {}, createdAt: "2026-01-01T00:00:00Z" },
]

function fakeApp(name: string, project: string, namespace: string): ArgoApp {
  return {
    metadata: { name },
    spec: { project, destination: { namespace } },
    status: {
      sync: { status: "Synced" },
      health: { status: "Healthy" },
      history: [{ id: 1, revision: "abcdef1234", deployedAt: "2026-08-20T00:00:00Z" }],
    },
  }
}
const platformApp = fakeApp("platform-app", "platform", "platform-system")
const frontendApp = fakeApp("frontend-app", "apps", "frontend-app")

const platformAlert = {
  labels: { alertname: "PlatformDown", namespace: "platform-system", severity: "critical" },
  annotations: { summary: "platform down" },
  status: { state: "active" },
  startsAt: "2026-08-20T00:00:00Z",
}
const frontendAlert = {
  labels: { alertname: "FrontendDown", namespace: "frontend-app", severity: "warning" },
  annotations: { summary: "frontend down" },
  status: { state: "active" },
  startsAt: "2026-08-20T00:00:00Z",
}
const clusterWideAlert = {
  labels: { alertname: "NodeDiskPressure", severity: "critical", instance: "worker-1" },
  annotations: { summary: "node disk pressure" },
  status: { state: "active" },
  startsAt: "2026-08-20T00:00:00Z",
}

function req() {
  return new Request("http://localhost/api/events")
}

beforeEach(() => {
  vi.clearAllMocks()
  const cache = new Map<string, unknown>()
  vi.mocked(getNamespaces).mockResolvedValue(namespaces)
  vi.mocked(getArgoApps).mockResolvedValue([platformApp, frontendApp])
  vi.mocked(getAlerts).mockResolvedValue([])
  vi.mocked(cacheGet).mockImplementation(async (key: string) => cache.get(key) ?? null)
  vi.mocked(cacheSet).mockImplementation(async (key: string, value: unknown) => {
    cache.set(key, value)
  })
})

describe("GET /api/events — scope enforcement", () => {
  it("does not leak another team's deploy events to a cross-scope caller", async () => {
    vi.mocked(auth).mockResolvedValue(frontendTeamSession as never)
    const res = await GET(req())
    expect(res.status).toBe(200)
    const body = await res.json()
    const titles = body.map((e: { title: string }) => e.title)
    expect(titles).toContain("frontend-app")
    expect(titles).not.toContain("platform-app")
  })

  it("returns the caller's own team events (positive control)", async () => {
    vi.mocked(auth).mockResolvedValue(platformTeamSession as never)
    const res = await GET(req())
    expect(res.status).toBe(200)
    const body = await res.json()
    const titles = body.map((e: { title: string }) => e.title)
    expect(titles).toContain("platform-app")
    expect(titles).not.toContain("frontend-app")
  })

  it("keys the cache by scope fingerprint and never serves platform-team's cached timeline to frontend-team", async () => {
    vi.mocked(auth).mockResolvedValue(platformTeamSession as never)
    const platformBody = await (await GET(req())).json()
    expect(platformBody.map((e: { title: string }) => e.title)).toEqual(["platform-app"])

    vi.mocked(auth).mockResolvedValue(frontendTeamSession as never)
    const frontendBody = await (await GET(req())).json()
    // If the two scopes collided on one cache key, this would come back as
    // platform-team's already-cached timeline instead of frontend-team's own.
    expect(frontendBody.map((e: { title: string }) => e.title)).toEqual(["frontend-app"])

    const setKeys = vi.mocked(cacheSet).mock.calls.map((c) => c[0])
    expect(new Set(setKeys).size).toBe(2)

    // Re-request platform-team: must hit its own cache entry, not frontend-team's.
    vi.mocked(auth).mockResolvedValue(platformTeamSession as never)
    const cachedPlatformBody = await (await GET(req())).json()
    expect(cachedPlatformBody.map((e: { title: string }) => e.title)).toEqual(["platform-app"])
  })

  it("401s an unauthenticated caller", async () => {
    vi.mocked(auth).mockResolvedValue(null as never)
    const res = await GET(req())
    expect(res.status).toBe(401)
  })

  it("shows cluster-admin every team's deploy events (full view preserved)", async () => {
    vi.mocked(auth).mockResolvedValue(clusterAdminSession as never)
    const res = await GET(req())
    expect(res.status).toBe(200)
    const body = await res.json()
    const titles = body.map((e: { title: string }) => e.title)
    expect(titles).toContain("platform-app")
    expect(titles).toContain("frontend-app")
  })

  it("denies a non-admin caller with no scope mapping with 403 ACCESS_NOT_GRANTED", async () => {
    for (const session of [unscopedSession, unmappedDeveloperSession, teamlessDeveloperSession]) {
      vi.mocked(auth).mockResolvedValue(session as never)
      const res = await GET(req())
      expect(res.status).toBe(403)
      expect(await res.json()).toEqual({ error: "Forbidden", code: "ACCESS_NOT_GRANTED" })
    }
  })

  it("filters Alertmanager alerts by structured namespace label, not text", async () => {
    vi.mocked(getAlerts).mockResolvedValue([platformAlert, frontendAlert] as never)
    vi.mocked(auth).mockResolvedValue(frontendTeamSession as never)
    const res = await GET(req())
    expect(res.status).toBe(200)
    const body = await res.json()
    const titles = body.map((e: { title: string }) => e.title)
    expect(titles).toContain("FrontendDown")
    expect(titles).not.toContain("PlatformDown")
  })

  it("hides namespace-less alerts from non-admins and shows them to cluster-admin", async () => {
    vi.mocked(getAlerts).mockResolvedValue([platformAlert, frontendAlert, clusterWideAlert] as never)

    vi.mocked(auth).mockResolvedValue(frontendTeamSession as never)
    const devRes = await GET(req())
    expect(devRes.status).toBe(200)
    const devBody = await devRes.json()
    expect(devBody.map((e: { title: string }) => e.title)).not.toContain("NodeDiskPressure")
    expect(JSON.stringify(devBody)).not.toContain("platform-app")

    vi.mocked(auth).mockResolvedValue({ groups: ["viewer"], teams: ["frontend-team"], user: { role: "viewer" } } as never)
    const viewerRes = await GET(req())
    expect(viewerRes.status).toBe(200)
    const viewerBody = await viewerRes.json()
    expect(viewerBody.map((e: { title: string }) => e.title)).not.toContain("NodeDiskPressure")

    vi.mocked(auth).mockResolvedValue(clusterAdminSession as never)
    const adminRes = await GET(req())
    expect(adminRes.status).toBe(200)
    const adminBody = await adminRes.json()
    expect(adminBody.map((e: { title: string }) => e.title)).toContain("NodeDiskPressure")
  })
})

describe("GET /api/events — project revocation cache isolation (M2)", () => {
  it("invalidates cache across restart and re-filters cached events when an argocdProject grant is revoked with unchanged namespaces/claims", async () => {
    const sharedCache = new Map<string, unknown>()
    let activeProjects = ["alpha", "beta"]

    const setupRoute = async () => {
      vi.resetModules()
      vi.doMock("fs", async (importOriginal) => {
        const actual = await importOriginal<typeof import("fs")>()
        const mockedReadFileSync = ((...args: Parameters<typeof actual.readFileSync>) => {
          if (String(args[0]).endsWith("role-filter.json")) {
            return JSON.stringify({
              teamMappings: [
                { group: "team-a", namespaces: ["team-a-ns"], argocdProjects: activeProjects },
              ],
            })
          }
          return actual.readFileSync(...args)
        }) as typeof actual.readFileSync
        return {
          ...actual,
          default: { ...actual, readFileSync: mockedReadFileSync },
          readFileSync: mockedReadFileSync,
        }
      })
      vi.doMock("@/lib/auth", () => ({
        auth: vi.fn().mockResolvedValue({ groups: ["developer"], teams: ["team-a"], user: { role: "developer" } }),
      }))
      vi.doMock("@/lib/argocd", async (importOriginal) => {
        const actual = await importOriginal<typeof import("@/lib/argocd")>()
        return {
          ...actual,
          getArgoApps: vi.fn().mockResolvedValue([
            fakeApp("team-a-app", "alpha", "team-a-ns"),
            fakeApp("team-b-secret", "beta", "team-b-ns"),
          ]),
        }
      })
      vi.doMock("@/lib/alertmanager", () => ({ getAlerts: vi.fn().mockResolvedValue([]) }))
      vi.doMock("@/lib/valkey", () => ({
        cacheGet: vi.fn(async (key: string) => sharedCache.get(key) ?? null),
        cacheSet: vi.fn(async (key: string, value: unknown) => {
          sharedCache.set(key, value)
        }),
      }))
      vi.doMock("@/lib/k8s-client", async (importOriginal) => {
        const actual = await importOriginal<typeof import("@/lib/k8s-client")>()
        return {
          ...actual,
          getNamespaces: vi.fn().mockResolvedValue([
            { name: "team-a-ns", status: "Active", labels: {}, createdAt: "2026-01-01T00:00:00Z" },
            { name: "team-b-ns", status: "Active", labels: {}, createdAt: "2026-01-01T00:00:00Z" },
          ]),
        }
      })
      return import("./route")
    }

    try {
      // 1. Before revocation: beta is granted -> cache warmed with team-a-app & team-b-secret
      const routeBefore = await setupRoute()
      const resBefore = await routeBefore.GET(req())
      expect(resBefore.status).toBe(200)
      const beforeTitles = (await resBefore.json()).map((e: { title: string }) => e.title)
      expect(beforeTitles).toContain("team-a-app")
      expect(beforeTitles).toContain("team-b-secret")
      expect(sharedCache.size).toBe(1)
      const oldPayload = Array.from(sharedCache.values())[0]

      // 2. Revoke beta -> alpha only (same claims, same namespaces), simulate restart with warm Valkey
      activeProjects = ["alpha"]
      const routeAfter = await setupRoute()
      const resAfter = await routeAfter.GET(req())
      expect(resAfter.status).toBe(200)
      const afterBody = await resAfter.json()
      expect(afterBody.map((e: { title: string }) => e.title)).toEqual(["team-a-app"])
      expect(JSON.stringify(afterBody)).not.toContain("team-b-secret")
      expect(sharedCache.size).toBe(2)

      // 3. Even if the old cached payload were served via key collision, re-filtering on read must drop team-b-secret
      const { cacheGet } = await import("@/lib/valkey")
      vi.mocked(cacheGet).mockResolvedValueOnce(oldPayload as never)
      const resForcedHit = await routeAfter.GET(req())
      expect(resForcedHit.status).toBe(200)
      const forcedBody = await resForcedHit.json()
      expect(forcedBody.map((e: { title: string }) => e.title)).toEqual(["team-a-app"])
      expect(JSON.stringify(forcedBody)).not.toContain("team-b-secret")
    } finally {
      vi.doUnmock("fs")
    }
  })
})
