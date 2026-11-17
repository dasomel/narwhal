import { describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

const mockGetConfigAuditDetail = vi.hoisted(() => vi.fn())
const mockGetRbacAuditDetail = vi.hoisted(() => vi.fn())
const mockGetLocale = vi.hoisted(() => vi.fn(async () => "ko" as const))

vi.mock("server-only", () => ({}))
vi.mock("@/lib/dashboard-access", () => ({
  requireDashboardAccess: vi.fn(async () => ({ user: { role: "cluster-admin" } })),
}))
vi.mock("@/lib/i18n-server", () => ({
  getLocale: mockGetLocale,
}))
vi.mock("@/lib/compliance", () => ({
  getConfigAuditDetail: mockGetConfigAuditDetail,
  getRbacAuditDetail: mockGetRbacAuditDetail,
}))

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-query")>()
  return {
    ...actual,
    useQuery: vi.fn(),
  }
})

import { useQuery } from "@tanstack/react-query"
import { LocaleProvider } from "@/lib/i18n-client"
import { ko, translateAcceptedReason } from "@/lib/i18n"
import type {
  ComplianceSummary,
  ConfigAuditDetail,
  ConfigAuditRow,
  RbacAuditDetail,
  RbacAuditRow,
} from "@/types/compliance"
import { FailureCounters } from "./failure-counters"
import { ConfigAuditTable, ExpandedConfigPanel } from "./config-audit-table"
import { ExpandedRbacPanel, RbacAuditTable } from "./rbac-audit-table"
import ConfigAuditDetailPage from "@/app/(dashboard)/compliance/config-audit/[ns]/[name]/page"
import RbacAuditDetailPage from "@/app/(dashboard)/compliance/rbac-audit/[ns]/[name]/page"

