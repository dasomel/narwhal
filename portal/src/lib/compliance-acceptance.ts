// D-P2-1: Exact acceptance rule contract — every rule identifies exact
// (scope, namespace, kind, name, checks[]) with no regex, substring, prefix,
// or namespace-blanket matching. Generated ReplicaSet names resolve only to
// their owning Deployment via normalizeWorkloadName (metadata.ownerReferences
// + Kubernetes SafeEncodeString hash suffix).
// Cost: every newly accepted workload/role/check must be added explicitly.
// Escape hatch: none — broad pattern matching hides real findings.
//
// D-P2-2: Every rule carries a true, verifiable reason (reasonKey -> i18n en/ko),
// owner (upstream chart/component and repo pin path), and reviewedAt ISO date.
// Uncovered findings stay ACTIONABLE.
// Cost: higher actionable counts until individual components are audited or fixed.
// Escape hatch: add verified rules to src/data/compliance-acceptance-rules.json.
//
// D-P2-3: False/stale rules removed after repo/snapshot review (no live measurement):
// Harbor KSV014 (6 workloads, chart 1.19.1 exposes containerSecurityContext),
// velero KSV012 (runs as numeric 1002:1000 with runAsNonRoot: true),
// apisix-ingress-controller KSV020/021 (runs as 65532:65532),
// k8s-monitoring-alloy-logs KSV118 (podSecurityContext sets RuntimeDefault seccomp),
// metallb-speaker is left actionable: its capability necessity is unverified here.
// Cost: uncovered Harbor/system/component checks become actionable.
// Escape hatch: re-add only with verified container write-path evidence.
//
// D-P2-4: Unused acceptance rules are surfaced (never silently hidden, never a
// runtime crash because cluster topologies differ). findUnusedAcceptanceRules
// computes unmatched rules per fulfilled report family for summary API & UI.
// Cost: one set-membership pass over the rule list per summary request.
// Escape hatch: remove stale entries after reviewing the target and check list.
//
// D-P2-6: Rule data lives in src/data/compliance-acceptance-rules.json with
// this typed loader/validator so compliance.ts and compliance-accepted.ts stay
// small and auditable. Cost: JSON schema validation on module load; escape hatch:
// split JSON by domain if rule count grows large.

import rulesData from "@/data/compliance-acceptance-rules.json"
import { en, type TranslationKey } from "./i18n"
import type { AcceptedClass, UnusedAcceptanceRule } from "@/types/compliance"

export const COMPLIANCE_EXCEPTIONS_DOC_ANCHOR = "docs/common/compliance-hardening.md#risk-accepted-exceptions"
export const COMPLIANCE_EXCEPTIONS_DOC_URL = `https://github.com/dasomel/narwhal/blob/main/${COMPLIANCE_EXCEPTIONS_DOC_ANCHOR}`

export type AcceptanceScope = "cluster" | "namespace"
export type WorkloadKind = "ReplicaSet" | "DaemonSet" | "StatefulSet" | "Pod" | "CronJob"
export type RbacRuleKind = "ClusterRole" | "Role"
export type RbacBindingKind = "ClusterRoleBinding" | "RoleBinding"
export type AcceptedReasonKey = Extract<TranslationKey, `compliance.acceptedReason.${string}`>

export interface AcceptedConfigAuditRule {
  id: string
  scope: AcceptanceScope
  namespace: string
  kind: WorkloadKind
  name: string
  checks: ReadonlySet<string>
  class: AcceptedClass
  reasonKey: AcceptedReasonKey
  reason: string
  owner: string
  reviewedAt: string
  docAnchor: string
}

export interface AcceptedRbacRule {
  id: string
  scope: AcceptanceScope
  /** Empty string "" for ClusterRole (scope "cluster"), exact namespace for Role (scope "namespace"). */
  namespace: string
  kind: RbacRuleKind
  roleName: string
  checks: ReadonlySet<string>
  class: AcceptedClass
  reasonKey: AcceptedReasonKey
  reason: string
  owner: string
  reviewedAt: string
  docAnchor: string
}

export interface AcceptedInfraRule {
  id: string
  scope: "cluster"
  node: string
  checks: ReadonlySet<string>
  class: AcceptedClass
  reasonKey: AcceptedReasonKey
  reason: string
  owner: string
  reviewedAt: string
  docAnchor: string
}

