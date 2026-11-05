import { afterEach, describe, expect, it, vi } from "vitest"
import type { TrivyDbStatus } from "@/types/security"

vi.mock("@/lib/auth", () => ({ requireRole: vi.fn() }))
vi.mock("@/lib/trivy-db-status", () => ({ getTrivyDbStatus: vi.fn() }))

import { requireRole } from "@/lib/auth"
import { getTrivyDbStatus } from "@/lib/trivy-db-status"
import { GET } from "./route"

const status = (overall: TrivyDbStatus["overall"], harborReachable = true): TrivyDbStatus => ({
  observedAt: "2026-11-04T00:00:00.000Z",
  sloDays: 7,
  harborReachable,
  overall,
  dbRegistry: "harbor.devtools.svc.cluster.local/trivy-db/aquasecurity/trivy-db",
  artifacts: [{ name: "trivy-db", consumer: "vulnScan", sloDays: 7, repository: "aquasecurity/trivy-db", tag: "2", status: overall }],
  scanner: { state: "ok", reportCount: 7, newestReportAt: null, recentScanFailures: 0, lastFailureAt: null },
})

describe("GET /api/security/trivy-db", () => {
  afterEach(() => vi.resetAllMocks())

  it("returns 401 and 403 before reading anything", async () => {
    vi.mocked(requireRole).mockResolvedValue({ error: "unauthorized" })
    expect((await GET(new Request("http://x"))).status).toBe(401)
    vi.mocked(requireRole).mockResolvedValue({ error: "forbidden" } as never)
    expect((await GET(new Request("http://x"))).status).toBe(403)
    expect(getTrivyDbStatus).not.toHaveBeenCalled()
    expect(requireRole).toHaveBeenCalledWith("cluster-admin")
  })

  it.each([["fresh", true], ["stale", true], ["missing", true], ["unknown", false]] as const)(
    "returns the %s status body",
    async (overall, reachable) => {
      vi.mocked(requireRole).mockResolvedValue({ session: { user: { role: "cluster-admin" } } } as never)
      vi.mocked(getTrivyDbStatus).mockResolvedValue(status(overall, reachable))
      const res = await GET(new Request("http://x"))
      expect(res.status).toBe(200)
      const body = await res.json()
      expect(body.overall).toBe(overall)
      expect(body.harborReachable).toBe(reachable)
    },
  )

  it("returns a generic 500 without leaking the error", async () => {
    vi.mocked(requireRole).mockResolvedValue({ session: { user: { role: "cluster-admin" } } } as never)
    vi.mocked(getTrivyDbStatus).mockRejectedValue(new Error("secret-host:1234 refused"))
    vi.spyOn(console, "error").mockImplementation(() => {})
    const res = await GET(new Request("http://x"))
    expect(res.status).toBe(500)
    expect(await res.text()).not.toContain("secret-host")
  })
})
