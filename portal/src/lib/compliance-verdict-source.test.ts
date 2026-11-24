import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("server-only", () => ({}))
vi.mock("./config", () => ({ getK8sApiServer: () => "http://k8s.mock" }))
vi.mock("./k8s-token", () => ({ getK8sBearerToken: () => "mock-token" }))

const cache = new Map<string, unknown>()
vi.mock("./valkey", () => ({
  cacheGet: vi.fn(async (key: string) => cache.get(key) ?? null),
  cacheSet: vi.fn(async (key: string, val: unknown) => {
    cache.set(key, val)
  }),
}))

const fetchMock = vi.fn()
const readMock = vi.fn()
vi.mock("./http-client", () => ({
  fetchWithPolicy: (...args: unknown[]) => fetchMock(...args),
  readJsonWithPolicy: (...args: unknown[]) => readMock(...args),
}))

import {
  ComplianceDataUnavailableError,
  getComplianceFrameworkDetail,
  getComplianceFrameworks,
} from "./compliance"
import { resolveCanonicalCheckId } from "./trivy-check-catalogue"

const TRIVY = "/apis/aquasecurity.github.io/v1alpha1/"

function makeReport(opts: {
  namespace?: string
  kind: string
  name: string
  checks: Array<{ checkID: string; success: boolean; severity?: string }>
}) {
  const fails = opts.checks.filter((c) => !c.success)
  return {
    metadata: {
      name: `${opts.kind.toLowerCase()}-${opts.name}`,
      ...(opts.namespace ? { namespace: opts.namespace } : {}),
      creationTimestamp: "2026-11-25T00:00:00Z",
      labels: {
        "trivy-operator.resource.kind": opts.kind,
        "trivy-operator.resource.name": opts.name,
      },
      ownerReferences: [{ kind: opts.kind, name: opts.name }],
    },
    report: {
      summary: { criticalCount: 0, highCount: fails.length, mediumCount: 0, lowCount: 0 },
      checks: opts.checks.map((c) => ({
        checkID: c.checkID,
        title: `Check ${c.checkID}`,
        severity: c.severity ?? "HIGH",
        success: c.success,
      })),
    },
  }
}

function baseRoutes(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    "/api/v1/nodes": {
      items: [{ metadata: { name: "narwhal-master-1", labels: { "node-role.kubernetes.io/control-plane": "" } } }],
    },
    "/apis/apps/v1/namespaces/security-system/deployments/trivy-operator": {
      spec: {
        template: {
          spec: {
            containers: [{
              env: [
                { name: "OPERATOR_EXCLUDE_NAMESPACES", value: "kube-public,kube-node-lease,trivy-system" },
                { name: "OPERATOR_CONFIG_AUDIT_SCANNER_ENABLED", value: "true" },
                { name: "OPERATOR_RBAC_ASSESSMENT_SCANNER_ENABLED", value: "true" },
                { name: "OPERATOR_INFRA_ASSESSMENT_SCANNER_ENABLED", value: "true" },
              ],
            }],
          },
        },
      },
    },
    "/api/v1/namespaces/security-system/configmaps/trivy-operator-trivy-config": {
      data: {
        "trivy.supportedConfigAuditKinds": "Workload,Service,Role,ClusterRole,RoleBinding,ClusterRoleBinding,NetworkPolicy",
      },
    },
    ...overrides,
  }
}

function installRoutes(routes: Record<string, unknown>) {
  fetchMock.mockImplementation(async (url: string) => ({
    ok: true,
    status: 200,
    statusText: "OK",
    path: new URL(url).pathname,
  }))
  readMock.mockImplementation(async (res: { path: string }) => routes[res.path] ?? { items: [] })
}

beforeEach(() => {
  cache.clear()
  fetchMock.mockReset()
  readMock.mockReset()
})