export type RbacSubjectKind = "User" | "Group" | "ServiceAccount"

export interface RbacSubject {
  kind: RbacSubjectKind
  name: string
  namespace?: string
}

export interface AcceptedRbacBindingRule {
  id: string
  scope: AcceptanceScope
  /** Empty string "" for ClusterRoleBinding (scope "cluster"), exact namespace for RoleBinding (scope "namespace"). */
  namespace: string
  kind: RbacBindingKind
  name: string
  roleRefKind: RbacRuleKind
  roleRefName: string
  requireBootstrapMetadata: boolean
  checks: ReadonlySet<string>
  subjects: readonly RbacSubject[]
  class: AcceptedClass
  reasonKey: AcceptedReasonKey
  reason: string
  owner: string
  reviewedAt: string
  docAnchor: string
}

export interface AcceptedBenchmarkRule {
  id: string
  benchmarkId: string
  controlId: string
  /** Exact node name when scoped to a single node; undefined when targeting the control across nodes. */
  node?: string
  class: AcceptedClass
  reasonKey: AcceptedReasonKey
  reason: string
  owner: string
  reviewedAt: string
  docAnchor: string
}

export interface AcceptedMatch {
  ruleId: string
  class: AcceptedClass
  reasonKey: AcceptedReasonKey
  reason: string
  owner: string
  reviewedAt: string
  docAnchor: string
}

interface RawRuleEntry {
  id: string
  scope: string
  namespace: string
  kind: string
  name: string
  checks: string[]
  class: string
  reasonKey: string
  owner: string
  reviewedAt: string
}

interface RawRbacSubject {
  kind: string
  name: string
  namespace?: string
}

interface RawRbacBindingEntry {
  id: string
  scope: string
  namespace: string
  kind: string
  name: string
  roleRefKind: string
  roleRefName: string
  requireBootstrapMetadata: boolean
  checks: string[]
  subjects: RawRbacSubject[]
  class: string
  reasonKey: string
  owner: string
  reviewedAt: string
}

interface RawBenchmarkRuleEntry {
  id: string
  benchmarkId: string
  controlId: string
  node?: string
  class: string
  reasonKey: string
  owner: string
  reviewedAt: string
}

interface RawAcceptanceRulesFile {
  schemaVersion: number
  configAudit: RawRuleEntry[]
  rbac: RawRuleEntry[]
  rbacBindings?: RawRbacBindingEntry[]
  infra: RawRuleEntry[]
  benchmark?: RawBenchmarkRuleEntry[]
}

const VALID_WORKLOAD_KINDS = new Set<string>(["ReplicaSet", "DaemonSet", "StatefulSet", "Pod", "CronJob"])
const VALID_RBAC_KINDS = new Set<string>(["ClusterRole", "Role"])
const VALID_RBAC_BINDING_KINDS = new Set<string>(["ClusterRoleBinding", "RoleBinding"])
const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/
const PATTERN_METACHAR_RE = /[*?[\]()^$|\\]/

export const CHECK_ID_PAD_WIDTH = { KSV: 3, KCV: 4 } as const
const CHECK_ID_RE = /^(?:AVD-(KSV|KCV)-|(KSV|KCV))0*(\d+)$/

/**
 * Normalizes Trivy KSV/KCV check IDs so equivalent forms match identically:
 * - "AVD-KSV-0017" -> "KSV017"
 * - "AVD-KSV-0108" -> "KSV108"
 * - "ksv17"        -> "KSV017"
 * - "AVD-KCV-0056" -> "KCV0056"
 */
export function normalizeCheckId(raw: string): string {
  const upper = raw.trim().toUpperCase()
  const match = CHECK_ID_RE.exec(upper)
  if (!match) return upper
  const family = (match[1] ?? match[2]) as keyof typeof CHECK_ID_PAD_WIDTH
  return `${family}${match[3].padStart(CHECK_ID_PAD_WIDTH[family], "0")}`
}

