import { beforeEach, describe, expect, it, vi } from "vitest"
vi.mock("@/lib/auth", () => ({ auth: vi.fn() }))
vi.mock("@/lib/firing-alerts", () => ({ getFiringAlerts: vi.fn() }))
import { auth } from "@/lib/auth"
import { getFiringAlerts } from "@/lib/firing-alerts"
import { GET } from "./route"
beforeEach(() => vi.resetAllMocks())
describe("firing alerts RBAC", () => {
  it.each(["developer", "viewer", "guest"])("denies %s without contacting Alertmanager", async (role) => {
    vi.mocked(auth).mockResolvedValue({ user: { role } } as never)
    expect((await GET()).status).toBe(403)
    expect(getFiringAlerts).not.toHaveBeenCalled()
  })
  it("denies unauthenticated callers", async () => {
    vi.mocked(auth).mockResolvedValue(null as never)
    expect((await GET()).status).toBe(401)
    expect(getFiringAlerts).not.toHaveBeenCalled()
  })
  it("allows the Portal cluster-admin identity (Kubernetes platform-admin binding) and returns degraded evidence", async () => {
    vi.mocked(auth).mockResolvedValue({ user: { role: "cluster-admin" } } as never)
    vi.mocked(getFiringAlerts).mockResolvedValue({ alerts: [], degraded: true })
    const res = await GET()
    expect(res.status).toBe(200)
    expect(await res.json()).toEqual({ alerts: [], degraded: true })
  })
})
