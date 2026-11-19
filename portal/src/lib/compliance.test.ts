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

import ccrLiveTrimmed from "./__fixtures__/ccr-live-trimmed.json"
import {
  getComplianceFrameworkDetail,
  getComplianceFrameworks,
  getComplianceSummary,
  getConfigAuditDetail,
  getConfigAuditList,
  getRbacAuditDetail,
  getRbacAuditList,
  isAcceptedRbacRole,
  parseComplianceTarget,
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

function makeRs(opts: {
  namespace: string
  name: string
  replicas?: number
  statusReplicas?: number
  terminatingReplicas?: number
  revision?: string | number
  ownerDeployment?: string
  ownerKind?: string
}) {
  return {
    metadata: {
      name: opts.name,
      namespace: opts.namespace,
      ...(opts.revision !== undefined
        ? { annotations: { "deployment.kubernetes.io/revision": String(opts.revision) } }
        : {}),
      ...(opts.ownerDeployment
        ? { ownerReferences: [{ kind: opts.ownerKind ?? "Deployment", name: opts.ownerDeployment }] }
        : {}),
    },
    ...(opts.replicas !== undefined ? { spec: { replicas: opts.replicas } } : {}),
    ...(opts.statusReplicas !== undefined
      ? {
          status: {
            replicas: opts.statusReplicas,
            // Default: explicitly drained. Pass -1 to omit the field (cluster does not report it).
            ...(opts.terminatingReplicas === -1 ? {} : { terminatingReplicas: opts.terminatingReplicas ?? 0 }),
          },
        }
      : {}),
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

  it("treats exact chaos-mesh and kubernetes-dashboard chart roles as upstream RBAC roles only for matching (namespace, kind, name) triples", () => {
    expect(isAcceptedRbacRole("chaos-mesh-chaos-controller-manager-control-plane", "chaos-testing", "Role")).toBe(true)
    expect(isAcceptedRbacRole("chaos-mesh-chaos-controller-manager-target-namespace", "", "ClusterRole")).toBe(true)
    expect(isAcceptedRbacRole("chaos-mesh-chaos-controller-manager-target-namespace", undefined, "ClusterRole")).toBe(true)
    expect(isAcceptedRbacRole("kubernetes-dashboard-api", "devtools", "Role")).toBe(true)
    expect(isAcceptedRbacRole("kubernetes-dashboard-web", "devtools", "Role")).toBe(true)
    expect(isAcceptedRbacRole("kubernetes-dashboard-metrics-scraper", "", "ClusterRole")).toBe(true)
    expect(isAcceptedRbacRole("kubernetes-dashboard-metrics-scraper", undefined, "ClusterRole")).toBe(true)

    // Wrong namespace, wrong kind, or unknown namespace/kind fail closed
    expect(isAcceptedRbacRole("kubernetes-dashboard-api", "dev", "Role")).toBe(false)
    expect(isAcceptedRbacRole("kubernetes-dashboard-web", "", "ClusterRole")).toBe(false)
    expect(isAcceptedRbacRole("kubernetes-dashboard-web", undefined, "ClusterRole")).toBe(false)
    expect(isAcceptedRbacRole("kubernetes-dashboard-api", "", "Role")).toBe(false)
    expect(isAcceptedRbacRole("kubernetes-dashboard-api", undefined, undefined)).toBe(false)
    expect(isAcceptedRbacRole("kubernetes-dashboard-api", "devtools", "Unknown")).toBe(false)
    expect(isAcceptedRbacRole("kubernetes-dashboard-metrics-scraper", "devtools", "ClusterRole")).toBe(false)
    expect(isAcceptedRbacRole("chaos-mesh-chaos-controller-manager-control-plane", "dev", "Role")).toBe(false)
    expect(isAcceptedRbacRole("chaos-mesh-chaos-controller-manager-target-namespace", "chaos-testing", "Role")).toBe(false)

    // Authored roles & pre-existing built-in/substring behaviour
    expect(isAcceptedRbacRole("platform-admin-chaos-mesh", "", "ClusterRole")).toBe(false)
    expect(isAcceptedRbacRole("developer-kubernetes-dashboard-admin", "", "ClusterRole")).toBe(false)
    expect(isAcceptedRbacRole("platform-admin", "", "ClusterRole")).toBe(false)
    expect(isAcceptedRbacRole("platform-admin-extra", "", "ClusterRole")).toBe(false)
    expect(isAcceptedRbacRole("velero-ui-custom-role", "storage", "Role")).toBe(false)
    expect(isAcceptedRbacRole("system:discovery")).toBe(true)
    expect(isAcceptedRbacRole("cert-manager-cainjector")).toBe(true)
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
            name: "harbor-core-99b8c7d6f",
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

    const harborCore = byName.get("harbor-core-99b8c7d6f")!
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
    expect(ksv020?.acceptedReasonKey).toBe("compliance.acceptedReason.portalUid1001")
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
    expect(paDetail?.checks.find((c) => c.id === "KSV041")?.acceptedReasonKey).toBe(
      "compliance.acceptedReason.rbacPlatformAdmin",
    )
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

  it("fails closed when summary counts exceed checks[] (e.g. Critical 1 / High 1 with only one accepted High check)", async () => {
    installK8sMock({
      "/apis/aquasecurity.github.io/v1alpha1/configauditreports": {
        items: [
          {
            metadata: {
              name: "replicaset-harbor-core-b569bd8dd",
              namespace: "devtools",
              creationTimestamp: "2026-11-18T00:00:00Z",
              labels: {
                "trivy-operator.resource.kind": "ReplicaSet",
                "trivy-operator.resource.name": "harbor-core-b569bd8dd",
              },
            },
            report: {
              summary: { criticalCount: 1, highCount: 1, mediumCount: 0, lowCount: 0 },
              checks: [{ checkID: "KSV014", title: "Root FS", severity: "HIGH", success: false }],
            },
          },
          // Also verify checks > summary mismatch is not accepted
          {
            metadata: {
              name: "replicaset-harbor-nginx-67d8f97945",
              namespace: "devtools",
              creationTimestamp: "2026-11-18T00:00:00Z",
              labels: {
                "trivy-operator.resource.kind": "ReplicaSet",
                "trivy-operator.resource.name": "harbor-nginx-67d8f97945",
              },
            },
            report: {
              summary: { criticalCount: 0, highCount: 1, mediumCount: 0, lowCount: 0 },
              checks: [
                { checkID: "KSV014", title: "Root FS", severity: "HIGH", success: false },
                { checkID: "KSV014", title: "Root FS", severity: "HIGH", success: false },
              ],
            },
          },
          // Standalone-looking ReplicaSet suffix abc12345 stays actionable
          makeCar({
            namespace: "devtools",
            kind: "ReplicaSet",
            name: "harbor-core-abc12345",
            checks: [{ checkID: "KSV014", severity: "HIGH", success: false }],
          }),
        ],
      },
      "/apis/aquasecurity.github.io/v1alpha1/rbacassessmentreports": {
        items: [
          {
            metadata: {
              name: "hash-narwhal-portal-jobs",
              namespace: "devtools",
              creationTimestamp: "2026-11-18T00:00:00Z",
              labels: {
                "trivy-operator.resource.kind": "Role",
                "trivy-operator.resource.name": "narwhal-portal-jobs",
              },
              ownerReferences: [{ kind: "Role", name: "narwhal-portal-jobs" }],
            },
            report: {
              summary: { criticalCount: 1, highCount: 0, mediumCount: 1, lowCount: 0 },
              checks: [{ checkID: "KSV048", title: "Manage workloads", severity: "MEDIUM", success: false }],
            },
          },
        ],
      },
      "/apis/aquasecurity.github.io/v1alpha1/clusterrbacassessmentreports": {
        items: [
          makeRbac({
            kind: "ClusterRole",
            roleName: "platform-admin-chaos-mesh",
            checks: [{ checkID: "KSV041", severity: "CRITICAL", success: false }],
          }),
          makeRbac({
            kind: "ClusterRole",
            roleName: "developer-kubernetes-dashboard-admin",
            checks: [{ checkID: "KSV041", severity: "CRITICAL", success: false }],
          }),
        ],
      },
    })

    const carRows = await getConfigAuditList()
    const harborMismatch = carRows.find((r) => r.name === "harbor-core-b569bd8dd")!
    expect(harborMismatch.accepted).toBe(false)
    expect(harborMismatch.actionableSummary).toEqual({ Critical: 1, High: 0, Medium: 0, Low: 0 })
    expect(harborMismatch.acceptedSummary).toEqual({ Critical: 0, High: 1, Medium: 0, Low: 0 })

    const harborOvercount = carRows.find((r) => r.name === "harbor-nginx-67d8f97945")!
    expect(harborOvercount.accepted).toBe(false)

    const standaloneRs = carRows.find((r) => r.name === "harbor-core-abc12345")!
    expect(standaloneRs.accepted).toBe(false)
    expect(standaloneRs.actionableSummary).toEqual({ Critical: 0, High: 1, Medium: 0, Low: 0 })

    const rbacRows = await getRbacAuditList()
    const rbacMismatch = rbacRows.find((r) => r.name === "narwhal-portal-jobs")!
    expect(rbacMismatch.accepted).toBe(false)
    expect(rbacMismatch.actionableSummary).toEqual({ Critical: 1, High: 0, Medium: 0, Low: 0 })
    expect(rbacMismatch.acceptedSummary).toEqual({ Critical: 0, High: 0, Medium: 1, Low: 0 })

    expect(rbacRows.find((r) => r.name === "platform-admin-chaos-mesh")?.accepted).toBe(false)
    expect(rbacRows.find((r) => r.name === "developer-kubernetes-dashboard-admin")?.accepted).toBe(false)

    const summary = await getComplianceSummary()
    expect(summary.totalConfigAuditFailures).toEqual({ Critical: 1, High: 1, Medium: 0, Low: 0 })
    expect(summary.totalRbacFailures).toEqual({ Critical: 3, High: 0, Medium: 0, Low: 0 })
  })

  it("keeps unlisted CRITICAL findings actionable when an exact RBAC role name appears in a wrong namespace or kind", async () => {
    installK8sMock({
      "/apis/aquasecurity.github.io/v1alpha1/rbacassessmentreports": {
        items: [
          makeRbac({
            namespace: "devtools",
            kind: "Role",
            roleName: "kubernetes-dashboard-api",
            checks: [{ checkID: "KSV049", severity: "MEDIUM", success: false }],
          }),
          makeRbac({
            namespace: "dev",
            kind: "Role",
            roleName: "kubernetes-dashboard-api",
            checks: [{ checkID: "KSV042", severity: "CRITICAL", success: false }],
          }),
          // Unknown resource kind label/ownerRef must also fail closed
          {
            metadata: {
              name: "hash-unknown-kind",
              namespace: "devtools",
              creationTimestamp: "2026-11-18T00:00:00Z",
              labels: { "trivy-operator.resource.name": "kubernetes-dashboard-web" },
            },
            report: {
              summary: { criticalCount: 1, highCount: 0, mediumCount: 0, lowCount: 0 },
              checks: [{ checkID: "KSV042", title: "RBAC KSV042", severity: "CRITICAL", success: false }],
            },
          },
        ],
      },
      "/apis/aquasecurity.github.io/v1alpha1/clusterrbacassessmentreports": {
        items: [
          makeRbac({
            kind: "ClusterRole",
            roleName: "kubernetes-dashboard-web",
            checks: [{ checkID: "KSV042", severity: "CRITICAL", success: false }],
          }),
        ],
      },
    })

    const rows = await getRbacAuditList()
    const devtoolsApi = rows.find((r) => r.namespace === "devtools" && r.name === "kubernetes-dashboard-api")!
    expect(devtoolsApi.accepted).toBe(true)
    expect(devtoolsApi.actionableSummary).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 0 })
    expect(devtoolsApi.acceptedSummary).toEqual({ Critical: 0, High: 0, Medium: 1, Low: 0 })

    const devApi = rows.find((r) => r.namespace === "dev" && r.name === "kubernetes-dashboard-api")!
    expect(devApi.accepted).toBe(false)
    expect(devApi.actionableSummary).toEqual({ Critical: 1, High: 0, Medium: 0, Low: 0 })
    expect(devApi.acceptedSummary).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 0 })

    const clusterWeb = rows.find((r) => r.kind === "ClusterRole" && r.name === "kubernetes-dashboard-web")!
    expect(clusterWeb.accepted).toBe(false)
    expect(clusterWeb.actionableSummary).toEqual({ Critical: 1, High: 0, Medium: 0, Low: 0 })

    const unknownKindWeb = rows.find((r) => r.kind === "Unknown" && r.name === "kubernetes-dashboard-web")!
    expect(unknownKindWeb.accepted).toBe(false)
    expect(unknownKindWeb.actionableSummary).toEqual({ Critical: 1, High: 0, Medium: 0, Low: 0 })

    const devApiDetail = await getRbacAuditDetail("dev", "kubernetes-dashboard-api")
    expect(devApiDetail?.accepted).toBe(false)
    expect(devApiDetail?.checks.find((c) => c.id === "KSV042")?.accepted).toBe(false)

    const summary = await getComplianceSummary()
    expect(summary.totalRbacFailures).toEqual({ Critical: 3, High: 0, Medium: 0, Low: 0 })
    expect(summary.acceptedRbacFailures).toEqual({ Critical: 0, High: 0, Medium: 1, Low: 0 })
  })

  it("fails closed when an authored RBAC report has Unknown or mismatched kind (narwhal#717)", async () => {
    installK8sMock({
      "/apis/aquasecurity.github.io/v1alpha1/rbacassessmentreports": {
        items: [
          // Counter-example from narwhal#717: devtools/Unknown/narwhal-portal-jobs with failing KSV048
          {
            metadata: {
              name: "hash-unknown-portal-jobs",
              namespace: "devtools",
              creationTimestamp: "2026-11-18T00:00:00Z",
              labels: { "trivy-operator.resource.name": "narwhal-portal-jobs" },
            },
            report: {
              summary: { criticalCount: 0, highCount: 0, mediumCount: 1, lowCount: 0 },
              checks: [{ checkID: "KSV048", title: "Manage workloads", severity: "MEDIUM", success: false }],
            },
          },
          makeRbac({
            namespace: "devtools",
            kind: "Role",
            roleName: "platform-admin",
            checks: [{ checkID: "KSV041", severity: "CRITICAL", success: false }],
          }),
        ],
      },
      "/apis/aquasecurity.github.io/v1alpha1/clusterrbacassessmentreports": {
        items: [
          makeRbac({
            kind: "ClusterRole",
            roleName: "narwhal-portal-jobs",
            checks: [{ checkID: "KSV048", severity: "MEDIUM", success: false }],
          }),
        ],
      },
    })

    const rows = await getRbacAuditList()
    const unknownPortalJobs = rows.find((r) => r.kind === "Unknown" && r.name === "narwhal-portal-jobs")!
    expect(unknownPortalJobs.namespace).toBe("devtools")
    expect(unknownPortalJobs.accepted).toBe(false)
    expect(unknownPortalJobs.actionableSummary).toEqual({ Critical: 0, High: 0, Medium: 1, Low: 0 })
    expect(unknownPortalJobs.acceptedSummary).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 0 })

    const clusterPortalJobs = rows.find((r) => r.kind === "ClusterRole" && r.name === "narwhal-portal-jobs")!
    expect(clusterPortalJobs.accepted).toBe(false)
    expect(clusterPortalJobs.actionableSummary).toEqual({ Critical: 0, High: 0, Medium: 1, Low: 0 })

    const namespacedPlatformAdmin = rows.find((r) => r.kind === "Role" && r.name === "platform-admin")!
    expect(namespacedPlatformAdmin.accepted).toBe(false)
    expect(namespacedPlatformAdmin.actionableSummary).toEqual({ Critical: 1, High: 0, Medium: 0, Low: 0 })

    const detail = await getRbacAuditDetail("devtools", "narwhal-portal-jobs")
    expect(detail?.accepted).toBe(false)
    expect(detail?.checks.find((c) => c.id === "KSV048")?.accepted).toBe(false)

    const summary = await getComplianceSummary()
    expect(summary.totalRbacFailures).toEqual({ Critical: 1, High: 0, Medium: 2, Low: 0 })
    expect(summary.acceptedRbacFailures).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 0 })
  })
})

