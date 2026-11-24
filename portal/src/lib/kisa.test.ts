import { beforeEach, describe, expect, it, vi } from "vitest"
import type { ComplianceFramework, ConfigAuditRow } from "@/types/compliance"
import type { SecuritySummary } from "@/types/security"
import type { KisaFetchResult, KisaWorkloadInventory } from "./k8s-client"

vi.mock("server-only", () => ({}))

const mockCache = new Map<string, unknown>()
vi.mock("./valkey", () => ({
  cacheGet: vi.fn(async (key: string) => mockCache.get(key) ?? null),
  cacheSet: vi.fn(async (key: string, val: unknown) => {
    mockCache.set(key, val)
  }),
}))

const mockGetComplianceFrameworks = vi.fn<() => Promise<ComplianceFramework[]>>()
const mockGetRbacAuditList = vi.fn(async () => [])
const mockGetInfraAuditList = vi.fn(async () => [])
const mockGetConfigAuditList = vi.fn<() => Promise<ConfigAuditRow[]>>()

vi.mock("./compliance", () => ({
  SYSTEM_NAMESPACES: ["kube-system", "kube-public", "kube-node-lease", "istio-system"],
  getRbacAuditList: () => mockGetRbacAuditList(),
  getInfraAuditList: () => mockGetInfraAuditList(),
  getComplianceFrameworks: () => mockGetComplianceFrameworks(),
  getConfigAuditList: () => mockGetConfigAuditList(),
}))

const mockGetKisaKyvernoClusterPolicies = vi.fn(async () => ({ status: "ok" as const, data: [] as unknown[] }))
const mockGetKisaWorkloadInventory = vi.fn<() => Promise<KisaWorkloadInventory>>(async () => ({
  pods: { status: "ok", data: [] },
  replicasets: { status: "ok", data: [] },
  deployments: { status: "ok", data: [] },
  statefulsets: { status: "ok", data: [] },
  daemonsets: { status: "ok", data: [] },
  cronjobs: { status: "ok", data: [] },
}))
const mockGetSecuritySummary = vi.fn<() => Promise<SecuritySummary>>(async () => ({
  totals: { Critical: 0, High: 0, Medium: 0, Low: 0, Unknown: 0 },
  scannedImages: 1,
  scannedWorkloads: 1,
  lastUpdated: "2026-11-24T00:00:00Z",
}))
const mockGetKisaCertificates = vi.fn<() => Promise<KisaFetchResult<unknown[]>>>(async () => ({ status: "ok", data: [] }))
const mockGetKisaKyvernoPolicies = vi.fn<() => Promise<KisaFetchResult<unknown[]>>>(async () => ({ status: "ok", data: [] }))
const mockGetKisaApiServerPods = vi.fn<() => Promise<KisaFetchResult<unknown[]>>>(async () => ({ status: "ok", data: [] }))
const mockGetKisaNamespaces = vi.fn<() => Promise<KisaFetchResult<unknown[]>>>(async () => ({ status: "ok", data: [] }))
const mockGetKisaNetworkPolicies = vi.fn<() => Promise<KisaFetchResult<unknown[]>>>(async () => ({ status: "ok", data: [] }))

vi.mock("./trivy", () => ({
  getSecuritySummary: () => mockGetSecuritySummary(),
}))

