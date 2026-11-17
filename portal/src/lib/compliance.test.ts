import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("server-only", () => ({}))
vi.mock("./config", () => ({
  getK8sApiServer: () => "http://k8s.mock",
}))
vi.mock("./k8s-token", () => ({
  getK8sBearerToken: () => "mock-token",
}))

const mockCache = new Map<string, unknown>()
vi.mock("./valkey", () => ({
  cacheGet: vi.fn(async (key: string) => mockCache.get(key) ?? null),
  cacheSet: vi.fn(async (key: string, val: unknown) => {
    mockCache.set(key, val)
  }),
}))

const mockFetchWithPolicy = vi.fn()
const mockReadJsonWithPolicy = vi.fn()
vi.mock("./http-client", () => ({
  fetchWithPolicy: (...args: unknown[]) => mockFetchWithPolicy(...args),
  readJsonWithPolicy: (...args: unknown[]) => mockReadJsonWithPolicy(...args),
}))

import {
  getComplianceSummary,
  getConfigAuditDetail,
  getConfigAuditList,
  getRbacAuditDetail,
  getRbacAuditList,
  isAcceptedRbacRole,
} from "./compliance"

interface MockCheck {
  checkID: string
  severity: "CRITICAL" | "HIGH" | "MEDIUM" | "LOW"
  success: boolean
  title?: string
  remediation?: string
}

function makeCar(opts: {
  namespace: string
  kind: string
  name: string
  checks: MockCheck[]
  timestamp?: string
}) {
  const summary = { criticalCount: 0, highCount: 0, mediumCount: 0, lowCount: 0 }
  for (const c of opts.checks) {
    if (c.success) continue
    if (c.severity === "CRITICAL") summary.criticalCount++
    else if (c.severity === "HIGH") summary.highCount++
    else if (c.severity === "MEDIUM") summary.mediumCount++
    else if (c.severity === "LOW") summary.lowCount++
  }
  return {
    metadata: {
      name: `${opts.kind.toLowerCase()}-${opts.name}`,
      namespace: opts.namespace,
      creationTimestamp: opts.timestamp ?? "2026-11-18T00:00:00Z",
      labels: {
        "trivy-operator.resource.kind": opts.kind,
        "trivy-operator.resource.name": opts.name,
      },
      ownerReferences: [{ kind: opts.kind, name: opts.name }],
    },
    report: {
      summary,
      checks: opts.checks.map((c) => ({
        checkID: c.checkID,
        title: c.title ?? `Check ${c.checkID}`,
        severity: c.severity,
        success: c.success,
        remediation: c.remediation ?? `Fix ${c.checkID}`,
      })),
    },
  }
}

function makeRbac(opts: {
  namespace?: string
  kind: "Role" | "ClusterRole"
  roleName: string
  checks: MockCheck[]
}) {
  const summary = { criticalCount: 0, highCount: 0, mediumCount: 0, lowCount: 0 }
  for (const c of opts.checks) {
    if (c.success) continue
    if (c.severity === "CRITICAL") summary.criticalCount++
    else if (c.severity === "HIGH") summary.highCount++
    else if (c.severity === "MEDIUM") summary.mediumCount++
    else if (c.severity === "LOW") summary.lowCount++
  }
  return {
    metadata: {
      name: `hash-${opts.roleName}`,
      ...(opts.namespace ? { namespace: opts.namespace } : {}),
      creationTimestamp: "2026-11-18T00:00:00Z",
      labels: {
        "trivy-operator.resource.kind": opts.kind,
        "trivy-operator.resource.name": opts.roleName,
      },
      ownerReferences: [{ kind: opts.kind, name: opts.roleName }],
    },
    report: {
      summary,
      checks: opts.checks.map((c) => ({
        checkID: c.checkID,
        title: c.title ?? `RBAC ${c.checkID}`,
        severity: c.severity,
        success: c.success,
      })),
    },
  }
}

function installK8sMock(routes: Record<string, unknown>) {
  mockFetchWithPolicy.mockImplementation(async (url: string) => {
    const path = new URL(url).pathname
    return { ok: true, status: 200, statusText: "OK", _path: path }
  })
  mockReadJsonWithPolicy.mockImplementation(async (res: { _path: string }) => {
    return routes[res._path] ?? { items: [] }
  })
}

