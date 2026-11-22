import fs from "node:fs"
import path from "node:path"
import { describe, expect, it } from "vitest"
import { en, ko } from "./i18n"
import {
  ACCEPTED_CONFIG_AUDIT_RULES,
  ACCEPTED_INFRA_RULES,
  ACCEPTED_RBAC_RULES,
  findUnusedAcceptanceRules,
  matchConfigAuditException,
  matchInfraException,
  matchRbacException,
  normalizeCheckId,
  normalizeWorkloadName,
} from "./compliance-accepted"

interface ParsedDocRow {
  namespace: string
  workload: string
  checks: string[]
  cls: string
  reason: string
}

function parseMarkdownSectionTable(markdown: string, heading: string): ParsedDocRow[] {
  const lines = markdown.split("\n")
  const startIdx = lines.findIndex((line) => line.trim() === heading)
  if (startIdx === -1) {
    throw new Error(`Missing heading "${heading}" in docs/common/compliance-hardening.md`)
  }
  const rows: ParsedDocRow[] = []
  let inTable = false
  for (let i = startIdx + 1; i < lines.length; i++) {
    const trimmed = lines[i].trim()
    if (trimmed.startsWith("#")) break
    if (!trimmed.startsWith("|")) {
      if (inTable && trimmed.length === 0) break
      continue
    }
    inTable = true
    const cells = trimmed
      .slice(1, -1)
      .split("|")
      .map((c) => c.trim())
    if (cells.length !== 5) {
      throw new Error(`Expected 5 columns under "${heading}", got ${cells.length}: ${trimmed}`)
    }
    const [namespace, workload, checksRaw, cls, reason] = cells
    if (namespace === "namespace" || /^-+$/.test(namespace)) continue
    rows.push({
      namespace,
      workload,
      checks: checksRaw
        .split(",")
        .map((c) => normalizeCheckId(c))
        .filter(Boolean)
        .sort(),
      cls,
      reason,
    })
  }
  return rows
}

describe("normalizeCheckId", () => {
  it("normalizes AVD-KSV and short KSV IDs to canonical KSVnnn", () => {
    expect(normalizeCheckId("KSV017")).toBe("KSV017")
    expect(normalizeCheckId("AVD-KSV-0017")).toBe("KSV017")
    expect(normalizeCheckId("avd-ksv-0108")).toBe("KSV108")
    expect(normalizeCheckId("ksv17")).toBe("KSV017")
    expect(normalizeCheckId(" KSV001 ")).toBe("KSV001")
  })

  it("normalizes AVD-KCV and short KCV IDs to canonical KCVnnnn", () => {
    expect(normalizeCheckId("KCV0056")).toBe("KCV0056")
    expect(normalizeCheckId("AVD-KCV-0056")).toBe("KCV0056")
    expect(normalizeCheckId("kcv56")).toBe("KCV0056")
    expect(normalizeCheckId(" KCV0059 ")).toBe("KCV0059")
  })

  it.each([
    ["AVD-KSV-0001", "KSV001"],
    ["avd-ksv-17", "KSV017"],
    ["KSV0000", "KSV000"],
    ["KSV-0017", "KSV-0017"],
    ["AVD-KSV0017", "AVD-KSV0017"],
    ["AVD-KCV-00059", "KCV0059"],
    ["avd-kcv-9", "KCV0009"],
    ["KCV00000", "KCV0000"],
    ["KCV-0056", "KCV-0056"],
    ["AVD-KCV0056", "AVD-KCV0056"],
    ["avd-dsv-0001", "AVD-DSV-0001"],
    [" khv12 ", "KHV12"],
  ])("normalizes %s -> %s", (input, expected) => {
    expect(normalizeCheckId(input)).toBe(expected)
  })
})

