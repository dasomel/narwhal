import { beforeEach, describe, expect, it, vi } from "vitest"
import type { ComplianceFramework, ConfigAuditRow } from "@/types/compliance"

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

vi.mock("./trivy", () => ({
  getSecuritySummary: vi.fn(async () => ({ totals: { Critical: 0, High: 0, Medium: 0, Low: 0 } })),
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
}))

import { getKisaControls } from "./kisa"

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