vi.mock("./k8s-client", () => ({
  getCertificates: vi.fn(async () => []),
  getKyvernoPolicies: vi.fn(async () => []),
  getApiServerPods: vi.fn(async () => []),
  getNamespaces: vi.fn(async () => []),
  getNetworkPolicies: vi.fn(async () => []),
  getRbacBindings: vi.fn(async () => []),
  getClusterRoles: vi.fn(async () => []),
  getRoles: vi.fn(async () => []),
  getKisaCertificates: () => mockGetKisaCertificates(),
  getKisaKyvernoPolicies: () => mockGetKisaKyvernoPolicies(),
  getKisaApiServerPods: () => mockGetKisaApiServerPods(),
  getKisaNamespaces: () => mockGetKisaNamespaces(),
  getKisaNetworkPolicies: () => mockGetKisaNetworkPolicies(),
  getKisaDeployment: vi.fn(async () => ({ status: "not-found" })),
  getKisaStatefulSet: vi.fn(async () => ({ status: "not-found" })),
  getKisaCronJob: vi.fn(async () => ({ status: "not-found" })),
  getKisaSecretMetadata: vi.fn(async () => ({ status: "not-found" })),
  getKisaNamespacedNetworkPolicies: vi.fn(async () => ({ status: "ok", data: [] })),
  getKisaPeerAuthentications: vi.fn(async () => ({ status: "ok", data: [] })),
  getKisaNotificationConfig: vi.fn(async () => ({ status: "not-found" })),
  getKisaNamespacedPods: vi.fn(async () => ({ status: "ok", data: [] })),
  getKisaKyvernoClusterPolicies: () => mockGetKisaKyvernoClusterPolicies(),
  getKisaPolicyReports: vi.fn(async () => ({ status: "ok", data: [] })),
  getKisaWorkloadInventory: () => mockGetKisaWorkloadInventory(),
  getKisaRbacSnapshot: vi.fn(async () => ({
    clusterRoleBindings: { status: "ok", data: [] },
    roleBindings: { status: "ok", data: [] },
    clusterRoles: { status: "ok", data: [] },
    roles: { status: "ok", data: [] },
  })),
}))

import policyBaselineData from "@/data/kisa-policy-baseline.json"
import { checkAdm01, getKisaControls } from "./kisa"
import type { KisaPolicyBaseline } from "./kisa-checks-ops"

describe("KISA CFG-01 evaluated verdict", () => {
  beforeEach(() => {
    mockCache.clear()
    mockGetComplianceFrameworks.mockReset()
    mockGetConfigAuditList.mockReset()
    mockGetConfigAuditList.mockResolvedValue([])
  })

  it("prevents KISA CFG-01 from passing when controls are unevaluated and fails on actionable controls", async () => {
    // 1. All controls unevaluated despite 100% raw passRate -> warn (never pass)
    mockGetComplianceFrameworks.mockResolvedValueOnce([
      {
        id: "k8s-cis-1.23",
        title: "CIS",
        passCount: 116,
        evaluatedPassCount: 0,
        notEvaluatedCount: 116,
        failCount: 0,
        actionableFailCount: 0,
        acceptedFailCount: 0,
        totalControls: 116,
        passRate: 1,
        evaluatedPassRate: 0,
        effectivePassRate: 0,
      },
    ])
    const resWarn = await getKisaControls()
    const cfgWarn = resWarn.controls.find((c) => c.id === "KISA-CFG-01")
    expect(cfgWarn?.status).toBe("warn")
    expect(cfgWarn?.detail).toContain("미평가 프레임워크 컨트롤 116건")

    // 2. Any actionable failure -> fail (even if raw passRate >= 90%)
    mockCache.clear()
    mockGetComplianceFrameworks.mockResolvedValueOnce([
      {
        id: "k8s-cis-1.23",
        title: "CIS",
        passCount: 115,
        evaluatedPassCount: 115,
        notEvaluatedCount: 0,
        failCount: 1,
        actionableFailCount: 1,
        acceptedFailCount: 0,
        totalControls: 116,
        passRate: 115 / 116,
        evaluatedPassRate: 115 / 116,
        effectivePassRate: 115 / 116,
      },
    ])
    const resFail = await getKisaControls()
    const cfgFail = resFail.controls.find((c) => c.id === "KISA-CFG-01")
    expect(cfgFail?.status).toBe("fail")
    expect(cfgFail?.detail).toContain("조치가능 프레임워크 컨트롤 실패 1건")

    // 3. Pass ONLY when everything is evaluated and nothing is actionable
    mockCache.clear()
    mockGetComplianceFrameworks.mockResolvedValueOnce([
      {
        id: "k8s-cis-1.23",
        title: "CIS",
        passCount: 10,
        evaluatedPassCount: 8,
        notEvaluatedCount: 0,
        failCount: 2,
        actionableFailCount: 0,
        acceptedFailCount: 2,
        totalControls: 10,
        passRate: 0.8,
        evaluatedPassRate: 0.8,
        effectivePassRate: 1,
      },
    ])
    const resPass = await getKisaControls()
    const cfgPass = resPass.controls.find((c) => c.id === "KISA-CFG-01")
    expect(cfgPass?.status).toBe("pass")
    expect(cfgPass?.detail).toContain("10개 컨트롤 평가 완료")
  })
})

