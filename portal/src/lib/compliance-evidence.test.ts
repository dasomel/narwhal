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
  routes[prefix + "configauditreports"] = { items: [report("KCV0007", true, "Pod", "kube-system")] }
  expect((await getComplianceFrameworkDetail("experiment"))!.controls[0].evaluationState).toBe("not_evaluated")
  routes[prefix + "infraassessmentreports"] = { items: [report("KCV0007", true, "Pod", "kube-system")] }
  cache.clear()
  expect((await getComplianceFrameworkDetail("experiment"))!.controls[0].evaluationState).toBe("passed")
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
  routes[prefix + "configauditreports"] = { items: [report("KSV041", true), report("KSV048", true), report("KSV038", true)] }
  expect((await getComplianceFrameworkDetail("experiment"))!.evaluatedPassCount).toBe(0)
  routes[prefix + "rbacassessmentreports"] = { items: [report("KSV048", true, "Role")] }
  routes[prefix + "clusterrbacassessmentreports"] = { items: [report("KSV041", true, "ClusterRole", "")] }
  cache.clear()
  expect((await getComplianceFrameworkDetail("experiment"))!.evaluatedPassCount).toBe(2)
  routes[prefix + "rbacassessmentreports"] = { items: [report("KSV048", false, "Role")] }
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