describe("D-COMPLIANCE-VERDICT-SOURCE (per-resource report verdicts)", () => {
  it("(a) trivy-operator 0.25.0 failures-only shape preserves exact honest states with zero tested-pass counts", async () => {
    installRoutes(baseRoutes({
      [TRIVY + "configauditreports"]: {
        items: [
          makeReport({ namespace: "dev", kind: "Deployment", name: "app", checks: [{ checkID: "KSV014", success: false }] }),
          makeReport({ namespace: "dev", kind: "NetworkPolicy", name: "deny", checks: [] }),
        ],
      },
      [TRIVY + "clusterinfraassessmentreports"]: {
        items: [makeReport({ kind: "Node", name: "narwhal-master-1", checks: [] })],
      },
      [TRIVY + "clustercompliancereports"]: {
        items: [{
          metadata: { name: "pss-025", creationTimestamp: "2026-11-25T00:00:00Z" },
          spec: {
            compliance: {
              id: "pss-025",
              title: "PSS 0.25.0",
              controls: [
                { id: "C-FAIL", name: "Read-only root fs", severity: "HIGH", checks: [{ id: "AVD-KSV-0014" }] },
                { id: "C-NOFAIL", name: "Clean workload check", severity: "MEDIUM", checks: [{ id: "AVD-KSV-0103" }] },
                { id: "C-INFRA", name: "Infra without pass record", severity: "HIGH", checks: [{ id: "AVD-KCV-0048" }] },
              ],
            },
          },
          status: {
            updateTimestamp: "2026-11-25T00:00:00Z",
            summary: { passCount: 2, failCount: 1 },
            detailReport: {
              results: [
                { id: "C-FAIL", name: "Read-only root fs", severity: "HIGH", checks: [{ checkID: "AVD-KSV-0014", target: "dev/deployment-app", success: false }] },
                { id: "C-NOFAIL", name: "Clean workload check", severity: "MEDIUM", checks: [{ checkID: "", success: true }] },
                { id: "C-INFRA", name: "Infra without pass record", severity: "HIGH", checks: [{ checkID: "", success: true }] },
              ],
            },
          },
        }],
      },
    }))

    const detail = await getComplianceFrameworkDetail("pss-025")
    expect(detail).not.toBeNull()
    expect(detail!.failCount).toBe(1)
    expect(detail!.actionableFailCount).toBe(1)
    expect(detail!.evaluatedPassCount).toBe(1)
    expect(detail!.noFailureRecordedPassCount).toBe(1)
    expect(detail!.notEvaluatedCount).toBe(1)
    expect(detail!.reportTestedPassCount).toBeUndefined()
    expect(detail!.testedPassCount).toBeUndefined()

    const byId = new Map(detail!.controls.map((c) => [c.id, c]))
    expect(byId.get("C-FAIL")).toMatchObject({ evaluationState: "failed", failCount: 1, actionableFailCount: 1 })
    expect(byId.get("C-NOFAIL")).toMatchObject({ evaluationState: "passed", passCount: 0 })
    expect(byId.get("C-NOFAIL")?.testedByReport).toBeUndefined()
    expect(byId.get("C-INFRA")).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "infra_no_per_check_evidence" })
  })

  it("(b) trivy-operator 0.36.1 explicit success:true and success:false produces tested-pass and fail per control across report families", async () => {
    installRoutes(baseRoutes({
      [TRIVY + "configauditreports"]: {
        items: [
          makeReport({
            namespace: "dev",
            kind: "Deployment",
            name: "app-a",
            checks: [
              { checkID: "KSV001", success: true },
              { checkID: "KSV014", success: true },
            ],
          }),
          makeReport({
            namespace: "dev",
            kind: "Deployment",
            name: "app-b",
            checks: [
              { checkID: "KSV001", success: true },
              { checkID: "KSV014", success: false },
            ],
          }),
        ],
      },
      [TRIVY + "clusterrbacassessmentreports"]: {
        items: [
          makeReport({ kind: "ClusterRole", name: "custom-reader", checks: [{ checkID: "KSV041", success: true }] }),
        ],
      },
      [TRIVY + "infraassessmentreports"]: {
        items: [
          makeReport({ namespace: "kube-system", kind: "Pod", name: "kube-apiserver-narwhal-master-1", checks: [{ checkID: "KCV0033", success: true }] }),
        ],
      },
      [TRIVY + "clusterinfraassessmentreports"]: {
        items: [
          makeReport({ kind: "Node", name: "narwhal-master-1", checks: [{ checkID: "KCV0048", success: true }] }),
        ],
      },
      [TRIVY + "clustercompliancereports"]: {
        items: [{
          metadata: { name: "fw-036", creationTimestamp: "2026-11-25T00:00:00Z" },
          spec: {
            compliance: {
              id: "fw-036",
              title: "Framework 0.36.1",
              controls: [
                { id: "C-CAR-PASS", name: "All resources passed KSV001", severity: "HIGH", checks: [{ id: "AVD-KSV-0001" }] },
                { id: "C-CAR-MIXED", name: "One passed, one failed KSV014", severity: "HIGH", checks: [{ id: "AVD-KSV-0014" }] },
                { id: "C-RBAC-PASS", name: "ClusterRole passed KSV041", severity: "CRITICAL", checks: [{ id: "AVD-KSV-0041" }] },
                { id: "C-INFRA-POD-PASS", name: "Pod infra passed KCV0033", severity: "HIGH", checks: [{ id: "AVD-KCV-0033" }] },
                { id: "C-INFRA-NODE-PASS", name: "Node infra passed KCV0048", severity: "HIGH", checks: [{ id: "AVD-KCV-0048" }] },
              ],
            },
          },
          status: { updateTimestamp: "2026-11-25T00:00:00Z", summary: { passCount: 0, failCount: 5 } },
        }],
      },
    }))

    const detail = await getComplianceFrameworkDetail("fw-036")
    expect(detail).not.toBeNull()
    expect(detail!.totalControls).toBe(5)
    expect(detail!.evaluatedPassCount).toBe(4)
    expect(detail!.reportTestedPassCount).toBe(4)
    expect(detail!.testedPassCount).toBe(4)
    expect(detail!.noFailureRecordedPassCount).toBe(0)
    expect(detail!.failCount).toBe(1)
    expect(detail!.actionableFailCount).toBe(1)
    expect(detail!.notEvaluatedCount).toBe(0)

    const byId = new Map(detail!.controls.map((c) => [c.id, c]))
    expect(byId.get("C-CAR-PASS")).toMatchObject({ evaluationState: "passed", testedByReport: true, passCount: 2, failCount: 0 })
    expect(byId.get("C-CAR-MIXED")).toMatchObject({ evaluationState: "failed", passCount: 1, failCount: 1, actionableFailCount: 1 })
    expect(byId.get("C-RBAC-PASS")).toMatchObject({ evaluationState: "passed", testedByReport: true, passCount: 1 })
    expect(byId.get("C-INFRA-POD-PASS")).toMatchObject({ evaluationState: "passed", testedByReport: true, passCount: 1 })
    expect(byId.get("C-INFRA-NODE-PASS")).toMatchObject({ evaluationState: "passed", testedByReport: true, passCount: 1 })
  })

  it("(c) CCR detailReport listing passed checks as success:false (FromDetailReport bug) and inflated summary.failCount do NOT produce failures", async () => {
    installRoutes(baseRoutes({
      [TRIVY + "configauditreports"]: {
        items: [
          makeReport({ namespace: "dev", kind: "Deployment", name: "clean-app", checks: [{ checkID: "KSV001", success: true }] }),
        ],
      },
      [TRIVY + "clustercompliancereports"]: {
        items: [{
          metadata: { name: "buggy-ccr", creationTimestamp: "2026-11-25T00:00:00Z" },
          spec: {
            compliance: {
              id: "buggy-ccr",
              title: "Buggy v0.36.1 CCR",
              controls: [
                { id: "1", name: "Privilege escalation", severity: "HIGH", checks: [{ id: "AVD-KSV-0001" }] },
              ],
            },
          },
          status: {
            updateTimestamp: "2026-11-25T00:00:00Z",
            summary: { passCount: 0, failCount: 1 },
            detailReport: {
              results: [{
                id: "1",
                name: "Privilege escalation",
                severity: "HIGH",
                // Upstream v0.36.1 FromDetailReport hardcodes success:false for passed entries.
                checks: [{ checkID: "AVD-KSV-0001", target: "dev/deployment-clean-app", success: false }],
              }],
            },
          },
        }],
      },
    }))

    const frameworks = await getComplianceFrameworks()
    expect(frameworks[0]).toMatchObject({
      id: "buggy-ccr",
      failCount: 0,
      actionableFailCount: 0,
      acceptedFailCount: 0,
      evaluatedPassCount: 1,
      reportTestedPassCount: 1,
      testedPassCount: 1,
      noFailureRecordedPassCount: 0,
      notEvaluatedCount: 0,
    })
    const detail = await getComplianceFrameworkDetail("buggy-ccr")
    expect(detail!.controls[0]).toMatchObject({
      evaluationState: "passed",
      testedByReport: true,
      passCount: 1,
      failCount: 0,
    })
  })

  it("(d) handles missing CCR (empty or unavailable) and summary-only CCR without detailReport", async () => {
    // 1. Missing CCR (empty list)
    installRoutes(baseRoutes({ [TRIVY + "clustercompliancereports"]: { items: [] } }))
    expect(await getComplianceFrameworks()).toEqual([])
    expect(await getComplianceFrameworkDetail("k8s-cis-1.23")).toBeNull()

    // 2. Missing CCR (API error -> fail closed)
    cache.clear()
    fetchMock.mockImplementation(async (url: string) => {
      const path = new URL(url).pathname
      return { ok: path !== TRIVY + "clustercompliancereports", status: 404, statusText: "Not Found", path }
    })
    await expect(getComplianceFrameworks()).rejects.toThrowError(ComplianceDataUnavailableError)

    // 3. Summary-only CCR (no detailReport, e.g. etcd object size limit exceeded):
    // per-resource records still produce tested-pass and fail, while zero-record controls stay summary_only.
    cache.clear()
    installRoutes(baseRoutes({
      [TRIVY + "configauditreports"]: {
        items: [
          makeReport({
            namespace: "dev",
            kind: "Deployment",
            name: "app",
            checks: [
              { checkID: "KSV001", success: true },
              { checkID: "KSV014", success: false },
            ],
          }),
        ],
      },
      [TRIVY + "clustercompliancereports"]: {
        items: [{
          metadata: { name: "summary-only-036", creationTimestamp: "2026-11-25T00:00:00Z" },
          spec: {
            compliance: {
              id: "summary-only-036",
              title: "Summary Only 0.36.1",
              controls: [
                { id: "S-PASS", name: "Tested pass without detailReport", severity: "HIGH", checks: [{ id: "AVD-KSV-0001" }] },
                { id: "S-FAIL", name: "Failed without detailReport", severity: "HIGH", checks: [{ id: "AVD-KSV-0014" }] },
                { id: "S-NONE", name: "Zero records without detailReport", severity: "MEDIUM", checks: [{ id: "AVD-KSV-0020" }] },
              ],
            },
          },
          status: { updateTimestamp: "2026-11-25T00:00:00Z", summary: { passCount: 0, failCount: 3 } },
        }],
      },
    }))

    const detail = await getComplianceFrameworkDetail("summary-only-036")
    const byId = new Map(detail!.controls.map((c) => [c.id, c]))
    expect(byId.get("S-PASS")).toMatchObject({ evaluationState: "passed", testedByReport: true, passCount: 1 })
    expect(byId.get("S-FAIL")).toMatchObject({ evaluationState: "failed", failCount: 1, actionableFailCount: 1 })
    expect(byId.get("S-NONE")).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "summary_only" })
    expect(byId.get("S-NONE")?.testedByReport).toBeUndefined()
  })

  it("(e) normalises AVD-KSV-0001 / KSV001 / KSV-0001 / KCV0033 / AVD-KCV-0033 without colliding KSV0012 and KSV012", async () => {
    expect(resolveCanonicalCheckId("AVD-KSV-0001")).toBe("KSV001")
    expect(resolveCanonicalCheckId("KSV001")).toBe("KSV001")
    expect(resolveCanonicalCheckId("KSV-0001")).toBe("KSV001")
    expect(resolveCanonicalCheckId("ksv1")).toBe("KSV001")
    expect(resolveCanonicalCheckId("AVD-KCV-0033")).toBe("KCV0033")
    expect(resolveCanonicalCheckId("KCV0033")).toBe("KCV0033")
    expect(resolveCanonicalCheckId("KCV-0033")).toBe("KCV0033")
    expect(resolveCanonicalCheckId("KSV0012")).toBe("KCV0012")
    expect(resolveCanonicalCheckId("AVD-KSV-0012")).toBe("KSV012")

    installRoutes(baseRoutes({
      [TRIVY + "configauditreports"]: {
        items: [
          makeReport({
            namespace: "dev",
            kind: "Deployment",
            name: "app",
            checks: [
              { checkID: "KSV-0001", success: true },
              { checkID: "AVD-KSV-0014", success: false },
            ],
          }),
        ],
      },
      [TRIVY + "infraassessmentreports"]: {
        items: [
          makeReport({
            namespace: "kube-system",
            kind: "Pod",
            name: "kube-controller-manager-narwhal-master-1",
            checks: [{ checkID: "KCV-0033", success: true }],
          }),
        ],
      },
      [TRIVY + "clustercompliancereports"]: {
        items: [{
          metadata: { name: "id-norm", creationTimestamp: "2026-11-25T00:00:00Z" },
          spec: {
            compliance: {
              id: "id-norm",
              title: "ID Normalisation",
              controls: [
                { id: "N1", name: "AVD form matches KSV-0001", severity: "HIGH", checks: [{ id: "AVD-KSV-0001" }] },
                { id: "N2", name: "Short form matches AVD-KSV-0014", severity: "HIGH", checks: [{ id: "KSV014" }] },
                { id: "N3", name: "AVD-KCV-0033 matches KCV-0033", severity: "HIGH", checks: [{ id: "AVD-KCV-0033" }] },
              ],
            },
          },
          status: { summary: { passCount: 0, failCount: 0 } },
        }],
      },
    }))

    const detail = await getComplianceFrameworkDetail("id-norm")
    const byId = new Map(detail!.controls.map((c) => [c.id, c]))
    expect(byId.get("N1")).toMatchObject({ evaluationState: "passed", testedByReport: true, passCount: 1 })
    expect(byId.get("N2")).toMatchObject({ evaluationState: "failed", failCount: 1 })
    expect(byId.get("N3")).toMatchObject({ evaluationState: "passed", testedByReport: true, passCount: 1 })
  })

  it("(f) a control with mapped checks but zero per-resource records is never tested-passed", async () => {
    installRoutes(baseRoutes({
      [TRIVY + "configauditreports"]: {
        items: [makeReport({ namespace: "dev", kind: "Deployment", name: "app", checks: [{ checkID: "KSV001", success: true }] })],
      },
      [TRIVY + "clusterinfraassessmentreports"]: {
        items: [makeReport({ kind: "Node", name: "narwhal-master-1", checks: [] })],
      },
      [TRIVY + "clustercompliancereports"]: {
        items: [{
          metadata: { name: "zero-rec", creationTimestamp: "2026-11-25T00:00:00Z" },
          spec: {
            compliance: {
              id: "zero-rec",
              title: "Zero Records",
              controls: [
                { id: "Z-TESTED", name: "Has explicit KSV001 pass", severity: "HIGH", checks: [{ id: "AVD-KSV-0001" }] },
                { id: "Z-NO-REC-CAR", name: "Mapped KSV014 with zero records", severity: "HIGH", checks: [{ id: "AVD-KSV-0014" }] },
                { id: "Z-NO-REC-INFRA", name: "Mapped KCV0048 with zero records", severity: "HIGH", checks: [{ id: "AVD-KCV-0048" }] },
              ],
            },
          },
          status: {
            summary: { passCount: 3, failCount: 0 },
            detailReport: {
              results: [
                { id: "Z-TESTED", name: "Has explicit KSV001 pass", severity: "HIGH", checks: [{ checkID: "", success: true }] },
                { id: "Z-NO-REC-CAR", name: "Mapped KSV014 with zero records", severity: "HIGH", checks: [{ checkID: "", success: true }] },
                { id: "Z-NO-REC-INFRA", name: "Mapped KCV0048 with zero records", severity: "HIGH", checks: [{ checkID: "", success: true }] },
              ],
            },
          },
        }],
      },
    }))

    const detail = await getComplianceFrameworkDetail("zero-rec")
    expect(detail!.reportTestedPassCount).toBe(1)
    expect(detail!.testedPassCount).toBe(1)
    expect(detail!.noFailureRecordedPassCount).toBe(1)
    expect(detail!.notEvaluatedCount).toBe(1)

    const byId = new Map(detail!.controls.map((c) => [c.id, c]))
    expect(byId.get("Z-TESTED")).toMatchObject({ evaluationState: "passed", testedByReport: true, passCount: 1 })
    expect(byId.get("Z-NO-REC-CAR")).toMatchObject({ evaluationState: "passed", passCount: 0 })
    expect(byId.get("Z-NO-REC-CAR")?.testedByReport).toBeUndefined()
    expect(byId.get("Z-NO-REC-INFRA")).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "infra_no_per_check_evidence" })
    expect(byId.get("Z-NO-REC-INFRA")?.testedByReport).toBeUndefined()
  })

  it("paginates per-resource report lists with limit/continue, restarts once on 410 Gone, and fails closed on truncation", async () => {
    const carCalls: Array<{ limit: string | null; cont: string | null }> = []
    let returned410 = false
    const routes = baseRoutes({
      [TRIVY + "clustercompliancereports"]: {
        items: [{
          metadata: { name: "paged-fw", creationTimestamp: "2026-11-25T00:00:00Z" },
          spec: {
            compliance: {
              id: "paged-fw",
              title: "Paged",
              controls: [
                { id: "P1", name: "Passed on page 1, failed on page 2", severity: "HIGH", checks: [{ id: "AVD-KSV-0001" }] },
                { id: "P2", name: "Passed on page 2 only", severity: "MEDIUM", checks: [{ id: "AVD-KSV-0014" }] },
              ],
            },
          },
          status: { summary: { passCount: 2, failCount: 0 } },
        }],
      },
    })

    fetchMock.mockImplementation(async (url: string) => {
      const parsed = new URL(url)
      const path = parsed.pathname
      const cont = parsed.searchParams.get("continue")
      if (path === TRIVY + "configauditreports") {
        carCalls.push({ limit: parsed.searchParams.get("limit"), cont })
        if (cont === "tok-page-2" && !returned410) {
          returned410 = true
          return { ok: false, status: 410, statusText: "Gone", path, cont }
        }
      }
      return { ok: true, status: 200, statusText: "OK", path, cont }
    })
    readMock.mockImplementation(async (res: { path: string; cont: string | null }) => {
      if (res.path === TRIVY + "configauditreports") {
        if (!res.cont) {
          return {
            metadata: { continue: "tok-page-2" },
            items: [makeReport({ namespace: "dev", kind: "Deployment", name: "app-1", checks: [{ checkID: "KSV001", success: true }] })],
          }
        }
        return {
          metadata: {},
          items: [makeReport({
            namespace: "dev",
            kind: "Deployment",
            name: "app-2",
            checks: [
              { checkID: "KSV001", success: false },
              { checkID: "KSV014", success: true },
            ],
          })],
        }
      }
      return routes[res.path] ?? { items: [] }
    })

    const detail = await getComplianceFrameworkDetail("paged-fw")
    expect(carCalls.every((c) => c.limit === "200")).toBe(true)
    expect(carCalls.map((c) => c.cont)).toEqual([null, "tok-page-2", null, "tok-page-2"])
    const byId = new Map(detail!.controls.map((c) => [c.id, c]))
    expect(byId.get("P1")).toMatchObject({ evaluationState: "failed", failCount: 1, passCount: 1 })
    expect(byId.get("P2")).toMatchObject({ evaluationState: "passed", testedByReport: true, passCount: 1 })

    // Truncated list (continue token never ends past maxPages) fails closed to coverage_unknown and skips cache.
    cache.clear()
    fetchMock.mockImplementation(async (url: string) => ({
      ok: true,
      status: 200,
      statusText: "OK",
      path: new URL(url).pathname,
    }))
    readMock.mockImplementation(async (res: { path: string }) => {
      if (res.path === TRIVY + "configauditreports") {
        return {
          metadata: { continue: "never-ends" },
          items: [makeReport({ namespace: "dev", kind: "Deployment", name: "app-1", checks: [{ checkID: "KSV001", success: true }] })],
        }
      }
      return routes[res.path] ?? { items: [] }
    })
    const truncated = await getComplianceFrameworkDetail("paged-fw")
    expect(truncated!.coverageUnknown).toBe(true)
    expect(truncated!.controls.every((c) => c.notEvaluatedReason === "coverage_unknown")).toBe(true)
    expect(cache.size).toBe(0)
  })
})