describe("Compliance UI accepted exceptions", () => {
  it("FailureCounters shows accepted count alongside actionable count in ko and en", () => {
    const summary: ComplianceSummary = {
      totalConfigAuditFailures: { Critical: 0, High: 3, Medium: 2, Low: 0 },
      lowSeverityConfigAuditFailures: { Critical: 0, High: 0, Medium: 0, Low: 40 },
      acceptedSystemConfigAuditFailures: { Critical: 0, High: 22, Medium: 15, Low: 8 },
      totalRbacFailures: { Critical: 0, High: 0, Medium: 2, Low: 0 },
      acceptedRbacFailures: { Critical: 10, High: 20, Medium: 30, Low: 0 },
      totalInfraFailures: { Critical: 0, High: 1, Medium: 0, Low: 0 },
      frameworks: [{ id: "cis", title: "CIS", passCount: 8, failCount: 2, totalControls: 10, passRate: 0.8 }],
      scannedWorkloads: 76,
      scannedRbacObjects: 192,
      scannedNodes: 6,
      lastUpdated: new Date().toISOString(),
    }

    const koHtml = renderToStaticMarkup(<FailureCounters summary={summary} locale="ko" />)
    expect(koHtml).toContain("5")
    expect(koHtml).toContain("위생 40건(LOW) · 수용 45건(시스템/예외)")
    expect(koHtml).toContain("+60 수용(빌트인/업스트림/예외)")

    const enHtml = renderToStaticMarkup(<FailureCounters summary={summary} locale="en" />)
    expect(enHtml).toContain("40 low-severity (hygiene) · 45 accepted (system/exception)")
    expect(enHtml).toContain("+60 accepted (built-in/upstream/exception)")
  })

  it("ConfigAuditTable shows accepted badges and keeps accepted rows visible", () => {
    const rows: ConfigAuditRow[] = [
      {
        namespace: "security-system",
        kind: "DaemonSet",
        name: "falco",
        summary: { Critical: 0, High: 3, Medium: 4, Low: 5 },
        actionableSummary: { Critical: 0, High: 0, Medium: 0, Low: 0 },
        acceptedSummary: { Critical: 0, High: 3, Medium: 4, Low: 5 },
        accepted: true,
        acceptedScope: "accept",
      },
      {
        namespace: "devtools",
        kind: "ReplicaSet",
        name: "harbor-core-b569bd8dd",
        summary: { Critical: 0, High: 1, Medium: 0, Low: 3 },
        actionableSummary: { Critical: 0, High: 0, Medium: 0, Low: 0 },
        acceptedSummary: { Critical: 0, High: 1, Medium: 0, Low: 2 },
        accepted: true,
        acceptedScope: "upstream",
      },
      {
        namespace: "devtools",
        kind: "ReplicaSet",
        name: "narwhal-portal-74b459c658",
        summary: { Critical: 0, High: 1, Medium: 0, Low: 2 },
        actionableSummary: { Critical: 0, High: 1, Medium: 0, Low: 0 },
        acceptedSummary: { Critical: 0, High: 0, Medium: 0, Low: 2 },
        accepted: false,
      },
    ]

    vi.mocked(useQuery).mockReturnValue({
      data: rows,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as never)

    const html = renderToStaticMarkup(
      <LocaleProvider locale="en">
        <ConfigAuditTable />
      </LocaleProvider>,
    )

    expect(html).toContain("1 actionable · 0 low-severity (hygiene) · 2 accepted (system/exception)")
    expect(html).toContain("falco")
    expect(html).toContain("Risk accepted")
    expect(html).toContain("harbor-core-b569bd8dd")
    expect(html).toContain("Upstream accepted")
    expect(html).toContain("narwhal-portal-74b459c658")
  })

  it("ExpandedConfigPanel displays accepted badge with reason and prioritizes actionable checks", () => {
    const detail: ConfigAuditDetail = {
      namespace: "devtools",
      kind: "ReplicaSet",
      name: "narwhal-portal-74b459c658",
      summary: { Critical: 0, High: 1, Medium: 0, Low: 2 },
      accepted: false,
      checks: [
        {
          id: "KSV020",
          title: "Runs with UID <= 10000",
          severity: "Low",
          success: false,
          accepted: true,
          acceptedClass: "accept",
          acceptedReasonKey: "compliance.acceptedReason.portalUid1001",
          acceptedReason: "Portal image fixes non-root nextjs:nodejs UID/GID 1001 in portal/Dockerfile",
        },
        {
          id: "KSV014",
          title: "Root file system is not read-only",
          severity: "High",
          success: false,
          accepted: false,
        },
      ],
    }

    vi.mocked(useQuery).mockReturnValue({
      data: detail,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as never)

    const html = renderToStaticMarkup(
      <LocaleProvider locale="en">
        <ExpandedConfigPanel namespace="devtools" name="narwhal-portal-74b459c658" />
      </LocaleProvider>,
    )

    expect(html).toContain("KSV014")
    expect(html).toContain("KSV020")
    expect(html).toContain("Accepted")
    expect(html).toContain("Portal image fixes non-root nextjs:nodejs UID/GID 1001 in portal/Dockerfile")
    expect(html.indexOf("KSV014")).toBeLessThan(html.indexOf("KSV020"))

    const koHtml = renderToStaticMarkup(
      <LocaleProvider locale="ko">
        <ExpandedConfigPanel namespace="devtools" name="narwhal-portal-74b459c658" />
      </LocaleProvider>,
    )
    expect(koHtml).toContain(ko["compliance.acceptedReason.portalUid1001"])
    expect(koHtml).not.toContain("Portal image fixes non-root")
  })

  it("RbacAuditTable and ExpandedRbacPanel surface accepted exception badges and reasons", () => {
    const rbacRows: RbacAuditRow[] = [
      {
        namespace: "devtools",
        kind: "Role",
        name: "narwhal-portal-jobs",
        summary: { Critical: 0, High: 0, Medium: 1, Low: 0 },
        actionableSummary: { Critical: 0, High: 0, Medium: 0, Low: 0 },
        acceptedSummary: { Critical: 0, High: 0, Medium: 1, Low: 0 },
        accepted: true,
        acceptedScope: "accept",
      },
      {
        namespace: "",
        kind: "ClusterRole",
        name: "platform-admin",
        summary: { Critical: 1, High: 2, Medium: 3, Low: 0 },
        actionableSummary: { Critical: 0, High: 0, Medium: 1, Low: 0 },
        acceptedSummary: { Critical: 1, High: 2, Medium: 2, Low: 0 },
        accepted: false,
      },
    ]

    vi.mocked(useQuery).mockReturnValue({
      data: rbacRows,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as never)

    const tableHtml = renderToStaticMarkup(
      <LocaleProvider locale="en">
        <RbacAuditTable />
      </LocaleProvider>,
    )
    expect(tableHtml).toContain("1 actionable · 1 accepted (built-in/upstream/exception)")
    expect(tableHtml).toContain("Risk accepted")
    expect(tableHtml).toContain("platform-admin")

    const detail: RbacAuditDetail = {
      namespace: "",
      kind: "ClusterRole",
      name: "platform-admin",
      summary: { Critical: 1, High: 0, Medium: 1, Low: 0 },
      accepted: false,
      checks: [
        {
          id: "KSV041",
          title: "Manage secrets",
          severity: "Critical",
          success: false,
          accepted: true,
          acceptedClass: "accept",
          acceptedReasonKey: "compliance.acceptedReason.rbacPlatformAdmin",
          acceptedReason: "Administrative privileges required by OIDC platform-admin role",
        },
        {
          id: "KSV042",
          title: "Delete pod logs",
          severity: "Medium",
          success: false,
          accepted: false,
        },
      ],
    }

    vi.mocked(useQuery).mockReturnValue({
      data: detail,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as never)

    const panelHtml = renderToStaticMarkup(
      <LocaleProvider locale="en">
        <ExpandedRbacPanel namespace="" name="platform-admin" />
      </LocaleProvider>,
    )
    expect(panelHtml).toContain("KSV042")
    expect(panelHtml).toContain("KSV041")
    expect(panelHtml).toContain("Administrative privileges required by OIDC platform-admin role")
    expect(panelHtml.indexOf("KSV042")).toBeLessThan(panelHtml.indexOf("KSV041"))

    const koPanelHtml = renderToStaticMarkup(
      <LocaleProvider locale="ko">
        <ExpandedRbacPanel namespace="" name="platform-admin" />
      </LocaleProvider>,
    )
    expect(koPanelHtml).toContain(ko["compliance.acceptedReason.rbacPlatformAdmin"])
    expect(koPanelHtml).not.toContain("Administrative privileges required")
  })

  it("ConfigAuditDetailPage and RbacAuditDetailPage translate accepted reasons in ko locale", async () => {
    mockGetLocale.mockResolvedValue("ko")
    mockGetConfigAuditDetail.mockResolvedValue({
      namespace: "devtools",
      kind: "ReplicaSet",
      name: "narwhal-portal-74b459c658",
      summary: { Critical: 0, High: 0, Medium: 0, Low: 1 },
      accepted: false,
      checks: [
        {
          id: "KSV020",
          title: "Runs with UID <= 10000",
          severity: "Low",
          success: false,
          accepted: true,
          acceptedClass: "accept",
          acceptedReasonKey: "compliance.acceptedReason.portalUid1001",
          acceptedReason: "Portal image fixes non-root nextjs:nodejs UID/GID 1001 in portal/Dockerfile",
        },
      ],
    })
    mockGetRbacAuditDetail.mockResolvedValue({
      namespace: "",
      kind: "ClusterRole",
      name: "platform-admin",
      summary: { Critical: 1, High: 0, Medium: 0, Low: 0 },
      accepted: true,
      checks: [
        {
          id: "KSV041",
          title: "Manage secrets",
          severity: "Critical",
          success: false,
          accepted: true,
          acceptedClass: "accept",
          acceptedReasonKey: "compliance.acceptedReason.rbacPlatformAdmin",
          acceptedReason: "Administrative privileges required by OIDC platform-admin role",
        },
      ],
    })

    const configPageEl = await ConfigAuditDetailPage({
      params: Promise.resolve({ ns: "devtools", name: "narwhal-portal-74b459c658" }),
    })
    const configPageHtml = renderToStaticMarkup(configPageEl)
    expect(configPageHtml).toContain(ko["compliance.acceptedReason.portalUid1001"])
    expect(configPageHtml).not.toContain("Portal image fixes non-root")

    const rbacPageEl = await RbacAuditDetailPage({
      params: Promise.resolve({ ns: "", name: "platform-admin" }),
    })
    const rbacPageHtml = renderToStaticMarkup(rbacPageEl)
    expect(rbacPageHtml).toContain(ko["compliance.acceptedReason.rbacPlatformAdmin"])
    expect(rbacPageHtml).not.toContain("Administrative privileges required")

    expect(translateAcceptedReason("ko", "compliance.acceptedReason.systemNamespace", "fallback")).toBe(
      ko["compliance.acceptedReason.systemNamespace"],
    )
    expect(translateAcceptedReason("ko", undefined, "fallback")).toBe("fallback")
  })
})
