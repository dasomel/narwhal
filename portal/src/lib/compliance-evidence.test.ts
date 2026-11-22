import { beforeEach, expect, it, vi } from "vitest"
import snapshot from "./__fixtures__/compliance-snapshot.json"

vi.mock("server-only", () => ({}))
vi.mock("./config", () => ({ getK8sApiServer: () => "http://k8s.mock" }))
vi.mock("./k8s-token", () => ({ getK8sBearerToken: () => "mock-token" }))
const cache = new Map<string, unknown>()
vi.mock("./valkey", () => ({
  cacheGet: vi.fn(async (key: string) => cache.get(key) ?? null),
  cacheSet: vi.fn(async (key: string, value: unknown) => { cache.set(key, value) }),
}))
const fetchMock = vi.fn()
const readMock = vi.fn()
vi.mock("./http-client", () => ({
  fetchWithPolicy: (...args: unknown[]) => fetchMock(...args),
  readJsonWithPolicy: (...args: unknown[]) => readMock(...args),
}))
import { getComplianceFrameworkDetail, getComplianceFrameworks, getComplianceSummary } from "./compliance"
import { cacheKeys } from "./cache-keys"

const prefix = "/apis/aquasecurity.github.io/v1alpha1/"
const auxiliary = [
  "/api/v1/nodes",
  "/apis/apps/v1/namespaces/security-system/deployments/trivy-operator",
  "/api/v1/namespaces/security-system/configmaps/trivy-operator-trivy-config",
]
function report(id: string, success: boolean, kind = "Deployment", namespace = "dev", name = "app") {
  return {
    metadata: { name: `${kind.toLowerCase()}-${name}`, namespace, labels: {
      "trivy-operator.resource.kind": kind, "trivy-operator.resource.name": name,
    } },
    report: { summary: { criticalCount: 0, highCount: success ? 0 : 1, mediumCount: 0, lowCount: 0 },
      checks: [{ checkID: id, success, severity: "HIGH", title: id }] },
  }
}
function setup(ids = ["AVD-KSV-0038"], failedPath?: string) {
  const controls = ids.map((id, n) => ({ id: String(n), name: id, severity: "HIGH", checks: [{ id }] }))
  const routes: Record<string, unknown> = {
    [auxiliary[0]]: snapshot.nodes,
    [auxiliary[1]]: snapshot.trivyOperatorDeployment,
    [auxiliary[2]]: snapshot.trivyConfigMap,
    [prefix + "clustercompliancereports"]: { items: [{
      metadata: { name: "experiment" }, spec: { compliance: { id: "experiment", title: "Experiment", controls } },
      status: { summary: { passCount: controls.length, failCount: 0 }, detailReport: { results: controls.map((c) =>
        ({ ...c, checks: [{ checkID: "", success: true }] })) } },
    }] },
  }
  fetchMock.mockImplementation(async (url: string) => {
    const path = new URL(url).pathname
    return { ok: path !== failedPath, status: path === failedPath ? 403 : 200, statusText: "Forbidden", path }
  })
  readMock.mockImplementation(async (r: { path: string }) => routes[r.path] ?? { items: [] })
  return routes
}
beforeEach(() => { cache.clear(); fetchMock.mockReset(); readMock.mockReset() })

it.each([false, true])("G1 failure evidence wins over mixed positive evidence and stale CCR placeholder pass (failure first: %s)", async (failureFirst) => {
  const routes = setup()
  routes[prefix + "configauditreports"] = { items: [report("KSV038", !failureFirst), report("KSV038", failureFirst)] }
  const detail = await getComplianceFrameworkDetail("experiment")
  expect(detail!.controls[0]).toMatchObject({ evaluationState: "failed", actionableFailCount: 1, accepted: false })
  expect(detail!.evaluatedPassCount).toBe(0)
  expect(detail!.actionableFailCount).toBe(1)
  // Failure still wins when scope discovery fails.
  cache.clear()
  fetchMock.mockImplementation(async (url: string) => ({ ok: new URL(url).pathname !== auxiliary[0], status: 403, statusText: "Forbidden", path: new URL(url).pathname }))
  expect((await getComplianceFrameworkDetail("experiment"))!.controls[0].evaluationState).toBe("failed")
})