it.each(["config", "rbac", "infra"])("G4 KISA CFG-01 fails on actionable %s findings without actionable framework controls", async (family) => {
  mockCache.clear()
  mockGetComplianceFrameworks.mockResolvedValue([{
    id: "clean", title: "Clean", totalControls: 1, passCount: 1, failCount: 0, passRate: 1,
    evaluatedPassCount: 1, notEvaluatedCount: 0, actionableFailCount: 0,
  }])
  const row = { namespace: "dev", summary: { Critical: 0, High: 1, Medium: 0, Low: 0 }, actionableSummary: { Critical: 0, High: 1, Medium: 0, Low: 0 }, accepted: false }
  mockGetConfigAuditList.mockResolvedValue(family === "config" ? [row] as ConfigAuditRow[] : [])
  mockGetRbacAuditList.mockResolvedValue(family === "rbac" ? [row] as never[] : [])
  mockGetInfraAuditList.mockResolvedValue(family === "infra" ? [row] as never[] : [])
  const cfg = (await getKisaControls()).controls.find((c) => c.id === "KISA-CFG-01")
  expect(cfg?.status).toBe("fail")
  expect(cfg?.detail).toContain(family === "config" ? "config-audit 조치가능 1건" : "RBAC/infra 조치가능 1건")
  expect(cfg?.detail).not.toContain("실패 없음")
  mockGetRbacAuditList.mockResolvedValue([])
  mockGetInfraAuditList.mockResolvedValue([])
})

it("G5 old-shape framework and KISA cache never produce CFG-01 pass", async () => {
  mockCache.clear()
  mockCache.set("compliance:kisa:list", { controls: [{ id: "KISA-CFG-01", status: "pass" }] })
  mockGetConfigAuditList.mockResolvedValue([])
  mockGetComplianceFrameworks.mockResolvedValue([{
    id: "old", title: "Old", totalControls: 10, passCount: 10, failCount: 0, passRate: 1,
  }])
  const cfg = (await getKisaControls()).controls.find((c) => c.id === "KISA-CFG-01")
  expect(cfg?.status).toBe("warn")
  expect(cfg?.detail).toContain("미평가 프레임워크 컨트롤 10건")
})

it("J2 KISA CFG-01 fails on cluster-scoped ClusterRoleBinding KSV122 finding in config-audit list", async () => {
  mockCache.clear()
  mockGetComplianceFrameworks.mockResolvedValue([{
    id: "clean", title: "Clean", totalControls: 1, passCount: 1, failCount: 0, passRate: 1,
    evaluatedPassCount: 1, notEvaluatedCount: 0, actionableFailCount: 0,
  }])
  mockGetConfigAuditList.mockResolvedValue([{
    namespace: "",
    scope: "cluster",
    kind: "ClusterRoleBinding",
    name: "custom:cluster-binding",
    summary: { Critical: 0, High: 1, Medium: 0, Low: 0 },
    actionableSummary: { Critical: 0, High: 1, Medium: 0, Low: 0 },
    acceptedSummary: { Critical: 0, High: 0, Medium: 0, Low: 0 },
    accepted: false,
  }])
  mockGetRbacAuditList.mockResolvedValue([])
  mockGetInfraAuditList.mockResolvedValue([])

  const cfg = (await getKisaControls()).controls.find((c) => c.id === "KISA-CFG-01")
  expect(cfg?.status).toBe("fail")
  expect(cfg?.detail).toContain("config-audit(1건)")
  expect(cfg?.detail).toContain("config-audit 조치가능 1건")
})