function assertExactName(name: string, ruleId: string): void {
  if (typeof name !== "string" || name.trim().length === 0 || name !== name.trim()) {
    throw new Error(`Invalid acceptance rule ${ruleId}: name must be a non-empty exact string`)
  }
  if (PATTERN_METACHAR_RE.test(name)) {
    throw new Error(`Invalid acceptance rule ${ruleId}: name "${name}" must be an exact identifier, not a pattern`)
  }
}

function assertCommonRuleFields(raw: RawRuleEntry, seenIds: Set<string>): {
  scope: AcceptanceScope
  checks: ReadonlySet<string>
  cls: AcceptedClass
  reasonKey: AcceptedReasonKey
  reason: string
} {
  // D-P2-8: Reject extra selectors rather than silently ignoring a future broad
  // rule field. Cost: explicit schema updates; escape hatch: review a schema change.
  const fields = new Set(["id", "scope", "namespace", "kind", "name", "checks", "class", "reasonKey", "owner", "reviewedAt"])
  if (Object.keys(raw).some((field) => !fields.has(field))) {
    throw new Error(`Invalid acceptance rule ${raw.id}: unknown rule field`)
  }
  if (!raw.id || typeof raw.id !== "string" || seenIds.has(raw.id)) {
    throw new Error(`Invalid or duplicate acceptance rule id: ${String(raw.id)}`)
  }
  seenIds.add(raw.id)

  if (raw.scope !== "cluster" && raw.scope !== "namespace") {
    throw new Error(`Invalid acceptance rule ${raw.id}: scope must be "cluster" or "namespace"`)
  }
  if (raw.scope === "cluster" && raw.namespace !== "") {
    throw new Error(`Invalid acceptance rule ${raw.id}: cluster-scoped rule must have empty namespace`)
  }
  if (raw.scope === "namespace" && raw.namespace.trim().length === 0) {
    throw new Error(`Invalid acceptance rule ${raw.id}: namespace-scoped rule requires non-empty namespace`)
  }
  assertExactName(raw.name, raw.id)

  if (!Array.isArray(raw.checks) || raw.checks.length === 0) {
    throw new Error(`Invalid acceptance rule ${raw.id}: checks must be a non-empty explicit list`)
  }
  const normalizedChecks = new Set<string>()
  for (const c of raw.checks) {
    if (typeof c !== "string" || c.trim().length === 0 || PATTERN_METACHAR_RE.test(c)) {
      throw new Error(`Invalid acceptance rule ${raw.id}: invalid check ID "${String(c)}"`)
    }
    normalizedChecks.add(normalizeCheckId(c))
  }

  if (raw.class !== "accept" && raw.class !== "upstream") {
    throw new Error(`Invalid acceptance rule ${raw.id}: class must be "accept" or "upstream"`)
  }
  if (!raw.reasonKey.startsWith("compliance.acceptedReason.") || !(raw.reasonKey in en)) {
    throw new Error(`Invalid acceptance rule ${raw.id}: unknown reasonKey "${raw.reasonKey}"`)
  }
  const reasonKey = raw.reasonKey as AcceptedReasonKey
  const reason = en[reasonKey]
  if (!reason || reason.trim().length === 0) {
    throw new Error(`Invalid acceptance rule ${raw.id}: empty en translation for "${reasonKey}"`)
  }
  if (typeof raw.owner !== "string" || raw.owner.trim().length === 0) {
    throw new Error(`Invalid acceptance rule ${raw.id}: owner is required`)
  }
  if (typeof raw.reviewedAt !== "string" || !ISO_DATE_RE.test(raw.reviewedAt)) {
    throw new Error(`Invalid acceptance rule ${raw.id}: reviewedAt must be YYYY-MM-DD`)
  }

  return { scope: raw.scope, checks: normalizedChecks, cls: raw.class, reasonKey, reason }
}

const BENCHMARK_CONTROL_ID_RE = /^\d+(?:\.\d+)+$/

