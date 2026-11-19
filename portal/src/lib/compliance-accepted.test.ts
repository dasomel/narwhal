import fs from "node:fs"
import path from "node:path"
import { describe, expect, it } from "vitest"
import { en, ko } from "./i18n"
import {
  ACCEPTED_CONFIG_AUDIT_RULES,
  ACCEPTED_RBAC_RULES,
  matchConfigAuditException,
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

describe("matchConfigAuditException", () => {
  it("matches an allowlisted workload and check ID (including ReplicaSet hash and AVD prefix)", () => {
    const m1 = matchConfigAuditException("security-system", "DaemonSet", "falco", "AVD-KSV-0017")
    expect(m1).not.toBeNull()
    expect(m1?.class).toBe("accept")
    expect(m1?.reason).toMatch(/privileged/i)

    const m2 = matchConfigAuditException("devtools", "ReplicaSet", "harbor-core-999888777b", "KSV014")
    expect(m2).not.toBeNull()
    expect(m2?.class).toBe("upstream")

    const m3 = matchConfigAuditException("devtools", "ReplicaSet", "narwhal-portal-74b459c658", "KSV020")
    expect(m3).not.toBeNull()
    expect(m3?.class).toBe("accept")
  })

  it("does NOT accept a standalone-looking ReplicaSet suffix like abc12345 or unowned hash-like suffix", () => {
    expect(matchConfigAuditException("devtools", "ReplicaSet", "harbor-core-abc12345", "KSV014")).toBeNull()
    expect(matchConfigAuditException("devtools", "ReplicaSet", "harbor-core-b569bd8dd", "KSV014")).not.toBeNull()
    expect(matchConfigAuditException("devtools", "ReplicaSet", "harbor-core-b569bd8dd", "KSV014", ["harbor-core"])).not.toBeNull()
    expect(matchConfigAuditException("devtools", "ReplicaSet", "harbor-core-b569bd8dd", "KSV014", [])).toBeNull()
    expect(matchConfigAuditException("devtools", "ReplicaSet", "harbor-core-b569bd8dd", "KSV014", ["other-deploy"])).toBeNull()
    expect(matchConfigAuditException("devtools", "ReplicaSet", "harbor-core-abc12345", "KSV014", ["harbor-core"])).toBeNull()
  })

  it("keeps removed deferred, out-of-scope, and mismatched-reason checks actionable", () => {
    expect(matchConfigAuditException("iam", "ReplicaSet", "keycloak-operator-74b459c658", "KSV014")).toBeNull()

    const kc = matchConfigAuditException("iam", "StatefulSet", "keycloak", "KSV014")
    expect(kc).not.toBeNull()
    expect(kc?.class).toBe("upstream")

    expect(matchConfigAuditException("storage", "ReplicaSet", "velero-6447588f5f", "KSV014")).toBeNull()
    expect(matchConfigAuditException("storage", "DaemonSet", "node-agent", "KSV014")).toBeNull()

    for (const id of ["KSV020", "KSV021", "KSV036"]) {
      const cnpgDb1 = matchConfigAuditException("database", "Pod", "narwhal-db-1", id)
      expect(cnpgDb1, `narwhal-db-1 ${id}`).not.toBeNull()
      expect(cnpgDb1?.class).toBe("accept")
      expect(matchConfigAuditException("database", "Pod", "narwhal-db-2", id)).toBeNull()
      expect(matchConfigAuditException("database", "ReplicaSet", "narwhal-db-1", id)).toBeNull()
    }
    expect(matchConfigAuditException("database", "Pod", "narwhal-db-1", "KSV014")).toBeNull()

    for (const id of ["KSV020", "KSV021"]) {
      const pooler = matchConfigAuditException("database", "ReplicaSet", "narwhal-db-pooler-rw-5b44f8765f", id, [
        "narwhal-db-pooler-rw",
      ])
      expect(pooler, `narwhal-db-pooler-rw ${id}`).not.toBeNull()
      expect(pooler?.class).toBe("upstream")
    }
    expect(
      matchConfigAuditException("database", "ReplicaSet", "narwhal-db-pooler-rw-5b44f8765f", "KSV011", [
        "narwhal-db-pooler-rw",
      ]),
    ).toBeNull()

    const alloyLogs = matchConfigAuditException("monitoring", "DaemonSet", "k8s-monitoring-alloy-logs", "KSV012")
    expect(alloyLogs).not.toBeNull()
    expect(alloyLogs?.class).toBe("accept")

    for (const id of ["KSV001", "KSV003", "KSV012", "KSV017", "KSV020", "KSV021", "KSV023", "KSV121"]) {
      const nqa = matchConfigAuditException("nfs-quota-agent", "DaemonSet", "nfs-quota-agent", id)
      expect(nqa, `nfs-quota-agent ${id}`).not.toBeNull()
      expect(nqa?.class).toBe("accept")
    }
    expect(matchConfigAuditException("nfs-quota-agent", "DaemonSet", "nfs-quota-agent", "KSV014")).toBeNull()
    expect(matchConfigAuditException("nfs-quota-agent", "DaemonSet", "nfs-quota-agent", "KSV104")).toBeNull()

    // OpenBao snapshot CronJob/holder keep UID 100 (existing PVC backup files are owned by it): exact KSV020/KSV021 only
    for (const id of ["KSV020", "KSV021"]) {
      const cron = matchConfigAuditException("storage", "CronJob", "openbao-snapshot", id)
      expect(cron, `openbao-snapshot ${id}`).not.toBeNull()
      expect(cron?.class).toBe("accept")
      const holder = matchConfigAuditException("storage", "ReplicaSet", "openbao-snapshot-holder-865bfd4d54", id, [
        "openbao-snapshot-holder",
      ])
      expect(holder, `openbao-snapshot-holder ${id}`).not.toBeNull()
      expect(holder?.class).toBe("accept")
      // openbao-auto-unseal runs as 10100 and must stay unaccepted
      expect(matchConfigAuditException("storage", "CronJob", "openbao-auto-unseal", id)).toBeNull()
    }
    expect(matchConfigAuditException("storage", "CronJob", "openbao-snapshot", "KSV014")).toBeNull()
    expect(matchConfigAuditException("storage", "Job", "openbao-snapshot", "KSV020")).toBeNull()

    expect(matchConfigAuditException("platform-system", "ReplicaSet", "waypoint-74b459c658", "KSV030")).toBeNull()
    expect(matchConfigAuditException("platform-system", "ReplicaSet", "waypoint-74b459c658", "KSV104")).toBeNull()
    expect(matchConfigAuditException("devtools", "ReplicaSet", "gitea-6cf86848f7", "KSV012")).toBeNull()
    expect(matchConfigAuditException("devtools", "ReplicaSet", "gitea-6cf86848f7", "KSV014")).not.toBeNull()
    expect(matchConfigAuditException("platform-system", "ReplicaSet", "apisix-6c89749f58", "KSV014")).toBeNull()
    expect(matchConfigAuditException("security-system", "DaemonSet", "falco", "KSV014")).toBeNull()
  })

  it("keeps fixable KSV014 findings actionable (keycloak-operator, velero, node-agent)", () => {
    expect(matchConfigAuditException("iam", "ReplicaSet", "keycloak-operator-74b459c658", "KSV014")).toBeNull()
    expect(matchConfigAuditException("storage", "ReplicaSet", "velero-6447588f5f", "KSV014")).toBeNull()
    expect(matchConfigAuditException("storage", "DaemonSet", "node-agent", "KSV014")).toBeNull()
  })

  it("fails closed when a NEW check ID appears on an allowlisted workload", () => {
    expect(matchConfigAuditException("devtools", "ReplicaSet", "narwhal-portal-74b459c658", "KSV017")).toBeNull()
    expect(matchConfigAuditException("devtools", "ReplicaSet", "harbor-core-b569bd8dd", "KSV017")).toBeNull()
    expect(matchConfigAuditException("storage", "ReplicaSet", "velero-6447588f5f", "KSV017")).toBeNull()
  })

  it("fails closed on other workloads, wrong namespace, or wrong kind", () => {
    expect(matchConfigAuditException("devtools", "ReplicaSet", "narwhal-portal-extra-74b459c658", "KSV020")).toBeNull()
    expect(matchConfigAuditException("storage", "ReplicaSet", "velero-ui-58c868d4b8", "KSV012")).toBeNull()
    expect(matchConfigAuditException("default", "DaemonSet", "falco", "KSV017")).toBeNull()
    expect(matchConfigAuditException("security-system", "StatefulSet", "falco", "KSV017")).toBeNull()
  })
})

describe("matchRbacException", () => {
  it("matches exact (namespace, kind, roleName) and check IDs only, failing closed on Unknown or mismatched kind", () => {
    const m = matchRbacException("platform-admin", "KSV041", "", "ClusterRole")
    expect(m).not.toBeNull()
    expect(m?.class).toBe("accept")

    expect(matchRbacException("platform-admin", "KSV042", "", "ClusterRole")).toBeNull()
    expect(matchRbacException("platform-admin-extra", "KSV041", "", "ClusterRole")).toBeNull()
    expect(matchRbacException("developer-workload-admin", "AVD-KSV-0053", "", "ClusterRole")).not.toBeNull()
    expect(matchRbacException("narwhal-portal-jobs", "KSV048", "devtools", "Role")).not.toBeNull()
    expect(matchRbacException("narwhal-portal-jobs", "KSV048", "prod", "Role")).toBeNull()
    expect(matchRbacException("narwhal-portal-notification-channels", "KSV113", "monitoring", "Role")).not.toBeNull()

    // narwhal#717: Unknown, omitted, or mismatched kind must fail closed
    expect(matchRbacException("narwhal-portal-jobs", "KSV048", "devtools", "Unknown")).toBeNull()
    expect(matchRbacException("narwhal-portal-jobs", "KSV048", "devtools", "ClusterRole")).toBeNull()
    expect(matchRbacException("narwhal-portal-jobs", "KSV048", "devtools")).toBeNull()
    expect(matchRbacException("narwhal-portal-jobs", "KSV048", "", "Role")).toBeNull()
    expect(matchRbacException("platform-admin", "KSV041", "", "Role")).toBeNull()
    expect(matchRbacException("platform-admin", "KSV041", "", "Unknown")).toBeNull()
    expect(matchRbacException("platform-admin", "KSV041", "devtools", "ClusterRole")).toBeNull()
    expect(matchRbacException("platform-admin", "KSV041", "")).toBeNull()
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

    // Parity for ReplicaSet ownership & residual-risk section (narwhal#727)
    expect(markdown).toContain("grants read-only `get`/`list` access on `apps` `replicasets`")
    expect(markdown).toContain("spec.replicas == 0")
    expect(markdown).toContain("status.replicas == 0")
    expect(markdown).toContain("deployment.kubernetes.io/revision")
    expect(markdown).toContain("metadata.ownerReferences")
    expect(markdown).not.toContain("(and not `replicasets`)")
  })

  it("provides en and ko translations in i18n.ts for every accepted reason key", () => {
    const extraKeys = [
      "compliance.acceptedReason.systemNamespace",
      "compliance.acceptedReason.k8sBuiltinRole",
      "compliance.acceptedReason.upstreamRbacRole",
    ] as const
    for (const key of extraKeys) {
      expect(en[key].trim().length).toBeGreaterThan(0)
      expect(ko[key]).toMatch(/[\uAC00-\uD7A3]/)
    }
    for (const rule of [...ACCEPTED_CONFIG_AUDIT_RULES, ...ACCEPTED_RBAC_RULES]) {
      expect(en[rule.reasonKey]).toBe(rule.reason)
      expect(ko[rule.reasonKey]).toMatch(/[\uAC00-\uD7A3]/)
    }
  })
})