describe("ReplicaSet filtering and exact Deployment ownership (narwhal#727)", () => {
  beforeEach(() => {
    mockCache.clear()
    mockFetchWithPolicy.mockReset()
    mockReadJsonWithPolicy.mockReset()
  })

  it("excludes ConfigAuditReports of fully drained superseded Deployment revisions from list, detail, and summary while keeping active ones", async () => {
    installK8sMock({
      "/apis/aquasecurity.github.io/v1alpha1/configauditreports": {
        items: [
          // Fully drained superseded revision of velero (rev 2 < rev 10) -> must be excluded
          makeCar({
            namespace: "storage",
            kind: "ReplicaSet",
            name: "velero-6447588f5f",
            checks: [
              { checkID: "KSV014", severity: "HIGH", success: false },
              { checkID: "KSV020", severity: "LOW", success: false },
            ],
          }),
          // Active revision of velero (rev 10, tests numeric revision ordering "10" > "2") -> must be kept
          makeCar({
            namespace: "storage",
            kind: "ReplicaSet",
            name: "velero-7b8c9d4f5g",
            checks: [{ checkID: "KSV020", severity: "LOW", success: false }],
          }),
          // Fully drained superseded revision of keycloak-operator (rev 1 < rev 2) -> must be excluded
          makeCar({
            namespace: "iam",
            kind: "ReplicaSet",
            name: "keycloak-operator-5f4d3c2b9",
            checks: [{ checkID: "KSV001", severity: "CRITICAL", success: false }],
          }),
        ],
      },
      "/apis/apps/v1/replicasets": {
        items: [
          makeRs({
            namespace: "storage",
            name: "velero-6447588f5f",
            replicas: 0,
            statusReplicas: 0,
            revision: 2,
            ownerDeployment: "velero",
          }),
          makeRs({
            namespace: "storage",
            name: "velero-7b8c9d4f5g",
            replicas: 1,
            statusReplicas: 1,
            revision: 10,
            ownerDeployment: "velero",
          }),
          makeRs({
            namespace: "iam",
            name: "keycloak-operator-5f4d3c2b9",
            replicas: 0,
            statusReplicas: 0,
            revision: 1,
            ownerDeployment: "keycloak-operator",
          }),
          makeRs({
            namespace: "iam",
            name: "keycloak-operator-9c8b7a6f5",
            replicas: 1,
            statusReplicas: 1,
            revision: 2,
            ownerDeployment: "keycloak-operator",
          }),
        ],
      },
    })

    const rows = await getConfigAuditList()
    expect(rows.map((r) => r.name)).toEqual(["velero-7b8c9d4f5g"])

    // Detail on the superseded revision is also excluded, while active one resolves
    expect(await getConfigAuditDetail("storage", "velero-6447588f5f")).toBeNull()
    const activeDetail = await getConfigAuditDetail("storage", "velero-7b8c9d4f5g")
    expect(activeDetail).not.toBeNull()
    expect(activeDetail!.checks.find((c) => c.id === "KSV020")?.accepted).toBe(true)

    const summary = await getComplianceSummary()
    expect(summary.scannedWorkloads).toBe(1)
    expect(summary.totalConfigAuditFailures).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 0 })
    expect(summary.lowSeverityConfigAuditFailures).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 1 })

    // No separate ReplicaSet cache entry should exist (only combined report caches)
    expect(mockCache.has("compliance:replicasets:list")).toBe(false)
  })

  it("keeps standalone 0-replica ReplicaSets, scaled-to-zero Deployments (current revision), terminating revisions, and unknown metadata", async () => {
    installK8sMock({
      "/apis/aquasecurity.github.io/v1alpha1/configauditreports": {
        items: [
          // 1. Standalone ReplicaSet at 0/0 (no ownerReferences) -> kept
          makeCar({
            namespace: "dev",
            kind: "ReplicaSet",
            name: "standalone-rs",
            checks: [{ checkID: "KSV014", severity: "HIGH", success: false }],
          }),
          // 2. Non-Deployment-owned ReplicaSet at 0/0 -> kept
          makeCar({
            namespace: "dev",
            kind: "ReplicaSet",
            name: "custom-owned-rs",
            checks: [{ checkID: "KSV014", severity: "HIGH", success: false }],
          }),
          // 3. Deployment scaled to 0: current revision (rev 2) at 0/0 -> kept (and allowlist still works via ownerRef)
          makeCar({
            namespace: "devtools",
            kind: "ReplicaSet",
            name: "harbor-core-b569bd8dd",
            checks: [{ checkID: "KSV014", severity: "HIGH", success: false }],
          }),
          // 3b. Deployment scaled to 0: old revision (rev 1) at 0/0 -> dropped (superseded by rev 2)
          makeCar({
            namespace: "devtools",
            kind: "ReplicaSet",
            name: "harbor-core-old11111",
            checks: [{ checkID: "KSV001", severity: "CRITICAL", success: false }],
          }),
          // 4. Old revision with status.replicas > 0 (pods still terminating) -> kept
          makeCar({
            namespace: "storage",
            kind: "ReplicaSet",
            name: "velero-terminating",
            checks: [{ checkID: "KSV014", severity: "HIGH", success: false }],
          }),
          // 5. Old revision missing status.replicas (unknown) -> kept
          makeCar({
            namespace: "storage",
            kind: "ReplicaSet",
            name: "velero-unknown-status",
            checks: [{ checkID: "KSV014", severity: "HIGH", success: false }],
          }),
          // 6. Old revision missing revision annotation (unknown) -> kept
          makeCar({
            namespace: "storage",
            kind: "ReplicaSet",
            name: "velero-unknown-rev",
            checks: [{ checkID: "KSV014", severity: "HIGH", success: false }],
          }),
          // 7. Cross-namespace Deployment with same name must not mark this RS superseded -> kept
          makeCar({
            namespace: "other-ns",
            kind: "ReplicaSet",
            name: "velero-other-ns",
            checks: [{ checkID: "KSV014", severity: "HIGH", success: false }],
          }),
        ],
      },
      "/apis/apps/v1/replicasets": {
        items: [
          makeRs({ namespace: "dev", name: "standalone-rs", replicas: 0, statusReplicas: 0, revision: 1 }),
          makeRs({
            namespace: "dev",
            name: "custom-owned-rs",
            replicas: 0,
            statusReplicas: 0,
            revision: 1,
            ownerDeployment: "custom-owner",
            ownerKind: "StatefulSet",
          }),
          makeRs({
            namespace: "devtools",
            name: "harbor-core-b569bd8dd",
            replicas: 0,
            statusReplicas: 0,
            revision: 2,
            ownerDeployment: "harbor-core",
          }),
          makeRs({
            namespace: "devtools",
            name: "harbor-core-old11111",
            replicas: 0,
            statusReplicas: 0,
            revision: 1,
            ownerDeployment: "harbor-core",
          }),
          makeRs({
            namespace: "storage",
            name: "velero-terminating",
            replicas: 0,
            statusReplicas: 0,
            terminatingReplicas: 1,
            revision: 1,
            ownerDeployment: "velero",
          }),
          makeRs({
            namespace: "storage",
            name: "velero-unknown-status",
            replicas: 0,
            revision: 1,
            ownerDeployment: "velero",
          }),
          makeRs({
            namespace: "storage",
            name: "velero-unknown-rev",
            replicas: 0,
            statusReplicas: 0,
            ownerDeployment: "velero",
          }),
          makeRs({
            namespace: "storage",
            name: "velero-current",
            replicas: 1,
            statusReplicas: 1,
            revision: 5,
            ownerDeployment: "velero",
          }),
          makeRs({
            namespace: "other-ns",
            name: "velero-other-ns",
            replicas: 0,
            statusReplicas: 0,
            revision: 1,
            ownerDeployment: "velero",
          }),
        ],
      },
    })

    const rows = await getConfigAuditList()
    expect(rows.map((r) => r.name)).toEqual([
      "standalone-rs",
      "custom-owned-rs",
      "harbor-core-b569bd8dd",
      "velero-terminating",
      "velero-unknown-status",
      "velero-unknown-rev",
      "velero-other-ns",
    ])

    // Scaled-to-zero Deployment's current revision keeps its allowlist matching via ownerRef
    expect(rows.find((r) => r.name === "harbor-core-b569bd8dd")?.accepted).toBe(true)
    // Superseded revision is excluded from detail too
    expect(await getConfigAuditDetail("devtools", "harbor-core-old11111")).toBeNull()
    // Terminating / standalone / unknown remain visible in detail and actionable summary
    expect(await getConfigAuditDetail("storage", "velero-terminating")).not.toBeNull()

    const summary = await getComplianceSummary()
    expect(summary.scannedWorkloads).toBe(7)
    expect(summary.totalConfigAuditFailures).toEqual({ Critical: 0, High: 6, Medium: 0, Low: 0 })
    expect(summary.acceptedSystemConfigAuditFailures).toEqual({ Critical: 0, High: 1, Medium: 0, Low: 0 })
  })

  it("restores a previously superseded ReplicaSet report on rollback and on the next computation without stale RS cache", async () => {
    let phase: "old" | "rolled_back" = "old"
    mockFetchWithPolicy.mockImplementation(async (url: string) => {
      const path = new URL(url).pathname
      return { ok: true, status: 200, statusText: "OK", _path: path }
    })
    mockReadJsonWithPolicy.mockImplementation(async (res: { _path: string }) => {
      if (res._path === "/apis/aquasecurity.github.io/v1alpha1/configauditreports") {
        return {
          items: [
            makeCar({
              namespace: "dev",
              kind: "ReplicaSet",
              name: "web-rs-a",
              checks: [{ checkID: "KSV014", severity: "HIGH", success: false }],
            }),
            makeCar({
              namespace: "dev",
              kind: "ReplicaSet",
              name: "web-rs-b",
              checks: [{ checkID: "KSV020", severity: "LOW", success: false }],
            }),
          ],
        }
      }
      if (res._path === "/apis/apps/v1/replicasets") {
        return phase === "old"
          ? {
              items: [
                makeRs({
                  namespace: "dev",
                  name: "web-rs-a",
                  replicas: 0,
                  statusReplicas: 0,
                  revision: 1,
                  ownerDeployment: "web",
                }),
                makeRs({
                  namespace: "dev",
                  name: "web-rs-b",
                  replicas: 1,
                  statusReplicas: 1,
                  revision: 2,
                  ownerDeployment: "web",
                }),
              ],
            }
          : {
              items: [
                // Rollback bumps web-rs-a's revision to 3 and scales it back up; web-rs-b drains to 0/0 at rev 2
                makeRs({
                  namespace: "dev",
                  name: "web-rs-a",
                  replicas: 1,
                  statusReplicas: 1,
                  revision: 3,
                  ownerDeployment: "web",
                }),
                makeRs({
                  namespace: "dev",
                  name: "web-rs-b",
                  replicas: 0,
                  statusReplicas: 0,
                  revision: 2,
                  ownerDeployment: "web",
                }),
              ],
            }
      }
      return { items: [] }
    })

    // First computation: web-rs-a is old/0 -> dropped; web-rs-b is current/1 -> kept
    const firstRows = await getConfigAuditList()
    expect(firstRows.map((r) => r.name)).toEqual(["web-rs-b"])
    expect(await getConfigAuditDetail("dev", "web-rs-a")).toBeNull()
    expect(mockCache.has("compliance:replicasets:list")).toBe(false)

    // Rollback happens before any separate RS TTL could expire; simulate report cache expiry
    phase = "rolled_back"
    mockCache.delete("compliance:config-audit:list")

    // Next computation fetches RS list in the same request path -> web-rs-a is immediately back, web-rs-b is dropped
    const secondRows = await getConfigAuditList()
    expect(secondRows.map((r) => r.name)).toEqual(["web-rs-a"])
    const restoredDetail = await getConfigAuditDetail("dev", "web-rs-a")
    expect(restoredDetail?.name).toBe("web-rs-a")
    expect(await getConfigAuditDetail("dev", "web-rs-b")).toBeNull()
  })

  it("fails safe when ReplicaSet list fails (403/timeout): keeps all reports, logs one warning, and does not cache failure", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {})
    try {
      mockFetchWithPolicy.mockImplementation(async (url: string) => {
        const path = new URL(url).pathname
        if (path === "/apis/apps/v1/replicasets") {
          return { ok: false, status: 403, statusText: "Forbidden", _path: path }
        }
        return { ok: true, status: 200, statusText: "OK", _path: path }
      })
      mockReadJsonWithPolicy.mockImplementation(async (res: { _path: string }) => {
        if (res._path === "/apis/aquasecurity.github.io/v1alpha1/configauditreports") {
          return {
            items: [
              makeCar({
                namespace: "storage",
                kind: "ReplicaSet",
                name: "velero-6447588f5f",
                checks: [{ checkID: "KSV014", severity: "HIGH", success: false }],
              }),
              makeCar({
                namespace: "devtools",
                kind: "ReplicaSet",
                name: "harbor-core-99b8c7d6f",
                checks: [{ checkID: "KSV014", severity: "HIGH", success: false }],
              }),
            ],
          }
        }
        return { items: [] }
      })

      const rows = await getConfigAuditList()
      expect(rows.map((r) => r.name)).toEqual(["velero-6447588f5f", "harbor-core-99b8c7d6f"])
      // Unknown ownership keeps existing alphabet fallback for harbor-core-99b8c7d6f
      expect(rows.find((r) => r.name === "harbor-core-99b8c7d6f")?.accepted).toBe(true)

      expect(warnSpy).toHaveBeenCalledTimes(1)
      expect(warnSpy.mock.calls[0]?.[0]).toContain("failed to list ReplicaSets")
      // Failed RS list must not be cached in compliance:replicasets:list
      expect(mockCache.has("compliance:replicasets:list")).toBe(false)

      const summary = await getComplianceSummary()
      expect(summary.scannedWorkloads).toBe(2)
      expect(summary.totalConfigAuditFailures).toEqual({ Critical: 0, High: 1, Medium: 0, Low: 0 })
    } finally {
      warnSpy.mockRestore()
    }
  })

  it("fails safe when a report's ReplicaSet is missing from the cluster list: keeps it and logs one warning", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {})
    try {
      installK8sMock({
        "/apis/aquasecurity.github.io/v1alpha1/configauditreports": {
          items: [
            makeCar({
              namespace: "storage",
              kind: "ReplicaSet",
              name: "velero-7b8c9d4f5g",
              checks: [{ checkID: "KSV014", severity: "HIGH", success: false }],
            }),
            makeCar({
              namespace: "iam",
              kind: "ReplicaSet",
              name: "keycloak-operator-5f4d3c2b9",
              checks: [{ checkID: "KSV014", severity: "HIGH", success: false }],
            }),
            makeCar({
              namespace: "devtools",
              kind: "ReplicaSet",
              name: "headlamp-6d7f8g9h2",
              checks: [{ checkID: "KSV014", severity: "HIGH", success: false }],
            }),
          ],
        },
        "/apis/apps/v1/replicasets": {
          items: [
            // Only velero is present; keycloak-operator and headlamp ReplicaSets are missing
            makeRs({ namespace: "storage", name: "velero-7b8c9d4f5g", replicas: 1, ownerDeployment: "velero" }),
          ],
        },
      })

      const rows = await getConfigAuditList()
      expect(rows.map((r) => r.name)).toEqual([
        "velero-7b8c9d4f5g",
        "keycloak-operator-5f4d3c2b9",
        "headlamp-6d7f8g9h2",
      ])
      expect(warnSpy).toHaveBeenCalledTimes(1)
      expect(warnSpy.mock.calls[0]?.[0]).toContain("missing from cluster list")

      const summary = await getComplianceSummary()
      expect(summary.scannedWorkloads).toBe(3)
      expect(summary.totalConfigAuditFailures).toEqual({ Critical: 0, High: 3, Medium: 0, Low: 0 })
    } finally {
      warnSpy.mockRestore()
    }
  })

  it("does NOT accept a standalone ReplicaSet named harbor-core-<hash-like> without a Deployment owner, while accepting Deployment-owned ones", async () => {
    installK8sMock({
      "/apis/aquasecurity.github.io/v1alpha1/configauditreports": {
        items: [
          // Standalone ReplicaSet with a valid SafeEncodeString hash-like suffix and NO ownerReferences
          makeCar({
            namespace: "devtools",
            kind: "ReplicaSet",
            name: "harbor-core-bcdfg",
            checks: [
              { checkID: "KSV014", severity: "HIGH", success: false },
              { checkID: "KSV020", severity: "LOW", success: false },
            ],
          }),
          // ReplicaSet owned by a non-Deployment resource (e.g. CustomSet) -> also NOT accepted
          makeCar({
            namespace: "devtools",
            kind: "ReplicaSet",
            name: "harbor-core-99b8c7d6f",
            checks: [{ checkID: "KSV014", severity: "HIGH", success: false }],
          }),
          // Real Deployment-owned ReplicaSet -> accepted as before
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
          // Non-hash suffix even with Deployment owner -> NOT accepted (never broaden)
          makeCar({
            namespace: "devtools",
            kind: "ReplicaSet",
            name: "harbor-core-abc12345",
            checks: [{ checkID: "KSV014", severity: "HIGH", success: false }],
          }),
        ],
      },
      "/apis/apps/v1/replicasets": {
        items: [
          makeRs({ namespace: "devtools", name: "harbor-core-bcdfg", replicas: 1 }),
          makeRs({
            namespace: "devtools",
            name: "harbor-core-99b8c7d6f",
            replicas: 1,
            ownerDeployment: "harbor-core",
            ownerKind: "StatefulSet",
          }),
          makeRs({
            namespace: "devtools",
            name: "harbor-core-b569bd8dd",
            replicas: 1,
            ownerDeployment: "harbor-core",
          }),
          makeRs({
            namespace: "devtools",
            name: "harbor-core-abc12345",
            replicas: 1,
            ownerDeployment: "harbor-core",
          }),
        ],
      },
    })

    const rows = await getConfigAuditList()
    const byName = new Map(rows.map((r) => [r.name, r]))

    expect(byName.get("harbor-core-bcdfg")?.accepted).toBe(false)
    expect(byName.get("harbor-core-bcdfg")?.actionableSummary).toEqual({ Critical: 0, High: 1, Medium: 0, Low: 1 })
    expect(byName.get("harbor-core-bcdfg")?.acceptedSummary).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 0 })

    expect(byName.get("harbor-core-99b8c7d6f")?.accepted).toBe(false)
    expect(byName.get("harbor-core-99b8c7d6f")?.actionableSummary).toEqual({ Critical: 0, High: 1, Medium: 0, Low: 0 })

    expect(byName.get("harbor-core-b569bd8dd")?.accepted).toBe(true)
    expect(byName.get("harbor-core-b569bd8dd")?.actionableSummary).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 0 })
    expect(byName.get("harbor-core-b569bd8dd")?.acceptedSummary).toEqual({ Critical: 0, High: 1, Medium: 0, Low: 2 })

    expect(byName.get("harbor-core-abc12345")?.accepted).toBe(false)

    const standaloneDetail = await getConfigAuditDetail("devtools", "harbor-core-bcdfg")
    expect(standaloneDetail?.accepted).toBe(false)
    expect(standaloneDetail?.checks.find((c) => c.id === "KSV014")?.accepted).toBe(false)

    const ownedDetail = await getConfigAuditDetail("devtools", "harbor-core-b569bd8dd")
    expect(ownedDetail?.accepted).toBe(true)
    expect(ownedDetail?.checks.find((c) => c.id === "KSV014")?.accepted).toBe(true)

    const summary = await getComplianceSummary()
    expect(summary.totalConfigAuditFailures).toEqual({ Critical: 0, High: 3, Medium: 0, Low: 0 })
    expect(summary.acceptedSystemConfigAuditFailures).toEqual({ Critical: 0, High: 1, Medium: 0, Low: 0 })
  })
})