function loadAcceptanceRules(rawFile: RawAcceptanceRulesFile): {
  configAudit: readonly AcceptedConfigAuditRule[]
  rbac: readonly AcceptedRbacRule[]
  rbacBindings: readonly AcceptedRbacBindingRule[]
  benchmark: readonly AcceptedBenchmarkRule[]
} {
  const seenIds = new Set<string>()
  const configAudit: AcceptedConfigAuditRule[] = []
  const rbac: AcceptedRbacRule[] = []

  for (const raw of rawFile.configAudit) {
    const common = assertCommonRuleFields(raw, seenIds)
    if (!VALID_WORKLOAD_KINDS.has(raw.kind)) {
      throw new Error(`Invalid ConfigAudit acceptance rule ${raw.id}: unsupported kind "${raw.kind}"`)
    }
    configAudit.push({
      id: raw.id,
      scope: common.scope,
      namespace: raw.namespace,
      kind: raw.kind as WorkloadKind,
      name: raw.name,
      checks: common.checks,
      class: common.cls,
      reasonKey: common.reasonKey,
      reason: common.reason,
      owner: raw.owner,
      reviewedAt: raw.reviewedAt,
      docAnchor: COMPLIANCE_EXCEPTIONS_DOC_ANCHOR,
    })
  }

  for (const raw of rawFile.rbac) {
    const common = assertCommonRuleFields(raw, seenIds)
    if (!VALID_RBAC_KINDS.has(raw.kind)) {
      throw new Error(`Invalid RBAC acceptance rule ${raw.id}: unsupported kind "${raw.kind}"`)
    }
    if (raw.kind === "ClusterRole" && common.scope !== "cluster") {
      throw new Error(`Invalid RBAC acceptance rule ${raw.id}: ClusterRole must use scope "cluster"`)
    }
    if (raw.kind === "Role" && common.scope !== "namespace") {
      throw new Error(`Invalid RBAC acceptance rule ${raw.id}: Role must use scope "namespace"`)
    }
    rbac.push({
      id: raw.id,
      scope: common.scope,
      namespace: raw.namespace,
      kind: raw.kind as RbacRuleKind,
      roleName: raw.name,
      checks: common.checks,
      class: common.cls,
      reasonKey: common.reasonKey,
      reason: common.reason,
      owner: raw.owner,
      reviewedAt: raw.reviewedAt,
      docAnchor: COMPLIANCE_EXCEPTIONS_DOC_ANCHOR,
    })
  }

  const rbacBindings: AcceptedRbacBindingRule[] = []
  const bindingAllowedFields = new Set([
    "id",
    "scope",
    "namespace",
    "kind",
    "name",
    "roleRefKind",
    "roleRefName",
    "requireBootstrapMetadata",
    "checks",
    "subjects",
    "class",
    "reasonKey",
    "owner",
    "reviewedAt",
  ])
  const subjectAllowedFields = new Set(["kind", "name", "namespace"])
  const validSubjectKinds = new Set<string>(["User", "Group", "ServiceAccount"])
  for (const raw of rawFile.rbacBindings ?? []) {
    if (Object.keys(raw).some((field) => !bindingAllowedFields.has(field))) {
      throw new Error(`Invalid RBAC binding acceptance rule ${raw.id}: unknown rule field`)
    }
    if (!raw.id || typeof raw.id !== "string" || seenIds.has(raw.id)) {
      throw new Error(`Invalid or duplicate RBAC binding acceptance rule id: ${String(raw.id)}`)
    }
    seenIds.add(raw.id)
    if (raw.scope !== "cluster" && raw.scope !== "namespace") {
      throw new Error(`Invalid RBAC binding acceptance rule ${raw.id}: invalid scope`)
    }
    if (raw.scope === "cluster" && raw.namespace !== "") {
      throw new Error(`Invalid RBAC binding acceptance rule ${raw.id}: cluster-scoped rule must have empty namespace`)
    }
    if (raw.scope === "namespace" && raw.namespace.trim().length === 0) {
      throw new Error(`Invalid RBAC binding acceptance rule ${raw.id}: namespace-scoped rule requires non-empty namespace`)
    }
    if (!VALID_RBAC_BINDING_KINDS.has(raw.kind)) {
      throw new Error(`Invalid RBAC binding acceptance rule ${raw.id}: unsupported kind "${raw.kind}"`)
    }
    if (raw.kind === "ClusterRoleBinding" && raw.scope !== "cluster") {
      throw new Error(`Invalid RBAC binding acceptance rule ${raw.id}: ClusterRoleBinding must use scope "cluster"`)
    }
    if (raw.kind === "RoleBinding" && raw.scope !== "namespace") {
      throw new Error(`Invalid RBAC binding acceptance rule ${raw.id}: RoleBinding must use scope "namespace"`)
    }
    assertExactName(raw.name, raw.id)
    if (!VALID_RBAC_KINDS.has(raw.roleRefKind)) {
      throw new Error(`Invalid RBAC binding acceptance rule ${raw.id}: invalid roleRefKind "${raw.roleRefKind}"`)
    }
    assertExactName(raw.roleRefName, raw.id)
    if (typeof raw.requireBootstrapMetadata !== "boolean") {
      throw new Error(`Invalid RBAC binding acceptance rule ${raw.id}: requireBootstrapMetadata must be boolean`)
    }
    if (!Array.isArray(raw.checks) || raw.checks.length === 0) {
      throw new Error(`Invalid RBAC binding acceptance rule ${raw.id}: checks must be a non-empty explicit list`)
    }
    const normalizedChecks = new Set<string>()
    for (const c of raw.checks) {
      if (typeof c !== "string" || c.trim().length === 0 || PATTERN_METACHAR_RE.test(c)) {
        throw new Error(`Invalid RBAC binding acceptance rule ${raw.id}: invalid check ID "${String(c)}"`)
      }
      normalizedChecks.add(normalizeCheckId(c))
    }
    if (!Array.isArray(raw.subjects)) {
      throw new Error(`Invalid RBAC binding acceptance rule ${raw.id}: subjects must be an explicit array`)
    }
    const subjects: RbacSubject[] = []
    for (const s of raw.subjects) {
      if (!s || typeof s !== "object" || Object.keys(s).some((f) => !subjectAllowedFields.has(f))) {
        throw new Error(`Invalid RBAC binding acceptance rule ${raw.id}: invalid subject entry`)
      }
      if (!validSubjectKinds.has(s.kind)) {
        throw new Error(`Invalid RBAC binding acceptance rule ${raw.id}: invalid subject kind "${String(s.kind)}"`)
      }
      assertExactName(s.name, raw.id)
      if (s.kind === "ServiceAccount") {
        if (typeof s.namespace !== "string" || s.namespace.trim().length === 0 || s.namespace !== s.namespace.trim() || PATTERN_METACHAR_RE.test(s.namespace)) {
          throw new Error(`Invalid RBAC binding acceptance rule ${raw.id}: ServiceAccount subject requires exact namespace`)
        }
        subjects.push({ kind: "ServiceAccount", name: s.name, namespace: s.namespace })
      } else {
        if (s.namespace !== undefined && s.namespace !== "") {
          throw new Error(`Invalid RBAC binding acceptance rule ${raw.id}: ${s.kind} subject must not specify namespace`)
        }
        subjects.push({ kind: s.kind as "User" | "Group", name: s.name })
      }
    }
    if (raw.class !== "accept" && raw.class !== "upstream") {
      throw new Error(`Invalid RBAC binding acceptance rule ${raw.id}: class must be "accept" or "upstream"`)
    }
    if (!raw.reasonKey.startsWith("compliance.acceptedReason.") || !(raw.reasonKey in en)) {
      throw new Error(`Invalid RBAC binding acceptance rule ${raw.id}: unknown reasonKey "${raw.reasonKey}"`)
    }
    const reasonKey = raw.reasonKey as AcceptedReasonKey
    const reason = en[reasonKey]
    if (!reason || reason.trim().length === 0) {
      throw new Error(`Invalid RBAC binding acceptance rule ${raw.id}: empty en translation for "${reasonKey}"`)
    }
    if (typeof raw.owner !== "string" || raw.owner.trim().length === 0) {
      throw new Error(`Invalid RBAC binding acceptance rule ${raw.id}: owner is required`)
    }
    if (typeof raw.reviewedAt !== "string" || !ISO_DATE_RE.test(raw.reviewedAt)) {
      throw new Error(`Invalid RBAC binding acceptance rule ${raw.id}: reviewedAt must be YYYY-MM-DD`)
    }
    rbacBindings.push({
      id: raw.id,
      scope: raw.scope,
      namespace: raw.namespace,
      kind: raw.kind as RbacBindingKind,
      name: raw.name,
      roleRefKind: raw.roleRefKind as RbacRuleKind,
      roleRefName: raw.roleRefName,
      requireBootstrapMetadata: raw.requireBootstrapMetadata,
      checks: normalizedChecks,
      subjects,
      class: raw.class,
      reasonKey,
      reason,
      owner: raw.owner,
      reviewedAt: raw.reviewedAt,
      docAnchor: COMPLIANCE_EXCEPTIONS_DOC_ANCHOR,
    })
  }

  const benchmark: AcceptedBenchmarkRule[] = []
  const benchmarkAllowedFields = new Set([
    "id",
    "benchmarkId",
    "controlId",
    "node",
    "class",
    "reasonKey",
    "owner",
    "reviewedAt",
  ])
  const seenBenchmarkTargets = new Set<string>()
  for (const raw of rawFile.benchmark ?? []) {
    if (Object.keys(raw).some((field) => !benchmarkAllowedFields.has(field))) {
      throw new Error(`Invalid benchmark acceptance rule ${raw.id}: unknown rule field`)
    }
    if (!raw.id || typeof raw.id !== "string" || seenIds.has(raw.id)) {
      throw new Error(`Invalid or duplicate benchmark acceptance rule id: ${String(raw.id)}`)
    }
    seenIds.add(raw.id)
    assertExactName(raw.benchmarkId, raw.id)
    assertExactName(raw.controlId, raw.id)
    if (!BENCHMARK_CONTROL_ID_RE.test(raw.controlId)) {
      throw new Error(`Invalid benchmark acceptance rule ${raw.id}: invalid controlId "${raw.controlId}"`)
    }
    if (raw.node !== undefined) {
      assertExactName(raw.node, raw.id)
    }
    const targetKey = `${raw.benchmarkId}\0${raw.controlId}\0${raw.node ?? ""}`
    if (seenBenchmarkTargets.has(targetKey)) {
      throw new Error(`Duplicate benchmark acceptance rule target for ${raw.id}: ${targetKey}`)
    }
    seenBenchmarkTargets.add(targetKey)
    if (raw.class !== "accept" && raw.class !== "upstream") {
      throw new Error(`Invalid benchmark acceptance rule ${raw.id}: class must be "accept" or "upstream"`)
    }
    if (!raw.reasonKey.startsWith("compliance.acceptedReason.") || !(raw.reasonKey in en)) {
      throw new Error(`Invalid benchmark acceptance rule ${raw.id}: unknown reasonKey "${raw.reasonKey}"`)
    }
    const reasonKey = raw.reasonKey as AcceptedReasonKey
    const reason = en[reasonKey]
    if (!reason || reason.trim().length === 0) {
      throw new Error(`Invalid benchmark acceptance rule ${raw.id}: empty en translation for "${reasonKey}"`)
    }
    if (typeof raw.owner !== "string" || raw.owner.trim().length === 0) {
      throw new Error(`Invalid benchmark acceptance rule ${raw.id}: owner is required`)
    }
    if (typeof raw.reviewedAt !== "string" || !ISO_DATE_RE.test(raw.reviewedAt)) {
      throw new Error(`Invalid benchmark acceptance rule ${raw.id}: reviewedAt must be YYYY-MM-DD`)
    }
    benchmark.push({
      id: raw.id,
      benchmarkId: raw.benchmarkId,
      controlId: raw.controlId,
      ...(raw.node !== undefined ? { node: raw.node } : {}),
      class: raw.class,
      reasonKey,
      reason,
      owner: raw.owner,
      reviewedAt: raw.reviewedAt,
      docAnchor: COMPLIANCE_EXCEPTIONS_DOC_ANCHOR,
    })
  }

  return { configAudit, rbac, rbacBindings, benchmark }
}

