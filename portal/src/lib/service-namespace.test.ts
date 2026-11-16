import { beforeEach, describe, expect, it, vi } from "vitest"
import type { EffectiveScope } from "./scope"

vi.mock("./argocd", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./argocd")>()
  return { ...actual, getArgoAppsOrThrow: vi.fn() }
})
vi.mock("./workload-health", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./workload-health")>()
  return { ...actual, getWorkloadSnapshot: vi.fn() }
})

const { getArgoAppsOrThrow } = await import("./argocd")
const { getWorkloadSnapshot } = await import("./workload-health")
const {
  buildServiceNamespaceMap,
  getInScopeServiceNames,
  hasServiceInScope,
  isServiceInScope,
} = await import("./service-namespace")

function makeScope(all: boolean, namespaces: string[]): EffectiveScope {
  return {
    all,
    namespaces: new Set(namespaces),
    argocdProjects: [],
    hasMapping: all || namespaces.length > 0,
    fingerprint: all ? "admin" : namespaces.join(","),
    resolved: { all, names: new Set(namespaces), byLabel: new Set(namespaces), byPattern: new Set() },
    clusterId: "default",
  }
}

describe("service-namespace ownership (fail closed)", () => {
  const teamA = makeScope(false, ["team-a-ns"])
  const bothTeams = makeScope(false, ["team-a-ns", "team-b-ns"])
  const admin = makeScope(true, [])

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getArgoAppsOrThrow).mockResolvedValue([
      {
        metadata: { name: "checkout" },
        spec: { project: "default", destination: { namespace: "team-a-ns" } },
        status: { sync: { status: "Synced" }, health: { status: "Healthy" } },
      },
    ] as never)
    vi.mocked(getWorkloadSnapshot).mockResolvedValue({
      complete: true,
      workloads: [{ namespace: "team-a-ns", kind: "Deployment", name: "checkout", status: "healthy" }],
      livePods: [],
      hidden: [],
    })
  })

  it("denies non-admins when workload snapshot is incomplete (complete: false)", async () => {
    vi.mocked(getWorkloadSnapshot).mockResolvedValue({
      complete: false,
      workloads: [{ namespace: "team-a-ns", kind: "Deployment", name: "checkout", status: "healthy" }],
      livePods: [],
      hidden: [],
    })

    const ownership = await buildServiceNamespaceMap()
    expect(ownership.complete).toBe(false)
    expect(isServiceInScope("checkout", ownership, teamA)).toBe(false)
    expect(hasServiceInScope("checkout", ownership, teamA)).toBe(false)
    expect(getInScopeServiceNames(ownership, teamA).size).toBe(0)
    expect(isServiceInScope("checkout", ownership, admin)).toBe(true)
  })

  it("denies non-admins when ArgoCD or workload snapshot provider rejects", async () => {
    vi.mocked(getArgoAppsOrThrow).mockRejectedValueOnce(new Error("argocd timeout"))
    const argoFailed = await buildServiceNamespaceMap()
    expect(argoFailed.complete).toBe(false)
    expect(isServiceInScope("checkout", argoFailed, teamA)).toBe(false)

    vi.mocked(getWorkloadSnapshot).mockRejectedValueOnce(new Error("k8s unreachable"))
    const k8sFailed = await buildServiceNamespaceMap()
    expect(k8sFailed.complete).toBe(false)
    expect(isServiceInScope("checkout", k8sFailed, teamA)).toBe(false)
  })

  it("denies ambiguous names in isServiceInScope unless caller scope covers every namespace", async () => {
    vi.mocked(getWorkloadSnapshot).mockResolvedValue({
      complete: true,
      workloads: [
        { namespace: "team-a-ns", kind: "Deployment", name: "checkout", status: "healthy" },
        { namespace: "team-b-ns", kind: "Deployment", name: "checkout", status: "healthy" },
      ],
      livePods: [],
      hidden: [],
    })

    const ownership = await buildServiceNamespaceMap()
    expect(ownership.complete).toBe(true)
    expect(isServiceInScope("checkout", ownership, teamA)).toBe(false)
    expect(getInScopeServiceNames(ownership, teamA).has("checkout")).toBe(false)
    expect(isServiceInScope("checkout", ownership, bothTeams)).toBe(true)
    expect(hasServiceInScope("checkout", ownership, teamA)).toBe(true)
  })
})