it("J3 (MAJOR 3): actionable framework failure + infra 403 stays fail and names both failing and unreadable sources", async () => {
  mockCache.clear()
  mockGetComplianceFrameworks.mockResolvedValue([{
    id: "k8s-cis-1.23",
    title: "CIS",
    totalControls: 10,
    passCount: 9,
    evaluatedPassCount: 9,
    notEvaluatedCount: 0,
    failCount: 1,
    actionableFailCount: 1,
    acceptedFailCount: 0,
    passRate: 0.9,
    evaluatedPassRate: 0.9,
    effectivePassRate: 0.9,
  }])
  mockGetConfigAuditList.mockResolvedValue([])
  mockGetRbacAuditList.mockResolvedValue([])
  mockGetInfraAuditList.mockRejectedValueOnce(new Error("K8s API 403 Forbidden for /apis/aquasecurity.github.io/v1alpha1/clusterinfraassessmentreports"))

  const cfg = (await getKisaControls()).controls.find((c) => c.id === "KISA-CFG-01")
  expect(cfg?.status).toBe("fail")
  expect(cfg?.live).toBe(true)
  expect(cfg?.detail).toContain("frameworks(1건)")
  expect(cfg?.detail).toContain("조회 불가 소스: infra")
  expect(mockCache.size).toBe(0)

  // No known failure + infra 403 -> warn with unreadable source reason (never reasonless manual, never pass).
  mockGetComplianceFrameworks.mockResolvedValue([{
    id: "k8s-cis-1.23",
    title: "CIS",
    totalControls: 10,
    passCount: 10,
    evaluatedPassCount: 10,
    notEvaluatedCount: 0,
    failCount: 0,
    actionableFailCount: 0,
    acceptedFailCount: 0,
    passRate: 1,
    evaluatedPassRate: 1,
    effectivePassRate: 1,
  }])
  mockGetInfraAuditList.mockRejectedValueOnce(new Error("K8s API 403 Forbidden"))
  const cfgWarn = (await getKisaControls()).controls.find((c) => c.id === "KISA-CFG-01")
  expect(cfgWarn?.status).toBe("warn")
  expect(cfgWarn?.detail).toContain("infra")
  expect(mockCache.size).toBe(0)

  // All 4 sources unavailable -> warn with reason, never reasonless manual.
  mockGetComplianceFrameworks.mockRejectedValueOnce(new Error("403"))
  mockGetConfigAuditList.mockRejectedValueOnce(new Error("403"))
  mockGetRbacAuditList.mockRejectedValueOnce(new Error("403"))
  mockGetInfraAuditList.mockRejectedValueOnce(new Error("403"))
  const cfgAllDown = (await getKisaControls()).controls.find((c) => c.id === "KISA-CFG-01")
  expect(cfgAllDown?.status).toBe("warn")
  expect(cfgAllDown?.detail).toContain("frameworks, configAudit, rbac, infra")
  expect(mockCache.size).toBe(0)

  // Reset mocks cleanly for any subsequent test
  mockGetConfigAuditList.mockResolvedValue([])
  mockGetRbacAuditList.mockResolvedValue([])
  mockGetInfraAuditList.mockResolvedValue([])
})