function makeCcr(opts: {
  id: string
  title?: string
  passCount: number
  failCount: number
  specControls?: Array<{ id: string; name: string; severity: string }>
  results?: Array<{
    id: string
    name: string
    severity: string
    checks: Array<{ checkID?: string; target?: string; success?: boolean }>
  }>
}) {
  return {
    metadata: { name: opts.id, creationTimestamp: "2026-11-19T00:00:00Z" },
    spec: {
      compliance: {
        id: opts.id,
        title: opts.title ?? opts.id,
        controls: opts.specControls ?? (opts.results ?? []).map((r) => ({ id: r.id, name: r.name, severity: r.severity })),
      },
    },
    status: {
      summary: {
        passCount: opts.passCount,
        failCount: opts.failCount,
      },
      ...(opts.results ? { detailReport: { results: opts.results } } : {}),
    },
  }
}

describe("parseComplianceTarget", () => {
  it("parses supported namespaced and cluster-scoped targets", () => {
    expect(parseComplianceTarget("devtools/replicaset-gitea-67f4766475")).toEqual({
      namespace: "devtools",
      kind: "ReplicaSet",
      name: "gitea-67f4766475",
    })
    expect(parseComplianceTarget("security-system/daemonset-falco")).toEqual({
      namespace: "security-system",
      kind: "DaemonSet",
      name: "falco",
    })
    expect(parseComplianceTarget("iam/statefulset-keycloak")).toEqual({
      namespace: "iam",
      kind: "StatefulSet",
      name: "keycloak",
    })
    expect(parseComplianceTarget("database/pod-narwhal-db-1")).toEqual({
      namespace: "database",
      kind: "Pod",
      name: "narwhal-db-1",
    })
    expect(parseComplianceTarget("dev/deployment-web")).toEqual({
      namespace: "dev",
      kind: "Deployment",
      name: "web",
    })
    expect(parseComplianceTarget("storage/cronjob-openbao-snapshot")).toEqual({
      namespace: "storage",
      kind: "CronJob",
      name: "openbao-snapshot",
    })
    expect(parseComplianceTarget("devtools/job-manual-run")).toEqual({
      namespace: "devtools",
      kind: "Job",
      name: "manual-run",
    })
    expect(parseComplianceTarget("devtools/role-narwhal-portal-jobs")).toEqual({
      namespace: "devtools",
      kind: "Role",
      name: "narwhal-portal-jobs",
    })
    expect(parseComplianceTarget("/clusterrole-cluster-admin")).toEqual({
      namespace: "",
      kind: "ClusterRole",
      name: "cluster-admin",
    })
    expect(parseComplianceTarget("/node-narwhal-master-1")).toEqual({
      namespace: "",
      kind: "Node",
      name: "narwhal-master-1",
    })
  })

  it("fails closed (null) on unknown kinds, missing/extra slashes, empty names, or scope mismatches", () => {
    expect(parseComplianceTarget("")).toBeNull()
    expect(parseComplianceTarget(null)).toBeNull()
    expect(parseComplianceTarget(undefined)).toBeNull()
    expect(parseComplianceTarget("clusterrole-cluster-admin")).toBeNull()
    expect(parseComplianceTarget("a/b/pod-foo")).toBeNull()
    expect(parseComplianceTarget("devtools/gitea")).toBeNull()
    expect(parseComplianceTarget("devtools/pod-")).toBeNull()
    expect(parseComplianceTarget("/clusterrole-")).toBeNull()
    expect(parseComplianceTarget("devtools/customcrd-foo")).toBeNull()
    expect(parseComplianceTarget("devtools/clusterrole-platform-admin")).toBeNull()
    expect(parseComplianceTarget("kube-system/node-narwhal-master-1")).toBeNull()
    expect(parseComplianceTarget("/pod-narwhal-db-1")).toBeNull()
    expect(parseComplianceTarget("/role-narwhal-portal-jobs")).toBeNull()
    expect(parseComplianceTarget("kube-system/constructor-foo")).toBeNull()
    expect(parseComplianceTarget("kube-system/__proto__-foo")).toBeNull()
    expect(parseComplianceTarget("kube-system/toString-foo")).toBeNull()
  })
})