it("G1 underlying failure keeps the existing accepted split and resolves absent CCR controls", async () => {
  const routes = setup(["AVD-KSV-0017"])
  routes[prefix + "configauditreports"] = { items: [report("KSV017", false, "DaemonSet", "kube-system", "cilium")] }
  const ccr = routes[prefix + "clustercompliancereports"] as { items: Array<{ status: { detailReport: { results: unknown[] } } }> }
  ccr.items[0].status.detailReport.results = []
  expect((await getComplianceFrameworkDetail("experiment"))!.controls[0]).toMatchObject({ evaluationState: "failed", accepted: true, acceptedFailCount: 1 })
})

it("G2 KCV0007 requires InfraAssessmentReport evidence and rejects ConfigAuditReport evidence", async () => {
  const routes = setup(["AVD-KCV-0007"])
  // Ensure kube-system is in scan scope so we test report-family isolation (infra vs configAudit).
  routes[auxiliary[1]] = {
    spec: { template: { spec: { containers: [{ env: [{ name: "OPERATOR_EXCLUDE_NAMESPACES", value: "kube-public" }] }] } } },
  }
  routes[prefix + "configauditreports"] = { items: [report("KCV0007", true, "Pod", "kube-system")] }
  expect((await getComplianceFrameworkDetail("experiment"))!.controls[0]).toMatchObject({
    evaluationState: "not_evaluated",
    notEvaluatedReason: "no_report_for_family",
  })
  // Clean (failed-only) kube-system Pod InfraAssessmentReport -> H1: infra NEVER becomes passed;
  // stays not_evaluated (infra_no_per_check_evidence) until a failing record exists.
  routes[prefix + "infraassessmentreports"] = {
    items: [{ ...report("KCV0007", true, "Pod", "kube-system"), report: { summary: { criticalCount: 0, highCount: 0, mediumCount: 0, lowCount: 0 }, checks: [] } }],
  }
  cache.clear()
  expect((await getComplianceFrameworkDetail("experiment"))!.controls[0]).toMatchObject({
    evaluationState: "not_evaluated",
    notEvaluatedReason: "infra_no_per_check_evidence",
  })
  routes[prefix + "infraassessmentreports"] = { items: [report("KCV0007", false, "Pod", "kube-system")] }
  cache.clear()
  expect((await getComplianceFrameworkDetail("experiment"))!.controls[0].evaluationState).toBe("failed")
})

it("G2 maps snapshot RBAC KSV041 and KSV048 to RBAC evidence, including mixed-family controls", async () => {
  expect(snapshot.rbacassessmentreports.items.flatMap((r) => r.report.checks.map((c) => c.checkID))).toContain("KSV048")
  expect(snapshot.clusterrbacassessmentreports.items.flatMap((r) => r.report.checks.map((c) => c.checkID))).toContain("KSV041")
  const routes = setup(["AVD-KSV-0041", "AVD-KSV-0048"])
  const ccr = routes[prefix + "clustercompliancereports"] as { items: Array<{ spec: { compliance: { controls: Array<{ checks: Array<{ id: string }> }> } } }> }
  ccr.items[0].spec.compliance.controls[0].checks.push({ id: "AVD-KSV-0038" })
  // ConfigAudit only (no RBAC reports yet): neither RBAC control can reach no-failure-recorded.
  routes[prefix + "configauditreports"] = { items: [report("KSV038", true, "NetworkPolicy", "dev", "netpol-1")] }
  expect((await getComplianceFrameworkDetail("experiment"))!.evaluatedPassCount).toBe(0)
  // Clean (failed-only) RBAC reports exist in the RBAC family alongside NetworkPolicy ConfigAuditReport -> both reach no-failure-recorded.
  const cleanSummary = { criticalCount: 0, highCount: 0, mediumCount: 0, lowCount: 0 }
  routes[prefix + "rbacassessmentreports"] = { items: [{ ...report("KSV048", true, "Role", "dev", "app-role"), report: { summary: cleanSummary, checks: [] } }] }
  routes[prefix + "clusterrbacassessmentreports"] = { items: [{ ...report("KSV041", true, "ClusterRole", "", "app-cluster-role"), report: { summary: cleanSummary, checks: [] } }] }
  cache.clear()
  expect((await getComplianceFrameworkDetail("experiment"))!.evaluatedPassCount).toBe(2)
  routes[prefix + "rbacassessmentreports"] = { items: [report("KSV048", false, "Role", "dev", "app-role")] }
  cache.clear()
  expect((await getComplianceFrameworkDetail("experiment"))!.controls[1].evaluationState).toBe("failed")
})

