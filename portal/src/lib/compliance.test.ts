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
import complianceSnapshot from "./__fixtures__/compliance-snapshot.json"
import { formatPassRatePct } from "@/types/compliance"
import {
  ComplianceDataUnavailableError,
  getComplianceFrameworkDetail,
  getComplianceFrameworks,
  getComplianceSummary,
  getConfigAuditDetail,
  getConfigAuditList,
  getInfraAuditDetail,
  getInfraAuditList,
  getRbacAuditDetail,
  getRbacAuditList,
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

  it("splits Config Audit rows into actionable vs accepted per check and handles ReplicaSet hash suffixes", async () => {
    installK8sMock({
      "/apis/apps/v1/replicasets": { items: [makeRs({ namespace: "devtools", name: "harbor-core-99b8c7d6f", ownerDeployment: "harbor-core", replicas: 1 }),makeRs({ namespace: "devtools", name: "narwhal-portal-74b459c658", ownerDeployment: "narwhal-portal", replicas: 1 })] },
      "/apis/aquasecurity.github.io/v1alpha1/configauditreports": {
        items: [
          // 1. Accepted system workload (cilium accepted KSV009; KSV030 is unaccepted LOW)
          makeCar({
            namespace: "kube-system",
            kind: "DaemonSet",
            name: "cilium",
            checks: [
              { checkID: "KSV009", severity: "HIGH", success: false },
              { checkID: "KSV030", severity: "LOW", success: false },
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
    expect(byName.get("cilium")?.acceptedScope).toBe("accept")

    const harborCore = byName.get("harbor-core-99b8c7d6f")!
    expect(harborCore.accepted).toBe(false)
    expect(harborCore.actionableSummary).toEqual({ Critical: 0, High: 1, Medium: 0, Low: 0 })
    expect(harborCore.acceptedSummary).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 2 })

    const portal = byName.get("narwhal-portal-74b459c658")!
    expect(portal.accepted).toBe(false)
    expect(portal.actionableSummary).toEqual({ Critical: 0, High: 1, Medium: 0, Low: 0 })
    expect(portal.acceptedSummary).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 2 })

    const harborNginx = byName.get("harbor-nginx-67d8f97945")!
    expect(harborNginx.accepted).toBe(false)
    expect(harborNginx.actionableSummary).toEqual({ Critical: 0, High: 2, Medium: 0, Low: 0 })
    expect(harborNginx.acceptedSummary).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 0 })

    const customApp = byName.get("custom-app-67d8f97945")!
    expect(customApp.accepted).toBe(false)
    expect(customApp.actionableSummary).toEqual({ Critical: 0, High: 1, Medium: 0, Low: 0 })

    const argocdServer = byName.get("argocd-server-5ff7697d4f")!
    expect(argocdServer.accepted).toBe(false)
    expect(argocdServer.actionableSummary).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 2 })
    expect(argocdServer.acceptedSummary).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 0 })

    // Detail keeps every check and annotates accepted ones with class + reason
    const portalDetail = await getConfigAuditDetail("namespace", "devtools", "ReplicaSet", "narwhal-portal-74b459c658")
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
      "/apis/apps/v1/replicasets": { items: [makeRs({ namespace: "devtools", name: "harbor-core-b569bd8dd", ownerDeployment: "harbor-core", replicas: 1 }),makeRs({ namespace: "devtools", name: "narwhal-portal-74b459c658", ownerDeployment: "narwhal-portal", replicas: 1 })] },
      "/apis/aquasecurity.github.io/v1alpha1/configauditreports": {
        items: [
          makeCar({
            namespace: "kube-system",
            kind: "DaemonSet",
            name: "cilium",
            checks: [
              { checkID: "KSV009", severity: "HIGH", success: false },
              { checkID: "KSV030", severity: "LOW", success: false },
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
    expect(rbacByName.get("kubernetes-dashboard-web")?.accepted).toBe(false)
    expect(rbacByName.get("chaos-mesh-chaos-controller-manager-target-namespace")?.accepted).toBe(false)
    expect(rbacByName.get("developer-workload-admin")?.accepted).toBe(false)
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
    // - actionable: harbor-core and narwhal-portal KSV014 (High: 2)
    // - low hygiene: harbor-core KSV011 (1) + harbor-core KSV020 (1) + narwhal-portal UID/GID (2) = 4.
    // - accepted: cilium KSV009 (High: 1); cilium KSV030 remains unaccepted LOW.
    expect(summary.totalConfigAuditFailures).toEqual({ Critical: 0, High: 2, Medium: 0, Low: 0 })
    expect(summary.lowSeverityConfigAuditFailures).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 4 })
    expect(summary.acceptedSystemConfigAuditFailures).toEqual({ Critical: 0, High: 1, Medium: 0, Low: 0 })

    // RBAC:
    // - actionable: platform-admin KSV042 (Medium: 1) + platform-admin-extra KSV041 (Critical: 1) + velero-ui-custom-role KSV113 (Medium: 1)
    expect(summary.totalRbacFailures).toEqual({ Critical: 2, High: 2, Medium: 5, Low: 0 })
    // - accepted: jobs (M:1) + notif (M:1) + k8s-dash (M:1) + platform-admin (C:1,H:2,M:2) + dev-workload (H:2,M:2) + chaos-mesh (C:1)
    expect(summary.acceptedRbacFailures).toEqual({ Critical: 1, High: 2, Medium: 4, Low: 0 })
  })

  it("fails closed when summary counts exceed checks[] (e.g. Critical 1 / High 1 with only one accepted High check)", async () => {
    installK8sMock({
      "/apis/apps/v1/replicasets": { items: [makeRs({ namespace: "devtools", name: "harbor-core-b569bd8dd", ownerDeployment: "harbor-core", replicas: 1 })] },
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
              checks: [{ checkID: "KSV011", title: "Root FS", severity: "HIGH", success: false }],
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
                { checkID: "KSV011", title: "Root FS", severity: "HIGH", success: false },
                { checkID: "KSV011", title: "Root FS", severity: "HIGH", success: false },
              ],
            },
          },
          // Standalone-looking ReplicaSet suffix abc12345 stays actionable
          makeCar({
            namespace: "devtools",
            kind: "ReplicaSet",
            name: "harbor-core-abc12345",
            checks: [{ checkID: "KSV011", severity: "HIGH", success: false }],
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
    expect(summary.totalConfigAuditFailures).toEqual({ Critical: 1, High: 3, Medium: 0, Low: 0 })
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
    expect(devtoolsApi.accepted).toBe(false)
    expect(devtoolsApi.actionableSummary).toEqual({ Critical: 0, High: 0, Medium: 1, Low: 0 })
    expect(devtoolsApi.acceptedSummary).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 0 })

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
    expect(summary.totalRbacFailures).toEqual({ Critical: 3, High: 0, Medium: 1, Low: 0 })
    expect(summary.acceptedRbacFailures).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 0 })
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
    expect(await getConfigAuditDetail("namespace", "storage", "ReplicaSet", "velero-6447588f5f")).toBeNull()
    const activeDetail = await getConfigAuditDetail("namespace", "storage", "ReplicaSet", "velero-7b8c9d4f5g")
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
            checks: [{ checkID: "KSV011", severity: "HIGH", success: false }],
          }),
          // 2. Non-Deployment-owned ReplicaSet at 0/0 -> kept
          makeCar({
            namespace: "dev",
            kind: "ReplicaSet",
            name: "custom-owned-rs",
            checks: [{ checkID: "KSV011", severity: "HIGH", success: false }],
          }),
          // 3. Deployment scaled to 0: current revision (rev 2) at 0/0 -> kept (and allowlist still works via ownerRef)
          makeCar({
            namespace: "devtools",
            kind: "ReplicaSet",
            name: "harbor-core-b569bd8dd",
            checks: [{ checkID: "KSV011", severity: "HIGH", success: false }],
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
            checks: [{ checkID: "KSV011", severity: "HIGH", success: false }],
          }),
          // 5. Old revision missing status.replicas (unknown) -> kept
          makeCar({
            namespace: "storage",
            kind: "ReplicaSet",
            name: "velero-unknown-status",
            checks: [{ checkID: "KSV011", severity: "HIGH", success: false }],
          }),
          // 6. Old revision missing revision annotation (unknown) -> kept
          makeCar({
            namespace: "storage",
            kind: "ReplicaSet",
            name: "velero-unknown-rev",
            checks: [{ checkID: "KSV011", severity: "HIGH", success: false }],
          }),
          // 7. Cross-namespace Deployment with same name must not mark this RS superseded -> kept
          makeCar({
            namespace: "other-ns",
            kind: "ReplicaSet",
            name: "velero-other-ns",
            checks: [{ checkID: "KSV011", severity: "HIGH", success: false }],
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
    expect(await getConfigAuditDetail("namespace", "devtools", "ReplicaSet", "harbor-core-old11111")).toBeNull()
    // Terminating / standalone / unknown remain visible in detail and actionable summary
    expect(await getConfigAuditDetail("namespace", "storage", "ReplicaSet", "velero-terminating")).not.toBeNull()

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
    expect(await getConfigAuditDetail("namespace", "dev", "ReplicaSet", "web-rs-a")).toBeNull()
    expect(mockCache.has("compliance:replicasets:list")).toBe(false)

    // Rollback happens before any separate RS TTL could expire; simulate report cache expiry
    phase = "rolled_back"
    mockCache.delete("compliance:v3:config-audit:list")

    // Next computation fetches RS list in the same request path -> web-rs-a is immediately back, web-rs-b is dropped
    const secondRows = await getConfigAuditList()
    expect(secondRows.map((r) => r.name)).toEqual(["web-rs-a"])
    const restoredDetail = await getConfigAuditDetail("namespace", "dev", "ReplicaSet", "web-rs-a")
    expect(restoredDetail?.name).toBe("web-rs-a")
    expect(await getConfigAuditDetail("namespace", "dev", "ReplicaSet", "web-rs-b")).toBeNull()
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
      // Unknown ownership fails closed despite a hash-like suffix
      expect(rows.find((r) => r.name === "harbor-core-99b8c7d6f")?.accepted).toBe(false)

      expect(warnSpy).toHaveBeenCalledTimes(1)
      expect(warnSpy.mock.calls[0]?.[0]).toContain("failed to list ReplicaSets")
      // Failed RS list must not be cached in compliance:replicasets:list
      expect(mockCache.has("compliance:replicasets:list")).toBe(false)

      const summary = await getComplianceSummary()
      expect(summary.scannedWorkloads).toBe(2)
      expect(summary.totalConfigAuditFailures).toEqual({ Critical: 0, High: 2, Medium: 0, Low: 0 })
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
              { checkID: "KSV011", severity: "HIGH", success: false },
              { checkID: "KSV020", severity: "LOW", success: false },
            ],
          }),
          // ReplicaSet owned by a non-Deployment resource (e.g. CustomSet) -> also NOT accepted
          makeCar({
            namespace: "devtools",
            kind: "ReplicaSet",
            name: "harbor-core-99b8c7d6f",
            checks: [{ checkID: "KSV011", severity: "HIGH", success: false }],
          }),
          // Real Deployment-owned ReplicaSet -> accepted as before
          makeCar({
            namespace: "devtools",
            kind: "ReplicaSet",
            name: "harbor-core-b569bd8dd",
            checks: [
              { checkID: "KSV011", severity: "HIGH", success: false },
              { checkID: "KSV011", severity: "LOW", success: false },
              { checkID: "KSV020", severity: "LOW", success: false },
            ],
          }),
          // Non-hash suffix even with Deployment owner -> NOT accepted (never broaden)
          makeCar({
            namespace: "devtools",
            kind: "ReplicaSet",
            name: "harbor-core-abc12345",
            checks: [{ checkID: "KSV011", severity: "HIGH", success: false }],
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

    const standaloneDetail = await getConfigAuditDetail("namespace", "devtools", "ReplicaSet", "harbor-core-bcdfg")
    expect(standaloneDetail?.accepted).toBe(false)
    expect(standaloneDetail?.checks.find((c) => c.id === "KSV011")?.accepted).toBe(false)

    const ownedDetail = await getConfigAuditDetail("namespace", "devtools", "ReplicaSet", "harbor-core-b569bd8dd")
    expect(ownedDetail?.accepted).toBe(true)
    expect(ownedDetail?.checks.find((c) => c.id === "KSV011")?.accepted).toBe(true)

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
  specControls?: Array<{ id: string; name: string; severity: string; checks?: Array<{ id: string }> }>
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
      "/apis/aquasecurity.github.io/v1alpha1/configauditreports": {
        items: [
          makeCar({
            namespace: "security-system",
            kind: "DaemonSet",
            name: "falco",
            checks: [
              { checkID: "KSV9999", severity: "HIGH", success: false },
              { checkID: "KSV121", severity: "MEDIUM", success: false },
            ],
          }),
          {
            metadata: {
              name: "widget-foo",
              namespace: "devtools",
              labels: { "trivy-operator.resource.kind": "Widget", "trivy-operator.resource.name": "foo" },
            },
            report: {
              summary: { criticalCount: 0, highCount: 1, mediumCount: 0, lowCount: 0 },
              checks: [{ checkID: "KSV018", title: "Unknown target kind", severity: "HIGH", success: false }],
            },
          },
          makeCar({
            namespace: "devtools",
            kind: "ReplicaSet",
            name: "gitea-67f4766475",
            checks: [{ checkID: "KSV020", severity: "HIGH", success: false }],
          }),
          makeCar({
            namespace: "dev",
            kind: "Pod",
            name: "unlisted-app",
            checks: [{ checkID: "KSV020", severity: "HIGH", success: false }],
          }),
          makeCar({
            namespace: "kube-system",
            kind: "DaemonSet",
            name: "cilium",
            checks: [{ checkID: "KSV014", severity: "MEDIUM", success: false }],
          }),
        ],
      },
      "/apis/aquasecurity.github.io/v1alpha1/clusterinfraassessmentreports": {
        items: [
          {
            metadata: {
              name: "node-narwhal-master-1",
              labels: { "trivy-operator.resource.kind": "Node", "trivy-operator.resource.name": "narwhal-master-1" },
            },
            report: {
              summary: { criticalCount: 0, highCount: 0, mediumCount: 0, lowCount: 2 },
              checks: [
                { checkID: "KCV0056", title: "CNI perms", severity: "LOW", success: false },
                { checkID: "KCV0059", title: "etcd ownership", severity: "LOW", success: false },
              ],
            },
          },
        ],
      },
      "/apis/aquasecurity.github.io/v1alpha1/clustercompliancereports": {
        items: [
          makeCcr({
            id: "test-fw",
            title: "Test Framework",
            passCount: 1,
            failCount: 5,
            specControls: [
              { id: "C0", name: "No failure recorded", severity: "LOW", checks: [{ id: "AVD-KSV-0001" }] },
              { id: "C1", name: "Unknown check ID", severity: "HIGH", checks: [{ id: "AVD-KSV-9999" }] },
              { id: "C2", name: "Unknown target", severity: "HIGH", checks: [{ id: "AVD-KSV-0018" }] },
              { id: "C3", name: "Mixed control", severity: "HIGH", checks: [{ id: "AVD-KSV-0020" }] },
              { id: "C4", name: "Actionable node checks", severity: "LOW", checks: [{ id: "AVD-KCV-0056" }, { id: "AVD-KCV-0059" }] },
              { id: "C5", name: "Mixed exact accepted and uncovered control", severity: "MEDIUM", checks: [{ id: "AVD-KSV-0014" }, { id: "AVD-KSV-0121" }] },
            ],
            results: [
              { id: "C0", name: "No failure recorded", severity: "LOW", checks: [{ checkID: "", success: true }] },
              { id: "C1", name: "Unknown check ID", severity: "HIGH", checks: [] },
              { id: "C2", name: "Unknown target", severity: "HIGH", checks: [] },
              { id: "C3", name: "Mixed control", severity: "HIGH", checks: [] },
              { id: "C4", name: "Actionable node checks", severity: "LOW", checks: [] },
              { id: "C5", name: "Mixed exact accepted and uncovered control", severity: "MEDIUM", checks: [] },
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
      actionableFailCount: 5,
      acceptedFailCount: 0,
      totalControls: 6,
    })
    expect(frameworks[0].passRate).toBeCloseTo(1 / 6)
    expect(frameworks[0].effectivePassRate).toBeCloseTo(0)

    const detail = await getComplianceFrameworkDetail("test-fw")
    expect(detail).not.toBeNull()
    const byId = new Map(detail!.controls.map((c) => [c.id, c]))

    expect(byId.get("C0")?.accepted).toBe(false)
    expect(byId.get("C1")).toMatchObject({ failCount: 1, actionableFailCount: 1, acceptedFailCount: 0, accepted: false })
    expect(byId.get("C2")).toMatchObject({ failCount: 1, actionableFailCount: 1, acceptedFailCount: 0, accepted: false })
    expect(byId.get("C3")).toMatchObject({ failCount: 2, actionableFailCount: 2, acceptedFailCount: 0, accepted: false })
    expect(byId.get("C4")).toMatchObject({ failCount: 2, actionableFailCount: 2, acceptedFailCount: 0, accepted: false })
    expect(byId.get("C5")).toMatchObject({
      failCount: 2,
      actionableFailCount: 1,
      acceptedFailCount: 1,
      accepted: false,
    })
  })

  it("drops superseded ReplicaSets while enforcing Deployment ownership on live-revision ReplicaSets", async () => {
    installK8sMock({
      "/apis/aquasecurity.github.io/v1alpha1/configauditreports": {
        items: [
          makeCar({
            namespace: "devtools",
            kind: "ReplicaSet",
            name: "unlisted-old11",
            checks: [{ checkID: "KSV014", severity: "HIGH", success: false }],
          }),
          makeCar({
            namespace: "devtools",
            kind: "ReplicaSet",
            name: "harbor-core-b569bd8dd",
            checks: [{ checkID: "KSV011", severity: "HIGH", success: false }],
          }),
          makeCar({
            namespace: "devtools",
            kind: "ReplicaSet",
            name: "harbor-core-bcdfg",
            checks: [{ checkID: "KSV020", severity: "HIGH", success: false }],
          }),
        ],
      },
      "/apis/aquasecurity.github.io/v1alpha1/clustercompliancereports": {
        items: [
          makeCcr({
            id: "rs-fw",
            passCount: 0,
            failCount: 3,
            specControls: [
              { id: "RS-SUPERSEDED", name: "Superseded RS only", severity: "HIGH", checks: [{ id: "AVD-KSV-0014" }] },
              { id: "RS-OWNED", name: "Live Deployment RS", severity: "HIGH", checks: [{ id: "AVD-KSV-0011" }] },
              { id: "RS-STANDALONE", name: "Standalone RS", severity: "HIGH", checks: [{ id: "AVD-KSV-0020" }] },
            ],
            results: [
              { id: "RS-SUPERSEDED", name: "Superseded RS only", severity: "HIGH", checks: [] },
              { id: "RS-OWNED", name: "Live Deployment RS", severity: "HIGH", checks: [] },
              { id: "RS-STANDALONE", name: "Standalone RS", severity: "HIGH", checks: [] },
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
      acceptedScope: "accept",
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
            specControls: [
              { id: "RBAC-OK", name: "Accepted RBAC", severity: "HIGH", checks: [{ id: "AVD-KSV-0041" }, { id: "AVD-KSV-0048" }] },
              { id: "RBAC-UNLISTED-CHECK", name: "Unlisted check", severity: "MEDIUM", checks: [{ id: "AVD-KSV-0042" }] },
              { id: "RBAC-HASHED-RESOLVED", name: "Hashed resolved", severity: "HIGH", checks: [{ id: "AVD-KSV-0046" }] },
              { id: "RBAC-HASHED-UNRESOLVED", name: "Hashed unresolved", severity: "HIGH", checks: [{ id: "AVD-KSV-0044" }] },
            ],
            results: [
              { id: "RBAC-OK", name: "Accepted RBAC", severity: "HIGH", checks: [] },
              { id: "RBAC-UNLISTED-CHECK", name: "Unlisted check", severity: "MEDIUM", checks: [] },
              { id: "RBAC-HASHED-RESOLVED", name: "Hashed resolved", severity: "HIGH", checks: [] },
              { id: "RBAC-HASHED-UNRESOLVED", name: "Hashed unresolved", severity: "HIGH", checks: [] },
            ],
          }),
        ],
      },
      "/apis/aquasecurity.github.io/v1alpha1/rbacassessmentreports": {
        items: [
          makeRbac({ namespace: "devtools", kind: "Role", roleName: "narwhal-portal-jobs", checks: [{ checkID: "KSV048", severity: "HIGH", success: false }] }),
        ],
      },
      "/apis/aquasecurity.github.io/v1alpha1/clusterrbacassessmentreports": {
        items: [
          makeRbac({ kind: "ClusterRole", roleName: "cluster-admin", checks: [{ checkID: "KSV041", severity: "HIGH", success: false }] }),
          makeRbac({ kind: "ClusterRole", roleName: "cert-manager-cainjector", checks: [{ checkID: "KSV041", severity: "HIGH", success: false }] }),
          makeRbac({
            kind: "ClusterRole",
            roleName: "platform-admin",
            checks: [
              { checkID: "KSV041", severity: "HIGH", success: false },
              { checkID: "KSV042", severity: "MEDIUM", success: false },
            ],
          }),
          {
            metadata: {
              name: "clusterrole-54cdc9b678",
              labels: { "trivy-operator.resource.kind": "ClusterRole" },
              ownerReferences: [{ kind: "ClusterRole", name: "system:kube-controller-manager" }],
            },
            report: {
              summary: { criticalCount: 1, highCount: 0, mediumCount: 0, lowCount: 0 },
              checks: [{ checkID: "KSV046", title: "Bootstrap role check", severity: "HIGH", success: false }],
            },
          },
          {
            metadata: {
              name: "clusterrole-deadbeef00",
              labels: { "trivy-operator.resource.kind": "ClusterRole" },
            },
            report: {
              summary: { criticalCount: 1, highCount: 0, mediumCount: 0, lowCount: 0 },
              checks: [{ checkID: "KSV044", title: "Unresolved hashed role", severity: "HIGH", success: false }],
            },
          },
        ],
      },
    })

    const detail = await getComplianceFrameworkDetail("rbac-fw")
    expect(detail).not.toBeNull()
    expect(detail!.actionableFailCount).toBe(3)
    expect(detail!.acceptedFailCount).toBe(1)

    const byId = new Map(detail!.controls.map((c) => [c.id, c]))
    expect(byId.get("RBAC-OK")?.accepted).toBe(false)
    expect(byId.get("RBAC-OK")?.acceptedScope).toBeUndefined()
    expect(byId.get("RBAC-UNLISTED-CHECK")?.accepted).toBe(false)
    expect(byId.get("RBAC-HASHED-RESOLVED")).toMatchObject({
      accepted: true,
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
      evaluatedPassCount: 0,
      actionableFailCount: 3,
      acceptedFailCount: 0,
      evaluatedPassRate: 0,
      effectivePassRate: 0,
    })

    const detail = await getComplianceFrameworkDetail("summary-only")
    expect(detail).not.toBeNull()
    expect(detail!.controls.every((c) => c.evaluationState === "not_evaluated" && c.notEvaluatedReason === "summary_only")).toBe(true)
  })

  it("fails closed to coverage_unknown when spec.checks metadata is trimmed (ccr-live-trimmed.json) without trusting detailReport verdicts", async () => {
    installK8sMock({
      "/apis/aquasecurity.github.io/v1alpha1/clustercompliancereports": ccrLiveTrimmed,
    })

    const frameworks = await getComplianceFrameworks()
    const byId = new Map(frameworks.map((f) => [f.id, f]))

    // Under D-COMPLIANCE-VERDICT-SOURCE, status.detailReport.results[].checks is never trusted as a
    // verdict source. When spec.compliance.controls[].checks is trimmed and no per-resource reports
    // are present, every control stays not_evaluated (coverage_unknown) and summary.failCount fails
    // closed as actionable.
    expect(byId.get("k8s-nsa-1.0")).toMatchObject({
      passCount: 18,
      failCount: 9,
      evaluatedPassCount: 0,
      notEvaluatedCount: 27,
      actionableFailCount: 9,
      acceptedFailCount: 0,
      coverageUnknown: true,
    })
    expect(byId.get("k8s-nsa-1.0")!.effectivePassRate).toBe(0)
    expect(byId.get("k8s-pss-baseline-0.1")).toMatchObject({
      passCount: 8,
      failCount: 3,
      evaluatedPassCount: 0,
      notEvaluatedCount: 11,
      actionableFailCount: 3,
      acceptedFailCount: 0,
      coverageUnknown: true,
    })
    expect(byId.get("k8s-pss-restricted-0.1")).toMatchObject({
      passCount: 14,
      failCount: 3,
      evaluatedPassCount: 0,
      notEvaluatedCount: 17,
      actionableFailCount: 3,
      acceptedFailCount: 0,
      coverageUnknown: true,
    })
    expect(byId.get("k8s-cis-1.23")).toMatchObject({
      passCount: 101,
      failCount: 15,
      evaluatedPassCount: 0,
      notEvaluatedCount: 116,
      actionableFailCount: 15,
      acceptedFailCount: 0,
      coverageUnknown: true,
    })
    expect(byId.get("k8s-kisa-1.0")).toMatchObject({
      passCount: 3,
      failCount: 4,
      evaluatedPassCount: 0,
      notEvaluatedCount: 7,
      actionableFailCount: 4,
      acceptedFailCount: 0,
      coverageUnknown: true,
    })
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
          if (res._path === "/apis/aquasecurity.github.io/v1alpha1/configauditreports") {
            return {
              items: [
                makeCar({
                  namespace: "devtools",
                  kind: "ReplicaSet",
                  name: "unlisted-old11",
                  checks: [{ checkID: "KSV014", severity: "HIGH", success: false }],
                }),
              ],
            }
          }
          if (res._path === "/apis/aquasecurity.github.io/v1alpha1/clustercompliancereports") {
            return {
              items: [
                makeCcr({
                  id: `rs-err-${status}`,
                  passCount: 0,
                  failCount: 1,
                  specControls: [{ id: "RS-1", name: "Unlisted RS", severity: "HIGH", checks: [{ id: "AVD-KSV-0014" }] }],
                  results: [{ id: "RS-1", name: "Unlisted RS", severity: "HIGH", checks: [] }],
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
        "/apis/aquasecurity.github.io/v1alpha1/configauditreports": {
          items: [
            makeCar({
              namespace: "devtools",
              kind: "ReplicaSet",
              name: "unlisted-old11",
              checks: [{ checkID: "KSV014", severity: "HIGH", success: false }],
            }),
          ],
        },
        "/apis/aquasecurity.github.io/v1alpha1/clustercompliancereports": {
          items: [
            makeCcr({
              id: "rs-empty",
              passCount: 0,
              failCount: 1,
              specControls: [{ id: "RS-MISSING", name: "Missing RS", severity: "HIGH", checks: [{ id: "AVD-KSV-0014" }] }],
              results: [{ id: "RS-MISSING", name: "Missing RS", severity: "HIGH", checks: [] }],
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

  it("ignores inflated CCR summary.failCount when spec.checks exist and handles controls with checks: []", async () => {
    installK8sMock({
      "/apis/aquasecurity.github.io/v1alpha1/configauditreports": {
        items: [
          makeCar({
            namespace: "security-system",
            kind: "DaemonSet",
            name: "falco",
            checks: [{ checkID: "KSV121", severity: "HIGH", success: false }],
          }),
          makeCar({
            namespace: "dev",
            kind: "Pod",
            name: "unlisted-app",
            checks: [{ checkID: "KSV014", severity: "HIGH", success: false }],
          }),
        ],
      },
      "/apis/aquasecurity.github.io/v1alpha1/clustercompliancereports": {
        items: [
          makeCcr({
            id: "unaccounted-and-empty",
            passCount: 5,
            failCount: 5,
            specControls: [
              { id: "C-EMPTY", name: "Empty checks control", severity: "LOW" },
              { id: "C-ACCEPTED", name: "Accepted control", severity: "HIGH", checks: [{ id: "AVD-KSV-0121" }] },
              { id: "C-ACTIONABLE", name: "Actionable control", severity: "HIGH", checks: [{ id: "AVD-KSV-0014" }] },
            ],
            results: [
              { id: "C-EMPTY", name: "Empty checks control", severity: "LOW", checks: [] },
              { id: "C-ACCEPTED", name: "Accepted control", severity: "HIGH", checks: [] },
              { id: "C-ACTIONABLE", name: "Actionable control", severity: "HIGH", checks: [] },
            ],
          }),
        ],
      },
    })

    const detail = await getComplianceFrameworkDetail("unaccounted-and-empty")
    expect(detail).not.toBeNull()
    expect(detail!.failCount).toBe(2)
    expect(detail!.actionableFailCount).toBe(1)
    expect(detail!.acceptedFailCount).toBe(1)
    expect(detail!.evaluatedPassCount).toBe(0)
    expect(detail!.notEvaluatedCount).toBe(1)
    expect(detail!.effectivePassRate).toBeCloseTo(1 / 3)

    const emptyCtrl = detail!.controls.find((c) => c.id === "C-EMPTY")
    expect(emptyCtrl).toMatchObject({
      passCount: 0,
      failCount: 0,
      actionableFailCount: 0,
      acceptedFailCount: 0,
      evaluationState: "not_evaluated",
      notEvaluatedReason: "empty_checks",
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
            specControls: [
              { id: "STS-RESOLVED", name: "Hashed StatefulSet resolved via ConfigAuditReport", severity: "LOW", checks: [{ id: "AVD-KSV-0011" }] },
              { id: "STS-UNRESOLVED", name: "Hashed StatefulSet without real resource name label", severity: "LOW", checks: [{ id: "AVD-KSV-0014" }] },
            ],
            results: [
              { id: "STS-RESOLVED", name: "Hashed StatefulSet resolved via ConfigAuditReport", severity: "LOW", checks: [] },
              { id: "STS-UNRESOLVED", name: "Hashed StatefulSet without real resource name label", severity: "LOW", checks: [] },
            ],
          }),
        ],
      },
      "/apis/aquasecurity.github.io/v1alpha1/configauditreports": {
        items: [
          {
            // monitoring/statefulset-78785dbd5c resolves real name from trivy-operator.resource.name label
            metadata: {
              namespace: "monitoring",
              name: "statefulset-78785dbd5c",
              labels: {
                "trivy-operator.resource.kind": "StatefulSet",
                "trivy-operator.resource.name": "prometheus-prometheus-stack-kube-prom-prometheus",
              },
            },
            report: {
              summary: { criticalCount: 0, highCount: 0, mediumCount: 0, lowCount: 1 },
              checks: [{ checkID: "KSV011", title: "CPU not limited", severity: "LOW", success: false }],
            },
          },
          {
            metadata: {
              namespace: "monitoring",
              name: "statefulset-deadbeef00",
              labels: { "trivy-operator.resource.kind": "StatefulSet" },
            },
            report: {
              summary: { criticalCount: 0, highCount: 0, mediumCount: 0, lowCount: 1 },
              checks: [{ checkID: "KSV014", title: "Read-only root fs", severity: "LOW", success: false }],
            },
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

describe("Infra audit accepted vs actionable split", () => {
  beforeEach(() => {
    mockCache.clear()
    mockFetchWithPolicy.mockReset()
    mockReadJsonWithPolicy.mockReset()
  })

  it("keeps failing KCV0056 and KCV0059 actionable and fails closed when checks[] is omitted", async () => {
    installK8sMock({
      "/apis/aquasecurity.github.io/v1alpha1/clusterinfraassessmentreports": {
        items: [
          {
            metadata: {
              name: "node-narwhal-master-1",
              creationTimestamp: "2026-11-20T00:00:00Z",
              labels: {
                "trivy-operator.resource.kind": "Node",
                "trivy-operator.resource.name": "narwhal-master-1",
              },
            },
            report: {
              summary: { criticalCount: 0, highCount: 1, mediumCount: 0, lowCount: 1 },
              checks: [
                { checkID: "KCV0056", title: "CNI perms", severity: "HIGH", success: false },
                { checkID: "KCV0059", title: "etcd ownership", severity: "LOW", success: false },
              ],
            },
          },
          {
            metadata: {
              name: "node-narwhal-master-2",
              creationTimestamp: "2026-11-20T00:00:00Z",
              labels: {
                "trivy-operator.resource.kind": "Node",
                "trivy-operator.resource.name": "narwhal-master-2",
              },
            },
            report: {
              summary: { criticalCount: 0, highCount: 1, mediumCount: 0, lowCount: 0 },
              checks: [
                { checkID: "KCV0056", title: "CNI perms", severity: "HIGH", success: false },
              ],
            },
          },
          {
            metadata: {
              name: "node-narwhal-master-3",
              creationTimestamp: "2026-11-20T00:00:00Z",
              labels: {
                "trivy-operator.resource.kind": "Node",
                "trivy-operator.resource.name": "narwhal-master-3",
              },
            },
            report: {
              summary: { criticalCount: 0, highCount: 1, mediumCount: 0, lowCount: 0 },
              checks: [],
            },
          },
        ],
      },
    })

    const rows = await getInfraAuditList()
    const m1 = rows.find((r) => r.node === "narwhal-master-1")!
    expect(m1.accepted).toBe(false)
    expect(m1.summary).toEqual({ Critical: 0, High: 1, Medium: 0, Low: 1 })
    expect(m1.actionableSummary).toEqual({ Critical: 0, High: 1, Medium: 0, Low: 1 })
    expect(m1.acceptedSummary).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 0 })

    const m2 = rows.find((r) => r.node === "narwhal-master-2")!
    expect(m2.accepted).toBe(false)
    expect(m2.acceptedScope).toBeUndefined()
    expect(m2.actionableSummary).toEqual({ Critical: 0, High: 1, Medium: 0, Low: 0 })
    expect(m2.acceptedSummary).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 0 })

    // Missing checks[] while summary.highCount === 1 -> fail closed (actionable)
    const m3 = rows.find((r) => r.node === "narwhal-master-3")!
    expect(m3.accepted).toBe(false)
    expect(m3.actionableSummary).toEqual({ Critical: 0, High: 1, Medium: 0, Low: 0 })
    expect(m3.acceptedSummary).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 0 })

    const detail = await getInfraAuditDetail("narwhal-master-1")
    expect(detail).not.toBeNull()
    expect(detail!.checks.find((c) => c.id === "KCV0056")).toMatchObject({
      accepted: false,
    })
    expect(detail!.checks.find((c) => c.id === "KCV0059")).toMatchObject({
      accepted: false,
    })

    const summary = await getComplianceSummary()
    expect(summary.totalInfraFailures).toEqual({ Critical: 0, High: 3, Medium: 0, Low: 1 })
    expect(summary.acceptedInfraFailures).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 0 })
  })
})

describe("honest 3-state compliance, unaccepted LOW, RS reconciliation, and data-unavailable", () => {
  beforeEach(() => {
    mockCache.clear()
    mockFetchWithPolicy.mockReset()
    mockReadJsonWithPolicy.mockReset()
  })

  it("never rounds pass rates with actionable failures or not-evaluated controls to 100%", () => {
    expect(formatPassRatePct(0.9994, { hasActionableFail: true })).toBe("99.9%")
    expect(formatPassRatePct(0.9999, { hasNotEvaluated: true })).toBe("99.9%")
    expect(formatPassRatePct(1, { hasNotEvaluated: true })).toBe("99.9%")
    expect(formatPassRatePct(1, { hasActionableFail: false, hasNotEvaluated: false })).toBe("100%")
    expect(formatPassRatePct(101 / 116, { hasActionableFail: true })).toBe("87.0%")
    expect(formatPassRatePct(10 / 10)).toBe("100%")
  })

  it("classifies excluded_namespace, unsupported_kind, missing_node_type, unlinked_check_id, manual, and passed controls", async () => {
    installK8sMock({
      "/api/v1/nodes": {
        items: [
          {
            metadata: {
              name: "master-1",
              labels: { "node-role.kubernetes.io/control-plane": "" },
            },
          },
        ],
      },
      "/apis/apps/v1/namespaces/security-system/deployments/trivy-operator": {
        spec: {
          template: {
            spec: {
              containers: [
                {
                  env: [
                    { name: "OPERATOR_EXCLUDE_NAMESPACES", value: "kube-system,kube-public" },
                    { name: "OPERATOR_CONFIG_AUDIT_SCANNER_ENABLED", value: "true" },
                    { name: "OPERATOR_RBAC_ASSESSMENT_SCANNER_ENABLED", value: "true" },
                    { name: "OPERATOR_INFRA_ASSESSMENT_SCANNER_ENABLED", value: "true" },
                  ],
                },
              ],
            },
          },
        },
      },
      "/api/v1/namespaces/security-system/configmaps/trivy-operator-trivy-config": {
        data: {
          "trivy.supportedConfigAuditKinds": "Workload,Service,Role,ClusterRole",
        },
      },
      "/apis/aquasecurity.github.io/v1alpha1/configauditreports": {
        items: [
          makeCar({
            namespace: "dev",
            kind: "Deployment",
            name: "sample-app",
            checks: [{ checkID: "KSV038", severity: "LOW", success: true }],
          }),
        ],
      },
      "/apis/aquasecurity.github.io/v1alpha1/clusterinfraassessmentreports": {
        items: [
          {
            metadata: {
              name: "node-master-1",
              creationTimestamp: "2026-11-22T00:00:00Z",
              labels: {
                "trivy-operator.resource.kind": "Node",
                "trivy-operator.resource.name": "master-1",
              },
            },
            report: {
              summary: { criticalCount: 0, highCount: 0, mediumCount: 0, lowCount: 0 },
              checks: [{ checkID: "KCV0048", title: "API server pod spec perms", severity: "HIGH", success: true }],
            },
          },
        ],
      },
      "/apis/aquasecurity.github.io/v1alpha1/clustercompliancereports": {
        items: [
          {
            metadata: { name: "synthetic-3state", creationTimestamp: "2026-11-22T00:00:00Z" },
            spec: {
              compliance: {
                id: "synthetic-3state",
                title: "Synthetic 3-State",
                controls: [
                  { id: "CP-EXCL", name: "Control plane excluded", severity: "HIGH", checks: [{ id: "AVD-KCV-0001" }] },
                  { id: "BIND-UNSUP", name: "Binding unsupported", severity: "HIGH", checks: [{ id: "AVD-KSV-0111" }] },
                  { id: "WORKER-ABS", name: "Worker absent", severity: "MEDIUM", checks: [{ id: "AVD-KCV-0069" }], commands: [{ id: "CMD-0022", nodeType: "worker" }] },
                  { id: "MASTER-OK", name: "Master ok", severity: "HIGH", checks: [{ id: "AVD-KCV-0048" }], commands: [{ id: "CMD-0001", nodeType: "master" }] },
                  { id: "LOWER-CASE", name: "PSS unlinked", severity: "MEDIUM", checks: [{ id: "avd-ksv-0024" }] },
                  { id: "MANUAL-OMIT", name: "Manual control (Manual)", severity: "LOW" },
                  { id: "WORKLOAD-OK", name: "Workload policy ok", severity: "MEDIUM", checks: [{ id: "AVD-KSV-0014" }] },
                ],
              },
            },
            status: {
              updateTimestamp: "2026-11-22T06:00:00Z",
              summary: { passCount: 7, failCount: 0 },
              detailReport: {
                results: [
                  { id: "CP-EXCL", name: "Control plane excluded", severity: "HIGH", checks: [{ checkID: "", success: true }] },
                  { id: "BIND-UNSUP", name: "Binding unsupported", severity: "HIGH", checks: [{ checkID: "", success: true }] },
                  { id: "WORKER-ABS", name: "Worker absent", severity: "MEDIUM", checks: [{ checkID: "", success: true }] },
                  { id: "MASTER-OK", name: "Master ok", severity: "HIGH", checks: [{ checkID: "", success: true }] },
                  { id: "LOWER-CASE", name: "PSS unlinked", severity: "MEDIUM", checks: [{ checkID: "", success: true }] },
                  { id: "MANUAL-OMIT", name: "Manual control (Manual)", severity: "LOW", checks: [{ checkID: "", success: true }] },
                  { id: "WORKLOAD-OK", name: "Workload policy ok", severity: "MEDIUM", checks: [{ checkID: "", success: true }] },
                ],
              },
            },
          },
        ],
      },
    })

    const detail = await getComplianceFrameworkDetail("synthetic-3state")
    expect(detail).not.toBeNull()
    const byId = new Map(detail!.controls.map((c) => [c.id, c]))

    expect(byId.get("CP-EXCL")).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "excluded_namespace" })
    expect(byId.get("BIND-UNSUP")).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "unsupported_kind" })
    expect(byId.get("WORKER-ABS")).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "missing_node_type" })
    expect(byId.get("LOWER-CASE")).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "unlinked_check_id" })
    expect(byId.get("MANUAL-OMIT")).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "manual" })
    // MASTER-OK has an explicit success:true record in ClusterInfraAssessmentReport -> tested-passed.
    expect(byId.get("MASTER-OK")).toMatchObject({ evaluationState: "passed", testedByReport: true, passCount: 1 })
    expect(byId.get("WORKLOAD-OK")).toMatchObject({ evaluationState: "passed", passCount: 0 })

    expect(detail!.rawPassCount).toBe(7)
    expect(detail!.evaluatedPassCount).toBe(2)
    expect(detail!.reportTestedPassCount).toBe(1)
    expect(detail!.testedPassCount).toBe(1)
    expect(detail!.noFailureRecordedPassCount).toBe(1)
    expect(detail!.notEvaluatedCount).toBe(5)
    expect(detail!.evaluatedPassRate).toBeCloseTo(2 / 7)
    expect(detail!.effectivePassRate).toBeCloseTo(2 / 7)
    expect(detail!.lastUpdated).toBe("2026-11-22T06:00:00Z")
  })

  it("tracks unaccepted LOW separately from accepted hygiene LOW and reconciles dropped superseded ReplicaSets", async () => {
    installK8sMock({
      "/apis/apps/v1/replicasets": {
        items: [
          makeRs({ namespace: "dev", name: "app-old", replicas: 0, statusReplicas: 0, terminatingReplicas: 0, revision: 1, ownerDeployment: "app" }),
          makeRs({ namespace: "dev", name: "app-live", replicas: 1, statusReplicas: 1, revision: 2, ownerDeployment: "app" }),
        ],
      },
      "/apis/aquasecurity.github.io/v1alpha1/configauditreports": {
        items: [
          makeCar({
            namespace: "dev",
            kind: "ReplicaSet",
            name: "app-old",
            checks: [
              { checkID: "KSV014", severity: "HIGH", success: false },
              { checkID: "KSV020", severity: "LOW", success: false },
            ],
          }),
          makeCar({
            namespace: "dev",
            kind: "ReplicaSet",
            name: "app-live",
            checks: [
              { checkID: "KSV014", severity: "HIGH", success: false },
              { checkID: "KSV999", severity: "LOW", success: false },
              { checkID: "KSV106", severity: "LOW", success: false },
            ],
          }),
        ],
      },
    })

    const summary = await getComplianceSummary()
    expect(summary.totalConfigAuditFailures).toEqual({ Critical: 0, High: 1, Medium: 0, Low: 0 })
    expect(summary.unacceptedLowConfigAuditFailures).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 2 })
    expect(summary.lowSeverityConfigAuditFailures).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 0 })
    expect(summary.droppedConfigAuditReports).toBe(1)
    expect(summary.droppedConfigAuditFailures).toEqual({ Critical: 0, High: 1, Medium: 0, Low: 1 })
  })

  it("throws ComplianceDataUnavailableError on K8s 403/500, never caches errors, and isolates failed sources in summary", async () => {
    for (const httpStatus of [403, 500]) {
      mockCache.clear()
      mockFetchWithPolicy.mockImplementation(async (url: string) => {
        const path = new URL(url).pathname
        if (path === "/apis/aquasecurity.github.io/v1alpha1/configauditreports") {
          return { ok: false, status: httpStatus, statusText: "Error", _path: path }
        }
        return { ok: true, status: 200, statusText: "OK", _path: path }
      })
      mockReadJsonWithPolicy.mockImplementation(async (res: { _path: string }) => {
        if (res._path === "/apis/aquasecurity.github.io/v1alpha1/rbacassessmentreports") {
          return {
            items: [
              makeRbac({
                namespace: "dev",
                kind: "Role",
                roleName: "custom-writer",
                checks: [{ checkID: "KSV041", severity: "CRITICAL", success: false }],
              }),
            ],
          }
        }
        return { items: [] }
      })

      await expect(getConfigAuditList()).rejects.toThrowError(ComplianceDataUnavailableError)
      await expect(getConfigAuditList()).rejects.toMatchObject({ source: "configAudit" })
      expect(mockCache.size).toBe(0)

      const summary = await getComplianceSummary()
      expect(summary.dataUnavailable).toBe(true)
      expect(summary.sources?.configAudit.status).toBe("unavailable")
      expect(summary.sources?.rbac.status).toBe("ok")
      expect(summary.totalRbacFailures.Critical).toBe(1)
    }
  })

  it("computes honest 3-state numbers on the trimmed live compliance snapshot fixture", async () => {
    const snap = complianceSnapshot as Record<string, unknown>
    installK8sMock({
      "/apis/aquasecurity.github.io/v1alpha1/clustercompliancereports": snap.clustercompliancereports,
      "/apis/aquasecurity.github.io/v1alpha1/configauditreports": snap.configauditreports,
      "/apis/aquasecurity.github.io/v1alpha1/rbacassessmentreports": snap.rbacassessmentreports,
      "/apis/aquasecurity.github.io/v1alpha1/clusterrbacassessmentreports": snap.clusterrbacassessmentreports,
      "/apis/aquasecurity.github.io/v1alpha1/clusterinfraassessmentreports": snap.clusterinfraassessmentreports,
      "/apis/apps/v1/replicasets": snap.replicasets,
      "/api/v1/nodes": snap.nodes,
      "/apis/apps/v1/namespaces/security-system/deployments/trivy-operator": snap.trivyOperatorDeployment,
      "/api/v1/namespaces/security-system/configmaps/trivy-operator-trivy-config": snap.trivyConfigMap,
    })

    const summary = await getComplianceSummary()
    expect(summary.dataUnavailable).toBe(false)
    expect(summary.partial).toBeUndefined()
    expect(summary.sources?.configAudit.status).toBe("ok")
    expect(summary.sources?.rbac.status).toBe("ok")
    expect(summary.sources?.infra.status).toBe("ok")
    expect(summary.sources?.frameworks.status).toBe("ok")
    expect(summary.sources?.frameworks.rebuildIntervalHours).toBe(6)
    expect(summary.scannedWorkloads).toBe(3)
    expect(summary.scannedRbacObjects).toBe(2)
    expect(summary.scannedNodes).toBe(1)
    expect(summary.totalConfigAuditFailures).toEqual({ Critical: 0, High: 2, Medium: 1, Low: 0 })
    expect(summary.unacceptedLowConfigAuditFailures).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 2 })
    expect(summary.lowSeverityConfigAuditFailures).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 1 })
    expect(summary.acceptedSystemConfigAuditFailures).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 0 })
    expect(summary.droppedConfigAuditReports).toBe(1)
    expect(summary.droppedConfigAuditFailures).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 2 })
    expect(summary.totalRbacFailures).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 0 })
    expect(summary.acceptedRbacFailures).toEqual({ Critical: 1, High: 0, Medium: 1, Low: 0 })
    expect(summary.totalInfraFailures).toEqual({ Critical: 0, High: 0, Medium: 0, Low: 0 })

    const frameworks = await getComplianceFrameworks()
    const byId = new Map(frameworks.map((f) => [f.id, f]))

    // Hand-derived expectations under D-COMPLIANCE-VERDICT-SOURCE + narrowed check-catalogue rule:
    // k8s-cis-1.23 (8 controls):
    //   1.1.1 (KCV0048, master Node) -> not_evaluated (infra_no_per_check_evidence)
    //   1.1.9 (KCV0056) -> node-narwhal-master-1 report has checks: [] (stale CCR detailReport ignored) -> not_evaluated (infra_no_per_check_evidence)
    //   1.2.1 (KCV0001, kube-system Pod) -> not_evaluated (excluded_namespace: kube-system excluded)
    //   3.1.1 (Manual) -> not_evaluated (manual)
    //   4.1.5 (KCV0073, master/worker Node) -> not_evaluated (infra_no_per_check_evidence)
    //   5.1.1 (KSV111, RoleBinding/ClusterRoleBinding) -> not_evaluated (unsupported_kind)
    //   5.2.2 (KSV017) -> failed (actionable: falco KSV017 removed by D-FALCO-LEAST-PRIV)
    //   5.7.3 (KSV020/KSV021) -> failed (actionable: argocd-dex-server, accepted: harbor-core; droppedCheckCount: 2)
    expect(byId.get("k8s-cis-1.23")).toMatchObject({
      totalControls: 8,
      passCount: 5,
      evaluatedPassCount: 0,
      notEvaluatedCount: 6,
      failCount: 2,
      actionableFailCount: 2,
      acceptedFailCount: 0,
      droppedFailCount: 0,
      droppedCheckCount: 2,
    })
    expect(byId.get("k8s-cis-1.23")!.evaluatedPassRate).toBeCloseTo(0)
    expect(byId.get("k8s-cis-1.23")!.effectivePassRate).toBeCloseTo(0)

    // k8s-pss-restricted-0.1 (3 controls):
    //   1 (AVD-KSV-0103, Workload) -> no failure recorded (in-scope Workload ConfigAuditReports exist)
    //   3 (AVD-KSV-0017) -> failed (actionable: falco KSV017 removed by D-FALCO-LEAST-PRIV)
    //   13 (avd-ksv-0001, lowercase) -> not_evaluated (unlinked_check_id)
    expect(byId.get("k8s-pss-restricted-0.1")).toMatchObject({
      totalControls: 3,
      passCount: 2,
      evaluatedPassCount: 1,
      notEvaluatedCount: 1,
      failCount: 1,
      actionableFailCount: 1,
      acceptedFailCount: 0,
    })

    const reasonCounts = (d: NonNullable<Awaited<ReturnType<typeof getComplianceFrameworkDetail>>>) =>
      d.controls.reduce<Record<string, number>>((acc, c) => {
        if (c.notEvaluatedReason) acc[c.notEvaluatedReason] = (acc[c.notEvaluatedReason] ?? 0) + 1
        return acc
      }, {})
    expect(reasonCounts((await getComplianceFrameworkDetail("k8s-cis-1.23"))!)).toEqual({
      excluded_namespace: 1,
      infra_no_per_check_evidence: 3,
      manual: 1,
      unsupported_kind: 1,
    })
    expect(reasonCounts((await getComplianceFrameworkDetail("k8s-pss-restricted-0.1"))!)).toEqual({
      unlinked_check_id: 1,
    })

    const totals = frameworks.reduce((acc, f) => ({
      controls: acc.controls + f.totalControls,
      raw: acc.raw + f.passCount,
      evaluated: acc.evaluated + (f.evaluatedPassCount ?? 0),
      notEvaluated: acc.notEvaluated + (f.notEvaluatedCount ?? 0),
      actionable: acc.actionable + (f.actionableFailCount ?? 0),
      accepted: acc.accepted + (f.acceptedFailCount ?? 0),
    }), { controls: 0, raw: 0, evaluated: 0, notEvaluated: 0, actionable: 0, accepted: 0 })
    expect(totals).toEqual({ controls: 11, raw: 7, evaluated: 1, notEvaluated: 7, actionable: 3, accepted: 0 })
    console.info("trimmed snapshot replay:", JSON.stringify(totals))

    // Pin the measured 2026-11-22 full-snapshot pooled aggregate expectations carried in the trimmed fixture.
    const agg = complianceSnapshot.measuredFullSnapshotAggregates
    expect(agg.pooledTotalControls).toBe(178)
    expect(formatPassRatePct(agg.pooledRawPass / agg.pooledTotalControls, { hasActionableFail: true, hasNotEvaluated: true })).toBe("81.4%")
    expect(formatPassRatePct(agg.pooledEvaluatedPass / agg.pooledTotalControls, { hasActionableFail: true, hasNotEvaluated: true })).toBe("0.0%")
    expect(formatPassRatePct(agg.pooledEffectivePass / agg.pooledTotalControls, { hasActionableFail: true, hasNotEvaluated: true })).toBe("17.9%")

    for (const fw of frameworks) {
      expect((fw.evaluatedPassCount ?? 0) + (fw.notEvaluatedCount ?? 0) + fw.failCount).toBe(fw.totalControls)
      expect(fw.evaluatedPassCount ?? 0).toBeLessThanOrEqual(fw.passCount)
    }
  })

  it("never classifies unknown check ID (AVD-KSV-9999) or uncatalogued optional IDs as passed while allowing clean in-scope catalogue checks", async () => {
    installK8sMock({
      "/api/v1/nodes": {
        items: [{ metadata: { name: "narwhal-master-1", labels: { "node-role.kubernetes.io/control-plane": "" } } }],
      },
      "/apis/apps/v1/namespaces/security-system/deployments/trivy-operator": {
        spec: {
          template: {
            spec: {
              containers: [{ env: [{ name: "OPERATOR_EXCLUDE_NAMESPACES", value: "kube-public" }] }],
            },
          },
        },
      },
      "/api/v1/namespaces/security-system/configmaps/trivy-operator-trivy-config": {
        data: { "trivy.supportedConfigAuditKinds": "Workload,Role,ClusterRole,RoleBinding,ClusterRoleBinding" },
      },
      "/apis/aquasecurity.github.io/v1alpha1/configauditreports": {
        items: [
          makeCar({
            namespace: "dev",
            kind: "Deployment",
            name: "sample-app",
            checks: [],
          }),
        ],
      },
      "/apis/aquasecurity.github.io/v1alpha1/clusterinfraassessmentreports": {
        items: [
          {
            metadata: {
              name: "node-narwhal-master-1",
              creationTimestamp: "2026-11-22T09:24:19Z",
              labels: { "trivy-operator.resource.kind": "Node", "trivy-operator.resource.name": "narwhal-master-1" },
            },
            report: { summary: { criticalCount: 0, highCount: 0, mediumCount: 0, lowCount: 0 }, checks: [] },
          },
        ],
      },
      "/apis/aquasecurity.github.io/v1alpha1/clustercompliancereports": {
        items: [
          {
            metadata: { name: "evidence-check", creationTimestamp: "2026-11-22T06:00:00Z" },
            spec: {
              compliance: {
                id: "evidence-check",
                title: "Evidence Check",
                controls: [
                  { id: "UNKNOWN-ID", name: "Unknown check", severity: "HIGH", checks: [{ id: "AVD-KSV-9999" }] },
                  { id: "CLEAN-INFRA", name: "Clean empty infra", severity: "HIGH", checks: [{ id: "AVD-KCV-0048" }], commands: [{ id: "CMD-0001" }] },
                  { id: "PARTIAL-MULTI", name: "Multi check with uncatalogued ID", severity: "MEDIUM", checks: [{ id: "AVD-KSV-0014" }, { id: "AVD-KSV-0029" }] },
                  { id: "WORKLOAD-CLEAN", name: "In-scope workload check with failed-only report", severity: "MEDIUM", checks: [{ id: "AVD-KSV-0014" }] },
                ],
              },
            },
            status: {
              updateTimestamp: "2026-11-22T06:00:00Z",
              summary: { passCount: 4, failCount: 0 },
              detailReport: {
                results: [
                  { id: "UNKNOWN-ID", name: "Unknown check", severity: "HIGH", checks: [{ checkID: "", success: true }] },
                  { id: "CLEAN-INFRA", name: "Clean empty infra", severity: "HIGH", checks: [{ checkID: "", success: true }] },
                  { id: "PARTIAL-MULTI", name: "Multi check with optional uncatalogued ID", severity: "MEDIUM", checks: [{ checkID: "", success: true }] },
                  { id: "WORKLOAD-CLEAN", name: "In-scope workload check with failed-only report", severity: "MEDIUM", checks: [{ checkID: "", success: true }] },
                ],
              },
            },
          },
        ],
      },
    })

    const detail = await getComplianceFrameworkDetail("evidence-check")
    expect(detail).not.toBeNull()
    const byId = new Map(detail!.controls.map((c) => [c.id, c]))
    expect(byId.get("UNKNOWN-ID")).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "not_in_catalogue" })
    expect(byId.get("CLEAN-INFRA")).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "infra_no_per_check_evidence" })
    expect(byId.get("PARTIAL-MULTI")).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "not_in_catalogue" })
    expect(byId.get("WORKLOAD-CLEAN")).toMatchObject({ evaluationState: "passed" })
    expect(detail!.evaluatedPassCount).toBe(1)
    expect(detail!.notEvaluatedCount).toBe(3)
  })

  it("fails closed to coverage_unknown and surfaces partial without caching when auxiliary Deployment/ConfigMap read returns 403", async () => {
    for (const forbiddenPath of [
      "/apis/apps/v1/namespaces/security-system/deployments/trivy-operator",
      "/api/v1/namespaces/security-system/configmaps/trivy-operator-trivy-config",
    ]) {
      mockCache.clear()
      mockFetchWithPolicy.mockImplementation(async (url: string) => {
        const path = new URL(url).pathname
        if (path === forbiddenPath) {
          return { ok: false, status: 403, statusText: "Forbidden", _path: path }
        }
        return { ok: true, status: 200, statusText: "OK", _path: path }
      })
      mockReadJsonWithPolicy.mockImplementation(async (res: { _path: string }) => {
        if (res._path === "/apis/aquasecurity.github.io/v1alpha1/configauditreports") {
          return {
            items: [
              makeCar({
                namespace: "dev",
                kind: "Deployment",
                name: "sample-app",
                checks: [{ checkID: "KSV038", severity: "LOW", success: true }],
              }),
            ],
          }
        }
        if (res._path === "/api/v1/nodes") {
          return { items: [{ metadata: { name: "narwhal-master-1", labels: { "node-role.kubernetes.io/control-plane": "" } } }] }
        }
        if (res._path === "/apis/apps/v1/namespaces/security-system/deployments/trivy-operator") {
          return { spec: { template: { spec: { containers: [{ env: [] }] } } } }
        }
        if (res._path === "/api/v1/namespaces/security-system/configmaps/trivy-operator-trivy-config") {
          return { data: { "trivy.supportedConfigAuditKinds": "Workload,Role,ClusterRole,RoleBinding,ClusterRoleBinding" } }
        }
        if (res._path === "/apis/aquasecurity.github.io/v1alpha1/clustercompliancereports") {
          return {
            items: [
              {
                metadata: { name: "aux-403", creationTimestamp: "2026-11-22T06:00:00Z" },
                spec: {
                  compliance: {
                    id: "aux-403",
                    title: "Aux 403",
                    controls: [
                      { id: "C-WORKLOAD", name: "Workload check", severity: "MEDIUM", checks: [{ id: "AVD-KSV-0038" }] },
                    ],
                  },
                },
                status: {
                  updateTimestamp: "2026-11-22T06:00:00Z",
                  summary: { passCount: 1, failCount: 0 },
                  detailReport: {
                    results: [
                      { id: "C-WORKLOAD", name: "Workload check", severity: "MEDIUM", checks: [{ checkID: "", success: true }] },
                    ],
                  },
                },
              },
            ],
          }
        }
        return { items: [] }
      })

      const detail = await getComplianceFrameworkDetail("aux-403")
      expect(detail).not.toBeNull()
      expect(detail!.coverageUnknown).toBe(true)
      expect(detail!.evaluatedPassCount).toBe(0)
      expect(detail!.notEvaluatedCount).toBe(1)
      expect(detail!.controls[0]).toMatchObject({
        evaluationState: "not_evaluated",
        notEvaluatedReason: "coverage_unknown",
      })
      expect(mockCache.size).toBe(0)

      const summary = await getComplianceSummary()
      expect(summary.dataUnavailable).toBe(false)
      expect(summary.partial).toBe(true)
      expect(summary.sources?.frameworks.partial).toBe(true)
      expect(summary.sources?.frameworks.partialSources).toContain(
        forbiddenPath.includes("deployments") ? "deploy/trivy-operator" : "cm/trivy-operator-trivy-config",
      )
      expect(mockCache.size).toBe(0)
    }
  })

  it("evaluates 4.1.5 (KCV0073) on control-plane nodes from positive infra report evidence without worker-node over-correction", async () => {
    installK8sMock({
      "/api/v1/nodes": {
        items: [{ metadata: { name: "narwhal-master-1", labels: { "node-role.kubernetes.io/control-plane": "" } } }],
      },
      "/apis/apps/v1/namespaces/security-system/deployments/trivy-operator": {
        spec: { template: { spec: { containers: [{ env: [] }] } } },
      },
      "/api/v1/namespaces/security-system/configmaps/trivy-operator-trivy-config": {
        data: { "trivy.supportedConfigAuditKinds": "Workload" },
      },
      "/apis/aquasecurity.github.io/v1alpha1/clusterinfraassessmentreports": {
        items: [
          {
            metadata: {
              name: "node-narwhal-master-1",
              creationTimestamp: "2026-11-22T09:24:19Z",
              labels: { "trivy-operator.resource.kind": "Node", "trivy-operator.resource.name": "narwhal-master-1" },
            },
            report: {
              summary: { criticalCount: 0, highCount: 0, mediumCount: 0, lowCount: 0 },
              checks: [
                { checkID: "KCV0073", title: "kubelet.conf permissions", severity: "HIGH", success: true },
              ],
            },
          },
        ],
      },
      "/apis/aquasecurity.github.io/v1alpha1/clustercompliancereports": {
        items: [
          {
            metadata: { name: "cis-node-applicability", creationTimestamp: "2026-11-22T06:00:00Z" },
            spec: {
              compliance: {
                id: "cis-node-applicability",
                title: "CIS Node Applicability",
                controls: [
                  { id: "4.1.5", name: "kubelet.conf perms", severity: "HIGH", checks: [{ id: "AVD-KCV-0073" }], commands: [{ id: "CMD-0026" }] },
                  { id: "4.1.6", name: "kubelet.conf ownership", severity: "HIGH", checks: [{ id: "AVD-KCV-0074" }], commands: [{ id: "CMD-0027" }] },
                  { id: "4.W", name: "Explicit worker-only command", severity: "MEDIUM", checks: [{ id: "AVD-KCV-0071" }], commands: [{ id: "CMD-0024", nodeType: "worker" }] },
                ],
              },
            },
            status: {
              updateTimestamp: "2026-11-22T06:00:00Z",
              summary: { passCount: 3, failCount: 0 },
              detailReport: {
                results: [
                  { id: "4.1.5", name: "kubelet.conf perms", severity: "HIGH", checks: [{ checkID: "", success: true }] },
                  { id: "4.1.6", name: "kubelet.conf ownership", severity: "HIGH", checks: [{ checkID: "", success: true }] },
                  { id: "4.W", name: "Explicit worker-only command", severity: "MEDIUM", checks: [{ checkID: "", success: true }] },
                ],
              },
            },
          },
        ],
      },
    })

    const detail = await getComplianceFrameworkDetail("cis-node-applicability")
    expect(detail).not.toBeNull()
    const byId = new Map(detail!.controls.map((c) => [c.id, c]))
    // 4.1.5 (KCV0073) has an explicit success:true record in ClusterInfraAssessmentReport -> tested-passed.
    // 4.1.6 (KCV0074) has zero records -> stays not_evaluated (infra_no_per_check_evidence).
    expect(byId.get("4.1.5")).toMatchObject({ evaluationState: "passed", testedByReport: true, passCount: 1 })
    expect(byId.get("4.1.6")).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "infra_no_per_check_evidence" })
    // Explicit spec/collector nodeType: "worker" on master-only cluster -> missing_node_type by data, not by CIS section number
    expect(byId.get("4.W")).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "missing_node_type" })
  })

  it("materialises spec.compliance.controls absent from status.detailReport.results as not_evaluated (absent_from_report)", async () => {
    installK8sMock({
      "/api/v1/nodes": {
        items: [{ metadata: { name: "narwhal-master-1", labels: { "node-role.kubernetes.io/control-plane": "" } } }],
      },
      "/apis/apps/v1/namespaces/security-system/deployments/trivy-operator": {
        spec: { template: { spec: { containers: [{ env: [] }] } } },
      },
      "/api/v1/namespaces/security-system/configmaps/trivy-operator-trivy-config": {
        data: { "trivy.supportedConfigAuditKinds": "Workload" },
      },
      "/apis/aquasecurity.github.io/v1alpha1/configauditreports": {
        items: [
          makeCar({
            namespace: "dev",
            kind: "Deployment",
            name: "sample-app",
            checks: [],
          }),
        ],
      },
      "/apis/aquasecurity.github.io/v1alpha1/clustercompliancereports": {
        items: [
          {
            metadata: { name: "missing-from-detail", creationTimestamp: "2026-11-22T06:00:00Z" },
            spec: {
              compliance: {
                id: "missing-from-detail",
                title: "Missing From Detail",
                controls: [
                  { id: "C-1", name: "Present and in-scope", severity: "MEDIUM", checks: [{ id: "AVD-KSV-0103" }] },
                  { id: "C-2", name: "Absent from detailReport", severity: "HIGH", checks: [{ id: "AVD-KSV-0014" }] },
                  { id: "C-3", name: "Also absent", severity: "LOW", checks: [{ id: "AVD-KSV-0020" }] },
                ],
              },
            },
            status: {
              updateTimestamp: "2026-11-22T06:00:00Z",
              summary: { passCount: 1, failCount: 0 },
              detailReport: {
                results: [
                  { id: "C-1", name: "Present and in-scope", severity: "MEDIUM", checks: [{ checkID: "", success: true }] },
                ],
              },
            },
          },
        ],
      },
    })

    const detail = await getComplianceFrameworkDetail("missing-from-detail")
    expect(detail).not.toBeNull()
    expect(detail!.totalControls).toBe(3)
    expect(detail!.evaluatedPassCount).toBe(1)
    expect(detail!.notEvaluatedCount).toBe(2)
    expect(detail!.controls.map((c) => c.id)).toEqual(["C-1", "C-2", "C-3"])
    expect(detail!.controls[1]).toMatchObject({
      id: "C-2",
      evaluationState: "not_evaluated",
      notEvaluatedReason: "absent_from_report",
    })
    expect(detail!.controls[2]).toMatchObject({
      id: "C-3",
      evaluationState: "not_evaluated",
      notEvaluatedReason: "absent_from_report",
    })
  })

  it("evaluates every C2 check-catalogue branch on failed-only reports with exact hand-derived counts", async () => {
    // 1. Excluded-namespace-only reports for general Workload check -> excluded_namespace
    installK8sMock({
      "/api/v1/nodes": {
        items: [{ metadata: { name: "narwhal-master-1", labels: { "node-role.kubernetes.io/control-plane": "" } } }],
      },
      "/apis/apps/v1/namespaces/security-system/deployments/trivy-operator": {
        spec: {
          template: {
            spec: {
              containers: [{ env: [{ name: "OPERATOR_EXCLUDE_NAMESPACES", value: "kube-system,default" }] }],
            },
          },
        },
      },
      "/api/v1/namespaces/security-system/configmaps/trivy-operator-trivy-config": {
        data: { "trivy.supportedConfigAuditKinds": "Workload,Role,ClusterRole,NetworkPolicy" },
      },
      // Only an excluded-namespace workload report exists; no RBAC, NetworkPolicy, or Infra reports exist.
      "/apis/aquasecurity.github.io/v1alpha1/configauditreports": {
        items: [makeCar({ namespace: "kube-system", kind: "Pod", name: "excluded-pod", checks: [] })],
      },
      "/apis/aquasecurity.github.io/v1alpha1/clustercompliancereports": {
        items: [
          {
            metadata: { name: "c2-branches", creationTimestamp: "2026-11-22T06:00:00Z" },
            spec: {
              compliance: {
                id: "c2-branches",
                title: "C2 Branches",
                controls: [
                  { id: "B-UNKNOWN", name: "Unknown ID", severity: "HIGH", checks: [{ id: "AVD-KSV-9999" }] },
                  { id: "B-REMOVED", name: "Removed ID (NSA 1.6 KSV029)", severity: "MEDIUM", checks: [{ id: "AVD-KSV-0029" }] },
                  { id: "B-OPTIONAL", name: "Uncatalogued ID (KSV9998)", severity: "LOW", checks: [{ id: "AVD-KSV-9998" }] },
                  { id: "B-UNSUP-KIND", name: "Kind not audited (RoleBinding)", severity: "HIGH", checks: [{ id: "AVD-KSV-0111" }] },
                  { id: "B-EXCL-ONLY", name: "Excluded namespace reports only", severity: "HIGH", checks: [{ id: "AVD-KSV-0014" }] },
                  { id: "B-EXCL-DEFAULT", name: "default namespace excluded", severity: "LOW", checks: [{ id: "AVD-KSV-0110" }] },
                  { id: "B-NO-RBAC", name: "RBAC family without any report", severity: "CRITICAL", checks: [{ id: "AVD-KSV-0041" }] },
                  { id: "B-NO-NETPOL", name: "ConfigAudit NetworkPolicy without report", severity: "MEDIUM", checks: [{ id: "AVD-KSV-0038" }] },
                  { id: "B-NO-INFRA", name: "Infra family without node report", severity: "HIGH", checks: [{ id: "AVD-KCV-0048" }] },
                ],
              },
            },
            status: {
              updateTimestamp: "2026-11-22T06:00:00Z",
              summary: { passCount: 9, failCount: 0 },
              detailReport: {
                results: [
                  { id: "B-UNKNOWN", name: "Unknown ID", severity: "HIGH", checks: [{ checkID: "", success: true }] },
                  { id: "B-REMOVED", name: "Removed ID (NSA 1.6 KSV029)", severity: "MEDIUM", checks: [{ checkID: "", success: true }] },
                  { id: "B-OPTIONAL", name: "Uncatalogued ID (KSV9998)", severity: "LOW", checks: [{ checkID: "", success: true }] },
                  { id: "B-UNSUP-KIND", name: "Kind not audited (RoleBinding)", severity: "HIGH", checks: [{ checkID: "", success: true }] },
                  { id: "B-EXCL-ONLY", name: "Excluded namespace reports only", severity: "HIGH", checks: [{ checkID: "", success: true }] },
                  { id: "B-EXCL-DEFAULT", name: "default namespace excluded", severity: "LOW", checks: [{ checkID: "", success: true }] },
                  { id: "B-NO-RBAC", name: "RBAC family without any report", severity: "CRITICAL", checks: [{ checkID: "", success: true }] },
                  { id: "B-NO-NETPOL", name: "ConfigAudit NetworkPolicy without report", severity: "MEDIUM", checks: [{ checkID: "", success: true }] },
                  { id: "B-NO-INFRA", name: "Infra family without node report", severity: "HIGH", checks: [{ checkID: "", success: true }] },
                ],
              },
            },
          },
        ],
      },
    })

    const branches = await getComplianceFrameworkDetail("c2-branches")
    expect(branches).not.toBeNull()
    const bMap = new Map(branches!.controls.map((c) => [c.id, c]))
    expect(bMap.get("B-UNKNOWN")).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "not_in_catalogue" })
    expect(bMap.get("B-REMOVED")).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "not_in_catalogue" })
    expect(bMap.get("B-OPTIONAL")).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "not_in_catalogue" })
    expect(bMap.get("B-UNSUP-KIND")).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "unsupported_kind" })
    expect(bMap.get("B-EXCL-ONLY")).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "excluded_namespace" })
    expect(bMap.get("B-EXCL-DEFAULT")).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "excluded_namespace" })
    expect(bMap.get("B-NO-RBAC")).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "no_report_for_family" })
    expect(bMap.get("B-NO-NETPOL")).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "no_report_for_family" })
    expect(bMap.get("B-NO-INFRA")).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "no_report_for_family" })
    expect(branches!.evaluatedPassCount).toBe(0)
    expect(branches!.notEvaluatedCount).toBe(9)

    // 2. Live-like failed-only fixture (ZERO success:true records anywhere):
    // Hand-derived expectations across 11 controls (H1: infra checks never pass from report existence):
    //   L-1 (KCV0048, master Node) -> not_evaluated (infra_no_per_check_evidence)
    //   L-2 (KCV0073, master/worker Node) -> not_evaluated (infra_no_per_check_evidence)
    //   L-3 (KCV0002, kube-system Pod infra) -> not_evaluated (infra_no_per_check_evidence)
    //   L-4 (KCV0001, kube-system Pod infra) -> failed (accepted: 1 [system namespace]; underlying kube-apiserver InfraAssessmentReport failed KCV0001)
    //   L-5 (KSV103, Workload) -> passed (no failure recorded; dev Deployment ConfigAuditReport exists)
    //   L-6 (KSV038, NetworkPolicy) -> passed (no failure recorded; dev NetworkPolicy ConfigAuditReport exists)
    //   L-7 (KSV045, RBAC Role/ClusterRole) -> passed (no failure recorded; ClusterRbacAssessmentReport exists)
    //   L-8 (KSV111, RoleBinding) -> passed (no failure recorded; RoleBinding in supportedConfigAuditKinds & report exists)
    //   L-9 (KSV029, not in catalogue) -> not_evaluated (not_in_catalogue)
    //   L-10 (KCV0069 with explicit worker nodeType on master-only cluster) -> not_evaluated (missing_node_type)
    //   L-11 (KSV014, Workload) -> failed (actionable: 1; underlying dev Deployment ConfigAuditReport failed KSV014)
    // Totals: 11 controls = 4 no-failure-recorded + 1 actionable fail + 1 accepted fail + 5 not_evaluated.
    mockCache.clear()
    installK8sMock({
      "/api/v1/nodes": {
        items: [{ metadata: { name: "narwhal-master-1", labels: { "node-role.kubernetes.io/control-plane": "" } } }],
      },
      "/apis/apps/v1/namespaces/security-system/deployments/trivy-operator": {
        spec: {
          template: {
            spec: {
              containers: [{ env: [{ name: "OPERATOR_EXCLUDE_NAMESPACES", value: "kube-public,kube-node-lease,trivy-system" }] }],
            },
          },
        },
      },
      "/api/v1/namespaces/security-system/configmaps/trivy-operator-trivy-config": {
        data: { "trivy.supportedConfigAuditKinds": "Workload,Service,Role,ClusterRole,RoleBinding,ClusterRoleBinding,NetworkPolicy,Ingress,LimitRange,ResourceQuota" },
      },
      "/apis/aquasecurity.github.io/v1alpha1/configauditreports": {
        items: [
          makeCar({ namespace: "dev", kind: "Deployment", name: "app", checks: [{ checkID: "KSV014", severity: "HIGH", success: false }] }),
          makeCar({ namespace: "dev", kind: "NetworkPolicy", name: "default-deny", checks: [] }),
          makeCar({ namespace: "dev", kind: "RoleBinding", name: "dev-binding", checks: [] }),
        ],
      },
      "/apis/aquasecurity.github.io/v1alpha1/clusterrbacassessmentreports": {
        items: [
          makeRbac({ namespace: "", kind: "ClusterRole", roleName: "custom-reader", checks: [] }),
        ],
      },
      "/apis/aquasecurity.github.io/v1alpha1/infraassessmentreports": {
        items: [
          {
            metadata: {
              namespace: "kube-system",
              name: "pod-kube-apiserver-narwhal-master-1",
              creationTimestamp: "2026-11-22T12:00:00Z",
              labels: { "trivy-operator.resource.kind": "Pod", "trivy-operator.resource.name": "kube-apiserver-narwhal-master-1" },
            },
            report: {
              summary: { criticalCount: 0, highCount: 0, mediumCount: 1, lowCount: 0 },
              checks: [{ checkID: "KCV0001", title: "anonymous-auth", severity: "MEDIUM", success: false }],
            },
          },
        ],
      },
      "/apis/aquasecurity.github.io/v1alpha1/clusterinfraassessmentreports": {
        items: [
          {
            metadata: {
              name: "node-narwhal-master-1",
              creationTimestamp: "2026-11-22T12:00:00Z",
              labels: { "trivy-operator.resource.kind": "Node", "trivy-operator.resource.name": "narwhal-master-1" },
            },
            report: { summary: { criticalCount: 0, highCount: 0, mediumCount: 0, lowCount: 0 }, checks: [] },
          },
        ],
      },
      "/apis/aquasecurity.github.io/v1alpha1/clustercompliancereports": {
        items: [
          {
            metadata: { name: "live-failed-only", creationTimestamp: "2026-11-22T12:00:00Z" },
            spec: {
              compliance: {
                id: "live-failed-only",
                title: "Live Failed-Only Picture",
                controls: [
                  { id: "L-1", name: "Master node file", severity: "HIGH", checks: [{ id: "AVD-KCV-0048" }] },
                  { id: "L-2", name: "Kubelet file", severity: "HIGH", checks: [{ id: "AVD-KCV-0073" }] },
                  { id: "L-3", name: "APIServer flag clean", severity: "LOW", checks: [{ id: "AVD-KCV-0002" }] },
                  { id: "L-4", name: "APIServer flag failing (system)", severity: "MEDIUM", checks: [{ id: "AVD-KCV-0001" }] },
                  { id: "L-5", name: "Workload clean check", severity: "MEDIUM", checks: [{ id: "AVD-KSV-0103" }] },
                  { id: "L-6", name: "NetworkPolicy clean check", severity: "MEDIUM", checks: [{ id: "AVD-KSV-0038" }] },
                  { id: "L-7", name: "RBAC clean check", severity: "CRITICAL", checks: [{ id: "AVD-KSV-0045" }] },
                  { id: "L-8", name: "RoleBinding clean check", severity: "MEDIUM", checks: [{ id: "AVD-KSV-0111" }] },
                  { id: "L-9", name: "Removed check ID", severity: "MEDIUM", checks: [{ id: "AVD-KSV-0029" }] },
                  { id: "L-10", name: "Worker-only node check", severity: "HIGH", checks: [{ id: "AVD-KCV-0069" }], commands: [{ id: "CMD-0022", nodeType: "worker" }] },
                  { id: "L-11", name: "Workload failing check (actionable)", severity: "HIGH", checks: [{ id: "AVD-KSV-0014" }] },
                ],
              },
            },
            status: {
              updateTimestamp: "2026-11-22T12:00:00Z",
              summary: { passCount: 11, failCount: 0 },
              detailReport: {
                results: [
                  { id: "L-1", name: "Master node file", severity: "HIGH", checks: [{ checkID: "", success: true }] },
                  { id: "L-2", name: "Kubelet file", severity: "HIGH", checks: [{ checkID: "", success: true }] },
                  { id: "L-3", name: "APIServer flag clean", severity: "LOW", checks: [{ checkID: "", success: true }] },
                  { id: "L-4", name: "APIServer flag failing (system)", severity: "MEDIUM", checks: [{ checkID: "", success: true }] },
                  { id: "L-5", name: "Workload clean check", severity: "MEDIUM", checks: [{ checkID: "", success: true }] },
                  { id: "L-6", name: "NetworkPolicy clean check", severity: "MEDIUM", checks: [{ checkID: "", success: true }] },
                  { id: "L-7", name: "RBAC clean check", severity: "CRITICAL", checks: [{ checkID: "", success: true }] },
                  { id: "L-8", name: "RoleBinding clean check", severity: "MEDIUM", checks: [{ checkID: "", success: true }] },
                  { id: "L-9", name: "Removed check ID", severity: "MEDIUM", checks: [{ checkID: "", success: true }] },
                  { id: "L-10", name: "Worker-only node check", severity: "HIGH", checks: [{ checkID: "", success: true }] },
                  { id: "L-11", name: "Workload failing check (actionable)", severity: "HIGH", checks: [{ checkID: "", success: true }] },
                ],
              },
            },
          },
        ],
      },
    })

    const liveLike = await getComplianceFrameworkDetail("live-failed-only")
    expect(liveLike).not.toBeNull()
    expect(liveLike!.totalControls).toBe(11)
    expect(liveLike!.evaluatedPassCount).toBe(4)
    expect(liveLike!.actionableFailCount).toBe(1)
    expect(liveLike!.acceptedFailCount).toBe(1)
    expect(liveLike!.notEvaluatedCount).toBe(5)
    expect(liveLike!.evaluatedPassRate).toBeCloseTo(4 / 11)
    expect(liveLike!.effectivePassRate).toBeCloseTo(5 / 11)
    const lMap = new Map(liveLike!.controls.map((c) => [c.id, c]))
    expect(lMap.get("L-4")).toMatchObject({ evaluationState: "failed", accepted: true, actionableFailCount: 0, acceptedFailCount: 1 })
    expect(lMap.get("L-11")).toMatchObject({ evaluationState: "failed", actionableFailCount: 1, accepted: false })
    expect(lMap.get("L-9")).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "not_in_catalogue" })
    expect(lMap.get("L-10")).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "missing_node_type" })
    for (const id of ["L-1", "L-2", "L-3"]) {
      expect(lMap.get(id)).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "infra_no_per_check_evidence" })
    }
    for (const id of ["L-5", "L-6", "L-7", "L-8"]) {
      expect(lMap.get(id)).toMatchObject({ evaluationState: "passed" })
    }
  })
})
