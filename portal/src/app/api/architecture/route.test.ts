import { beforeEach, expect, it, vi } from "vitest"
vi.mock("@/lib/auth", () => ({ auth: vi.fn(async () => ({ user: {} })) }))
vi.mock("@/lib/valkey", () => ({ cacheGet: vi.fn(), cacheSet: vi.fn() }))
vi.mock("@/lib/argocd", () => ({ getArgoApps: vi.fn() }))
vi.mock("@/lib/workload-health", async (original) => ({ ...await original<typeof import("@/lib/workload-health")>(), getWorkloadSnapshot: vi.fn() }))
import { getArgoApps } from "@/lib/argocd"
import { cacheGet } from "@/lib/valkey"
import { getWorkloadSnapshot } from "@/lib/workload-health"
import { GET } from "./route"

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(getArgoApps).mockResolvedValue([])
  vi.mocked(getWorkloadSnapshot).mockResolvedValue({ complete: false, workloads: [], hidden: [] })
})
it("resolves actual workload identities, including nodes without Argo applications", async () => {
  vi.mocked(getWorkloadSnapshot).mockResolvedValue({ complete: false, hidden: [], workloads: [
    { namespace: "devtools", kind: "Deployment", name: "narwhal-portal", status: "healthy" },
    { namespace: "iam", kind: "StatefulSet", name: "keycloak", status: "degraded" },
    { namespace: "devtools", kind: "Deployment", name: "argocd-server", status: "down" },
    { namespace: "platform-system", kind: "Deployment", name: "cnpg-cloudnative-pg", status: "healthy" },
    { namespace: "other", kind: "Deployment", name: "gitea", status: "healthy" },
  ] })
  const data = await (await GET()).json()
  const status = (id: string) => data.nodes.find((n: { id: string }) => n.id === id).status
  expect(status("portal")).toBe("healthy")
  expect(status("keycloak")).toBe("degraded")
  expect(status("argocd")).toBe("offline")
  expect(status("cnpg")).toBe("healthy")
  expect(status("gitea")).toBe("unknown")
})
it("falls back to existing Argo apps, leaving absent evidence unknown", async () => {
  vi.mocked(getWorkloadSnapshot).mockRejectedValue(new Error("403"))
  vi.mocked(getArgoApps).mockResolvedValue([{ metadata: { name: "prometheus-stack" }, status: { health: { status: "Healthy" } } }] as never)
  const data = await (await GET()).json()
  expect(data.nodes.find((n: { id: string }) => n.id === "grafana").status).toBe("healthy")
  expect(data.nodes.find((n: { id: string }) => n.id === "gitea").status).toBe("unknown")
})
it("does not list workloads on a topology cache hit", async () => {
  vi.mocked(cacheGet).mockResolvedValue({ nodes: [], edges: [] })
  expect(await (await GET()).json()).toEqual({ nodes: [], edges: [] })
  expect(getWorkloadSnapshot).not.toHaveBeenCalled()
})