it.each(auxiliary)("G3 auxiliary read failure %s makes positive workload evidence coverage_unknown", async (path) => {
  const routes = setup(["AVD-KSV-0038", "AVD-KSV-0039"], path)
  const ccr = routes[prefix + "clustercompliancereports"] as { items: Array<{ status: { detailReport: { results: unknown[] } } }> }
  ccr.items[0].status.detailReport.results.pop()
  routes[prefix + "configauditreports"] = { items: [report("KSV038", true)] }
  expect((await getComplianceFrameworkDetail("experiment"))!.controls[0]).toMatchObject({
    evaluationState: "not_evaluated", notEvaluatedReason: "coverage_unknown",
  })
  expect((await getComplianceFrameworkDetail("experiment"))!.controls.every((c) => c.notEvaluatedReason === "coverage_unknown")).toBe(true)
  ccr.items[0].status.detailReport.results = []
  expect((await getComplianceFrameworkDetail("experiment"))!.controls.every((c) => c.notEvaluatedReason === "coverage_unknown")).toBe(true)
  expect(cache.size).toBe(0)
})

it("G5 old-shape compliance caches use different versioned keys", async () => {
  const old = { id: "stale", title: "Stale", totalControls: 1, passCount: 1, failCount: 0, passRate: 1 }
  cache.set("compliance:frameworks:list", [old])
  cache.set("compliance:frameworks:experiment", { ...old, controls: [] })
  cache.set("compliance:summary", { frameworks: [old] })
  setup()
  expect((await getComplianceFrameworks())[0].id).toBe("experiment")
  expect((await getComplianceFrameworkDetail("experiment"))!.notEvaluatedCount).toBe(1)
  expect((await getComplianceSummary()).frameworks[0].notEvaluatedCount).toBe(1)
  expect(cacheKeys.kisaComplianceList()).not.toBe("compliance:kisa:list")
})

it("H1 (MAJOR 1+2): infra node and control-plane component checks never become passed from clean reports", async () => {
  const routes = setup(["AVD-KCV-0066", "AVD-KCV-0087", "AVD-KCV-0007"])
  routes[auxiliary[1]] = {
    spec: { template: { spec: { containers: [{ env: [{ name: "OPERATOR_EXCLUDE_NAMESPACES", value: "kube-public" }] }] } } },
  }
  const cleanSummary = { criticalCount: 0, highCount: 0, mediumCount: 0, lowCount: 0 }
  routes[prefix + "clusterinfraassessmentreports"] = {
    items: [{
      metadata: {
        name: "node-narwhal-master-1",
        labels: { "trivy-operator.resource.kind": "Node", "trivy-operator.resource.name": "narwhal-master-1" },
      },
      report: { summary: cleanSummary, checks: [] },
    }],
  }
  // Only a scheduler Pod infra report exists — apiserver KCV0007 must stay not_evaluated.
  routes[prefix + "infraassessmentreports"] = {
    items: [{
      metadata: {
        namespace: "kube-system",
        name: "pod-kube-scheduler-narwhal-master-1",
        labels: { "trivy-operator.resource.kind": "Pod", "trivy-operator.resource.name": "kube-scheduler-narwhal-master-1" },
      },
      report: { summary: cleanSummary, checks: [] },
    }],
  }
  const detail = await getComplianceFrameworkDetail("experiment")
  expect(detail!.evaluatedPassCount).toBe(0)
  expect(detail!.notEvaluatedCount).toBe(3)
  for (const ctrl of detail!.controls) {
    expect(ctrl).toMatchObject({
      evaluationState: "not_evaluated",
      notEvaluatedReason: "infra_no_per_check_evidence",
    })
  }
})

