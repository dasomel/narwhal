import { beforeEach, describe, expect, it, vi } from "vitest"
import cisRecordTrimmed from "./__fixtures__/cis-record-trimmed.json"
import snapshot from "./__fixtures__/compliance-snapshot.json"

vi.mock("server-only", () => ({}))
vi.mock("./config", () => ({ getK8sApiServer: () => "http://k8s.mock" }))
vi.mock("./k8s-token", () => ({ getK8sBearerToken: () => "mock-token" }))

const cache = new Map<string, unknown>()
vi.mock("./valkey", () => ({
  cacheGet: vi.fn(async (key: string) => cache.get(key) ?? null),
  cacheSet: vi.fn(async (key: string, value: unknown) => {
    cache.set(key, value)
  }),
}))

const fetchMock = vi.fn()
const readMock = vi.fn()
vi.mock("./http-client", () => ({
  fetchWithPolicy: (...args: unknown[]) => fetchMock(...args),
  readJsonWithPolicy: (...args: unknown[]) => readMock(...args),
}))

import { getComplianceFrameworkDetail, getComplianceFrameworks, getComplianceSummary } from "./compliance"
import { BENCHMARK_MAX_AGE_HOURS, MAX_BENCHMARK_RECORD_BYTES } from "./kube-bench"

const TRIVY_PREFIX = "/apis/aquasecurity.github.io/v1alpha1/"
const NODES_PATH = "/api/v1/nodes"
const OPERATOR_DEPLOY_PATH = "/apis/apps/v1/namespaces/security-system/deployments/trivy-operator"
const OPERATOR_CM_PATH = "/api/v1/namespaces/security-system/configmaps/trivy-operator-trivy-config"
const BENCHMARK_CM_PATH = "/api/v1/namespaces/security-system/configmaps"

type TrimmedRecord = typeof cisRecordTrimmed

function cloneTrimmedRecord(overrides: Partial<TrimmedRecord> = {}): TrimmedRecord {
  return {
    ...JSON.parse(JSON.stringify(cisRecordTrimmed)),
    finishedAt: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
    ...overrides,
  }
}

function makeConfigMap(name: string, recordObj: unknown, rawOverride?: string) {
  return {
    metadata: {
      name,
      namespace: "security-system",
      labels: { "narwhal.io/compliance-tool": "kube-bench" },
    },
    data: {
      "record.json": rawOverride ?? JSON.stringify(recordObj),
    },
  }
}

function makeCisCcr(controls: Array<{ id: string; name: string; severity: string; checks?: Array<{ id: string }> }>) {
  return {
    items: [
      {
        metadata: { name: "k8s-cis-1.23", creationTimestamp: "2026-11-24T06:00:00Z" },
        spec: {
          compliance: {
            id: "k8s-cis-1.23",
            title: "CIS Kubernetes Benchmarks v1.23",
            controls,
          },
        },
        status: {
          updateTimestamp: "2026-11-24T06:00:00Z",
          summary: { passCount: controls.length, failCount: 0 },
          detailReport: {
            results: controls.map((c) => ({
              id: c.id,
              name: c.name,
              severity: c.severity,
              checks: [{ checkID: "", success: true }],
            })),
          },
        },
      },
    ],
  }
}

function setupRoutes(opts: {
  nodes?: Array<{ name: string; controlPlane?: boolean }>
  controls?: Array<{ id: string; name: string; severity: string; checks?: Array<{ id: string }> }>
  benchmarkConfigMaps?: unknown[]
  benchmarkStatus?: number
  configAuditItems?: unknown[]
  rbacItems?: unknown[]
  infraItems?: unknown[]
}) {
  const nodes = opts.nodes ?? [{ name: "narwhal-master-1", controlPlane: true }]
  const controls = opts.controls ?? [
    { id: "1.1.1", name: "API server pod spec perms", severity: "HIGH", checks: [{ id: "AVD-KCV-0048" }] },
    { id: "1.1.12", name: "etcd data dir ownership", severity: "LOW", checks: [{ id: "AVD-KCV-0059" }] },
    { id: "1.3.6", name: "RotateKubeletServerCertificate", severity: "HIGH", checks: [{ id: "AVD-KCV-0036" }] },
    { id: "3.2.1", name: "Minimal audit policy", severity: "LOW" },
    { id: "4.1.1", name: "kubelet service perms", severity: "HIGH", checks: [{ id: "AVD-KCV-0069" }] },
    { id: "4.2.6", name: "protect-kernel-defaults", severity: "HIGH", checks: [{ id: "AVD-KCV-0084" }] },
    { id: "5.1.8", name: "Limit Bind/Impersonate/Escalate", severity: "HIGH", checks: [{ id: "AVD-KSV-0043" }] },
  ]

  const routes: Record<string, unknown> = {
    [NODES_PATH]: {
      items: nodes.map((n) => ({
        metadata: {
          name: n.name,
          labels: n.controlPlane ? { "node-role.kubernetes.io/control-plane": "" } : {},
        },
      })),
    },
    [OPERATOR_DEPLOY_PATH]: snapshot.trivyOperatorDeployment,
    [OPERATOR_CM_PATH]: {
      data: {
        "trivy.supportedConfigAuditKinds": "Workload,Service,Role,ClusterRole,RoleBinding,ClusterRoleBinding,NetworkPolicy",
      },
    },
    [TRIVY_PREFIX + "clustercompliancereports"]: makeCisCcr(controls),
    [TRIVY_PREFIX + "configauditreports"]: { items: opts.configAuditItems ?? [] },
    [TRIVY_PREFIX + "clusterconfigauditreports"]: { items: [] },
    [TRIVY_PREFIX + "rbacassessmentreports"]: { items: opts.rbacItems ?? [] },
    [TRIVY_PREFIX + "clusterrbacassessmentreports"]: { items: [] },
    [TRIVY_PREFIX + "infraassessmentreports"]: { items: opts.infraItems ?? [] },
    [TRIVY_PREFIX + "clusterinfraassessmentreports"]: { items: [] },
    [BENCHMARK_CM_PATH]: { items: opts.benchmarkConfigMaps ?? [] },
  }

  fetchMock.mockImplementation(async (url: string) => {
    const parsed = new URL(url)
    const path = parsed.pathname
    if (path === BENCHMARK_CM_PATH && opts.benchmarkStatus && opts.benchmarkStatus >= 400) {
      return { ok: false, status: opts.benchmarkStatus, statusText: "Forbidden", path }
    }
    return { ok: true, status: 200, statusText: "OK", path }
  })
  readMock.mockImplementation(async (res: { path: string }) => routes[res.path] ?? { items: [] })
}

