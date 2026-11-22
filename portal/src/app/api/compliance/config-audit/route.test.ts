import { beforeEach, describe, expect, it, vi } from "vitest"
import type { ConfigAuditDetail, ConfigAuditRow } from "@/types/compliance"

vi.mock("server-only", () => ({}))

vi.mock("@/lib/auth", () => ({
  requireRole: vi.fn(async () => ({ user: { role: "cluster-admin" } })),
}))

const mockGetConfigAuditList = vi.fn<() => Promise<ConfigAuditRow[]>>()
const mockGetConfigAuditDetail = vi.fn<
  (scope: "cluster" | "namespace", namespace: string, kind: string, name: string) => Promise<ConfigAuditDetail | null>
>()

vi.mock("@/lib/compliance", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/compliance")>()
  return {
    ...actual,
    getConfigAuditList: () => mockGetConfigAuditList(),
    getConfigAuditDetail: (
      scope: "cluster" | "namespace",
      namespace: string,
      kind: string,
      name: string,
    ) => mockGetConfigAuditDetail(scope, namespace, kind, name),
  }
})

import { ComplianceDataUnavailableError, ComplianceDetailAmbiguousError } from "@/lib/compliance"
import { GET } from "./route"

describe("GET /api/compliance/config-audit", () => {
  beforeEach(() => {
    mockGetConfigAuditList.mockReset()
    mockGetConfigAuditDetail.mockReset()
  })

  it("passes (scope, namespace, kind, name) through to getConfigAuditDetail", async () => {
    const detail: ConfigAuditDetail = {
      scope: "cluster",
      namespace: "",
      kind: "ClusterRoleBinding",
      name: "shared:rbac",
      summary: { Critical: 0, High: 1, Medium: 0, Low: 0 },
      accepted: false,
      checks: [{ id: "KSV122", title: "Broad privileges", severity: "High", success: false }],
    }
    mockGetConfigAuditDetail.mockResolvedValueOnce(detail)

    const req = new Request(
      "http://localhost/api/compliance/config-audit?scope=cluster&namespace=&kind=ClusterRoleBinding&name=shared%3Arbac",
    )
    const res = await GET(req)
    expect(res.status).toBe(200)
    expect(mockGetConfigAuditDetail).toHaveBeenCalledWith("cluster", "", "ClusterRoleBinding", "shared:rbac")
    expect(await res.json()).toEqual(detail)
  })

  it("returns HTTP 409 ambiguous when kind is missing or getConfigAuditDetail throws ComplianceDetailAmbiguousError", async () => {
    mockGetConfigAuditDetail.mockRejectedValueOnce(
      new ComplianceDetailAmbiguousError({ scope: "cluster", namespace: "", kind: "", name: "shared:rbac" }),
    )

    const req = new Request(
      "http://localhost/api/compliance/config-audit?scope=cluster&namespace=&name=shared%3Arbac",
    )
    const res = await GET(req)
    expect(res.status).toBe(409)
    expect(await res.json()).toMatchObject({ ambiguous: true })
  })

  it("returns HTTP 400 on invalid scope and HTTP 404 when detail is null", async () => {
    const badScopeRes = await GET(
      new Request("http://localhost/api/compliance/config-audit?scope=pod&namespace=dev&kind=Pod&name=app"),
    )
    expect(badScopeRes.status).toBe(400)
    expect(mockGetConfigAuditDetail).not.toHaveBeenCalled()

    mockGetConfigAuditDetail.mockResolvedValueOnce(null)
    const notFoundRes = await GET(
      new Request("http://localhost/api/compliance/config-audit?scope=namespace&namespace=dev&kind=Service&name=missing"),
    )
    expect(notFoundRes.status).toBe(404)
  })

  it("returns HTTP 503 unavailable when configAudit source fails", async () => {
    mockGetConfigAuditList.mockRejectedValueOnce(new ComplianceDataUnavailableError("configAudit", "403"))
    const res = await GET(new Request("http://localhost/api/compliance/config-audit"))
    expect(res.status).toBe(503)
    expect(await res.json()).toMatchObject({ unavailable: true, source: "configAudit" })
  })
})