it("H2 (MAJOR 3): failure join matches legacy/alias IDs across families without colliding KSV0012 with KSV012", async () => {
  const routes = setup(["AVD-KCV-0012", "AVD-KCV-0035", "AVD-KSV-0012", "AVD-KSV-0124"])
  routes[auxiliary[1]] = {
    spec: { template: { spec: { containers: [{ env: [{ name: "OPERATOR_EXCLUDE_NAMESPACES", value: "kube-public" }] }] } } },
  }
  const cleanSummary = { criticalCount: 0, highCount: 0, mediumCount: 0, lowCount: 0 }
  // Reviewer reproduction: ConfigAudit/KSV0012 failing + clean infra report -> KCV0012 MUST be failed,
  // while KSV012 ("Runs as root user") must NOT inherit KSV0012's failure.
  // Also KCV0135 failing in ConfigAudit -> KCV0035 failed; KSV-0124 failing -> KSV124 failed.
  routes[prefix + "configauditreports"] = {
    items: [
      report("KSV0012", false, "Pod", "kube-system", "kube-apiserver-narwhal-master-1"),
      report("KCV0135", false, "Pod", "kube-system", "kube-controller-manager-narwhal-master-1"),
      report("KSV-0124", false, "Deployment", "dev", "gatekeeper-sample"),
    ],
  }
  routes[prefix + "infraassessmentreports"] = {
    items: [{
      metadata: {
        namespace: "kube-system",
        name: "pod-kube-apiserver-narwhal-master-1",
        labels: { "trivy-operator.resource.kind": "Pod", "trivy-operator.resource.name": "kube-apiserver-narwhal-master-1" },
      },
      report: { summary: cleanSummary, checks: [] },
    }],
  }
  const detail = await getComplianceFrameworkDetail("experiment")
  expect(detail!.controls[0]).toMatchObject({ name: "AVD-KCV-0012", evaluationState: "failed", failCount: 1 })
  expect(detail!.controls[1]).toMatchObject({ name: "AVD-KCV-0035", evaluationState: "failed", failCount: 1 })
  expect(detail!.controls[2]).toMatchObject({ name: "AVD-KSV-0012", evaluationState: "passed", failCount: 0 })
  expect(detail!.controls[3]).toMatchObject({ name: "AVD-KSV-0124", evaluationState: "failed", failCount: 1 })

  // Reverse collision check: workload failing KSV012 (runs as root) must NOT make AVD-KCV-0012 fail.
  cache.clear()
  routes[prefix + "configauditreports"] = {
    items: [report("KSV012", false, "Deployment", "dev", "root-workload")],
  }
  const detail2 = await getComplianceFrameworkDetail("experiment")
  expect(detail2!.controls[0]).toMatchObject({
    name: "AVD-KCV-0012",
    evaluationState: "not_evaluated",
    notEvaluatedReason: "infra_no_per_check_evidence",
  })
  expect(detail2!.controls[2]).toMatchObject({ name: "AVD-KSV-0012", evaluationState: "failed", failCount: 1 })
})