describe("normalizeWorkloadName", () => {
  it("strips Kubernetes pod-template-hash suffixes only for ReplicaSet", () => {
    expect(normalizeWorkloadName("ReplicaSet", "narwhal-portal-74b459c658")).toBe("narwhal-portal")
    expect(normalizeWorkloadName("ReplicaSet", "harbor-core-b569bd8dd")).toBe("harbor-core")
    expect(normalizeWorkloadName("ReplicaSet", "prometheus-stack-kube-prom-operator-95f77896")).toBe(
      "prometheus-stack-kube-prom-operator",
    )
    expect(normalizeWorkloadName("ReplicaSet", "cert-manager-cainjector-7b66d49b8")).toBe(
      "cert-manager-cainjector",
    )
    expect(normalizeWorkloadName("ReplicaSet", "harbor-core-bcdfg")).toBe("harbor-core")
    expect(normalizeWorkloadName("ReplicaSet", "cert-manager-cainjector")).toBe("cert-manager-cainjector")
    expect(normalizeWorkloadName("ReplicaSet", "apisix-ingress-controller")).toBe("apisix-ingress-controller")
    expect(normalizeWorkloadName("DaemonSet", "metallb-speaker")).toBe("metallb-speaker")
    expect(normalizeWorkloadName("DaemonSet", "harbor-core-b569bd8dd")).toBe("harbor-core-b569bd8dd")
    expect(normalizeWorkloadName("Deployment", "harbor-core-b569bd8dd")).toBe("harbor-core-b569bd8dd")
    expect(normalizeWorkloadName("StatefulSet", "seaweedfs-volume")).toBe("seaweedfs-volume")
  })

  it("does not strip standalone-looking suffixes containing vowels or 0/1/3 or wrong length", () => {
    expect(normalizeWorkloadName("ReplicaSet", "harbor-core-abc12345")).toBe("harbor-core-abc12345")
    expect(normalizeWorkloadName("ReplicaSet", "harbor-core-12345678")).toBe("harbor-core-12345678")
    expect(normalizeWorkloadName("ReplicaSet", "harbor-core-00000000")).toBe("harbor-core-00000000")
    expect(normalizeWorkloadName("ReplicaSet", "harbor-core-abcd")).toBe("harbor-core-abcd")
    expect(normalizeWorkloadName("ReplicaSet", "harbor-core-bcdfghjklmn")).toBe("harbor-core-bcdfghjklmn")
  })

  it("uses Deployment ownerReferences when known and keeps alphabet fallback only when unknown", () => {
    expect(normalizeWorkloadName("ReplicaSet", "harbor-core-b569bd8dd", ["harbor-core"])).toBe("harbor-core")
    expect(normalizeWorkloadName("ReplicaSet", "harbor-core-b569bd8dd", [])).toBe("harbor-core-b569bd8dd")
    expect(normalizeWorkloadName("ReplicaSet", "harbor-core-b569bd8dd", ["other-deploy"])).toBe("harbor-core-b569bd8dd")
    // Must not broaden acceptance to non-SafeEncodeString suffixes even when Deployment owner matches
    expect(normalizeWorkloadName("ReplicaSet", "harbor-core-abc12345", ["harbor-core"])).toBe("harbor-core-abc12345")
    // Ownership unknown (undefined) keeps alphabet fallback
    expect(normalizeWorkloadName("ReplicaSet", "harbor-core-b569bd8dd", undefined)).toBe("harbor-core")
  })
})