it("skips Valkey caching and marks partialSources when KISA-IMG-02 workload inventory has 403", async () => {
  mockCache.clear()
  mockGetComplianceFrameworks.mockResolvedValue([{
    id: "clean", title: "Clean", totalControls: 1, passCount: 1, failCount: 0, passRate: 1,
    evaluatedPassCount: 1, notEvaluatedCount: 0, actionableFailCount: 0,
  }])
  mockGetConfigAuditList.mockResolvedValue([])
  mockGetRbacAuditList.mockResolvedValue([])
  mockGetInfraAuditList.mockResolvedValue([])
  mockGetKisaKyvernoClusterPolicies.mockResolvedValueOnce({
    status: "ok",
    data: [
      {
        metadata: { name: "disallow-latest-tag" },
        spec: {
          validationFailureAction: "Enforce",
          rules: [{
            name: "validate-image-tag",
            match: { any: [{ resources: { kinds: ["Pod"] } }] },
            validate: { pattern: { spec: { containers: [{ image: "!*:latest" }] } } },
          }],
        },
      },
      {
        metadata: { name: "verify-image-signatures" },
        spec: {
          validationFailureAction: "Enforce",
          rules: [{
            name: "verify-signature",
            match: { any: [{ resources: { kinds: ["Pod"] } }] },
            verifyImages: [{
              imageReferences: ["*"],
              required: true,
              attestors: [{ count: 1, entries: [{ keys: { publicKeys: "-----BEGIN PUBLIC KEY-----\ntest\n-----END PUBLIC KEY-----" } }] }],
            }],
          }],
        },
      },
    ],
  })
  mockGetKisaWorkloadInventory.mockResolvedValueOnce({
    pods: { status: "ok", data: [] },
    replicasets: { status: "ok", data: [] },
    deployments: { status: "forbidden", resource: "deployments.apps" },
    statefulsets: { status: "ok", data: [] },
    daemonsets: { status: "ok", data: [] },
    cronjobs: { status: "ok", data: [] },
  })

  const res = await getKisaControls()
  const img02 = res.controls.find((c) => c.id === "KISA-IMG-02")
  expect(img02?.status).toBe("warn")
  expect(img02?.partial).toBe(true)
  expect(img02?.partialSources).toContain("deployments(forbidden)")
  expect(res.partial).toBe(true)
  expect(res.partialSources).toContain("deployments(forbidden)")
  expect(mockCache.size).toBe(0)
})