it("H3 (MAJOR 4): fetches and joins ClusterConfigAuditReport ClusterRoleBinding KSV111 failures and marks 403 as coverage_unknown", async () => {
  const routes = setup(["AVD-KSV-0111", "AVD-KSV-0014"])
  routes[auxiliary[2]] = {
    data: { "trivy.supportedConfigAuditKinds": "Workload,Role,ClusterRole,RoleBinding,ClusterRoleBinding" },
  }
  routes[prefix + "configauditreports"] = {
    items: [{ ...report("KSV014", true, "Deployment", "dev", "app"), report: { summary: { criticalCount: 0, highCount: 0, mediumCount: 0, lowCount: 0 }, checks: [] } }],
  }
  routes[prefix + "clusterconfigauditreports"] = {
    items: [
      {
        metadata: {
          name: "clusterrolebinding-67649766f5",
          labels: { "trivy-operator.resource.kind": "ClusterRoleBinding", "trivy-operator.resource.name-hash": "67649766f5" },
          ownerReferences: [{ kind: "ClusterRoleBinding", name: "kubeadm:cluster-admins" }],
        },
        report: {
          summary: { criticalCount: 0, highCount: 0, mediumCount: 1, lowCount: 0 },
          checks: [{ checkID: "KSV111", success: false, severity: "MEDIUM", title: "User with admin access" }],
        },
      },
      {
        metadata: {
          name: "clusterrolebinding-cluster-admin",
          labels: { "trivy-operator.resource.kind": "ClusterRoleBinding", "trivy-operator.resource.name": "cluster-admin" },
          ownerReferences: [{ kind: "ClusterRoleBinding", name: "cluster-admin" }],
        },
        report: {
          summary: { criticalCount: 0, highCount: 0, mediumCount: 1, lowCount: 0 },
          checks: [{ checkID: "KSV111", success: false, severity: "MEDIUM", title: "User with admin access" }],
        },
      },
    ],
  }
  const detail = await getComplianceFrameworkDetail("experiment")
  expect(detail!.controls[0]).toMatchObject({
    name: "AVD-KSV-0111",
    evaluationState: "failed",
    failCount: 2,
    actionableFailCount: 2,
    accepted: false,
  })
  expect(detail!.controls[1]).toMatchObject({ name: "AVD-KSV-0014", evaluationState: "passed" })

  // 403 on clusterconfigauditreports -> non-failed controls become coverage_unknown and partialSources names it.
  cache.clear()
  setup(["AVD-KSV-0111", "AVD-KSV-0014"], prefix + "clusterconfigauditreports")
  const detail403 = await getComplianceFrameworkDetail("experiment")
  expect(detail403!.coverageUnknown).toBe(true)
  expect(detail403!.controls[0]).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "coverage_unknown" })
  expect(detail403!.controls[0].notEvaluatedDetail).toContain("clusterconfigauditreports")
})

it("H4 (MAJOR 5): failed /api/v1/nodes read never hides existing infra failures even with explicit nodeType", async () => {
  const routes = setup(["AVD-KCV-0056", "AVD-KSV-0014"], "/api/v1/nodes")
  const ccr = routes[prefix + "clustercompliancereports"] as {
    items: Array<{ spec: { compliance: { controls: Array<{ id: string; nodeType?: string; commands?: Array<{ id: string; nodeType: string }> }> } } }>
  }
  ccr.items[0].spec.compliance.controls[0].nodeType = "master"
  ccr.items[0].spec.compliance.controls[0].commands = [{ id: "CMD-0009", nodeType: "master" }]
  routes[prefix + "clusterinfraassessmentreports"] = {
    items: [{
      metadata: {
        name: "node-narwhal-master-1",
        labels: { "trivy-operator.resource.kind": "Node", "trivy-operator.resource.name": "narwhal-master-1" },
      },
      report: {
        summary: { criticalCount: 0, highCount: 1, mediumCount: 0, lowCount: 0 },
        checks: [{ checkID: "KCV0056", success: false, severity: "HIGH", title: "CNI file permissions" }],
      },
    }],
  }
  routes[prefix + "configauditreports"] = {
    items: [{ ...report("KSV014", true, "Deployment", "dev", "app"), report: { summary: { criticalCount: 0, highCount: 0, mediumCount: 0, lowCount: 0 }, checks: [] } }],
  }
  const detail = await getComplianceFrameworkDetail("experiment")
  expect(detail!.controls[0]).toMatchObject({
    evaluationState: "failed",
    failCount: 1,
    actionableFailCount: 1,
  })
  expect(detail!.controls[1]).toMatchObject({
    evaluationState: "not_evaluated",
    notEvaluatedReason: "coverage_unknown",
  })
  expect(cache.size).toBe(0)
})

