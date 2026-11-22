import { beforeEach, describe, expect, it, vi } from "vitest"
import type { ComplianceSummary } from "@/types/compliance"

vi.mock("server-only", () => ({}))

vi.mock("@/lib/auth", () => ({
  requireRole: vi.fn(async () => ({ user: { role: "cluster-admin" } })),
}))

const mockGetComplianceSummary = vi.fn<() => Promise<ComplianceSummary>>()
vi.mock("@/lib/compliance", () => ({
  ComplianceDataUnavailableError: class ComplianceDataUnavailableError extends Error {
    readonly source: string
    constructor(source: string, message: string) {
      super(message)
      this.name = "ComplianceDataUnavailableError"
      this.source = source
    }
  },
  getComplianceSummary: () => mockGetComplianceSummary(),
}))

import { GET } from "./route"

describe("GET /api/compliance/summary", () => {
  beforeEach(() => {
    mockGetComplianceSummary.mockReset()
  })

  it("returns HTTP 503 unavailable from /api/compliance/summary when every primary source fails", async () => {
    mockGetComplianceSummary.mockResolvedValueOnce({
      totalConfigAuditFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      lowSeverityConfigAuditFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      acceptedSystemConfigAuditFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      totalRbacFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      acceptedRbacFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      totalInfraFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      acceptedInfraFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      frameworks: [],
      scannedWorkloads: 0,
      scannedRbacObjects: 0,
      scannedNodes: 0,
      lastUpdated: "",
      dataUnavailable: true,
      sources: {
        configAudit: { status: "unavailable", lastUpdated: null, error: "failed" },
        rbac: { status: "unavailable", lastUpdated: null, error: "failed" },
        infra: { status: "unavailable", lastUpdated: null, rebuildIntervalHours: 6, error: "failed" },
        frameworks: { status: "unavailable", lastUpdated: null, rebuildIntervalHours: 6, error: "failed" },
      },
    })

    const res = await GET()
    expect(res.status).toBe(503)
    const body = await res.json()
    expect(body).toMatchObject({ unavailable: true })
  })

  it("returns HTTP 200 when at least one primary source succeeded (even if partial or another source failed)", async () => {
    mockGetComplianceSummary.mockResolvedValueOnce({
      totalConfigAuditFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      lowSeverityConfigAuditFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      acceptedSystemConfigAuditFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      totalRbacFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      acceptedRbacFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      totalInfraFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      acceptedInfraFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      frameworks: [],
      scannedWorkloads: 10,
      scannedRbacObjects: 0,
      scannedNodes: 1,
      lastUpdated: "2026-11-22T06:00:00Z",
      dataUnavailable: true,
      partial: true,
      sources: {
        configAudit: { status: "ok", lastUpdated: "2026-11-22T06:00:00Z", partial: true, partialSources: ["replicasets"] },
        rbac: { status: "unavailable", lastUpdated: null, error: "failed" },
        infra: { status: "ok", lastUpdated: "2026-11-22T09:24:19Z", rebuildIntervalHours: 6 },
        frameworks: { status: "ok", lastUpdated: "2026-11-22T06:00:00Z", rebuildIntervalHours: 6 },
      },
    })

    const res = await GET()
    expect(res.status).toBe(200)
    const body = await res.json()
    expect(body.partial).toBe(true)
    expect(body.sources.configAudit.partialSources).toEqual(["replicasets"])
  })
})