describe("compliance accepted exceptions", () => {
  beforeEach(() => {
    mockCache.clear()
    mockFetchWithPolicy.mockReset()
    mockReadJsonWithPolicy.mockReset()
  })

  it("treats chaos-mesh and kubernetes-dashboard as upstream RBAC roles", () => {
    expect(isAcceptedRbacRole("chaos-mesh-chaos-controller-manager-control-plane")).toBe(true)
    expect(isAcceptedRbacRole("chaos-mesh-chaos-controller-manager-target-namespace")).toBe(true)
    expect(isAcceptedRbacRole("kubernetes-dashboard-web")).toBe(true)
    expect(isAcceptedRbacRole("platform-admin")).toBe(false)
    expect(isAcceptedRbacRole("platform-admin-extra")).toBe(false)
    expect(isAcceptedRbacRole("velero-ui-custom-role")).toBe(false)
  })

  it("splits Config Audit rows into actionable vs accepted per check and handles ReplicaSet hash suffixes", async () => {
    installK8sMock({
      "/apis/aquasecurity.github.io/v1alpha1/configauditreports": {
        items: [
          // 1. System namespace: all accepted
          makeCar({
            namespace: "kube-system",
            kind: "DaemonSet",
            name: "cilium",
            checks: [
              { checkID: "KSV017", severity: "HIGH", success: false },
              { checkID: "KSV020", severity: "LOW", success: false },
            ],
          }),
          // 2. Non-system workload with ALL non-LOW checks allowlisted (ReplicaSet with rotated hash)
          makeCar({
            namespace: "devtools",
            kind: "ReplicaSet",
            name: "harbor-core-99a8b7c6d",
            checks: [
              { checkID: "AVD-KSV-0014", severity: "HIGH", success: false },
              { checkID: "KSV011", severity: "LOW", success: false },
              { checkID: "KSV020", severity: "LOW", success: false },
            ],
          }),
          // 3. Partially accepted: KSV020/021 allowlisted LOW, KSV014 HIGH stays actionable
          makeCar({
            namespace: "devtools",
            kind: "ReplicaSet",
            name: "narwhal-portal-74b459c658",
            checks: [
              { checkID: "KSV014", severity: "HIGH", success: false },
              { checkID: "KSV020", severity: "LOW", success: false },
              { checkID: "KSV021", severity: "LOW", success: false },
            ],
          }),
          // 4. Same allowlisted workload but NEW unreviewed check ID -> stays actionable (fail closed)
          makeCar({
            namespace: "devtools",
            kind: "ReplicaSet",
            name: "harbor-nginx-67d8f97945",
            checks: [
              { checkID: "KSV014", severity: "HIGH", success: false },
              { checkID: "KSV017", severity: "HIGH", success: false },
            ],
          }),
          // 5. Other workload with same check ID -> stays actionable
          makeCar({
            namespace: "devtools",
            kind: "ReplicaSet",
            name: "custom-app-67d8f97945",
            checks: [{ checkID: "KSV014", severity: "HIGH", success: false }],
          }),
          // 6. LOW-only allowlisted workload -> stays LOW hygiene (row.accepted = false)
          makeCar({
            namespace: "devtools",
            kind: "ReplicaSet",
            name: "argocd-server-5ff7697d4f",
            checks: [
              { checkID: "KSV020", severity: "LOW", success: false },
              { checkID: "KSV021", severity: "LOW", success: false },
            ],
          }),
        ],
      },
    })

    const rows = await getConfigAuditList()
    const byName = new Map(rows.map((r) => [r.name, r]))

    expect(byName.get("cilium")?.accepted).toBe(true)
    expect(byName.get("cilium")?.acceptedScope).toBe("system")

    const harborCore = byName.get("harbor-core-99a8b7c6d")!
    expect(harborCore.accepted).toBe(true)
    expect(harborCore.actionableSummary).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 0 })
    expect(harborCore.acceptedSummary).toEqual({ Critical: 0, High: 1, Medium: 0, Low: 2 })

    const portal = byName.get("narwhal-portal-74b459c658")!
    expect(portal.accepted).toBe(false)
    expect(portal.actionableSummary).toEqual({ Critical: 0, High: 1, Medium: 0, Low: 0 })
    expect(portal.acceptedSummary).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 2 })

    const harborNginx = byName.get("harbor-nginx-67d8f97945")!
    expect(harborNginx.accepted).toBe(false)
    expect(harborNginx.actionableSummary).toEqual({ Critical: 0, High: 1, Medium: 0, Low: 0 })
    expect(harborNginx.acceptedSummary).toEqual({ Critical: 0, High: 1, Medium: 0, Low: 0 })

    const customApp = byName.get("custom-app-67d8f97945")!
    expect(customApp.accepted).toBe(false)
    expect(customApp.actionableSummary).toEqual({ Critical: 0, High: 1, Medium: 0, Low: 0 })

    const argocdServer = byName.get("argocd-server-5ff7697d4f")!
    expect(argocdServer.accepted).toBe(false)
    expect(argocdServer.actionableSummary).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 0 })
    expect(argocdServer.acceptedSummary).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 2 })

    // Detail keeps every check and annotates accepted ones with class + reason
    const portalDetail = await getConfigAuditDetail("devtools", "narwhal-portal-74b459c658")
    expect(portalDetail).not.toBeNull()
    const ksv014 = portalDetail!.checks.find((c) => c.id === "KSV014")
    const ksv020 = portalDetail!.checks.find((c) => c.id === "KSV020")
    expect(ksv014?.accepted).toBe(false)
    expect(ksv020?.accepted).toBe(true)
    expect(ksv020?.acceptedClass).toBe("accept")
    expect(ksv020?.acceptedReason).toBeTruthy()
  })

  it("splits RBAC findings per check and computes summary without double-counting LOW hygiene", async () => {
    installK8sMock({
      "/apis/aquasecurity.github.io/v1alpha1/configauditreports": {
        items: [
          makeCar({
            namespace: "kube-system",
            kind: "DaemonSet",
            name: "cilium",
            checks: [
              { checkID: "KSV017", severity: "HIGH", success: false },
              { checkID: "KSV020", severity: "LOW", success: false },
            ],
          }),
          makeCar({
            namespace: "devtools",
            kind: "ReplicaSet",
            name: "harbor-core-b569bd8dd",
            checks: [
              { checkID: "KSV014", severity: "HIGH", success: false },
              { checkID: "KSV011", severity: "LOW", success: false },
              { checkID: "KSV020", severity: "LOW", success: false },
            ],
          }),
          makeCar({
            namespace: "devtools",
            kind: "ReplicaSet",
            name: "narwhal-portal-74b459c658",
            checks: [
              { checkID: "KSV014", severity: "HIGH", success: false },
              { checkID: "KSV020", severity: "LOW", success: false },
              { checkID: "KSV021", severity: "LOW", success: false },
            ],
          }),
        ],
      },
      "/apis/aquasecurity.github.io/v1alpha1/rbacassessmentreports": {
        items: [
          makeRbac({
            namespace: "devtools",
            kind: "Role",
            roleName: "narwhal-portal-jobs",
            checks: [{ checkID: "KSV048", severity: "MEDIUM", success: false }],
          }),
          makeRbac({
            namespace: "monitoring",
            kind: "Role",
            roleName: "narwhal-portal-notification-channels",
            checks: [{ checkID: "KSV113", severity: "MEDIUM", success: false }],
          }),
          makeRbac({
            namespace: "devtools",
            kind: "Role",
            roleName: "kubernetes-dashboard-web",
            checks: [{ checkID: "KSV049", severity: "MEDIUM", success: false }],
          }),
          makeRbac({
            namespace: "storage",
            kind: "Role",
            roleName: "velero-ui-custom-role",
            checks: [{ checkID: "KSV113", severity: "MEDIUM", success: false }],
          }),
        ],
      },
      "/apis/aquasecurity.github.io/v1alpha1/clusterrbacassessmentreports": {
        items: [
          makeRbac({
            kind: "ClusterRole",
            roleName: "platform-admin",
            checks: [
              { checkID: "KSV041", severity: "CRITICAL", success: false },
              { checkID: "KSV042", severity: "MEDIUM", success: false }, // NOT allowlisted!
              { checkID: "KSV048", severity: "MEDIUM", success: false },
              { checkID: "KSV049", severity: "MEDIUM", success: false },
              { checkID: "KSV053", severity: "HIGH", success: false },
              { checkID: "KSV056", severity: "HIGH", success: false },
            ],
          }),
          makeRbac({
            kind: "ClusterRole",
            roleName: "platform-admin-extra",
            checks: [{ checkID: "KSV041", severity: "CRITICAL", success: false }],
          }),
          makeRbac({
            kind: "ClusterRole",
            roleName: "developer-workload-admin",
            checks: [
              { checkID: "KSV048", severity: "MEDIUM", success: false },
              { checkID: "KSV049", severity: "MEDIUM", success: false },
              { checkID: "KSV053", severity: "HIGH", success: false },
              { checkID: "KSV056", severity: "HIGH", success: false },
            ],
          }),
          makeRbac({
            kind: "ClusterRole",
            roleName: "chaos-mesh-chaos-controller-manager-target-namespace",
            checks: [{ checkID: "KSV041", severity: "CRITICAL", success: false }],
          }),
        ],
      },
    })

    const rbacRows = await getRbacAuditList()
    const rbacByName = new Map(rbacRows.map((r) => [r.name, r]))
    expect(rbacByName.get("narwhal-portal-jobs")?.accepted).toBe(true)
    expect(rbacByName.get("narwhal-portal-notification-channels")?.accepted).toBe(true)
    expect(rbacByName.get("kubernetes-dashboard-web")?.accepted).toBe(true)
    expect(rbacByName.get("chaos-mesh-chaos-controller-manager-target-namespace")?.accepted).toBe(true)
    expect(rbacByName.get("developer-workload-admin")?.accepted).toBe(true)
    expect(rbacByName.get("velero-ui-custom-role")?.accepted).toBe(false)
    expect(rbacByName.get("platform-admin-extra")?.accepted).toBe(false)

    // platform-admin has 5 accepted checks and 1 actionable (KSV042)
    const pa = rbacByName.get("platform-admin")!
    expect(pa.accepted).toBe(false)
    expect(pa.actionableSummary).toEqual({ Critical: 0, High: 0, Medium: 1, Low: 0 })
    expect(pa.acceptedSummary).toEqual({ Critical: 1, High: 2, Medium: 2, Low: 0 })

    const paDetail = await getRbacAuditDetail("", "platform-admin")
    expect(paDetail?.checks.find((c) => c.id === "KSV042")?.accepted).toBe(false)
    expect(paDetail?.checks.find((c) => c.id === "KSV041")?.accepted).toBe(true)
    expect(paDetail?.checks.find((c) => c.id === "KSV041")?.acceptedReason).toBeTruthy()

    const summary = await getComplianceSummary()
    // Config audit:
    // - actionable: narwhal-portal KSV014 (High: 1)
    // - low hygiene: harbor-core (2) + narwhal-portal (2) = 4 (NOT double counted!)
    // - accepted: kube-system (High: 1, Low: 1) + harbor-core KSV014 (High: 1) = High: 2, Low: 1
    expect(summary.totalConfigAuditFailures).toEqual({ Critical: 0, High: 1, Medium: 0, Low: 0 })
    expect(summary.lowSeverityConfigAuditFailures).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 4 })
    expect(summary.acceptedSystemConfigAuditFailures).toEqual({ Critical: 0, High: 2, Medium: 0, Low: 1 })

    // RBAC:
    // - actionable: platform-admin KSV042 (Medium: 1) + platform-admin-extra KSV041 (Critical: 1) + velero-ui-custom-role KSV113 (Medium: 1)
    expect(summary.totalRbacFailures).toEqual({ Critical: 1, High: 0, Medium: 2, Low: 0 })
    // - accepted: jobs (M:1) + notif (M:1) + k8s-dash (M:1) + platform-admin (C:1,H:2,M:2) + dev-workload (H:2,M:2) + chaos-mesh (C:1)
    expect(summary.acceptedRbacFailures).toEqual({ Critical: 2, High: 4, Medium: 7, Low: 0 })
  })
})