it("matches all 10 reviewer expected control states on a hand-built live-like fixture", async () => {
  const cleanSummary = { criticalCount: 0, highCount: 0, mediumCount: 0, lowCount: 0 }
  const routes: Record<string, unknown> = {
    [auxiliary[0]]: {
      items: [{ metadata: { name: "narwhal-master-1", labels: { "node-role.kubernetes.io/control-plane": "" } } }],
    },
    [auxiliary[1]]: {
      spec: {
        template: {
          spec: {
            containers: [{
              env: [
                { name: "OPERATOR_EXCLUDE_NAMESPACES", value: "kube-public,kube-node-lease,trivy-system" },
                { name: "OPERATOR_TARGET_NAMESPACES", value: "" },
                { name: "OPERATOR_CONFIG_AUDIT_SCANNER_ENABLED", value: "true" },
                { name: "OPERATOR_RBAC_ASSESSMENT_SCANNER_ENABLED", value: "true" },
                { name: "OPERATOR_INFRA_ASSESSMENT_SCANNER_ENABLED", value: "true" },
              ],
            }],
          },
        },
      },
    },
    [auxiliary[2]]: {
      data: { "trivy.supportedConfigAuditKinds": "Workload,Service,Role,ClusterRole,RoleBinding,ClusterRoleBinding,NetworkPolicy,Ingress,LimitRange,ResourceQuota" },
    },
    [prefix + "configauditreports"]: {
      items: [
        report("KSV0012", false, "Pod", "kube-system", "kube-apiserver-narwhal-master-1"),
        report("KSV017", false, "DaemonSet", "security-system", "falco"),
        report("KSV106", false, "Deployment", "dev", "unrestricted-app"),
        { ...report("KSV038", true, "NetworkPolicy", "dev", "default-deny"), report: { summary: cleanSummary, checks: [] } },
      ],
    },
    [prefix + "clusterconfigauditreports"]: { items: [] },
    [prefix + "rbacassessmentreports"]: { items: [] },
    [prefix + "clusterrbacassessmentreports"]: {
      items: [{
        metadata: {
          name: "clusterrole-platform-admin",
          labels: { "trivy-operator.resource.kind": "ClusterRole", "trivy-operator.resource.name": "platform-admin" },
          ownerReferences: [{ kind: "ClusterRole", name: "platform-admin" }],
        },
        report: {
          summary: { criticalCount: 1, highCount: 0, mediumCount: 0, lowCount: 0 },
          checks: [{ checkID: "KSV041", success: false, severity: "CRITICAL", title: "Manage secrets" }],
        },
      }],
    },
    [prefix + "infraassessmentreports"]: {
      items: [{
        metadata: {
          namespace: "kube-system",
          name: "pod-kube-apiserver-narwhal-master-1",
          labels: { "trivy-operator.resource.kind": "Pod", "trivy-operator.resource.name": "kube-apiserver-narwhal-master-1" },
        },
        report: { summary: cleanSummary, checks: [] },
      }],
    },
    [prefix + "clusterinfraassessmentreports"]: {
      items: [{
        metadata: {
          name: "node-narwhal-master-1",
          labels: { "trivy-operator.resource.kind": "Node", "trivy-operator.resource.name": "narwhal-master-1" },
        },
        report: { summary: cleanSummary, checks: [] },
      }],
    },
    [prefix + "clustercompliancereports"]: {
      items: [
        {
          metadata: { name: "k8s-cis-1.23" },
          spec: {
            compliance: {
              id: "k8s-cis-1.23",
              title: "CIS Kubernetes Benchmark v1.23",
              controls: [
                { id: "1.1.19", name: "PKI dir/file ownership", severity: "CRITICAL", checks: [{ id: "AVD-KCV-0066" }], commands: [{ id: "CMD-0019" }] },
                { id: "4.2.9", name: "event-qps", severity: "LOW", checks: [{ id: "AVD-KCV-0087" }], commands: [{ id: "CMD-0040" }] },
                { id: "1.2.12", name: "AlwaysPullImages", severity: "LOW", checks: [{ id: "AVD-KCV-0012" }] },
                { id: "3.1.1", name: "Client cert auth (Manual)", severity: "LOW" },
                { id: "5.2.9", name: "Added capabilities", severity: "LOW", checks: [{ id: "AVD-KSV-0004" }] },
                { id: "5.1.2", name: "Minimize access to secrets", severity: "CRITICAL", checks: [{ id: "AVD-KSV-0041" }] },
                { id: "5.2.2", name: "Minimize privileged containers", severity: "HIGH", checks: [{ id: "AVD-KSV-0017" }] },
              ],
            },
          },
          status: {
            summary: { passCount: 5, failCount: 2 },
            detailReport: {
              results: [
                { id: "1.1.19", name: "PKI dir/file ownership", severity: "CRITICAL", checks: [{ checkID: "", success: true }] },
                { id: "4.2.9", name: "event-qps", severity: "LOW", checks: [{ checkID: "", success: true }] },
                { id: "1.2.12", name: "AlwaysPullImages", severity: "LOW", checks: [{ checkID: "", success: true }] },
                { id: "3.1.1", name: "Client cert auth (Manual)", severity: "LOW", checks: [{ checkID: "", success: true }] },
                { id: "5.2.9", name: "Added capabilities", severity: "LOW", checks: [{ checkID: "", success: true }] },
                { id: "5.1.2", name: "Minimize access to secrets", severity: "CRITICAL", checks: [{ checkID: "AVD-KSV-0041", target: "/clusterrole-platform-admin", success: false }] },
                { id: "5.2.2", name: "Minimize privileged containers", severity: "HIGH", checks: [{ checkID: "AVD-KSV-0017", target: "security-system/daemonset-falco", success: false }] },
              ],
            },
          },
        },
        {
          metadata: { name: "k8s-kisa-1.0" },
          spec: {
            compliance: {
              id: "k8s-kisa-1.0",
              title: "KISA",
              controls: [{ id: "KISA-NET-01", name: "NetworkPolicy selector", severity: "MEDIUM", checks: [{ id: "AVD-KSV-0038" }] }],
            },
          },
          status: {
            summary: { passCount: 1, failCount: 0 },
            detailReport: {
              results: [{ id: "KISA-NET-01", name: "NetworkPolicy selector", severity: "MEDIUM", checks: [{ checkID: "", success: true }] }],
            },
          },
        },
        {
          metadata: { name: "k8s-nsa-1.0" },
          spec: {
            compliance: {
              id: "k8s-nsa-1.0",
              title: "NSA",
              controls: [{ id: "4.1", name: "LimitRange", severity: "MEDIUM", checks: [{ id: "AVD-KSV-0039" }] }],
            },
          },
          status: {
            summary: { passCount: 1, failCount: 0 },
            detailReport: {
              results: [{ id: "4.1", name: "LimitRange", severity: "MEDIUM", checks: [{ checkID: "", success: true }] }],
            },
          },
        },
        {
          metadata: { name: "k8s-pss-restricted-0.1" },
          spec: {
            compliance: {
              id: "k8s-pss-restricted-0.1",
              title: "PSS Restricted",
              controls: [{ id: "17", name: "Capabilities", severity: "LOW", checks: [{ id: "AVD-KSV-0106" }] }],
            },
          },
          // Mirror live CCR where detailReport.results is absent until next 6h rebuild.
          status: { summary: { passCount: 0, failCount: 0 } },
        },
      ],
    },
  }
  fetchMock.mockImplementation(async (url: string) => ({ ok: true, status: 200, statusText: "OK", path: new URL(url).pathname }))
  readMock.mockImplementation(async (r: { path: string }) => routes[r.path] ?? { items: [] })

  const cis = await getComplianceFrameworkDetail("k8s-cis-1.23")
  const cisById = new Map(cis!.controls.map((c) => [c.id, c]))
  expect(cisById.get("1.1.19")).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "infra_no_per_check_evidence" })
  expect(cisById.get("4.2.9")).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "infra_no_per_check_evidence" })
  expect(cisById.get("1.2.12")).toMatchObject({ evaluationState: "failed" })
  expect(cisById.get("3.1.1")).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "manual" })
  expect(cisById.get("5.2.9")).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "not_in_catalogue" })
  expect(cisById.get("5.1.2")).toMatchObject({ evaluationState: "failed" })
  expect(cisById.get("5.2.2")).toMatchObject({ evaluationState: "failed" })

  const kisa = await getComplianceFrameworkDetail("k8s-kisa-1.0")
  expect(kisa!.controls[0]).toMatchObject({ id: "KISA-NET-01", evaluationState: "passed" })

  const nsa = await getComplianceFrameworkDetail("k8s-nsa-1.0")
  expect(nsa!.controls[0]).toMatchObject({ id: "4.1", evaluationState: "not_evaluated", notEvaluatedReason: "not_in_catalogue" })

  const pss = await getComplianceFrameworkDetail("k8s-pss-restricted-0.1")
  expect(pss!.controls[0]).toMatchObject({ id: "17", evaluationState: "failed", actionableFailCount: 1 })
})