describe("compliance framework actionable vs accepted split", () => {
  beforeEach(() => {
    mockCache.clear()
    mockFetchWithPolicy.mockReset()
    mockReadJsonWithPolicy.mockReset()
  })

  it("fails closed on unknown check IDs, unknown targets, mixed controls, and node checks", async () => {
    installK8sMock({
      "/apis/aquasecurity.github.io/v1alpha1/clustercompliancereports": {
        items: [
          makeCcr({
            id: "test-fw",
            title: "Test Framework",
            passCount: 1,
            failCount: 5,
            results: [
              // 0. Passing control -> never marked accepted
              {
                id: "C0",
                name: "All passing",
                severity: "LOW",
                checks: [{ checkID: "AVD-KSV-0020", target: "devtools/replicaset-gitea-67f4766475", success: true }],
              },
              // 1. Unknown check ID on allowlisted workload -> actionable
              {
                id: "C1",
                name: "Unknown check ID",
                severity: "HIGH",
                checks: [
                  { checkID: "AVD-KSV-9999", target: "security-system/daemonset-falco", success: false },
                  { target: "security-system/daemonset-falco", success: false },
                ],
              },
              // 2. Unparseable / unknown target -> actionable
              {
                id: "C2",
                name: "Unknown target",
                severity: "HIGH",
                checks: [
                  { checkID: "AVD-KSV-0017", target: "no-slash-target", success: false },
                  { checkID: "AVD-KSV-0017", target: "devtools/widget-foo", success: false },
                ],
              },
              // 3. Mixed control (one actionable + one accepted) -> actionable (control.accepted = false)
              {
                id: "C3",
                name: "Mixed control",
                severity: "HIGH",
                checks: [
                  { checkID: "AVD-KSV-0020", target: "devtools/replicaset-gitea-67f4766475", success: false },
                  { checkID: "AVD-KSV-0020", target: "dev/pod-unlisted-app", success: false },
                ],
              },
              // 4. Node / infra checks -> actionable (matches Infra tab)
              {
                id: "C4",
                name: "Node check",
                severity: "HIGH",
                checks: [{ checkID: "AVD-KCV-0056", target: "/node-narwhal-master-1", success: false }],
              },
              // 5. All accepted (system namespace + allowlisted workload) -> accepted
              {
                id: "C5",
                name: "Accepted control",
                severity: "MEDIUM",
                checks: [
                  { checkID: "AVD-KSV-0017", target: "kube-system/daemonset-cilium", success: false },
                  { checkID: "AVD-KSV-0017", target: "security-system/daemonset-falco", success: false },
                ],
              },
            ],
          }),
        ],
      },
    })

    const frameworks = await getComplianceFrameworks()
    expect(frameworks).toHaveLength(1)
    expect(frameworks[0]).toMatchObject({
      id: "test-fw",
      passCount: 1,
      failCount: 5,
      actionableFailCount: 4,
      acceptedFailCount: 1,
      totalControls: 6,
    })
    expect(frameworks[0].passRate).toBeCloseTo(1 / 6)
    expect(frameworks[0].effectivePassRate).toBeCloseTo(2 / 6)

    const detail = await getComplianceFrameworkDetail("test-fw")
    expect(detail).not.toBeNull()
    const byId = new Map(detail!.controls.map((c) => [c.id, c]))

    expect(byId.get("C0")?.accepted).toBe(false)
    expect(byId.get("C1")).toMatchObject({ failCount: 2, actionableFailCount: 2, acceptedFailCount: 0, accepted: false })
    expect(byId.get("C2")).toMatchObject({ failCount: 2, actionableFailCount: 2, acceptedFailCount: 0, accepted: false })
    expect(byId.get("C3")).toMatchObject({ failCount: 2, actionableFailCount: 1, acceptedFailCount: 1, accepted: false })
    expect(byId.get("C4")).toMatchObject({ failCount: 1, actionableFailCount: 1, acceptedFailCount: 0, accepted: false })
    expect(byId.get("C5")).toMatchObject({
      failCount: 2,
      actionableFailCount: 0,
      acceptedFailCount: 2,
      accepted: true,
      acceptedScope: "mixed",
    })
  })

  it("drops superseded ReplicaSets while enforcing Deployment ownership on live-revision ReplicaSets", async () => {
    installK8sMock({
      "/apis/aquasecurity.github.io/v1alpha1/clustercompliancereports": {
        items: [
          makeCcr({
            id: "rs-fw",
            passCount: 0,
            failCount: 3,
            results: [
              // Only superseded RS -> dropped (0 actionable, 0 accepted)
              {
                id: "RS-SUPERSEDED",
                name: "Superseded RS only",
                severity: "HIGH",
                checks: [
                  { checkID: "AVD-KSV-0014", target: "devtools/replicaset-unlisted-old11", success: false },
                ],
              },
              // Live-revision allowlisted RS with Deployment owner -> accepted
              {
                id: "RS-OWNED",
                name: "Live Deployment RS",
                severity: "HIGH",
                checks: [
                  { checkID: "AVD-KSV-0014", target: "devtools/replicaset-harbor-core-b569bd8dd", success: false },
                ],
              },
              // Standalone RS without Deployment owner even with hash-like suffix -> actionable
              {
                id: "RS-STANDALONE",
                name: "Standalone RS",
                severity: "HIGH",
                checks: [
                  { checkID: "AVD-KSV-0014", target: "devtools/replicaset-harbor-core-bcdfg", success: false },
                ],
              },
            ],
          }),
        ],
      },
      "/apis/apps/v1/replicasets": {
        items: [
          makeRs({
            namespace: "devtools",
            name: "unlisted-old11",
            replicas: 0,
            statusReplicas: 0,
            revision: 1,
            ownerDeployment: "unlisted",
          }),
          makeRs({
            namespace: "devtools",
            name: "unlisted-new22",
            replicas: 1,
            statusReplicas: 1,
            revision: 2,
            ownerDeployment: "unlisted",
          }),
          makeRs({
            namespace: "devtools",
            name: "harbor-core-b569bd8dd",
            replicas: 1,
            statusReplicas: 1,
            revision: 3,
            ownerDeployment: "harbor-core",
          }),
          makeRs({
            namespace: "devtools",
            name: "harbor-core-bcdfg",
            replicas: 1,
            statusReplicas: 1,
          }),
        ],
      },
    })

    const detail = await getComplianceFrameworkDetail("rs-fw")
    expect(detail).not.toBeNull()
    expect(detail!.failCount).toBe(3)
    expect(detail!.actionableFailCount).toBe(1)
    expect(detail!.acceptedFailCount).toBe(1)

    const byId = new Map(detail!.controls.map((c) => [c.id, c]))
    expect(byId.get("RS-SUPERSEDED")).toMatchObject({
      failCount: 1,
      actionableFailCount: 0,
      acceptedFailCount: 0,
      accepted: false,
    })
    expect(byId.get("RS-OWNED")).toMatchObject({
      failCount: 1,
      actionableFailCount: 0,
      acceptedFailCount: 1,
      accepted: true,
      acceptedScope: "upstream",
    })
    expect(byId.get("RS-STANDALONE")).toMatchObject({
      failCount: 1,
      actionableFailCount: 1,
      acceptedFailCount: 0,
      accepted: false,
    })
  })

  it("evaluates RBAC framework controls using built-in, upstream, ACCEPTED_RBAC_RULES, and report-hash resolution", async () => {
    installK8sMock({
      "/apis/aquasecurity.github.io/v1alpha1/clustercompliancereports": {
        items: [
          makeCcr({
            id: "rbac-fw",
            passCount: 0,
            failCount: 4,
            results: [
              // Built-in + upstream + ACCEPTED_RBAC_RULES -> accepted
              {
                id: "RBAC-OK",
                name: "Accepted RBAC",
                severity: "HIGH",
                checks: [
                  { checkID: "AVD-KSV-0041", target: "/clusterrole-cluster-admin", success: false },
                  { checkID: "AVD-KSV-0041", target: "/clusterrole-cert-manager-cainjector", success: false },
                  { checkID: "AVD-KSV-0041", target: "/clusterrole-platform-admin", success: false },
                  { checkID: "AVD-KSV-0048", target: "devtools/role-narwhal-portal-jobs", success: false },
                ],
              },
              // Unlisted check KSV042 on platform-admin -> stays actionable
              {
                id: "RBAC-UNLISTED-CHECK",
                name: "Unlisted check",
                severity: "MEDIUM",
                checks: [{ checkID: "AVD-KSV-0042", target: "/clusterrole-platform-admin", success: false }],
              },
              // Hashed ClusterRole resolved via clusterrbacassessmentreports -> accepted (system:*)
              {
                id: "RBAC-HASHED-RESOLVED",
                name: "Hashed resolved",
                severity: "HIGH",
                checks: [{ checkID: "AVD-KSV-0041", target: "/clusterrole-54cdc9b678", success: false }],
              },
              // Hashed ClusterRole NOT in clusterrbacassessmentreports -> fails closed (actionable)
              {
                id: "RBAC-HASHED-UNRESOLVED",
                name: "Hashed unresolved",
                severity: "HIGH",
                checks: [{ checkID: "AVD-KSV-0041", target: "/clusterrole-deadbeef00", success: false }],
              },
            ],
          }),
        ],
      },
      "/apis/aquasecurity.github.io/v1alpha1/clusterrbacassessmentreports": {
        items: [
          {
            metadata: {
              name: "clusterrole-54cdc9b678",
              labels: { "trivy-operator.resource.kind": "ClusterRole" },
              ownerReferences: [{ kind: "ClusterRole", name: "system:kube-controller-manager" }],
            },
            report: { summary: { criticalCount: 1, highCount: 0, mediumCount: 0, lowCount: 0 }, checks: [] },
          },
        ],
      },
    })

    const detail = await getComplianceFrameworkDetail("rbac-fw")
    expect(detail).not.toBeNull()
    expect(detail!.actionableFailCount).toBe(2)
    expect(detail!.acceptedFailCount).toBe(2)

    const byId = new Map(detail!.controls.map((c) => [c.id, c]))
    expect(byId.get("RBAC-OK")?.accepted).toBe(true)
    expect(byId.get("RBAC-OK")?.acceptedScope).toBe("mixed")
    expect(byId.get("RBAC-UNLISTED-CHECK")?.accepted).toBe(false)
    expect(byId.get("RBAC-HASHED-RESOLVED")).toMatchObject({
      accepted: true,
      acceptedScope: "builtin",
      actionableFailCount: 0,
      acceptedFailCount: 1,
    })
    expect(byId.get("RBAC-HASHED-UNRESOLVED")).toMatchObject({
      accepted: false,
      actionableFailCount: 1,
      acceptedFailCount: 0,
    })
  })

  it("fails closed when reportType=summary omits detailReport.results", async () => {
    installK8sMock({
      "/apis/aquasecurity.github.io/v1alpha1/clustercompliancereports": {
        items: [
          makeCcr({
            id: "summary-only",
            passCount: 10,
            failCount: 3,
            specControls: [
              { id: "1.1", name: "Ctrl 1", severity: "HIGH" },
              { id: "1.2", name: "Ctrl 2", severity: "LOW" },
            ],
          }),
        ],
      },
    })

    const frameworks = await getComplianceFrameworks()
    expect(frameworks[0]).toMatchObject({
      id: "summary-only",
      passCount: 10,
      failCount: 3,
      actionableFailCount: 3,
      acceptedFailCount: 0,
    })
    expect(frameworks[0].effectivePassRate).toBeCloseTo(frameworks[0].passRate)
  })

  it("computes the exact actionable vs accepted set from the trimmed live ClusterComplianceReport fixture", async () => {
    installK8sMock({
      "/apis/aquasecurity.github.io/v1alpha1/clustercompliancereports": ccrLiveTrimmed,
    })

    const frameworks = await getComplianceFrameworks()
    const byId = new Map(frameworks.map((f) => [f.id, f]))

    expect(byId.get("k8s-nsa-1.0")).toMatchObject({
      passCount: 18,
      failCount: 9,
      actionableFailCount: 0,
      acceptedFailCount: 9,
      effectivePassRate: 1,
    })
    expect(byId.get("k8s-pss-baseline-0.1")).toMatchObject({
      passCount: 8,
      failCount: 3,
      actionableFailCount: 0,
      acceptedFailCount: 3,
      effectivePassRate: 1,
    })
    expect(byId.get("k8s-pss-restricted-0.1")).toMatchObject({
      passCount: 14,
      failCount: 3,
      actionableFailCount: 0,
      acceptedFailCount: 3,
      effectivePassRate: 1,
    })
    expect(byId.get("k8s-cis-1.23")).toMatchObject({
      passCount: 101,
      failCount: 15,
      actionableFailCount: 5,
      acceptedFailCount: 10,
    })
    expect(byId.get("k8s-kisa-1.0")).toMatchObject({
      passCount: 3,
      failCount: 4,
      actionableFailCount: 2,
      acceptedFailCount: 2,
    })

    const cisDetail = await getComplianceFrameworkDetail("k8s-cis-1.23")
    expect(cisDetail).not.toBeNull()
    const cisActionable = cisDetail!.controls
      .filter((c) => (c.actionableFailCount ?? 0) > 0)
      .map((c) => ({ id: c.id, act: c.actionableFailCount, acc: c.acceptedFailCount }))
    expect(cisActionable).toEqual([
      { id: "1.1.9", act: 1, acc: 0 },
      { id: "1.1.12", act: 1, acc: 0 },
      { id: "5.1.2", act: 5, acc: 27 },
      { id: "5.1.3", act: 6, acc: 10 },
      { id: "5.7.3", act: 8, acc: 150 },
    ])

    const kisaDetail = await getComplianceFrameworkDetail("k8s-kisa-1.0")
    expect(kisaDetail).not.toBeNull()
    const kisaActionable = kisaDetail!.controls
      .filter((c) => (c.actionableFailCount ?? 0) > 0)
      .map((c) => ({ id: c.id, act: c.actionableFailCount, acc: c.acceptedFailCount }))
    expect(kisaActionable).toEqual([
      { id: "KISA-RBAC-01", act: 5, acc: 27 },
      { id: "KISA-RBAC-02", act: 6, acc: 10 },
    ])
  })

  it("fails safe on the framework path when replicasets list returns 403 or 500 (nothing dropped)", async () => {
    for (const status of [403, 500]) {
      mockCache.clear()
      const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {})
      try {
        mockFetchWithPolicy.mockImplementation(async (url: string) => {
          const path = new URL(url).pathname
          if (path === "/apis/apps/v1/replicasets") {
            return { ok: false, status, statusText: "Error", _path: path }
          }
          return { ok: true, status: 200, statusText: "OK", _path: path }
        })
        mockReadJsonWithPolicy.mockImplementation(async (res: { _path: string }) => {
          if (res._path === "/apis/aquasecurity.github.io/v1alpha1/clustercompliancereports") {
            return {
              items: [
                makeCcr({
                  id: `rs-err-${status}`,
                  passCount: 0,
                  failCount: 1,
                  results: [
                    {
                      id: "RS-1",
                      name: "Unlisted RS",
                      severity: "HIGH",
                      checks: [
                        { checkID: "AVD-KSV-0014", target: "devtools/replicaset-unlisted-old11", success: false },
                      ],
                    },
                  ],
                }),
              ],
            }
          }
          return { items: [] }
        })

        const frameworks = await getComplianceFrameworks()
        expect(frameworks[0]).toMatchObject({
          id: `rs-err-${status}`,
          failCount: 1,
          actionableFailCount: 1,
          acceptedFailCount: 0,
        })

        const detail = await getComplianceFrameworkDetail(`rs-err-${status}`)
        expect(detail).not.toBeNull()
        expect(detail!.controls[0]).toMatchObject({
          id: "RS-1",
          failCount: 1,
          actionableFailCount: 1,
          acceptedFailCount: 0,
          accepted: false,
        })

        const warnMsgs = warnSpy.mock.calls.map((c) => String(c[0]))
        expect(warnMsgs.some((m) => m.includes("failed to list ReplicaSets"))).toBe(true)
        expect(warnMsgs.some((m) => m.includes("missing from cluster list"))).toBe(false)
      } finally {
        warnSpy.mockRestore()
      }
    }
  })

  it("warns about missing ReplicaSets on the framework path even when replicasets list is empty", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {})
    try {
      installK8sMock({
        "/apis/aquasecurity.github.io/v1alpha1/clustercompliancereports": {
          items: [
            makeCcr({
              id: "rs-empty",
              passCount: 0,
              failCount: 1,
              results: [
                {
                  id: "RS-MISSING",
                  name: "Missing RS",
                  severity: "HIGH",
                  checks: [
                    { checkID: "AVD-KSV-0014", target: "devtools/replicaset-unlisted-old11", success: false },
                  ],
                },
              ],
            }),
          ],
        },
        "/apis/apps/v1/replicasets": { items: [] },
      })

      const frameworks = await getComplianceFrameworks()
      expect(frameworks[0]).toMatchObject({ failCount: 1, actionableFailCount: 1, acceptedFailCount: 0 })

      const detail = await getComplianceFrameworkDetail("rs-empty")
      expect(detail?.controls[0]).toMatchObject({
        id: "RS-MISSING",
        failCount: 1,
        actionableFailCount: 1,
        acceptedFailCount: 0,
      })

      const missingWarns = warnSpy.mock.calls
        .map((c) => String(c[0]))
        .filter((m) => m.includes("missing from cluster list"))
      expect(missingWarns).toHaveLength(2)
      expect(missingWarns[0]).toContain("devtools/unlisted-old11")
    } finally {
      warnSpy.mockRestore()
    }
  })

  it("fails closed when summary.failCount exceeds detail controls and handles controls with checks: []", async () => {
    installK8sMock({
      "/apis/aquasecurity.github.io/v1alpha1/clustercompliancereports": {
        items: [
          makeCcr({
            id: "unaccounted-and-empty",
            passCount: 5,
            failCount: 5,
            results: [
              {
                id: "C-EMPTY",
                name: "Empty checks control",
                severity: "LOW",
                checks: [],
              },
              {
                id: "C-ACCEPTED",
                name: "Accepted control",
                severity: "HIGH",
                checks: [{ checkID: "AVD-KSV-0017", target: "kube-system/daemonset-cilium", success: false }],
              },
              {
                id: "C-ACTIONABLE",
                name: "Actionable control",
                severity: "HIGH",
                checks: [{ checkID: "AVD-KSV-0014", target: "dev/pod-unlisted-app", success: false }],
              },
            ],
          }),
        ],
      },
    })

    const detail = await getComplianceFrameworkDetail("unaccounted-and-empty")
    expect(detail).not.toBeNull()
    // detailFailControls = 2 (C-ACCEPTED, C-ACTIONABLE); summary.failCount = 5 -> unaccountedFail = 3
    // actionableFailCount = 1 + 3 = 4; acceptedFailCount = 1; evaluated = 10 -> effectivePassRate = 6 / 10
    expect(detail!.actionableFailCount).toBe(4)
    expect(detail!.acceptedFailCount).toBe(1)
    expect(detail!.effectivePassRate).toBeCloseTo(6 / 10)

    const emptyCtrl = detail!.controls.find((c) => c.id === "C-EMPTY")
    expect(emptyCtrl).toMatchObject({
      passCount: 0,
      failCount: 0,
      actionableFailCount: 0,
      acceptedFailCount: 0,
      accepted: false,
    })
  })

  it("restores hashed StatefulSet names through configauditreports and fails closed when absent", async () => {
    installK8sMock({
      "/apis/aquasecurity.github.io/v1alpha1/clustercompliancereports": {
        items: [
          makeCcr({
            id: "sts-hash-fw",
            passCount: 0,
            failCount: 2,
            results: [
              {
                id: "STS-RESOLVED",
                name: "Hashed StatefulSet resolved via ConfigAuditReport",
                severity: "LOW",
                checks: [
                  { checkID: "AVD-KSV-0020", target: "monitoring/statefulset-78785dbd5c", success: false },
                ],
              },
              {
                id: "STS-UNRESOLVED",
                name: "Hashed StatefulSet missing from ConfigAuditReport",
                severity: "LOW",
                checks: [
                  { checkID: "AVD-KSV-0020", target: "monitoring/statefulset-deadbeef00", success: false },
                ],
              },
            ],
          }),
        ],
      },
      "/apis/aquasecurity.github.io/v1alpha1/configauditreports": {
        items: [
          {
            metadata: {
              namespace: "monitoring",
              name: "statefulset-78785dbd5c",
              labels: {
                "trivy-operator.resource.kind": "StatefulSet",
                "trivy-operator.resource.name": "prometheus-prometheus-stack-kube-prom-prometheus",
              },
            },
            report: { summary: { criticalCount: 0, highCount: 0, mediumCount: 0, lowCount: 1 }, checks: [] },
          },
        ],
      },
    })

    const detail = await getComplianceFrameworkDetail("sts-hash-fw")
    expect(detail).not.toBeNull()
    expect(detail!.actionableFailCount).toBe(1)
    expect(detail!.acceptedFailCount).toBe(1)

    const byId = new Map(detail!.controls.map((c) => [c.id, c]))
    expect(byId.get("STS-RESOLVED")).toMatchObject({
      accepted: true,
      acceptedScope: "accept",
      actionableFailCount: 0,
      acceptedFailCount: 1,
    })
    expect(byId.get("STS-UNRESOLVED")).toMatchObject({
      accepted: false,
      actionableFailCount: 1,
      acceptedFailCount: 0,
    })
  })
})