const loaded = loadAcceptanceRules(rulesData as RawAcceptanceRulesFile)

export const ACCEPTED_CONFIG_AUDIT_RULES: readonly AcceptedConfigAuditRule[] = loaded.configAudit
export const ACCEPTED_RBAC_RULES: readonly AcceptedRbacRule[] = loaded.rbac
export const ACCEPTED_RBAC_BINDING_RULES: readonly AcceptedRbacBindingRule[] = loaded.rbacBindings
export const ACCEPTED_BENCHMARK_RULES: readonly AcceptedBenchmarkRule[] = loaded.benchmark

export const configRuleIndex = new Map<string, AcceptedConfigAuditRule[]>()
for (const rule of ACCEPTED_CONFIG_AUDIT_RULES) {
  const key = `${rule.scope}\0${rule.namespace}\0${rule.kind}\0${rule.name}`
  const bucket = configRuleIndex.get(key)
  if (bucket) bucket.push(rule)
  else configRuleIndex.set(key, [rule])
}

export const rbacRuleIndex = new Map<string, AcceptedRbacRule[]>()
for (const rule of ACCEPTED_RBAC_RULES) {
  const key = `${rule.scope}\0${rule.namespace}\0${rule.kind}\0${rule.roleName}`
  const bucket = rbacRuleIndex.get(key)
  if (bucket) bucket.push(rule)
  else rbacRuleIndex.set(key, [rule])
}