describe("exact acceptance (D-P2-1/2/3)", () => {
  it("accepts reviewed exact tuples and returns their audit metadata", () => {
    const match = matchConfigAuditException("devtools", "ReplicaSet", "narwhal-portal-74b459c658", "AVD-KSV-0020", ["narwhal-portal"])
    expect(match).toMatchObject({ ruleId: "cfg:devtools:ReplicaSet:narwhal-portal:accept", class: "accept", reviewedAt: "2026-11-23" })
    expect(match?.owner).toContain("portal/Dockerfile")
    expect(match?.reason).toContain("1001")
    expect(matchConfigAuditException("security-system", "DaemonSet", "falco", "KSV017")?.reason).toContain("privileged")
  })

  it("rejects same-name wrong-kind/namespace, lookalikes, unknown checks and missing ownership", () => {
    for (const [ns, kind, name, check, owners] of [
      ["devtools", "Pod", "narwhal-portal", "KSV020", []],
      ["dev", "ReplicaSet", "narwhal-portal-74b459c658", "KSV020", ["narwhal-portal"]],
      ["devtools", "ReplicaSet", "narwhal-portal-evil-74b459c658", "KSV020", ["narwhal-portal-evil"]],
      ["devtools", "ReplicaSet", "narwhal-portal-74b459c658", "KSV999", ["narwhal-portal"]],
      ["devtools", "ReplicaSet", "narwhal-portal-74b459c658", "KSV020", []],
      ["devtools", "ReplicaSet", "narwhal-portal-74b459c658", "KSV020", undefined],
      ["kube-system", "DaemonSet", "cilium-evil", "KSV017", []],
      ["kube-system", "Pod", "unlisted-system-pod", "KSV017", []],
    ] as const) expect(matchConfigAuditException(ns, kind, name, check, owners)).toBeNull()
  })

  it("leaves audited false and unverifiable reasons actionable", () => {
    for (const [ns, kind, name, check, owners] of [
      ["devtools", "ReplicaSet", "harbor-core-95b9f6d79", "KSV014", ["harbor-core"]],
      ["storage", "ReplicaSet", "velero-dbc67d57", "KSV012", ["velero"]],
      ["platform-system", "ReplicaSet", "apisix-ingress-controller-7c95479b49", "KSV020", ["apisix-ingress-controller"]],
      ["platform-system", "ReplicaSet", "apisix-ingress-controller-7c95479b49", "KSV021", ["apisix-ingress-controller"]],
      ["monitoring", "DaemonSet", "k8s-monitoring-alloy-logs", "KSV118", []],
      ["platform-system", "DaemonSet", "metallb-speaker", "KSV119", []],
      ["istio-system", "DaemonSet", "ztunnel", "KSV030", []],
      ["storage", "StatefulSet", "seaweedfs-volume", "KSV012", []],
    ] as const) expect(matchConfigAuditException(ns, kind, name, check, owners)).toBeNull()
    expect(matchConfigAuditException("storage", "ReplicaSet", "velero-dbc67d57", "KSV020", ["velero"])?.reason).toContain("1002")
  })

  it("accepts verified host integration and bootstrap defaults without extending to lookalikes", () => {
    expect(matchConfigAuditException("istio-system", "DaemonSet", "ztunnel", "KSV005", [])?.reason).toContain("network namespaces")
    expect(matchConfigAuditException("kube-system", "DaemonSet", "cilium", "KSV017", [])?.owner).toContain("1.19.4")
    expect(matchConfigAuditException("kube-system", "DaemonSet", "cilium", "KSV014", [])).toBeNull()
    expect(matchRbacException("system:controller:deployment-controller", "KSV048", "", "ClusterRole")?.reason).toContain("reconciled by kube-apiserver")
    expect(matchRbacException("system:controller:deployment-controller-evil", "KSV048", "", "ClusterRole")).toBeNull()
    expect(matchRbacException("system:controller:deployment-controller", "KSV041", "", "ClusterRole")).toBeNull()
    expect(matchRbacException("cert-manager-controller-certificates", "KSV041", "", "ClusterRole")?.reason).toContain("issued certificates")
    expect(matchRbacException("cert-manager-controller-certificates", "KSV048", "", "ClusterRole")).toBeNull()
  })

  it("matches RBAC by exact scope/namespace/kind/name AND explicit checks", () => {
    expect(matchRbacException("platform-admin", "KSV041", "", "ClusterRole")?.ruleId).toBe("rbac:cluster:ClusterRole:platform-admin:accept")
    expect(matchRbacException("narwhal-portal-jobs", "KSV048", "devtools", "Role")).not.toBeNull()
    for (const [name, check, ns, kind] of [
      ["platform-admin", "KSV042", "", "ClusterRole"],
      ["platform-admin-extra", "KSV041", "", "ClusterRole"],
      ["platform-admin", "KSV041", "devtools", "ClusterRole"],
      ["platform-admin", "KSV041", "devtools", "Role"],
      ["narwhal-portal-jobs", "KSV048", "prod", "Role"],
      ["narwhal-portal-jobs", "KSV048", "devtools", "Unknown"],
      ["narwhal-portal-jobs", "KSV048", "devtools", "ClusterRole"],
      ["developer-workload-admin", "KSV048", "", "ClusterRole"],
      ["cilium-evil", "KSV041", "", "ClusterRole"],
      ["velero-server-evil", "KSV041", "", "ClusterRole"],
      ["system:discovery", "KSV041", "", "ClusterRole"],
    ]) expect(matchRbacException(name, check, ns, kind)).toBeNull()
    expect(matchRbacException("platform-admin", "KSV041")).toBeNull()
  })
})