describe("KISA sweep closed-world invariants (D-KISA-CLOSED-WORLD)", () => {
  beforeEach(() => {
    mockCache.clear()
    mockGetComplianceFrameworks.mockResolvedValue([{
      id: "clean", title: "Clean", totalControls: 1, passCount: 1, failCount: 0, passRate: 1,
      evaluatedPassCount: 1, notEvaluatedCount: 0, actionableFailCount: 0,
    }])
    mockGetConfigAuditList.mockResolvedValue([])
    mockGetRbacAuditList.mockResolvedValue([])
    mockGetInfraAuditList.mockResolvedValue([])
    mockGetSecuritySummary.mockResolvedValue({
      totals: { Critical: 0, High: 0, Medium: 0, Low: 0, Unknown: 0 },
      scannedImages: 1,
      scannedWorkloads: 1,
      lastUpdated: "2026-11-24T00:00:00Z",
    })
  })

  it("IMG-03 never passes on API error, zero scanned reports, or truncated list", async () => {
    mockGetSecuritySummary.mockResolvedValueOnce({
      totals: { Critical: 0, High: 0, Medium: 0, Low: 0, Unknown: 0 },
      scannedImages: 0,
      scannedWorkloads: 0,
      lastUpdated: "2026-11-24T00:00:00Z",
      failed: true,
    })
    const errRes = await getKisaControls()
    const imgErr = errRes.controls.find((c) => c.id === "KISA-IMG-03")
    expect(imgErr?.status).toBe("manual")
    expect(imgErr?.partial).toBe(true)

    mockCache.clear()
    mockGetSecuritySummary.mockResolvedValueOnce({
      totals: { Critical: 0, High: 0, Medium: 0, Low: 0, Unknown: 0 },
      scannedImages: 0,
      scannedWorkloads: 0,
      lastUpdated: "2026-11-24T00:00:00Z",
    })
    const emptyRes = (await getKisaControls()).controls.find((c) => c.id === "KISA-IMG-03")
    expect(emptyRes?.status).toBe("manual")

    mockCache.clear()
    mockGetSecuritySummary.mockResolvedValueOnce({
      totals: { Critical: 0, High: 0, Medium: 0, Low: 0, Unknown: 0 },
      scannedImages: 5,
      scannedWorkloads: 5,
      lastUpdated: "2026-11-24T00:00:00Z",
      partial: true,
      partialReason: "max-pages",
    })
    const partRes = (await getKisaControls()).controls.find((c) => c.id === "KISA-IMG-03")
    expect(partRes?.status).toBe("warn")
    expect(partRes?.partial).toBe(true)
  })

  it("POD-01 does not pass on audit/warn-only or privileged PSA labels; passes only when all namespaces enforce baseline/restricted", async () => {
    mockGetKisaNamespaces.mockResolvedValueOnce({
      status: "ok",
      data: [
        { name: "default", status: "Active", labels: { "pod-security.kubernetes.io/audit": "baseline", "pod-security.kubernetes.io/warn": "baseline" }, createdAt: "" },
        { name: "kube-system", status: "Active", labels: { "pod-security.kubernetes.io/enforce": "privileged" }, createdAt: "" },
      ],
    })
    const podWarn = (await getKisaControls()).controls.find((c) => c.id === "KISA-POD-01")
    expect(podWarn?.status).toBe("warn")

    mockCache.clear()
    mockGetKisaNamespaces.mockResolvedValueOnce({
      status: "ok",
      data: [
        { name: "default", status: "Active", labels: { "pod-security.kubernetes.io/enforce": "baseline" }, createdAt: "" },
        { name: "app", status: "Active", labels: { "pod-security.kubernetes.io/enforce": "restricted" }, createdAt: "" },
      ],
    })
    const podPass = (await getKisaControls()).controls.find((c) => c.id === "KISA-POD-01")
    expect(podPass?.status).toBe("pass")
  })

  it("NET-01 rejects podSelector:{} Ingress NetworkPolicy with allow-all ingress:[{}] and additive allow-all policies; 403/truncated returns manual", async () => {
    mockGetKisaNetworkPolicies.mockResolvedValueOnce({
      status: "ok",
      data: [
        { metadata: { name: "fake-deny", namespace: "iam" }, spec: { podSelector: {}, policyTypes: ["Ingress"], ingress: [{}] } },
        { metadata: { name: "ns-wide-allow", namespace: "devtools" }, spec: { podSelector: {}, policyTypes: ["Ingress"], ingress: [{ from: [{ namespaceSelector: {} }] }] } },
      ],
    })
    const netFail = (await getKisaControls()).controls.find((c) => c.id === "KISA-NET-01")
    expect(netFail?.status).toBe("fail")

    // Reproduction (R7): 5 namespaces each with default-deny AND a separate additive allow-all policy MUST NOT pass
    mockCache.clear()
    mockGetKisaNetworkPolicies.mockResolvedValueOnce({
      status: "ok",
      data: [
        { metadata: { name: "iam-deny", namespace: "iam" }, spec: { podSelector: {}, policyTypes: ["Ingress"] } },
        { metadata: { name: "iam-allow-all", namespace: "iam" }, spec: { podSelector: { matchLabels: { app: "kc" } }, policyTypes: ["Ingress"], ingress: [{}] } },
        { metadata: { name: "dev-deny", namespace: "devtools" }, spec: { podSelector: {}, policyTypes: ["Ingress"] } },
        { metadata: { name: "dev-no-from", namespace: "devtools" }, spec: { podSelector: { matchLabels: { app: "x" } }, policyTypes: ["Ingress"], ingress: [{ ports: [{ port: 80 }] }] } },
        { metadata: { name: "mon-deny", namespace: "monitoring" }, spec: { podSelector: {}, policyTypes: ["Ingress"] } },
        { metadata: { name: "mon-empty-peer", namespace: "monitoring" }, spec: { podSelector: { matchLabels: { app: "y" } }, policyTypes: ["Ingress"], ingress: [{ from: [{}] }] } },
        { metadata: { name: "sto-deny", namespace: "storage" }, spec: { podSelector: {}, policyTypes: ["Ingress"] } },
        { metadata: { name: "sto-ns-pod-empty", namespace: "storage" }, spec: { podSelector: { matchLabels: { app: "z" } }, policyTypes: ["Ingress"], ingress: [{ from: [{ podSelector: {}, namespaceSelector: {} }] }] } },
        { metadata: { name: "db-deny", namespace: "database" }, spec: { podSelector: {}, policyTypes: ["Ingress"] } },
        { metadata: { name: "db-ip6-all", namespace: "database" }, spec: { podSelector: { matchLabels: { app: "db" } }, policyTypes: ["Ingress"], ingress: [{ from: [{ ipBlock: { cidr: "::/0" } }] }] } },
      ],
    })
    const additiveFail = (await getKisaControls()).controls.find((c) => c.id === "KISA-NET-01")
    expect(additiveFail?.status).toBe("fail")
    expect(additiveFail?.detail).toContain("devtools")

    // Positive: 4/5 clean namespaces + devtools uncovered -> warn naming devtools
    mockCache.clear()
    mockGetKisaNetworkPolicies.mockResolvedValueOnce({
      status: "ok",
      data: [
        { metadata: { name: "iam-deny", namespace: "iam" }, spec: { podSelector: {}, policyTypes: ["Ingress"] } },
        { metadata: { name: "mon-deny", namespace: "monitoring" }, spec: { podSelector: {}, policyTypes: ["Ingress"] } },
        { metadata: { name: "sto-deny", namespace: "storage" }, spec: { podSelector: {}, policyTypes: ["Ingress"] } },
        { metadata: { name: "db-deny", namespace: "database" }, spec: { podSelector: {}, policyTypes: ["Ingress"], ingress: [{ from: [{ podSelector: {} }] }] } },
      ],
    })
    const fourOfFive = (await getKisaControls()).controls.find((c) => c.id === "KISA-NET-01")
    expect(fourOfFive?.status).toBe("warn")
    expect(fourOfFive?.detail).toContain("4/5")
    expect(fourOfFive?.detail).toContain("devtools")

    mockCache.clear()
    mockGetKisaNetworkPolicies.mockResolvedValueOnce({ status: "forbidden", resource: "networkpolicies.networking.k8s.io" })
    const netForb = (await getKisaControls()).controls.find((c) => c.id === "KISA-NET-01")
    expect(netForb?.status).toBe("manual")
    expect(netForb?.partial).toBe(true)
  })

  it("ADM-01 warns when any validate/verifyImages policy is Audit, Mixed, disabled (admission=false), notReady, or unreviewed against baseline", async () => {
    // Reproduction (R7): single unreviewed Enforce+Ready policy with false precondition/deny MUST NOT pass
    mockGetKisaKyvernoPolicies.mockResolvedValueOnce({
      status: "ok",
      data: [
        { name: "arbitrary-always-false", namespace: null, scope: "cluster", background: true, validationFailureAction: "Enforce", effectiveEnforcement: "Enforce", hasValidateOrVerifyRules: true, specSha256: "deadbeef", hasOverrides: false, ready: true, rulesCount: 1, rules: [], title: null, description: null, category: null, severity: null },
      ],
    })
    const admUnreviewed = (await getKisaControls()).controls.find((c) => c.id === "KISA-ADM-01")
    expect(admUnreviewed?.status).toBe("warn")
    expect(admUnreviewed?.detail).toContain("unreviewed")
    expect(admUnreviewed?.detailLocalized?.en).toContain("unreviewed")

    mockCache.clear()
    mockGetKisaKyvernoPolicies.mockResolvedValueOnce({
      status: "ok",
      data: [
        { name: "p1", namespace: null, scope: "cluster", background: true, validationFailureAction: "Enforce", effectiveEnforcement: "Disabled", hasValidateOrVerifyRules: true, ready: true, rulesCount: 1, rules: [], title: null, description: null, category: null, severity: null },
      ],
    })
    expect((await getKisaControls()).controls.find((c) => c.id === "KISA-ADM-01")?.status).toBe("warn")

    // Positive: custom baseline where all admissionRelevant entries match digest, Ready=True, and Enforce -> pass
    const repoBaseline = policyBaselineData as KisaPolicyBaseline
    const sample = repoBaseline.policies["disallow-latest-tag"]
    const allEnforcedBaseline: KisaPolicyBaseline = {
      ...repoBaseline,
      policies: {
        "disallow-latest-tag": sample,
      },
    }
    mockGetKisaKyvernoPolicies.mockResolvedValueOnce({
      status: "ok",
      data: [
        { name: "disallow-latest-tag", namespace: null, scope: "cluster", background: true, validationFailureAction: "Enforce", effectiveEnforcement: "Enforce", hasValidateOrVerifyRules: true, specSha256: sample.specSha256, hasOverrides: false, ready: true, rulesCount: 1, rules: [], title: null, description: null, category: null, severity: null },
      ],
    })
    expect((await checkAdm01(allEnforcedBaseline)).status).toBe("pass")
  })

  it("TLS-02 warns when a Ready certificate lacks valid notAfter; CP-01/ETCD-01 reject empty flags, missing container, or unready/statusless Pod", async () => {
    mockGetKisaCertificates.mockResolvedValueOnce({
      status: "ok",
      data: [{ name: "c1", namespace: "ns", ready: true, issuing: false, revision: 1, notAfter: null, notBefore: null, dnsNames: [], issuer: "ca", renewalTime: null }],
    })
    expect((await getKisaControls()).controls.find((c) => c.id === "KISA-TLS-02")?.status).toBe("warn")

    mockCache.clear()
    mockGetKisaApiServerPods.mockResolvedValueOnce({
      status: "ok",
      data: [{ name: "apiserver-1", hasApiserverContainer: false, ready: true, containerArgs: ["--audit-log-path=/var/log/a.log", "--audit-policy-file=/etc/a.yaml", "--encryption-provider-config=/etc/enc.yaml"] }],
    })
    const res = await getKisaControls()
    expect(res.controls.find((c) => c.id === "KISA-CP-01")?.status).toBe("manual")
    expect(res.controls.find((c) => c.id === "KISA-ETCD-01")?.status).toBe("manual")

    // Reproduction (R7): Pod without status / Ready=True (ready=false) -> manual, never pass
    mockCache.clear()
    mockGetKisaApiServerPods.mockResolvedValue({
      status: "ok",
      data: [{ name: "apiserver-1", hasApiserverContainer: true, ready: false, containerArgs: ["--audit-log-path=/var/log/a.log", "--audit-policy-file=/etc/a.yaml", "--encryption-provider-config=/etc/enc.yaml"] }],
    })
    const unreadyRes = await getKisaControls()
    expect(unreadyRes.controls.find((c) => c.id === "KISA-CP-01")?.status).toBe("manual")
    expect(unreadyRes.controls.find((c) => c.id === "KISA-ETCD-01")?.status).toBe("manual")

    // Positive: Ready=True with valid flags -> pass, and every control has ko + en detailLocalized
    mockCache.clear()
    mockGetKisaApiServerPods.mockResolvedValue({
      status: "ok",
      data: [{ name: "apiserver-1", hasApiserverContainer: true, ready: true, containerArgs: ["--audit-log-path=/var/log/a.log", "--audit-policy-file=/etc/a.yaml", "--encryption-provider-config=/etc/enc.yaml"] }],
    })
    const readyRes = await getKisaControls()
    mockGetKisaApiServerPods.mockResolvedValue({ status: "ok", data: [] })
    expect(readyRes.controls.find((c) => c.id === "KISA-CP-01")?.status).toBe("pass")
    expect(readyRes.controls.find((c) => c.id === "KISA-ETCD-01")?.status).toBe("pass")
    for (const c of readyRes.controls) {
      expect(c.detailLocalized?.ko?.trim().length, `${c.id} missing ko detail`).toBeGreaterThan(0)
      expect(c.detailLocalized?.en?.trim().length, `${c.id} missing en detail`).toBeGreaterThan(0)
      expect(c.reasonKey, `${c.id} missing reasonKey`).toBeTruthy()
    }
  })
})