export const rbacBindingRuleIndex = new Map<string, AcceptedRbacBindingRule[]>()
for (const rule of ACCEPTED_RBAC_BINDING_RULES) {
  const key = `${rule.scope}\0${rule.namespace}\0${rule.kind}\0${rule.name}\0${rule.roleRefKind}\0${rule.roleRefName}`
  const bucket = rbacBindingRuleIndex.get(key)
  if (bucket) bucket.push(rule)
  else rbacBindingRuleIndex.set(key, [rule])
}

export const benchmarkRuleIndex = new Map<string, AcceptedBenchmarkRule[]>()
for (const rule of ACCEPTED_BENCHMARK_RULES) {
  const key = `${rule.benchmarkId}\0${rule.controlId}`
  const bucket = benchmarkRuleIndex.get(key)
  if (bucket) bucket.push(rule)
  else benchmarkRuleIndex.set(key, [rule])
}

export function findUnusedAcceptanceRules(
  matchedRuleIds: ReadonlySet<string>,
  evaluatedDomains: {
    configAudit: boolean
    rbac: boolean
    infra: boolean
    benchmark?: boolean
  },
  infraRules: readonly AcceptedInfraRule[] = [],
): UnusedAcceptanceRule[] {
  const unused: UnusedAcceptanceRule[] = []
  if (evaluatedDomains.configAudit) {
    for (const rule of ACCEPTED_CONFIG_AUDIT_RULES) {
      if (!matchedRuleIds.has(rule.id)) {
        unused.push({
          id: rule.id,
          domain: "configAudit",
          scope: rule.scope,
          namespace: rule.namespace,
          kind: rule.kind,
          name: rule.name,
          checks: Array.from(rule.checks).sort(),
          owner: rule.owner,
          reviewedAt: rule.reviewedAt,
        })
      }
    }
  }
  if (evaluatedDomains.rbac) {
    for (const rule of ACCEPTED_RBAC_RULES) {
      if (!matchedRuleIds.has(rule.id)) {
        unused.push({
          id: rule.id,
          domain: "rbac",
          scope: rule.scope,
          namespace: rule.namespace,
          kind: rule.kind,
          name: rule.roleName,
          checks: Array.from(rule.checks).sort(),
          owner: rule.owner,
          reviewedAt: rule.reviewedAt,
        })
      }
    }
  }
  if (evaluatedDomains.infra) {
    for (const rule of infraRules) {
      if (!matchedRuleIds.has(rule.id)) {
        unused.push({
          id: rule.id,
          domain: "infra",
          scope: rule.scope,
          namespace: "",
          kind: "Node",
          name: rule.node,
          checks: Array.from(rule.checks).sort(),
          owner: rule.owner,
          reviewedAt: rule.reviewedAt,
        })
      }
    }
  }
  if (evaluatedDomains.benchmark) {
    for (const rule of ACCEPTED_BENCHMARK_RULES) {
      if (!matchedRuleIds.has(rule.id)) {
        unused.push({
          id: rule.id,
          domain: "benchmark",
          scope: "cluster",
          namespace: "",
          kind: rule.benchmarkId,
          name: rule.node ?? "all-nodes",
          checks: [rule.controlId],
          owner: rule.owner,
          reviewedAt: rule.reviewedAt,
        })
      }
    }
  }
  return unused
}