describe("findUnusedAcceptanceRules (D-P2-4)", () => {
  it("reports unmatched rules only for fulfilled domains and omits matched rules", () => {
    const matched = new Set<string>([
      ACCEPTED_CONFIG_AUDIT_RULES[0].id,
      ACCEPTED_RBAC_RULES[0].id,
    ])
    const allEval = findUnusedAcceptanceRules(matched, { configAudit: true, rbac: true, infra: true })
    expect(allEval.some((r) => r.id === ACCEPTED_CONFIG_AUDIT_RULES[0].id)).toBe(false)
    expect(allEval.some((r) => r.id === ACCEPTED_RBAC_RULES[0].id)).toBe(false)
    expect(allEval.length).toBe(ACCEPTED_CONFIG_AUDIT_RULES.length - 1 + ACCEPTED_RBAC_RULES.length - 1)

    // When configAudit source is unavailable, do not falsely mark configAudit rules as unused
    const rbacOnly = findUnusedAcceptanceRules(matched, { configAudit: false, rbac: true, infra: true })
    expect(rbacOnly.every((r) => r.domain === "rbac")).toBe(true)
    expect(rbacOnly.length).toBe(ACCEPTED_RBAC_RULES.length - 1)
  })
})

describe("matchInfraException", () => {
  it("keeps ACCEPTED_INFRA_RULES empty and fails closed (KCV0056/KCV0057/KCV0059 stay actionable)", () => {
    expect(ACCEPTED_INFRA_RULES).toEqual([])
    expect(matchInfraException("narwhal-master-1", "KCV0056")).toBeNull()
    expect(matchInfraException("narwhal-master-2", "AVD-KCV-0056")).toBeNull()
    expect(matchInfraException("narwhal-master-1", "KCV0059")).toBeNull()
    expect(matchInfraException("narwhal-master-1", "KCV0057")).toBeNull()
    expect(matchInfraException("", "KCV0056")).toBeNull()
    expect(matchInfraException("narwhal-master-1", "")).toBeNull()
  })
})

