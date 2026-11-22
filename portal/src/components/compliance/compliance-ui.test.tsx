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
vi.mock("@/lib/compliance", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/compliance")>()
  return {
    ...actual,
    getConfigAuditDetail: mockGetConfigAuditDetail,
    getRbacAuditDetail: mockGetRbacAuditDetail,
    getInfraAuditDetail: mockGetInfraAuditDetail,
    getComplianceFrameworkDetail: mockGetComplianceFrameworkDetail,
  }
})

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
    expect(koHtml).toContain("미수용 LOW 0건 · 위생 40건(LOW 수용) · 수용 45건(정확 규칙)")
    expect(koHtml).toContain("+60 수용(정확 규칙)")
    expect(koHtml).toContain("+1 수용(예외)")
    expect(koHtml).toContain("80.0%")

    const enHtml = renderToStaticMarkup(<FailureCounters summary={summary} locale="en" />)
    expect(enHtml).toContain("0 unaccepted LOW · 40 accepted hygiene (LOW) · 45 accepted (exact rules)")
    expect(enHtml).toContain("+60 accepted (exact rules)")
    expect(enHtml).toContain("+1 accepted (exception)")
    expect(enHtml).toContain("80.0%")
  })

  it("FailureCounters surfaces unused acceptance rules in ko and en", () => {
    const summary: ComplianceSummary = {
      totalConfigAuditFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      lowSeverityConfigAuditFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      acceptedSystemConfigAuditFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      totalRbacFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      acceptedRbacFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      totalInfraFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      acceptedInfraFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      frameworks: [],
      scannedWorkloads: 10,
      scannedRbacObjects: 5,
      scannedNodes: 1,
      lastUpdated: new Date().toISOString(),
      unusedAcceptanceRuleCount: 2,
      unusedAcceptanceRules: [
        {
          id: "cfg:platform-system:DaemonSet:metallb-speaker:accept",
          domain: "configAudit",
          scope: "namespace",
          namespace: "platform-system",
          kind: "DaemonSet",
          name: "metallb-speaker",
          checks: ["KSV009", "KSV119"],
          owner: "metallb 0.16.1",
          reviewedAt: "2026-11-23",
        },
        {
          id: "rbac:cluster:ClusterRole:ghost-pod-reaper:accept",
          domain: "rbac",
          scope: "cluster",
          namespace: "",
          kind: "ClusterRole",
          name: "ghost-pod-reaper",
          checks: ["KSV048"],
          owner: "narwhal",
          reviewedAt: "2026-11-23",
        },
      ],
    }

    const koHtml = renderToStaticMarkup(<FailureCounters summary={summary} locale="ko" />)
    expect(koHtml).toContain("미사용 수용 규칙 2건")
    expect(koHtml).toContain("cfg:platform-system:DaemonSet:metallb-speaker:accept")
    expect(koHtml).toContain("cluster/ClusterRole/ghost-pod-reaper")

    const enHtml = renderToStaticMarkup(<FailureCounters summary={summary} locale="en" />)
    expect(enHtml).toContain("2 unused acceptance rules")
    expect(enHtml).toContain("platform-system/DaemonSet/metallb-speaker")
    expect(enHtml).toContain("rbac:cluster:ClusterRole:ghost-pod-reaper:accept")
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

    expect(html).toContain("1 actionable (MEDIUM+) · 0 unaccepted LOW · 0 accepted hygiene (LOW) · 2 accepted (system/exception)")
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
        <ExpandedConfigPanel scope="namespace" namespace="devtools" kind="ReplicaSet" name="narwhal-portal-74b459c658" />
      </LocaleProvider>,
    )

    expect(html).toContain("KSV014")
    expect(html).toContain("KSV020")
    expect(html).toContain("Accepted")
    expect(html).toContain("Portal image fixes non-root nextjs:nodejs UID/GID 1001 in portal/Dockerfile")
    expect(html.indexOf("KSV014")).toBeLessThan(html.indexOf("KSV020"))

    const koHtml = renderToStaticMarkup(
      <LocaleProvider locale="ko">
        <ExpandedConfigPanel scope="namespace" namespace="devtools" kind="ReplicaSet" name="narwhal-portal-74b459c658" />
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
          acceptedReason: "rbac-policies.yaml grants the OIDC platform administrator",
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
    expect(panelHtml).toContain("rbac-policies.yaml grants the OIDC platform administrator")
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
          acceptedReason: "rbac-policies.yaml grants the OIDC platform administrator",
        },
      ],
    })

    const configPageEl = await ConfigAuditDetailPage({
      params: Promise.resolve({ ns: "devtools", name: "narwhal-portal-74b459c658" }),
      searchParams: Promise.resolve({ scope: "namespace", kind: "ReplicaSet" }),
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

    expect(translateAcceptedReason("ko", "compliance.acceptedReason.ciliumAccept", "fallback")).toBe(
      ko["compliance.acceptedReason.ciliumAccept"],
    )
    expect(translateAcceptedReason("ko", undefined, "fallback")).toBe("fallback")
  })

  it("FrameworksGrid, ExpandedFrameworkPanel, FrameworkDetailPage, and FailureCounters separate actionable from accepted controls", async () => {
    const frameworks: ComplianceFramework[] = [
      {
        id: "k8s-nsa-1.0",
        title: "NSA Kubernetes Hardening",
        passCount: 18,
        evaluatedPassCount: 18,
        notEvaluatedCount: 0,
        evaluatedPassRate: 18 / 27,
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
        evaluatedPassCount: 101,
        notEvaluatedCount: 0,
        evaluatedPassRate: 101 / 116,
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
    expect(countersHtml).toContain("83.2%")
    expect(countersHtml).toContain("(119/143)")
    expect(countersHtml).toContain("Exception-adjusted rate: 98.6% (141/143)")
    expect(countersHtml).toContain("2 actionable · 22 accepted")
    expect(countersHtml).toContain("No failure recorded (scanner records failures only)")
    expect(countersHtml).toContain("records failed checks only")

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
    expect(gridEn).toContain("2 actionable controls · 22 accepted · 0 not evaluated (raw failed 24)")
    expect(gridEn).toContain("87.0%")
    expect(gridEn).toContain("+9 accepted (raw 9)")
    expect(gridEn).toContain("+13 accepted (raw 15)")
    expect(gridEn).toContain(COMPLIANCE_EXCEPTIONS_DOC_ANCHOR)

    const gridKo = renderToStaticMarkup(
      <LocaleProvider locale="ko">
        <FrameworksGrid />
      </LocaleProvider>,
    )
    expect(gridKo).toContain("조치가능 컨트롤 2개 · 수용 22개 · 미평가 0개 (원본 실패 24개)")
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
          evaluationState: "failed",
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
          evaluationState: "failed",
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
    expect(pageEnHtml).toContain("87.0%")
    expect(pageEnHtml).toContain("Exception-adjusted rate: 98.2%")
    expect(pageEnHtml).toContain("No failure recorded (scanner records failures only)")
    expect(pageEnHtml).toContain("✗ 2 Failed (actionable)")
    expect(pageEnHtml).toContain("+13 accepted")
    expect(pageEnHtml).toContain("Raw failed 15 (raw pass rate 87.0%)")
    expect(pageEnHtml).toContain("Accepted exception controls (1)")
    expect(pageEnHtml).toContain("Risk accepted")
    expect(pageEnHtml).toContain(COMPLIANCE_EXCEPTIONS_DOC_ANCHOR)

    mockGetLocale.mockResolvedValue("ko")
    const pageKoEl = await FrameworkDetailPage({
      params: Promise.resolve({ id: "k8s-cis-1.23" }),
    })
    const pageKoHtml = renderToStaticMarkup(pageKoEl)
    expect(pageKoHtml).toContain("실패 기록 없음 (스캐너는 실패만 기록)")
    expect(pageKoHtml).toContain("실패한 검사만 기록")
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
        evaluatedPassCount: 8,
        notEvaluatedCount: 0,
        evaluatedPassRate: 8 / 10,
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
    expect(countersEn).toContain("80.0%")
    expect(countersEn).toContain("0 actionable · 0 accepted (raw 2)")

    const countersKo = renderToStaticMarkup(<FailureCounters summary={summary} locale="ko" />)
    expect(countersKo).toContain("실패 기록 없음 8 · 미평가 0 · 조치가능 0 · 수용 0 (원본 2)")
    expect(countersKo).toContain("실패 기록 없음 (스캐너는 실패만 기록)")

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
    expect(gridEn).toContain("0 actionable controls · 0 accepted · 0 not evaluated (raw failed 2)")
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
          evaluationState: "failed",
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
    expect(pageHtml).toContain("Raw failed 2 (raw pass rate 80.0%)")
    expect(pageHtml).toContain("raw 2")
    expect(pageHtml).not.toContain("+0 accepted")
  })

  it("renders not-evaluated groups, data-unavailable error states, and per-source timestamps with 6h rebuild note", async () => {
    const now = new Date().toISOString()
    const unavailableSummary: ComplianceSummary = {
      totalConfigAuditFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      unacceptedLowConfigAuditFailures: { Critical: 0, High: 0, Medium: 0, Low: 4 },
      lowSeverityConfigAuditFailures: { Critical: 0, High: 0, Medium: 0, Low: 12 },
      acceptedSystemConfigAuditFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      droppedConfigAuditReports: 3,
      droppedConfigAuditFailures: { Critical: 0, High: 2, Medium: 1, Low: 5 },
      totalRbacFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      acceptedRbacFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      totalInfraFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      acceptedInfraFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      frameworks: [
        {
          id: "k8s-pss-baseline-0.1",
          title: "PSS Baseline",
          passCount: 8,
          evaluatedPassCount: 2,
          notEvaluatedCount: 6,
          failCount: 3,
          actionableFailCount: 0,
          acceptedFailCount: 3,
          totalControls: 11,
          passRate: 8 / 11,
          evaluatedPassRate: 2 / 11,
          effectivePassRate: 5 / 11,
        },
      ],
      scannedWorkloads: 10,
      scannedRbacObjects: 0,
      scannedNodes: 1,
      lastUpdated: now,
      dataUnavailable: true,
      sources: {
        configAudit: { status: "ok", lastUpdated: now, oldestUpdated: now },
        rbac: { status: "unavailable", lastUpdated: null, error: "HTTP 503" },
        infra: { status: "ok", lastUpdated: now, rebuildIntervalHours: 6 },
        frameworks: { status: "ok", lastUpdated: now, rebuildIntervalHours: 6 },
      },
    }

    const countersEn = renderToStaticMarkup(<FailureCounters summary={unavailableSummary} locale="en" />)
    expect(countersEn).toContain("Data unavailable")
    expect(countersEn).toContain("4 unaccepted LOW · 12 accepted hygiene (LOW)")
    expect(countersEn).toContain("3 superseded RS dropped (8 checks)")
    expect(countersEn).toContain("6 not evaluated")
    expect(countersEn).toContain("6 hours")

    const detailWithNotEval: ComplianceFrameworkDetail = {
      ...unavailableSummary.frameworks[0],
      controls: [
        {
          id: "1",
          name: "HostProcess",
          severity: "High",
          passCount: 1,
          failCount: 0,
          evaluationState: "passed",
        },
        {
          id: "6",
          name: "Privilege Escalation",
          severity: "Medium",
          passCount: 0,
          failCount: 0,
          evaluationState: "not_evaluated",
          notEvaluatedReason: "unlinked_check_id",
          notEvaluatedDetail: "avd-ksv-0024",
        },
      ],
    }

    mockGetLocale.mockResolvedValue("en")
    mockGetComplianceFrameworkDetail.mockResolvedValue(detailWithNotEval)
    const fwPageEl = await FrameworkDetailPage({ params: Promise.resolve({ id: "k8s-pss-baseline-0.1" }) })
    const fwPageHtml = renderToStaticMarkup(fwPageEl)
    expect(fwPageHtml).toContain("HostProcess")
    expect(fwPageHtml).toContain("Not evaluated controls (1)")
    expect(fwPageHtml).toContain("Unlinked spec check ID")
    expect(fwPageHtml).toContain("avd-ksv-0024")
    expect(fwPageHtml).toContain("18.1%")

    const unavailableErr = Object.assign(new Error("unavailable"), { name: "ComplianceDataUnavailableError" })
    mockGetComplianceFrameworkDetail.mockRejectedValueOnce(unavailableErr)
    const errPageEl = await FrameworkDetailPage({ params: Promise.resolve({ id: "k8s-pss-baseline-0.1" }) })
    const errPageHtml = renderToStaticMarkup(errPageEl)
    expect(errPageHtml).toContain("Data unavailable")

    vi.mocked(useQuery).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch: vi.fn(),
    } as never)
    const gridErr = renderToStaticMarkup(
      <LocaleProvider locale="en">
        <FrameworksGrid />
      </LocaleProvider>,
    )
    expect(gridErr).toContain(en["dataState.unavailable"])
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

  it("computes pooled aggregate pass rates with numerator/denominator labels and never prints 100% when any control is not evaluated or actionable", async () => {
    const frameworks: ComplianceFramework[] = [
      {
        id: "k8s-cis-1.23",
        title: "CIS Benchmark",
        passCount: 102,
        evaluatedPassCount: 0,
        notEvaluatedCount: 102,
        failCount: 14,
        actionableFailCount: 1,
        acceptedFailCount: 13,
        totalControls: 116,
        passRate: 102 / 116,
        evaluatedPassRate: 0,
        effectivePassRate: 13 / 116,
      },
      {
        id: "k8s-kisa-1.0",
        title: "KISA",
        passCount: 3,
        evaluatedPassCount: 0,
        notEvaluatedCount: 3,
        failCount: 4,
        actionableFailCount: 0,
        acceptedFailCount: 4,
        totalControls: 7,
        passRate: 3 / 7,
        evaluatedPassRate: 0,
        effectivePassRate: 4 / 7,
      },
      {
        id: "k8s-nsa-1.0",
        title: "NSA",
        passCount: 18,
        evaluatedPassCount: 0,
        notEvaluatedCount: 18,
        failCount: 9,
        actionableFailCount: 0,
        acceptedFailCount: 9,
        totalControls: 27,
        passRate: 18 / 27,
        evaluatedPassRate: 0,
        effectivePassRate: 9 / 27,
      },
      {
        id: "k8s-pss-baseline-0.1",
        title: "PSS Baseline",
        passCount: 8,
        evaluatedPassCount: 0,
        notEvaluatedCount: 8,
        failCount: 3,
        actionableFailCount: 0,
        acceptedFailCount: 3,
        totalControls: 11,
        passRate: 8 / 11,
        evaluatedPassRate: 0,
        effectivePassRate: 3 / 11,
      },
      {
        id: "k8s-pss-restricted-0.1",
        title: "PSS Restricted",
        passCount: 14,
        evaluatedPassCount: 0,
        notEvaluatedCount: 14,
        failCount: 3,
        actionableFailCount: 0,
        acceptedFailCount: 3,
        totalControls: 17,
        passRate: 14 / 17,
        evaluatedPassRate: 0,
        effectivePassRate: 3 / 17,
      },
    ]

    const pooledSummary: ComplianceSummary = {
      totalConfigAuditFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      lowSeverityConfigAuditFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      acceptedSystemConfigAuditFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      totalRbacFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      acceptedRbacFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      totalInfraFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      acceptedInfraFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      frameworks,
      scannedWorkloads: 183,
      scannedRbacObjects: 189,
      scannedNodes: 1,
      lastUpdated: new Date().toISOString(),
    }

    const countersHtml = renderToStaticMarkup(<FailureCounters summary={pooledSummary} locale="en" />)
    // Pooled raw = 145/178 = 81.46% -> 81.4% (not unweighted 70.5%)
    expect(countersHtml).toContain("81.4% (145/178)")
    expect(countersHtml).toContain("0.0%")
    expect(countersHtml).toContain("(0/178)")
    expect(countersHtml).toContain("17.9% (32/178)")
    expect(countersHtml).not.toContain("70.5%")

    // 100% leak check: a framework whose raw passCount === totalControls (100% placeholders)
    // with notEvaluatedCount > 0 MUST NEVER display "100%" in summary, grid, or detail page.
    const allPlaceholderFw: ComplianceFramework = {
      id: "all-placeholder",
      title: "All Placeholder",
      passCount: 10,
      evaluatedPassCount: 0,
      notEvaluatedCount: 10,
      failCount: 0,
      actionableFailCount: 0,
      acceptedFailCount: 0,
      totalControls: 10,
      passRate: 1,
      evaluatedPassRate: 0,
      effectivePassRate: 0,
    }

    const leakSummaryHtml = renderToStaticMarkup(
      <FailureCounters summary={{ ...pooledSummary, frameworks: [allPlaceholderFw] }} locale="en" />,
    )
    expect(leakSummaryHtml).toContain("99.9% (10/10)")
    expect(leakSummaryHtml).not.toContain("100%")

    vi.mocked(useQuery).mockReturnValue({
      data: [allPlaceholderFw],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as never)
    const leakGridHtml = renderToStaticMarkup(
      <LocaleProvider locale="en">
        <FrameworksGrid />
      </LocaleProvider>,
    )
    expect(leakGridHtml).toContain("99.9% (10/10)")
    expect(leakGridHtml).not.toContain("100%")

    mockGetLocale.mockResolvedValue("en")
    mockGetComplianceFrameworkDetail.mockResolvedValue({ ...allPlaceholderFw, controls: [] })
    const leakDetailEl = await FrameworkDetailPage({ params: Promise.resolve({ id: "all-placeholder" }) })
    const leakDetailHtml = renderToStaticMarkup(leakDetailEl)
    expect(leakDetailHtml).toContain("99.9% (10/10)")
    expect(leakDetailHtml).not.toContain("100%")
  })

  it("renders visible partial-data notice naming failed auxiliary sources", () => {
    const now = new Date().toISOString()
    const partialSummary: ComplianceSummary = {
      totalConfigAuditFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      lowSeverityConfigAuditFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      acceptedSystemConfigAuditFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      totalRbacFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      acceptedRbacFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      totalInfraFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      acceptedInfraFailures: { Critical: 0, High: 0, Medium: 0, Low: 0 },
      frameworks: [],
      scannedWorkloads: 10,
      scannedRbacObjects: 5,
      scannedNodes: 1,
      lastUpdated: now,
      dataUnavailable: false,
      partial: true,
      sources: {
        configAudit: { status: "ok", lastUpdated: now, partial: true, partialSources: ["replicasets"] },
        rbac: { status: "ok", lastUpdated: now },
        infra: { status: "ok", lastUpdated: now, rebuildIntervalHours: 6 },
        frameworks: {
          status: "ok",
          lastUpdated: now,
          rebuildIntervalHours: 6,
          partial: true,
          partialSources: ["deploy/trivy-operator", "cm/trivy-operator-trivy-config"],
        },
      },
    }

    const htmlEn = renderToStaticMarkup(<FailureCounters summary={partialSummary} locale="en" />)
    expect(htmlEn).toContain("Partial compliance metadata")
    expect(htmlEn).toContain("Config Audit (replicasets)")
    expect(htmlEn).toContain("Frameworks (deploy/trivy-operator, cm/trivy-operator-trivy-config)")
  })
})

it("G5 old-shape object renders unknown in grid, summary SSR and framework detail", async () => {
  const old: ComplianceFramework = { id: "old", title: "Old cache", totalControls: 10,
    passCount: 10, failCount: 0, passRate: 1, effectivePassRate: 1 }
  vi.mocked(useQuery).mockReturnValue({ data: [old], isLoading: false, isError: false } as never)
  const grid = renderToStaticMarkup(<LocaleProvider locale="en"><FrameworksGrid /></LocaleProvider>)
  expect(grid).toContain("10 not evaluated")
  expect(grid).toContain('title="0/10"')
  expect(grid).not.toContain("100%")
  const zero = { Critical: 0, High: 0, Medium: 0, Low: 0 }
  const summary: ComplianceSummary = {
    totalConfigAuditFailures: zero, lowSeverityConfigAuditFailures: zero, acceptedSystemConfigAuditFailures: zero,
    totalRbacFailures: zero, acceptedRbacFailures: zero, totalInfraFailures: zero, acceptedInfraFailures: zero,
    frameworks: [old], scannedWorkloads: 0, scannedRbacObjects: 0, scannedNodes: 0, lastUpdated: "2026-11-22T00:00:00Z",
  }
  const ssr = renderToStaticMarkup(<FailureCounters summary={summary} locale="en" />)
  expect(ssr).toContain("0/10")
  expect(ssr).toContain("10 not evaluated")
  expect(ssr).not.toContain("100%")
  const detail: ComplianceFrameworkDetail = { ...old, controls: [{ id: "old-control", name: "Old control", severity: "High", passCount: 1, failCount: 0, accepted: false }] }
  mockGetLocale.mockResolvedValue("en")
  mockGetComplianceFrameworkDetail.mockResolvedValue(detail)
  const page = renderToStaticMarkup(await FrameworkDetailPage({ params: Promise.resolve({ id: "old" }) }))
  expect(page).toContain("Not evaluated")
  expect(page).toContain("0/10")
  expect(page).not.toContain("100%")
  vi.mocked(useQuery).mockReturnValue({ data: detail, isLoading: false, isError: false } as never)
  const panel = renderToStaticMarkup(<LocaleProvider locale="en"><ExpandedFrameworkPanel id="old" /></LocaleProvider>)
  expect(panel).toContain("Old control")
  expect(panel).toContain("Not evaluated")
  detail.controls.push(
    { id: "verified", name: "Verified pass stays visible", severity: "High", passCount: 1, failCount: 0, evaluationState: "passed" },
    { id: "old-failed", name: "Old failure stays visible", severity: "High", passCount: 0, failCount: 1 },
  )
  const panelWithEvidence = renderToStaticMarkup(<LocaleProvider locale="en"><ExpandedFrameworkPanel id="old" /></LocaleProvider>)
  expect(panelWithEvidence).toContain("Verified pass stays visible")
  expect(panelWithEvidence).toContain("Old failure stays visible")
  const pageWithEvidence = renderToStaticMarkup(await FrameworkDetailPage({ params: Promise.resolve({ id: "old" }) }))
  expect(pageWithEvidence).toContain("Verified pass stays visible")
  expect(pageWithEvidence).toContain("Old failure stays visible")
})

it("H5/H1: empty failure lists never say All checks passed or 모든 검사 통과 and infra surfaces point to verify-cluster.sh", async () => {
  const zero = { Critical: 0, High: 0, Medium: 0, Low: 0 }
  const cleanInfraRow: InfraAuditRow = { node: "narwhal-master-1", summary: zero, actionableSummary: zero, acceptedSummary: zero, accepted: false }
  const cleanInfraDetail: InfraAuditDetail = { ...cleanInfraRow, checks: [] }
  const cleanConfigDetail: ConfigAuditDetail = { namespace: "dev", kind: "Deployment", name: "app", summary: zero, accepted: false, checks: [] }
  const cleanRbacDetail: RbacAuditDetail = { namespace: "dev", kind: "Role", name: "reader", summary: zero, accepted: false, checks: [] }

  for (const locale of ["ko", "en"] as const) {
    const expectedNoFail = locale === "ko" ? "실패 기록 없음" : "No failure recorded"
    const forbidden = locale === "ko" ? "모든 검사 통과" : "All checks passed"

    vi.mocked(useQuery).mockReturnValue({ data: [cleanInfraRow], isLoading: false, isError: false, refetch: vi.fn() } as never)
    const infraListHtml = renderToStaticMarkup(<LocaleProvider locale={locale}><InfraAuditList /></LocaleProvider>)
    expect(infraListHtml).not.toContain(forbidden)
    expect(infraListHtml).not.toContain("Clean")
    expect(infraListHtml).toContain("scripts/test/verify-cluster.sh")

    vi.mocked(useQuery).mockReturnValue({ data: cleanInfraDetail, isLoading: false, isError: false, refetch: vi.fn() } as never)
    const infraPanelHtml = renderToStaticMarkup(<LocaleProvider locale={locale}><ExpandedInfraPanel node="narwhal-master-1" /></LocaleProvider>)
    expect(infraPanelHtml).toContain(expectedNoFail)
    expect(infraPanelHtml).toContain("scripts/test/verify-cluster.sh")
    expect(infraPanelHtml).not.toContain(forbidden)

    vi.mocked(useQuery).mockReturnValue({ data: cleanConfigDetail, isLoading: false, isError: false, refetch: vi.fn() } as never)
    const cfgPanelHtml = renderToStaticMarkup(<LocaleProvider locale={locale}><ExpandedConfigPanel scope="namespace" namespace="dev" kind="Deployment" name="app" /></LocaleProvider>)
    expect(cfgPanelHtml).toContain(expectedNoFail)
    expect(cfgPanelHtml).not.toContain(forbidden)

    vi.mocked(useQuery).mockReturnValue({ data: cleanRbacDetail, isLoading: false, isError: false, refetch: vi.fn() } as never)
    const rbacPanelHtml = renderToStaticMarkup(<LocaleProvider locale={locale}><ExpandedRbacPanel namespace="dev" name="reader" /></LocaleProvider>)
    expect(rbacPanelHtml).toContain(expectedNoFail)
    expect(rbacPanelHtml).not.toContain(forbidden)

    mockGetLocale.mockResolvedValue(locale)
    mockGetInfraAuditDetail.mockResolvedValue(cleanInfraDetail)
    mockGetConfigAuditDetail.mockResolvedValue(cleanConfigDetail)
    mockGetRbacAuditDetail.mockResolvedValue(cleanRbacDetail)

    const infraPageHtml = renderToStaticMarkup(await InfraAuditDetailPage({ params: Promise.resolve({ node: "narwhal-master-1" }) }))
    expect(infraPageHtml).toContain(expectedNoFail)
    expect(infraPageHtml).toContain("scripts/test/verify-cluster.sh")
    expect(infraPageHtml).not.toContain(forbidden)

    const cfgPageHtml = renderToStaticMarkup(
      await ConfigAuditDetailPage({
        params: Promise.resolve({ ns: "dev", name: "app" }),
        searchParams: Promise.resolve({ scope: "namespace", kind: "Deployment" }),
      }),
    )
    expect(cfgPageHtml).toContain(expectedNoFail)
    expect(cfgPageHtml).not.toContain(forbidden)

    const rbacPageHtml = renderToStaticMarkup(await RbacAuditDetailPage({ params: Promise.resolve({ ns: "dev", name: "reader" }) }))
    expect(rbacPageHtml).toContain(expectedNoFail)
    expect(rbacPageHtml).not.toContain(forbidden)

    // Framework detail page renders infra_no_per_check_evidence reason + verify-cluster.sh page note
    mockGetComplianceFrameworkDetail.mockResolvedValue({
      id: "k8s-cis-1.23",
      title: "CIS",
      totalControls: 1,
      passCount: 1,
      evaluatedPassCount: 0,
      notEvaluatedCount: 1,
      failCount: 0,
      actionableFailCount: 0,
      acceptedFailCount: 0,
      passRate: 1,
      evaluatedPassRate: 0,
      effectivePassRate: 0,
      controls: [{
        id: "1.1.19",
        name: "PKI ownership",
        severity: "Critical",
        passCount: 0,
        failCount: 0,
        evaluationState: "not_evaluated",
        notEvaluatedReason: "infra_no_per_check_evidence",
      }],
    })
    const fwDetailHtml = renderToStaticMarkup(await FrameworkDetailPage({ params: Promise.resolve({ id: "k8s-cis-1.23" }) }))
    expect(fwDetailHtml).toContain("scripts/test/verify-cluster.sh")
    expect(fwDetailHtml).toContain(
      locale === "ko"
        ? "노드/컨트롤플레인 검사: 스캐너가 항목별 평가 증거를 남기지 않음"
        : "Node/control-plane check: the scanner leaves no per-check evidence",
    )
  }
})

it("J1/J2 UI: renders cluster-scoped ConfigAuditRow with visible Cluster scope, actionable counter, _cluster detail link, and no_report_in_scope_namespace reason", async () => {
  const clusterRow: ConfigAuditRow = {
    namespace: "",
    scope: "cluster",
    kind: "ClusterRoleBinding",
    name: "custom:cluster-binding",
    summary: { Critical: 0, High: 1, Medium: 0, Low: 0 },
    actionableSummary: { Critical: 0, High: 1, Medium: 0, Low: 0 },
    acceptedSummary: { Critical: 0, High: 0, Medium: 0, Low: 0 },
    accepted: false,
  }

  vi.mocked(useQuery).mockReturnValue({
    data: [clusterRow],
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as never)

  const enTableHtml = renderToStaticMarkup(
    <LocaleProvider locale="en">
      <ConfigAuditTable />
    </LocaleProvider>,
  )
  expect(enTableHtml).toContain("1 actionable (MEDIUM+)")
  expect(enTableHtml).toContain("Cluster")
  expect(enTableHtml).toContain("ClusterRoleBinding")
  expect(enTableHtml).toContain("custom:cluster-binding")

  const koTableHtml = renderToStaticMarkup(
    <LocaleProvider locale="ko">
      <ConfigAuditTable />
    </LocaleProvider>,
  )
  expect(koTableHtml).toContain("클러스터")
  expect(koTableHtml).toContain("custom:cluster-binding")

  const clusterDetail: ConfigAuditDetail = {
    ...clusterRow,
    checks: [{ id: "KSV122", title: "Broad cluster binding", severity: "High", success: false, accepted: false }],
  }
  vi.mocked(useQuery).mockReturnValue({
    data: clusterDetail,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as never)
  vi.mocked(useQuery).mockClear()
  const panelHtml = renderToStaticMarkup(
    <LocaleProvider locale="en">
      <ExpandedConfigPanel scope="cluster" namespace="" kind="ClusterRoleBinding" name="custom:cluster-binding" />
    </LocaleProvider>,
  )
  expect(panelHtml).toContain("KSV122")
  expect(panelHtml).toContain("/compliance/config-audit/_cluster/custom%3Acluster-binding?scope=cluster&amp;kind=ClusterRoleBinding")
  const panelQueryOpts = vi.mocked(useQuery).mock.calls[0]?.[0] as { queryKey?: unknown[]; queryFn?: () => Promise<unknown> }
  expect(panelQueryOpts?.queryKey).toEqual([
    "compliance-config-audit-detail",
    "cluster",
    "",
    "ClusterRoleBinding",
    "custom:cluster-binding",
  ])

  mockGetLocale.mockResolvedValue("en")
  mockGetConfigAuditDetail.mockReset()
  mockGetConfigAuditDetail.mockResolvedValue(clusterDetail)
  const pageHtml = renderToStaticMarkup(
    await ConfigAuditDetailPage({
      params: Promise.resolve({ ns: "_cluster", name: "custom%3Acluster-binding" }),
      searchParams: Promise.resolve({ scope: "cluster", kind: "ClusterRoleBinding" }),
    }),
  )
  expect(mockGetConfigAuditDetail).toHaveBeenCalledWith("cluster", "", "ClusterRoleBinding", "custom:cluster-binding")
  expect(pageHtml).toContain("Cluster / ClusterRoleBinding / custom:cluster-binding")
  expect(pageHtml).toContain("KSV122")

  // Missing kind renders explicit ambiguous error without querying or showing clean/no-failure state.
  mockGetConfigAuditDetail.mockReset()
  const ambiguousPageHtml = renderToStaticMarkup(
    await ConfigAuditDetailPage({
      params: Promise.resolve({ ns: "_cluster", name: "custom%3Acluster-binding" }),
    }),
  )
  expect(mockGetConfigAuditDetail).not.toHaveBeenCalled()
  expect(ambiguousPageHtml).toContain(en["compliance.detail.ambiguous"])
  expect(ambiguousPageHtml).not.toContain(en["compliance.check.allPassed"])

  // ComplianceDetailAmbiguousError from lookup renders explicit ambiguous error state.
  const { ComplianceDetailAmbiguousError } = await import("@/lib/compliance")
  mockGetConfigAuditDetail.mockRejectedValueOnce(
    new ComplianceDetailAmbiguousError({
      scope: "cluster",
      namespace: "",
      kind: "ClusterRoleBinding",
      name: "custom:cluster-binding",
    }),
  )
  const dupAmbiguousHtml = renderToStaticMarkup(
    await ConfigAuditDetailPage({
      params: Promise.resolve({ ns: "_cluster", name: "custom%3Acluster-binding" }),
      searchParams: Promise.resolve({ scope: "cluster", kind: "ClusterRoleBinding" }),
    }),
  )
  expect(dupAmbiguousHtml).toContain(en["compliance.detail.ambiguous"])
  expect(dupAmbiguousHtml).not.toContain(en["compliance.check.allPassed"])

  // Contradictory scope/namespace renders Not found without querying.
  mockGetConfigAuditDetail.mockReset()
  const mismatchPageHtml = renderToStaticMarkup(
    await ConfigAuditDetailPage({
      params: Promise.resolve({ ns: "dev", name: "app" }),
      searchParams: Promise.resolve({ scope: "cluster", kind: "ClusterRole" }),
    }),
  )
  expect(mockGetConfigAuditDetail).not.toHaveBeenCalled()
  expect(mismatchPageHtml).toContain(en["common.notFound"])

  mockGetComplianceFrameworkDetail.mockResolvedValue({
    id: "k8s-cis-1.23",
    title: "CIS",
    totalControls: 1,
    passCount: 1,
    evaluatedPassCount: 0,
    notEvaluatedCount: 1,
    failCount: 0,
    actionableFailCount: 0,
    acceptedFailCount: 0,
    passRate: 1,
    evaluatedPassRate: 0,
    effectivePassRate: 0,
    controls: [{
      id: "5.7.4",
      name: "The default namespace should not be used",
      severity: "Low",
      passCount: 0,
      failCount: 0,
      evaluationState: "not_evaluated",
      notEvaluatedReason: "no_report_in_scope_namespace",
    }],
  })
  const fwHtml = renderToStaticMarkup(await FrameworkDetailPage({ params: Promise.resolve({ id: "k8s-cis-1.23" }) }))
  expect(fwHtml).toContain("No report in the namespaces this check applies to")
})