beforeEach(() => {
  cache.clear()
  fetchMock.mockReset()
  readMock.mockReset()
})

describe("kube-bench CIS test records", () => {
  it("evaluates the trimmed live fixture: PASS becomes tested-passed unless Trivy failed (1.3.6), FAIL IDs fail, WARN stays manual, and ConfigAudit/RBAC stays no-failure-recorded", async () => {
    const trimmed = cloneTrimmedRecord()
    const fixtureControls = trimmed.results.map((r) => ({
      id: r.controlId,
      name: r.text,
      severity: "HIGH",
      checks:
        r.controlId === "3.1.1" || r.controlId === "3.2.1"
          ? undefined
          : r.controlId === "1.3.6"
            ? [{ id: "AVD-KCV-0036" }]
            : r.controlId === "5.1.1"
              ? [{ id: "AVD-KSV-0111" }]
              : r.controlId === "5.2.2"
                ? [{ id: "AVD-KSV-0017" }]
                : [{ id: "AVD-KCV-0048" }],
    }))
    // Add 5.1.8 (not in trimmed WARN list, Trivy RBAC clean) to verify separate no-failure-recorded counting.
    fixtureControls.push({
      id: "5.1.8",
      name: "Limit Bind/Impersonate/Escalate",
      severity: "HIGH",
      checks: [{ id: "AVD-KSV-0043" }],
    })

    setupRoutes({
      controls: fixtureControls,
      benchmarkConfigMaps: [makeConfigMap("cis-bench-abc-latest", trimmed)],
      infraItems: [
        {
          metadata: {
            namespace: "kube-system",
            name: "pod-kube-controller-manager-narwhal-master-1",
            labels: {
              "trivy-operator.resource.kind": "Pod",
              "trivy-operator.resource.name": "kube-controller-manager-narwhal-master-1",
            },
          },
          report: {
            summary: { criticalCount: 0, highCount: 1, mediumCount: 0, lowCount: 0 },
            checks: [{ checkID: "KCV0036", success: false, severity: "HIGH", title: "RotateKubeletServerCertificate" }],
          },
        },
      ],
      rbacItems: [
        {
          metadata: {
            namespace: "dev",
            name: "role-clean",
            labels: { "trivy-operator.resource.kind": "Role", "trivy-operator.resource.name": "clean" },
          },
          report: { summary: { criticalCount: 0, highCount: 0, mediumCount: 0, lowCount: 0 }, checks: [] },
        },
      ],
      configAuditItems: [
        {
          metadata: {
            namespace: "security-system",
            name: "daemonset-falco",
            labels: { "trivy-operator.resource.kind": "DaemonSet", "trivy-operator.resource.name": "falco" },
          },
          report: {
            summary: { criticalCount: 0, highCount: 1, mediumCount: 0, lowCount: 0 },
            checks: [{ checkID: "KSV017", success: false, severity: "HIGH", title: "Privileged" }],
          },
        },
      ],
    })

    const detail = await getComplianceFrameworkDetail("k8s-cis-1.23")
    expect(detail).not.toBeNull()
    const byId = new Map(detail!.controls.map((c) => [c.id, c]))

    // PASS controls in trimmed fixture (excluding 1.3.6 which Trivy failed): 8 controls become tested-passed
    for (const passId of ["1.1.1", "1.1.2", "1.2.2", "1.4.1", "2.1", "3.2.1", "4.1.1", "4.2.1"]) {
      expect(byId.get(passId)).toMatchObject({
        evaluationState: "passed",
        testedByBenchmark: true,
        passCount: 1,
        failCount: 0,
        benchmarkEvidence: {
          tool: "kube-bench",
          toolVersion: "0.16.0",
          benchmarkId: "cis-1.23",
          nodeCount: 1,
          verdict: "passed",
        },
      })
    }

    // 1.3.6: Trivy failed + kube-bench PASS -> MUST stay failed with sourceDisagreement naming both verdicts
    expect(byId.get("1.3.6")).toMatchObject({
      evaluationState: "failed",
      actionableFailCount: 1,
      accepted: false,
      benchmarkEvidence: {
        verdict: "passed",
        sourceDisagreement: {
          trivyVerdict: "failed",
          benchmarkVerdict: "passed",
        },
      },
    })

    // 5 FAIL IDs in trimmed fixture -> failed with actualValue/expectedResult excerpts
    for (const failId of ["1.1.12", "1.2.6", "1.3.7", "1.4.2", "4.2.6"]) {
      const ctrl = byId.get(failId)!
      expect(ctrl.evaluationState).toBe("failed")
      const accepted = ["1.1.12", "1.3.7", "1.4.2"].includes(failId)
      expect(ctrl.actionableFailCount).toBe(accepted ? 0 : 1)
      expect(ctrl.accepted).toBe(accepted)
      expect(ctrl.benchmarkEvidence?.verdict).toBe("failed")
      expect(ctrl.benchmarkEvidence?.failFindings?.[0].node).toBe("narwhal-master-1")
      expect(ctrl.benchmarkEvidence?.failFindings?.[0].expectedResult?.length).toBeGreaterThan(0)
    }

    // WARN / manual controls without Trivy failure stay not_evaluated with reason manual
    for (const warnId of ["1.1.9", "1.2.1", "3.1.1", "4.2.9", "5.1.1"]) {
      expect(byId.get(warnId)).toMatchObject({
        evaluationState: "not_evaluated",
        notEvaluatedReason: "manual",
      })
    }

    // Trivy accepted failure (5.2.2 falco) with kube-bench WARN stays failed (accepted)
    expect(byId.get("5.2.2")).toMatchObject({
      evaluationState: "failed",
      accepted: true,
      acceptedFailCount: 1,
    })

    // 5.1.8: Trivy RBAC passed with no failure recorded (not in kube-bench results -> wait: benchmark_missing unless kube-bench WARN)
    // Add 5.1.8 as WARN in a separate check via summary counts: 8 tested pass, 0 no-failure-recorded when 5.1.8 absent from results,
    // wait: 5.1.8 is Trivy passed, so case 3 keeps it passed with testedByBenchmark: false!
    expect(byId.get("5.1.8")).toMatchObject({
      evaluationState: "passed",
      testedByBenchmark: false,
    })
    expect(detail!.benchmarkTestedPassCount).toBe(8)
    expect(detail!.noFailureRecordedPassCount).toBe(1)
    expect(detail!.evaluatedPassCount).toBe(9)
    expect(detail!.benchmarkTestedFailCount).toBe(5)
  })

  it.each([
    ["1.3.6", "KCV0038", "kube-controller-manager"],
    ["1.3.7", "KCV0039", "kube-controller-manager"],
    ["1.4.2", "KCV0041", "kube-scheduler"],
  ])("accepts reviewed Trivy resource identity for %s while preserving failed evidence", async (control, check, component) => {
    const target = `${component}-narwhal-master-1`
    setupRoutes({
      controls: [{ id: control, name: control, severity: "HIGH", checks: [{ id: check }] }],
      benchmarkConfigMaps: [makeConfigMap("cis-bench-latest", cloneTrimmedRecord())],
      infraItems: [{
        metadata: { namespace: "kube-system", name: `pod-${target}`, labels: {
          "trivy-operator.resource.kind": "Pod", "trivy-operator.resource.name": target,
        } },
        report: { summary: { criticalCount: 0, highCount: 1, mediumCount: 0, lowCount: 0 },
          checks: [{ checkID: check, success: false, severity: "HIGH", title: control }] },
      }],
    })
    const detail = await getComplianceFrameworkDetail("k8s-cis-1.23")
    expect(detail!.controls[0]).toMatchObject({ evaluationState: "failed", accepted: true, actionableFailCount: 0 })
    expect(detail!.controls[0].benchmarkEvidence?.verdict).toBe(control === "1.3.6" ? "passed" : "failed")
  })

  it.each<{
    name: string
    mutate: (base: TrimmedRecord) => { obj?: unknown; raw?: string; nodes?: Array<{ name: string; controlPlane?: boolean }> }
    expectedReason: string
  }>([
    {
      name: `stale (${BENCHMARK_MAX_AGE_HOURS + 1}h) record`,
      mutate: (base) => ({
        obj: { ...base, finishedAt: new Date(Date.now() - (BENCHMARK_MAX_AGE_HOURS + 1) * 60 * 60 * 1000).toISOString() },
      }),
      expectedReason: "benchmark_stale",
    },
    {
      name: "future finishedAt timestamp",
      mutate: (base) => ({
        obj: { ...base, finishedAt: new Date(Date.now() + 10 * 60 * 1000).toISOString() },
      }),
      expectedReason: "benchmark_stale",
    },
    {
      name: "status error record",
      mutate: (base) => ({
        obj: { ...base, status: "error", reason: "kube-bench exited 1" },
      }),
      expectedReason: "benchmark_error",
    },
    {
      name: "wrong benchmarkId",
      mutate: (base) => ({
        obj: { ...base, benchmarkId: "cis-1.24" },
      }),
      expectedReason: "benchmark_malformed",
    },
    {
      name: "wrong schemaVersion",
      mutate: (base) => ({
        obj: { ...base, schemaVersion: 2 },
      }),
      expectedReason: "benchmark_malformed",
    },
    {
      name: "wrong tool",
      mutate: (base) => ({
        obj: { ...base, tool: "other-scanner" },
      }),
      expectedReason: "benchmark_malformed",
    },
    {
      name: "duplicate controlId",
      mutate: (base) => {
        const dup = base.results[0]
        const results = [...base.results, { ...dup }]
        return {
          obj: {
            ...base,
            totals: { ...base.totals, pass: base.totals.pass + 1 },
            results,
          },
        }
      },
      expectedReason: "benchmark_malformed",
    },
    {
      name: "totals inconsistent with results",
      mutate: (base) => ({
        obj: { ...base, totals: { ...base.totals, pass: base.totals.pass + 1 } },
      }),
      expectedReason: "benchmark_malformed",
    },
    {
      name: "empty results array",
      mutate: (base) => ({
        obj: { ...base, totals: { pass: 0, fail: 0, warn: 0, info: 0 }, results: [] },
      }),
      expectedReason: "benchmark_malformed",
    },
    {
      name: "unknown cluster node",
      mutate: (base) => ({
        obj: { ...base, node: "ghost-node" },
      }),
      expectedReason: "benchmark_malformed",
    },
    {
      name: "oversized JSON exceeding byte cap",
      mutate: (base) => ({
        raw: JSON.stringify({
          ...base,
          results: base.results.map((r, i) =>
            i === 0 ? { ...r, actualValue: "x".repeat(MAX_BENCHMARK_RECORD_BYTES + 1024) } : r,
          ),
        }),
      }),
      expectedReason: "benchmark_malformed",
    },
    {
      name: "malformed JSON string",
      mutate: () => ({
        raw: "{not valid json",
      }),
      expectedReason: "benchmark_malformed",
    },
  ])("fails closed (never passed) on $name", async ({ mutate, expectedReason }) => {
    const base = cloneTrimmedRecord()
    const { obj, raw, nodes } = mutate(base)
    setupRoutes({
      nodes,
      controls: [{ id: "1.1.1", name: "API server pod spec perms", severity: "HIGH", checks: [{ id: "AVD-KCV-0048" }] }],
      benchmarkConfigMaps: [makeConfigMap("cis-bench-test-latest", obj, raw)],
    })

    const detail = await getComplianceFrameworkDetail("k8s-cis-1.23")
    expect(detail).not.toBeNull()
    expect(detail!.evaluatedPassCount).toBe(0)
    expect(detail!.benchmarkTestedPassCount).toBe(0)
    expect(detail!.controls[0]).toMatchObject({
      id: "1.1.1",
      evaluationState: "not_evaluated",
      notEvaluatedReason: expectedReason,
    })
  })

  it("handles two nodes where one lacks a record: control-plane passes when worker lacks record, node-scoped 4.x stays not_evaluated; missing control-plane node fails both", async () => {
    const masterRec = cloneTrimmedRecord({ node: "master-1", nodeRoles: ["control-plane"] })
    setupRoutes({
      nodes: [
        { name: "master-1", controlPlane: true },
        { name: "worker-1", controlPlane: false },
      ],
      controls: [
        { id: "1.1.1", name: "CP file perms", severity: "HIGH", checks: [{ id: "AVD-KCV-0048" }] },
        { id: "2.1", name: "etcd cert/key", severity: "HIGH", checks: [{ id: "AVD-KCV-0042" }] },
        { id: "3.2.1", name: "audit policy", severity: "LOW" },
        { id: "4.1.1", name: "kubelet service perms", severity: "HIGH", checks: [{ id: "AVD-KCV-0069" }] },
      ],
      benchmarkConfigMaps: [makeConfigMap("cis-bench-m1-latest", masterRec)],
    })

    const detail1 = await getComplianceFrameworkDetail("k8s-cis-1.23")
    const byId1 = new Map(detail1!.controls.map((c) => [c.id, c]))
    expect(byId1.get("1.1.1")?.evaluationState).toBe("passed")
    expect(byId1.get("2.1")?.evaluationState).toBe("passed")
    expect(byId1.get("3.2.1")?.evaluationState).toBe("passed")
    expect(byId1.get("4.1.1")).toMatchObject({
      evaluationState: "not_evaluated",
      notEvaluatedReason: "benchmark_missing",
    })

    // Now two control-plane nodes where master-2 lacks a record -> 1.1.1, 2.1, 3.2.1, 4.1.1 all stay not_evaluated!
    cache.clear()
    setupRoutes({
      nodes: [
        { name: "master-1", controlPlane: true },
        { name: "master-2", controlPlane: true },
      ],
      controls: [
        { id: "1.1.1", name: "CP file perms", severity: "HIGH", checks: [{ id: "AVD-KCV-0048" }] },
        { id: "4.1.1", name: "kubelet service perms", severity: "HIGH", checks: [{ id: "AVD-KCV-0069" }] },
      ],
      benchmarkConfigMaps: [makeConfigMap("cis-bench-m1-latest", masterRec)],
    })
    const detail2 = await getComplianceFrameworkDetail("k8s-cis-1.23")
    for (const ctrl of detail2!.controls) {
      expect(ctrl).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "benchmark_missing" })
    }
  })

  it("handles two nodes disagreeing: PASS vs FAIL becomes failed; PASS vs WARN becomes manual; PASS vs INFO becomes benchmark_info", async () => {
    const rec1 = cloneTrimmedRecord({
      node: "master-1",
      nodeRoles: ["control-plane"],
      totals: { pass: 3, fail: 0, warn: 0, info: 0 },
      results: [
        { controlId: "1.1.1", section: "1.1", text: "c1", status: "PASS", scored: true, type: "automated", actualValue: "600", expectedResult: "644", reason: "" },
        { controlId: "4.1.1", section: "4.1", text: "c2", status: "PASS", scored: true, type: "automated", actualValue: "600", expectedResult: "644", reason: "" },
        { controlId: "4.2.1", section: "4.2", text: "c3", status: "PASS", scored: true, type: "automated", actualValue: "false", expectedResult: "false", reason: "" },
      ],
    })
    const rec2 = cloneTrimmedRecord({
      node: "worker-1",
      nodeRoles: ["worker"],
      totals: { pass: 0, fail: 1, warn: 1, info: 1 },
      results: [
        { controlId: "4.1.1", section: "4.1", text: "c2", status: "FAIL", scored: true, type: "automated", actualValue: "644", expectedResult: "600", reason: "" },
        { controlId: "4.2.1", section: "4.2", text: "c3", status: "WARN", scored: false, type: "manual", actualValue: "", expectedResult: "", reason: "manual" },
        { controlId: "4.2.2", section: "4.2", text: "c4", status: "INFO", scored: false, type: "automated", actualValue: "", expectedResult: "", reason: "info" },
      ],
    })
    // Add 4.2.2 PASS on master-1 so 4.2.2 disagrees PASS vs INFO
    rec1.results.push({ controlId: "4.2.2", section: "4.2", text: "c4", status: "PASS", scored: true, type: "automated", actualValue: "Webhook", expectedResult: "not AlwaysAllow", reason: "" })
    rec1.totals.pass = 4

    setupRoutes({
      nodes: [
        { name: "master-1", controlPlane: true },
        { name: "worker-1", controlPlane: false },
      ],
      controls: [
        { id: "1.1.1", name: "CP file perms", severity: "HIGH", checks: [{ id: "AVD-KCV-0048" }] },
        { id: "4.1.1", name: "kubelet service perms", severity: "HIGH", checks: [{ id: "AVD-KCV-0069" }] },
        { id: "4.2.1", name: "kubelet anonymous auth", severity: "HIGH", checks: [{ id: "AVD-KCV-0077" }] },
        { id: "4.2.2", name: "kubelet authz mode", severity: "HIGH", checks: [{ id: "AVD-KCV-0078" }] },
      ],
      benchmarkConfigMaps: [
        makeConfigMap("cis-bench-m1-latest", rec1),
        makeConfigMap("cis-bench-w1-latest", rec2),
      ],
    })

    const detail = await getComplianceFrameworkDetail("k8s-cis-1.23")
    const byId = new Map(detail!.controls.map((c) => [c.id, c]))
    expect(byId.get("1.1.1")?.evaluationState).toBe("passed")
    expect(byId.get("4.1.1")).toMatchObject({
      evaluationState: "failed",
      actionableFailCount: 1,
      benchmarkEvidence: {
        verdict: "failed",
        failFindings: [{ node: "worker-1", status: "FAIL", actualValue: "644", expectedResult: "600" }],
      },
    })
    expect(byId.get("4.2.1")).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "manual" })
    expect(byId.get("4.2.2")).toMatchObject({ evaluationState: "not_evaluated", notEvaluatedReason: "benchmark_info" })
  })

  it("handles ConfigMap list 403 by failing closed without caching while keeping Trivy failures failed", async () => {
    setupRoutes({
      benchmarkStatus: 403,
      controls: [
        { id: "1.1.1", name: "CP file perms", severity: "HIGH", checks: [{ id: "AVD-KCV-0048" }] },
        { id: "1.2.6", name: "kubelet CA", severity: "HIGH", checks: [{ id: "AVD-KCV-0006" }] },
      ],
      infraItems: [
        {
          metadata: {
            namespace: "kube-system",
            name: "pod-kube-apiserver-narwhal-master-1",
            labels: {
              "trivy-operator.resource.kind": "Pod",
              "trivy-operator.resource.name": "kube-apiserver-narwhal-master-1",
            },
          },
          report: {
            summary: { criticalCount: 0, highCount: 1, mediumCount: 0, lowCount: 0 },
            checks: [{ checkID: "KCV0006", success: false, severity: "HIGH", title: "kubelet CA" }],
          },
        },
      ],
    })

    const detail = await getComplianceFrameworkDetail("k8s-cis-1.23")
    expect(detail!.coverageUnknown).toBe(true)
    const byId = new Map(detail!.controls.map((c) => [c.id, c]))
    expect(byId.get("1.1.1")).toMatchObject({
      evaluationState: "not_evaluated",
      notEvaluatedReason: "benchmark_list_forbidden",
    })
    expect(byId.get("1.2.6")?.evaluationState).toBe("failed")
    expect(cache.size).toBe(0)

    const summary = await getComplianceSummary()
    expect(summary.partial).toBe(true)
    expect(summary.sources?.frameworks.partial).toBe(true)
    expect(summary.sources?.frameworks.partialSources).toContain("kube-bench-configmaps")
    expect(cache.size).toBe(0)

    const frameworks = await getComplianceFrameworks()
    expect(frameworks[0].coverageUnknown).toBe(true)
    expect(cache.size).toBe(0)
  })

  it("keeps control failed with sourceDisagreement when Trivy passed (no failure recorded) but kube-bench failed", async () => {
    const rec = cloneTrimmedRecord({
      totals: { pass: 0, fail: 1, warn: 0, info: 0 },
      results: [
        {
          controlId: "5.1.8",
          section: "5.1",
          text: "Limit Bind/Impersonate/Escalate",
          status: "FAIL",
          scored: true,
          type: "automated",
          actualValue: "found bind",
          expectedResult: "no bind",
          reason: "",
        },
      ],
    })
    setupRoutes({
      controls: [{ id: "5.1.8", name: "Limit Bind/Impersonate/Escalate", severity: "HIGH", checks: [{ id: "AVD-KSV-0043" }] }],
      rbacItems: [
        {
          metadata: {
            namespace: "dev",
            name: "role-clean",
            labels: { "trivy-operator.resource.kind": "Role", "trivy-operator.resource.name": "clean" },
          },
          report: { summary: { criticalCount: 0, highCount: 0, mediumCount: 0, lowCount: 0 }, checks: [] },
        },
      ],
      benchmarkConfigMaps: [makeConfigMap("cis-bench-latest", rec)],
    })

    const detail = await getComplianceFrameworkDetail("k8s-cis-1.23")
    expect(detail!.controls[0]).toMatchObject({
      id: "5.1.8",
      evaluationState: "failed",
      actionableFailCount: 1,
      benchmarkEvidence: {
        verdict: "failed",
        sourceDisagreement: {
          trivyVerdict: "passed",
          benchmarkVerdict: "failed",
        },
      },
    })
  })

  it("rejects duplicate kube-bench ConfigMaps for the same node as benchmark_malformed (never passed)", async () => {
    const recA = cloneTrimmedRecord({ node: "narwhal-master-1" })
    const recB = cloneTrimmedRecord({ node: "narwhal-master-1" })
    setupRoutes({
      nodes: [{ name: "narwhal-master-1", controlPlane: true }],
      controls: [
        { id: "1.1.1", name: "CP file perms", severity: "HIGH", checks: [{ id: "AVD-KCV-0048" }] },
        { id: "4.1.1", name: "kubelet service perms", severity: "HIGH", checks: [{ id: "AVD-KCV-0069" }] },
      ],
      benchmarkConfigMaps: [
        makeConfigMap("cis-bench-m1-a", recA),
        makeConfigMap("cis-bench-m1-b", recB),
      ],
    })

    const detail = await getComplianceFrameworkDetail("k8s-cis-1.23")
    expect(detail!.evaluatedPassCount).toBe(0)
    expect(detail!.benchmarkTestedPassCount).toBe(0)
    expect(detail!.notEvaluatedCount).toBe(2)
    for (const ctrl of detail!.controls) {
      expect(ctrl).toMatchObject({
        evaluationState: "not_evaluated",
        notEvaluatedReason: "benchmark_malformed",
      })
    }
  })

  it("derives summary counters from merged controls even when CCR detailReport.results is absent (1.1.12 FAIL)", async () => {
    const rec = cloneTrimmedRecord({
      totals: { pass: 0, fail: 1, warn: 0, info: 0 },
      results: [
        {
          controlId: "1.1.12",
          section: "1.1",
          text: "etcd data dir ownership",
          status: "FAIL",
          scored: true,
          type: "automated",
          actualValue: "root:root",
          expectedResult: "etcd:etcd",
          reason: "",
        },
      ],
    })
    setupRoutes({
      controls: [
        { id: "1.1.12", name: "etcd data dir ownership", severity: "LOW", checks: [{ id: "AVD-KCV-0059" }] },
      ],
      benchmarkConfigMaps: [makeConfigMap("cis-bench-latest", rec)],
    })
    // Strip status.detailReport from CCR so only status.summary (failCount=0) + spec.controls exist.
    const ccrPath = TRIVY_PREFIX + "clustercompliancereports"
    const origRead = readMock.getMockImplementation()!
    readMock.mockImplementation(async (res: { path: string }) => {
      if (res.path === ccrPath) {
        return {
          items: [
            {
              metadata: { name: "k8s-cis-1.23", creationTimestamp: "2026-11-24T06:00:00Z" },
              spec: {
                compliance: {
                  id: "k8s-cis-1.23",
                  title: "CIS Kubernetes Benchmarks v1.23",
                  controls: [
                    { id: "1.1.12", name: "etcd data dir ownership", severity: "LOW", checks: [{ id: "AVD-KCV-0059" }] },
                  ],
                },
              },
              status: { updateTimestamp: "2026-11-24T06:00:00Z", summary: { passCount: 1, failCount: 0 } },
            },
          ],
        }
      }
      return origRead(res)
    })

    const detail = await getComplianceFrameworkDetail("k8s-cis-1.23")
    const frameworks = await getComplianceFrameworks()
    const summary = await getComplianceSummary()
    for (const fw of [detail!, frameworks[0], summary.frameworks[0]]) {
      expect(fw).toMatchObject({
        totalControls: 1,
        failCount: 1,
        actionableFailCount: 0,
        acceptedFailCount: 1,
        benchmarkTestedFailCount: 1,
        benchmarkTestedPassCount: 0,
        noFailureRecordedPassCount: 0,
        evaluatedPassCount: 0,
        notEvaluatedCount: 0,
      })
    }
    expect(detail!.controls[0]).toMatchObject({
      id: "1.1.12",
      evaluationState: "failed",
      actionableFailCount: 0,
      testedByBenchmark: true,
    })
  })

  it("maintains counter partition invariant across real fixture and randomized Trivy/benchmark combinations", async () => {
    const recount = (detail: NonNullable<Awaited<ReturnType<typeof getComplianceFrameworkDetail>>>) => {
      const c = detail.controls
      const passedByTest = c.filter((x) => x.evaluationState === "passed" && x.testedByBenchmark === true).length
      const noFailureRecorded = c.filter((x) => x.evaluationState === "passed" && x.testedByBenchmark !== true).length
      const failedActionable = c.filter((x) => (x.actionableFailCount ?? 0) > 0).length
      const failedAccepted = c.filter((x) => x.accepted === true).length
      const notEvaluated = c.filter((x) => x.evaluationState === "not_evaluated").length
      const benchFailed = c.filter((x) => x.evaluationState === "failed" && x.benchmarkEvidence?.verdict === "failed").length
      return { passedByTest, noFailureRecorded, failedActionable, failedAccepted, notEvaluated, benchFailed }
    }

    const assertMatchesDetail = (
      fw: NonNullable<Awaited<ReturnType<typeof getComplianceFrameworks>>>[number],
      detail: NonNullable<Awaited<ReturnType<typeof getComplianceFrameworkDetail>>>,
    ) => {
      const r = recount(detail)
      expect(r.passedByTest + r.noFailureRecorded + r.failedActionable + r.failedAccepted + r.notEvaluated).toBe(fw.totalControls)
      expect(fw.benchmarkTestedPassCount).toBe(r.passedByTest)
      expect(fw.noFailureRecordedPassCount).toBe(r.noFailureRecorded)
      expect(fw.evaluatedPassCount).toBe(r.passedByTest + r.noFailureRecorded)
      expect(fw.actionableFailCount).toBe(r.failedActionable)
      expect(fw.acceptedFailCount).toBe(r.failedAccepted)
      expect(fw.notEvaluatedCount).toBe(r.notEvaluated)
      expect(fw.benchmarkTestedFailCount).toBe(r.benchFailed)
      expect(fw.failCount).toBe(r.failedActionable + r.failedAccepted)
    }

    // 1. Real trimmed fixture (both with and without detailReport.results)
    const trimmed = cloneTrimmedRecord()
    const trimmedControls = trimmed.results.map((r) => ({
      id: r.controlId,
      name: r.text,
      severity: "HIGH",
      checks:
        r.controlId === "3.1.1" || r.controlId === "3.2.1"
          ? undefined
          : r.controlId === "1.3.6"
            ? [{ id: "AVD-KCV-0036" }]
            : r.controlId === "5.2.2"
              ? [{ id: "AVD-KSV-0017" }]
              : [{ id: "AVD-KCV-0048" }],
    }))
    trimmedControls.push({ id: "5.1.8", name: "RBAC clean", severity: "HIGH", checks: [{ id: "AVD-KSV-0043" }] })
    setupRoutes({
      controls: trimmedControls,
      benchmarkConfigMaps: [makeConfigMap("cis-bench-trimmed", trimmed)],
      rbacItems: [{
        metadata: { namespace: "dev", name: "role-clean", labels: { "trivy-operator.resource.kind": "Role", "trivy-operator.resource.name": "clean" } },
        report: { summary: { criticalCount: 0, highCount: 0, mediumCount: 0, lowCount: 0 }, checks: [] },
      }],
      configAuditItems: [{
        metadata: { namespace: "security-system", name: "daemonset-falco", labels: { "trivy-operator.resource.kind": "DaemonSet", "trivy-operator.resource.name": "falco" } },
        report: { summary: { criticalCount: 0, highCount: 1, mediumCount: 0, lowCount: 0 }, checks: [{ checkID: "KSV017", success: false, severity: "HIGH", title: "Privileged" }] },
      }],
    })
    assertMatchesDetail((await getComplianceFrameworks())[0], (await getComplianceFrameworkDetail("k8s-cis-1.23"))!)

    // 2. Deterministic seeded randomized combinations of Trivy + kube-bench states (with & without detailReport)
    let seed = 42
    const nextRand = () => {
      seed = (seed * 1664525 + 1013904223) >>> 0
      return seed / 0x100000000
    }
    for (let trial = 0; trial < 20; trial++) {
      cache.clear()
      const specControls = [
        { id: "1.1.1", name: "C1", severity: "HIGH", checks: [{ id: "AVD-KCV-0048" }] },
        { id: "1.3.6", name: "C2", severity: "HIGH", checks: [{ id: "AVD-KCV-0036" }] },
        { id: "4.1.1", name: "C3", severity: "HIGH", checks: [{ id: "AVD-KCV-0069" }] },
        { id: "5.1.8", name: "C4", severity: "HIGH", checks: [{ id: "AVD-KSV-0043" }] },
        { id: "5.2.2", name: "C5", severity: "HIGH", checks: [{ id: "AVD-KSV-0017" }] },
        { id: "5.7.1", name: "C6", severity: "MEDIUM", checks: [{ id: "AVD-KSV-0014" }] },
      ]
      const statuses = ["PASS", "FAIL", "WARN", "INFO", "OMIT"] as const
      const kbResults = specControls
        .map((c) => {
          const st = statuses[Math.floor(nextRand() * statuses.length)]
          if (st === "OMIT") return null
          return {
            controlId: c.id,
            section: c.id.split(".").slice(0, 2).join("."),
            text: c.name,
            status: st,
            scored: st !== "WARN",
            type: (st === "WARN" ? "manual" : "automated") as "manual" | "automated",
            actualValue: st === "FAIL" ? "bad" : "ok",
            expectedResult: "ok",
            reason: "",
          }
        })
        .filter((x): x is NonNullable<typeof x> => x !== null)
      const totals = {
        pass: kbResults.filter((r) => r.status === "PASS").length,
        fail: kbResults.filter((r) => r.status === "FAIL").length,
        warn: kbResults.filter((r) => r.status === "WARN").length,
        info: kbResults.filter((r) => r.status === "INFO").length,
      }
      const configAuditItems: unknown[] = []
      if (nextRand() < 0.5) {
        configAuditItems.push({
          metadata: { namespace: "security-system", name: "daemonset-falco", labels: { "trivy-operator.resource.kind": "DaemonSet", "trivy-operator.resource.name": "falco" } },
          report: { summary: { criticalCount: 0, highCount: 1, mediumCount: 0, lowCount: 0 }, checks: [{ checkID: "KSV017", success: false, severity: "HIGH", title: "Privileged" }] },
        })
      }
      if (nextRand() < 0.5) {
        configAuditItems.push({
          metadata: { namespace: "dev", name: "deployment-app", labels: { "trivy-operator.resource.kind": "Deployment", "trivy-operator.resource.name": "app" } },
          report: nextRand() < 0.5
            ? { summary: { criticalCount: 0, highCount: 1, mediumCount: 0, lowCount: 0 }, checks: [{ checkID: "KSV014", success: false, severity: "HIGH", title: "RO" }] }
            : { summary: { criticalCount: 0, highCount: 0, mediumCount: 0, lowCount: 0 }, checks: [] },
        })
      }
      const rbacItems = nextRand() < 0.5 ? [{
        metadata: { namespace: "dev", name: "role-clean", labels: { "trivy-operator.resource.kind": "Role", "trivy-operator.resource.name": "clean" } },
        report: { summary: { criticalCount: 0, highCount: 0, mediumCount: 0, lowCount: 0 }, checks: [] },
      }] : []
      const infraItems = nextRand() < 0.5 ? [{
        metadata: { namespace: "kube-system", name: "pod-cm", labels: { "trivy-operator.resource.kind": "Pod", "trivy-operator.resource.name": "cm" } },
        report: { summary: { criticalCount: 0, highCount: 1, mediumCount: 0, lowCount: 0 }, checks: [{ checkID: "KCV0036", success: false, severity: "HIGH", title: "Rotate" }] },
      }] : []

      setupRoutes({
        controls: specControls,
        configAuditItems,
        rbacItems,
        infraItems,
        benchmarkConfigMaps: kbResults.length > 0
          ? [makeConfigMap(`cis-bench-t${trial}`, cloneTrimmedRecord({ totals, results: kbResults }))]
          : [],
      })
      const omitDetail = trial % 2 === 1
      if (omitDetail) {
        const ccrPath = TRIVY_PREFIX + "clustercompliancereports"
        const orig = readMock.getMockImplementation()!
        readMock.mockImplementation(async (res: { path: string }) => {
          if (res.path === ccrPath) {
            return {
              items: [{
                metadata: { name: "k8s-cis-1.23", creationTimestamp: "2026-11-24T06:00:00Z" },
                spec: { compliance: { id: "k8s-cis-1.23", title: "CIS", controls: specControls } },
                status: { updateTimestamp: "2026-11-24T06:00:00Z", summary: { passCount: specControls.length, failCount: 0 } },
              }],
            }
          }
          return orig(res)
        })
      }

      const fwList = await getComplianceFrameworks()
      const fwDetail = await getComplianceFrameworkDetail("k8s-cis-1.23")
      assertMatchesDetail(fwList[0], fwDetail!)
    }
  })
})