describe("allowlist integrity and docs parity", () => {
  it("contains no duplicate (namespace, kind, name, class) or overlapping check IDs", () => {
    const seenClass = new Set<string>()
    const seenChecks = new Set<string>()
    for (const rule of ACCEPTED_CONFIG_AUDIT_RULES) {
      const classKey = `${rule.namespace}|${rule.kind}|${rule.name}|${rule.class}`
      expect(seenClass.has(classKey), `Duplicate class entry: ${classKey}`).toBe(false)
      seenClass.add(classKey)
      for (const id of rule.checks) {
        const checkKey = `${rule.namespace}|${rule.kind}|${rule.name}|${id}`
        expect(seenChecks.has(checkKey), `Overlapping check entry: ${checkKey}`).toBe(false)
        seenChecks.add(checkKey)
      }
      expect(rule.reason).not.toContain("|")
      expect(rule.reason.trim().length).toBeGreaterThan(0)
    }

    const seenRbacClass = new Set<string>()
    const seenRbacChecks = new Set<string>()
    for (const rule of ACCEPTED_RBAC_RULES) {
      if (rule.kind === "ClusterRole") expect(rule.namespace).toBe("")
      else expect(rule.namespace.length).toBeGreaterThan(0)
      const classKey = `${rule.namespace}|${rule.kind}|${rule.roleName}|${rule.class}`
      expect(seenRbacClass.has(classKey), `Duplicate RBAC class entry: ${classKey}`).toBe(false)
      seenRbacClass.add(classKey)
      for (const id of rule.checks) {
        const checkKey = `${rule.namespace}|${rule.kind}|${rule.roleName}|${id}`
        expect(seenRbacChecks.has(checkKey), `Overlapping RBAC check entry: ${checkKey}`).toBe(false)
        seenRbacChecks.add(checkKey)
      }
      expect(rule.reason).not.toContain("|")
      expect(rule.reason.trim().length).toBeGreaterThan(0)
    }

    const seenInfraClass = new Set<string>()
    const seenInfraChecks = new Set<string>()
    for (const rule of ACCEPTED_INFRA_RULES) {
      const classKey = `${rule.node}|${rule.class}`
      expect(seenInfraClass.has(classKey), `Duplicate Infra class entry: ${classKey}`).toBe(false)
      seenInfraClass.add(classKey)
      for (const id of rule.checks) {
        const checkKey = `${rule.node}|${id}`
        expect(seenInfraChecks.has(checkKey), `Overlapping Infra check entry: ${checkKey}`).toBe(false)
        seenInfraChecks.add(checkKey)
      }
      expect(rule.reason).not.toContain("|")
      expect(rule.reason.trim().length).toBeGreaterThan(0)
    }
  })

  it("matches docs/common/compliance-hardening.md Config Audit and RBAC allowlist tables exactly", () => {
    const docPath = path.resolve(__dirname, "../../../docs/common/compliance-hardening.md")
    const markdown = fs.readFileSync(docPath, "utf8")

    const docConfigRows = parseMarkdownSectionTable(markdown, "### Config Audit allowlist")
    const codeConfigRows: ParsedDocRow[] = ACCEPTED_CONFIG_AUDIT_RULES.map((r) => ({
      namespace: r.namespace,
      workload: `${r.kind}/${r.name}`,
      checks: Array.from(r.checks).map(normalizeCheckId).sort(),
      cls: r.class,
      reason: r.reason,
    }))
    expect(docConfigRows).toEqual(codeConfigRows)

    const docRbacRows = parseMarkdownSectionTable(markdown, "### RBAC allowlist")
    const codeRbacRows: ParsedDocRow[] = ACCEPTED_RBAC_RULES.map((r) => ({
      namespace: r.namespace || "cluster",
      workload: `${r.kind}/${r.roleName}`,
      checks: Array.from(r.checks).map(normalizeCheckId).sort(),
      cls: r.class,
      reason: r.reason,
    }))
    expect(docRbacRows).toEqual(codeRbacRows)

    const docInfraRows = parseMarkdownSectionTable(markdown, "### Infra allowlist")
    const codeInfraRows: ParsedDocRow[] = ACCEPTED_INFRA_RULES.map((r) => ({
      namespace: "cluster",
      workload: `Node/${r.node}`,
      checks: Array.from(r.checks).map(normalizeCheckId).sort(),
      cls: r.class,
      reason: r.reason,
    }))
    expect(docInfraRows).toEqual(codeInfraRows)

    // Parity for ReplicaSet ownership & residual-risk section (narwhal#727)
    expect(markdown).toContain("grants read-only `get`/`list` access on `apps` `replicasets`")
    expect(markdown).toContain("spec.replicas == 0")
    expect(markdown).toContain("status.replicas == 0")
    expect(markdown).toContain("deployment.kubernetes.io/revision")
    expect(markdown).toContain("metadata.ownerReferences")
    expect(markdown).not.toContain("(and not `replicasets`)")
  })

  it("provides en and ko translations and required audit metadata (id, owner, reviewedAt, explicit checks) for every rule", () => {
    const seenIds = new Set<string>()
    for (const rule of [...ACCEPTED_CONFIG_AUDIT_RULES, ...ACCEPTED_RBAC_RULES, ...ACCEPTED_INFRA_RULES]) {
      expect(rule.id.trim().length).toBeGreaterThan(0)
      expect(seenIds.has(rule.id), `Duplicate rule id: ${rule.id}`).toBe(false)
      seenIds.add(rule.id)
      expect(rule.owner.trim().length).toBeGreaterThan(0)
      expect(rule.reviewedAt).toMatch(/^\d{4}-\d{2}-\d{2}$/)
      expect(rule.checks.size).toBeGreaterThan(0)
      expect(en[rule.reasonKey]).toBe(rule.reason)
      expect(ko[rule.reasonKey]).toMatch(/[\uAC00-\uD7A3]/)
    }
  })
})
