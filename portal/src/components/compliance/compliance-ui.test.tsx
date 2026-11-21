import { describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

const mockGetConfigAuditDetail = vi.hoisted(() => vi.fn())
const mockGetRbacAuditDetail = vi.hoisted(() => vi.fn())
const mockGetInfraAuditDetail = vi.hoisted(() => vi.fn())
const mockGetComplianceFrameworkDetail = vi.hoisted(() => vi.fn())
const mockGetLocale = vi.hoisted(() => vi.fn(async (): Promise<"ko" | "en"> => "ko"))

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
  getInfraAuditDetail: mockGetInfraAuditDetail,
  getComplianceFrameworkDetail: mockGetComplianceFrameworkDetail,
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
import { en, ko, translateAcceptedReason } from "@/lib/i18n"
import type {
  ComplianceFramework,
  ComplianceFrameworkDetail,
  ComplianceSummary,
  ConfigAuditDetail,
  ConfigAuditRow,
  InfraAuditDetail,
  InfraAuditRow,
  RbacAuditDetail,
  RbacAuditRow,
} from "@/types/compliance"
import { COMPLIANCE_EXCEPTIONS_DOC_ANCHOR } from "@/lib/compliance-accepted"
import { FailureCounters } from "./failure-counters"
import { ConfigAuditTable, ExpandedConfigPanel } from "./config-audit-table"
import { ExpandedFrameworkPanel, FrameworksGrid } from "./frameworks-grid"
import { ExpandedInfraPanel, InfraAuditList } from "./infra-audit-list"
import { ExpandedRbacPanel, RbacAuditTable } from "./rbac-audit-table"
import ConfigAuditDetailPage from "@/app/(dashboard)/compliance/config-audit/[ns]/[name]/page"
import FrameworkDetailPage from "@/app/(dashboard)/compliance/frameworks/[id]/page"
import InfraAuditDetailPage from "@/app/(dashboard)/compliance/infra-audit/[node]/page"
import RbacAuditDetailPage from "@/app/(dashboard)/compliance/rbac-audit/[ns]/[name]/page"

describe("Compliance UI accepted exceptions", () => {
  it("FailureCounters shows accepted count alongside actionable count in ko and en", () => {
    const summary: ComplianceSummary = {
      totalConfigAuditFailures: { Critical: 0, High: 3, Medium: 2, Low: 0 },
      lowSeverityConfigAuditFailures: { Critical: 0, High: 0, Medium: 0, Low: 40 },
      acceptedSystemConfigAuditFailures: { Critical: 0, High: 22, Medium: 15, Low: 8 },
      totalRbacFailures: { Critical: 0, High: 0, Medium: 2, Low: 0 },
      acceptedRbacFailures: { Critical: 10, High: 20, Medium: 30, Low: 0 },
      totalInfraFailures: { Critical: 0, High: 0, Medium: 0, Low: 1 },
      acceptedInfraFailures: { Critical: 0, High: 1, Medium: 0, Low: 0 },
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
    expect(koHtml).toContain("+1 수용(예외)")

    const enHtml = renderToStaticMarkup(<FailureCounters summary={summary} locale="en" />)
    expect(enHtml).toContain("40 low-severity (hygiene) · 45 accepted (system/exception)")
    expect(enHtml).toContain("+60 accepted (built-in/upstream/exception)")
    expect(enHtml).toContain("+1 accepted (exception)")
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

  it("FrameworksGrid, ExpandedFrameworkPanel, FrameworkDetailPage, and FailureCounters separate actionable from accepted controls", async () => {
    const frameworks: ComplianceFramework[] = [
      {
        id: "k8s-nsa-1.0",
        title: "NSA Kubernetes Hardening",
        passCount: 18,
        failCount: 9,
        actionableFailCount: 0,
        acceptedFailCount: 9,
        totalControls: 27,
        passRate: 18 / 27,
        effectivePassRate: 1,
      },
      {
        id: "k8s-cis-1.23",
        title: "CIS Benchmark",
        passCount: 101,
        failCount: 15,
        actionableFailCount: 2,
        acceptedFailCount: 13,
        totalControls: 116,
        passRate: 101 / 116,
        effectivePassRate: 114 / 116,
      },
    ]

    const summaryWithFw: ComplianceSummary = {
      totalConfigAuditFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      lowSeverityConfigAuditFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      acceptedSystemConfigAuditFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      totalRbacFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      acceptedRbacFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      totalInfraFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      acceptedInfraFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      frameworks,
      scannedWorkloads: 50,
      scannedRbacObjects: 30,
      scannedNodes: 6,
      lastUpdated: new Date().toISOString(),
    }
    const countersHtml = renderToStaticMarkup(<FailureCounters summary={summaryWithFw} locale="en" />)
    expect(countersHtml).toContain("99%")
    expect(countersHtml).toContain("2 actionable · 22 accepted")

    vi.mocked(useQuery).mockReturnValue({
      data: frameworks,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as never)

    const gridEn = renderToStaticMarkup(
      <LocaleProvider locale="en">
        <FrameworksGrid />
      </LocaleProvider>,
    )
    expect(gridEn).toContain("2 actionable controls · 22 accepted (raw failed 24)")
    expect(gridEn).toContain("100%")
    expect(gridEn).toContain("+9 accepted (raw 9)")
    expect(gridEn).toContain("+13 accepted (raw 15)")
    expect(gridEn).toContain(COMPLIANCE_EXCEPTIONS_DOC_ANCHOR)

    const gridKo = renderToStaticMarkup(
      <LocaleProvider locale="ko">
        <FrameworksGrid />
      </LocaleProvider>,
    )
    expect(gridKo).toContain("조치가능 컨트롤 2개 · 수용 22개 (원본 실패 24개)")
    expect(gridKo).toContain("위험 수용 예외 문서")

    const detail: ComplianceFrameworkDetail = {
      ...frameworks[1],
      controls: [
        {
          id: "5.2.2",
          name: "Minimize privileged containers",
          severity: "High",
          passCount: 0,
          failCount: 3,
          actionableFailCount: 0,
          acceptedFailCount: 3,
          accepted: true,
          acceptedScope: "accept",
        },
        {
          id: "5.7.3",
          name: "Apply Security Context to Pods",
          severity: "High",
          passCount: 0,
          failCount: 158,
          actionableFailCount: 24,
          acceptedFailCount: 134,
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
        <ExpandedFrameworkPanel id="k8s-cis-1.23" />
      </LocaleProvider>,
    )
    expect(panelHtml).toContain("5.7.3")
    expect(panelHtml).toContain("✗ 24")
    expect(panelHtml).toContain("+134 accepted (raw 158)")
    expect(panelHtml).toContain("Accepted exception controls (1)")
    expect(panelHtml).toContain("5.2.2")
    expect(panelHtml).toContain(COMPLIANCE_EXCEPTIONS_DOC_ANCHOR)
    expect(panelHtml.indexOf("5.7.3")).toBeLessThan(panelHtml.indexOf("Accepted exception controls (1)"))

    mockGetLocale.mockResolvedValue("en")
    mockGetComplianceFrameworkDetail.mockResolvedValue(detail)
    const pageEnEl = await FrameworkDetailPage({
      params: Promise.resolve({ id: "k8s-cis-1.23" }),
    })
    const pageEnHtml = renderToStaticMarkup(pageEnEl)
    expect(pageEnHtml).toContain("98%")
    expect(pageEnHtml).toContain("✗ 2 Failed (actionable)")
    expect(pageEnHtml).toContain("+13 accepted")
    expect(pageEnHtml).toContain("Raw failed 15 (pass rate 87%)")
    expect(pageEnHtml).toContain("Accepted exception controls (1)")
    expect(pageEnHtml).toContain("Risk accepted")
    expect(pageEnHtml).toContain(COMPLIANCE_EXCEPTIONS_DOC_ANCHOR)

    mockGetLocale.mockResolvedValue("ko")
    const pageKoEl = await FrameworkDetailPage({
      params: Promise.resolve({ id: "k8s-cis-1.23" }),
    })
    const pageKoHtml = renderToStaticMarkup(pageKoEl)
    expect(pageKoHtml).toContain("실패 (조치가능)")
    expect(pageKoHtml).toContain("수용된 예외 컨트롤 (1)")
    expect(pageKoHtml).toContain("위험 수용 예외 문서")
  })

  it("keeps raw failCount visible when only superseded ReplicaSets were dropped (acceptedFail === 0)", async () => {
    const frameworks: ComplianceFramework[] = [
      {
        id: "rs-only",
        title: "Superseded RS Only",
        passCount: 8,
        failCount: 2,
        actionableFailCount: 0,
        acceptedFailCount: 0,
        totalControls: 10,
        passRate: 0.8,
        effectivePassRate: 1,
      },
    ]

    const summary: ComplianceSummary = {
      totalConfigAuditFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      lowSeverityConfigAuditFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      acceptedSystemConfigAuditFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      totalRbacFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      acceptedRbacFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      totalInfraFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      acceptedInfraFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      frameworks,
      scannedWorkloads: 10,
      scannedRbacObjects: 5,
      scannedNodes: 1,
      lastUpdated: new Date().toISOString(),
    }

    const countersEn = renderToStaticMarkup(<FailureCounters summary={summary} locale="en" />)
    expect(countersEn).toContain("100%")
    expect(countersEn).toContain("0 actionable · 0 accepted (raw 2)")

    const countersKo = renderToStaticMarkup(<FailureCounters summary={summary} locale="ko" />)
    expect(countersKo).toContain("조치가능 0건 · 수용 0건 (원본 2)")

    vi.mocked(useQuery).mockReturnValue({
      data: frameworks,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as never)

    const gridEn = renderToStaticMarkup(
      <LocaleProvider locale="en">
        <FrameworksGrid />
      </LocaleProvider>,
    )
    expect(gridEn).toContain("0 actionable controls · 0 accepted (raw failed 2)")
    expect(gridEn).toContain("raw 2")
    expect(gridEn).not.toContain("+0 accepted")

    const detail: ComplianceFrameworkDetail = {
      ...frameworks[0],
      controls: [
        {
          id: "5.2.1",
          name: "Superseded ReplicaSet control",
          severity: "High",
          passCount: 0,
          failCount: 2,
          actionableFailCount: 0,
          acceptedFailCount: 0,
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

    const panelEn = renderToStaticMarkup(
      <LocaleProvider locale="en">
        <ExpandedFrameworkPanel id="rs-only" />
      </LocaleProvider>,
    )
    expect(panelEn).toContain("✗ 0")
    expect(panelEn).toContain("raw 2")
    expect(panelEn).not.toContain("+0 accepted")

    mockGetLocale.mockResolvedValue("en")
    mockGetComplianceFrameworkDetail.mockResolvedValue(detail)
    const pageEl = await FrameworkDetailPage({
      params: Promise.resolve({ id: "rs-only" }),
    })
    const pageHtml = renderToStaticMarkup(pageEl)
    expect(pageHtml).toContain("✗ 0 Failed (actionable)")
    expect(pageHtml).toContain("Raw failed 2 (pass rate 80%)")
    expect(pageHtml).toContain("raw 2")
    expect(pageHtml).not.toContain("+0 accepted")
  })

  it("InfraAuditList, ExpandedInfraPanel, and InfraAuditDetailPage treat failing KCV0056 as actionable while preserving accepted-row UI machinery", async () => {
    const infraRows: InfraAuditRow[] = [
      {
        node: "narwhal-master-1",
        summary: { Critical: 0, High: 1, Medium: 0, Low: 0 },
        actionableSummary: { Critical: 0, High: 1, Medium: 0, Low: 0 },
        acceptedSummary: { Critical: 0, High: 0, Medium: 0, Low: 0 },
        accepted: false,
      },
      {
        node: "synthetic-accepted-node",
        summary: { Critical: 0, High: 0, Medium: 1, Low: 0 },
        actionableSummary: { Critical: 0, High: 0, Medium: 0, Low: 0 },
        acceptedSummary: { Critical: 0, High: 0, Medium: 1, Low: 0 },
        accepted: true,
        acceptedScope: "upstream",
      },
      {
        node: "synthetic-mixed-node",
        summary: { Critical: 0, High: 0, Medium: 1, Low: 1 },
        actionableSummary: { Critical: 0, High: 0, Medium: 0, Low: 1 },
        acceptedSummary: { Critical: 0, High: 0, Medium: 1, Low: 0 },
        accepted: false,
      },
    ]

    vi.mocked(useQuery).mockReturnValue({
      data: infraRows,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as never)

    const listEn = renderToStaticMarkup(
      <LocaleProvider locale="en">
        <InfraAuditList />
      </LocaleProvider>,
    )
    expect(listEn).toContain("2 actionable · 1 accepted (exception)")
    expect(listEn).toContain("narwhal-master-1")
    expect(listEn).toContain("synthetic-accepted-node")
    expect(listEn).toContain("Upstream accepted")
    expect(listEn).toContain("+1 accepted")
    expect(listEn).toContain("High: 1")
    expect(listEn).toContain("Low: 1")
    expect(listEn).toContain("1 issues")

    const listKo = renderToStaticMarkup(
      <LocaleProvider locale="ko">
        <InfraAuditList />
      </LocaleProvider>,
    )
    expect(listKo).toContain("+1 수용")

    const detail: InfraAuditDetail = {
      node: "narwhal-master-1",
      summary: { Critical: 0, High: 1, Medium: 0, Low: 1 },
      actionableSummary: { Critical: 0, High: 1, Medium: 0, Low: 1 },
      acceptedSummary: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      accepted: false,
      checks: [
        {
          id: "KCV0056",
          title: "Ensure CNI file perms 600",
          severity: "High",
          success: false,
          accepted: false,
        },
        {
          id: "KCV0059",
          title: "Ensure etcd data dir ownership",
          severity: "Low",
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

    const expandedKo = renderToStaticMarkup(
      <LocaleProvider locale="ko">
        <ExpandedInfraPanel node="narwhal-master-1" />
      </LocaleProvider>,
    )
    expect(expandedKo).toContain("KCV0056")
    expect(expandedKo).toContain("KCV0059")
    expect(expandedKo).not.toContain("업스트림 수용")
    expect(expandedKo.indexOf("KCV0056")).toBeLessThan(expandedKo.indexOf("KCV0059"))

    mockGetLocale.mockResolvedValue("en")
    mockGetInfraAuditDetail.mockResolvedValue(detail)
    const detailEl = await InfraAuditDetailPage({ params: Promise.resolve({ node: "narwhal-master-1" }) })
    const detailHtml = renderToStaticMarkup(detailEl)
    expect(detailHtml).toContain("KCV0056")
    expect(detailHtml).toContain("KCV0059")
    expect(detailHtml).not.toContain("Accepted (upstream)")
    expect(detailHtml).toContain("High")
    expect(detailHtml).toContain("Low")
  })
})
