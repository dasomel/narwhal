// D-P2-7: Bootstrap defaults were verified with BOTH bootstrapping label and
// autoupdate=true in the review dump. Trivy RBAC reports carry only resource
// identity, not target labels/annotations; the portal does not fetch Roles.
// Cost: runtime can enforce exact names/checks only. Escape hatch: if target
// metadata becomes available, require the bootstrap label before acceptance.
import { en } from "./i18n"
import type { AcceptedClass } from "@/types/compliance"
import {
  ACCEPTED_CONFIG_AUDIT_RULES,
  ACCEPTED_RBAC_BINDING_RULES,
  ACCEPTED_RBAC_RULES,
  CHECK_ID_PAD_WIDTH,
  COMPLIANCE_EXCEPTIONS_DOC_ANCHOR,
  COMPLIANCE_EXCEPTIONS_DOC_URL,
  configRuleIndex,
  findUnusedAcceptanceRules,
  normalizeCheckId,
  rbacBindingRuleIndex,
  rbacRuleIndex,
  type AcceptanceScope,
  type AcceptedConfigAuditRule,
  type AcceptedInfraRule,
  type AcceptedMatch,
  type AcceptedRbacBindingRule,
  type AcceptedRbacRule,
  type AcceptedReasonKey,
  type RbacBindingKind,
  type RbacRuleKind,
  type RbacSubject,
  type WorkloadKind,
} from "./compliance-acceptance"

export {
  ACCEPTED_CONFIG_AUDIT_RULES,
  ACCEPTED_RBAC_BINDING_RULES,
  ACCEPTED_RBAC_RULES,
  CHECK_ID_PAD_WIDTH,
  COMPLIANCE_EXCEPTIONS_DOC_ANCHOR,
  COMPLIANCE_EXCEPTIONS_DOC_URL,
  findUnusedAcceptanceRules,
  normalizeCheckId,
  type AcceptanceScope,
  type AcceptedConfigAuditRule,
  type AcceptedInfraRule,
  type AcceptedMatch,
  type AcceptedRbacBindingRule,
  type AcceptedRbacRule,
  type AcceptedReasonKey,
  type RbacBindingKind,
  type RbacRuleKind,
  type RbacSubject,
  type WorkloadKind,
}

/**
 * D-P2-1: Strips the single Kubernetes pod-template-hash suffix from a ReplicaSet name so
 * rollout-induced hash changes match the owning Deployment without loosening name matching to
 * substrings or prefixes. Matches only the Kubernetes SafeEncodeString alphabet (no vowels,
 * no 0/1/3, 5-10 chars) so standalone names like "-abc12345" or hyphenated words like
 * "-controller" are never stripped.
 *
 * When `deploymentOwners` is provided (from live ReplicaSet `metadata.ownerReferences`),
 * collapsing occurs ONLY if an owning `Deployment` matches the stripped prefix — so a
 * standalone ReplicaSet named `<allowlisted>-<hash-like>` with no Deployment owner is never
 * accepted. When `deploymentOwners` is `undefined` (ownership unknown), falls back to the
 * alphabet rule without broadening acceptance.
 */
const REPLICASET_HASH_SUFFIX_RE = /^(.+)-([bcdfghjklmnpqrstvwxz2456789]{5,10})$/

export function normalizeWorkloadName(
  kind: string,
  name: string,
  deploymentOwners?: readonly string[],
): string {
  if (kind !== "ReplicaSet") return name
  const m = REPLICASET_HASH_SUFFIX_RE.exec(name)
  if (!m) return name
  const candidateDeployment = m[1]
  if (deploymentOwners !== undefined) {
    return deploymentOwners.includes(candidateDeployment) ? candidateDeployment : name
  }
  return candidateDeployment
}

export function infraRule(
  node: string,
  ids: readonly string[],
  cls: AcceptedClass,
  reasonKey: AcceptedReasonKey,
  owner = "narwhal",
  reviewedAt = "2026-11-23",
): AcceptedInfraRule {
  return {
    id: `infra:cluster:Node:${node}:${cls}`,
    scope: "cluster",
    node,
    checks: new Set(ids.map(normalizeCheckId)),
    class: cls,
    reasonKey,
    reason: en[reasonKey],
    owner,
    reviewedAt,
    docAnchor: COMPLIANCE_EXCEPTIONS_DOC_ANCHOR,
  }
}

/**
 * Reviewed node-level Infra Assessment exceptions. Currently empty: KCV0059 is fixed via
 * host /etc/passwd and /etc/group mounts and KCV0056 is fixed via mounting host /etc/cni/net.d/
 * at /etc/cni in trivy-operator.yaml (so CMD-0009 stats CNI files instead of the directory).
 * Keeping the empty fail-closed table preserves data-only additions if needed later;
 * all unlisted KCV IDs stay actionable.
 */
export const ACCEPTED_INFRA_RULES: readonly AcceptedInfraRule[] = []

const infraRuleIndex = new Map<string, AcceptedInfraRule[]>()
for (const rule of ACCEPTED_INFRA_RULES) {
  const bucket = infraRuleIndex.get(rule.node)
  if (bucket) bucket.push(rule)
  else infraRuleIndex.set(rule.node, [rule])
}

export function matchConfigAuditException(
  namespace: string,
  kind: string,
  name: string,
  checkId: string,
  deploymentOwners?: readonly string[],
): AcceptedMatch | null {
  const scope: AcceptanceScope = namespace === "" ? "cluster" : "namespace"
  // D-P2-1: Unknown ownership cannot certify a generated resource. Cost: actionable on metadata outages; retry after recovery.
  if (kind === "ReplicaSet" && deploymentOwners === undefined) return null
  const normalizedName = normalizeWorkloadName(kind, name, deploymentOwners)
  const rules = configRuleIndex.get(`${scope}\0${namespace}\0${kind}\0${normalizedName}`)
  if (!rules) return null
  const normalizedCheck = normalizeCheckId(checkId)
  for (const rule of rules) {
    if (rule.checks.has(normalizedCheck)) {
      return {
        ruleId: rule.id,
        class: rule.class,
        reasonKey: rule.reasonKey,
        reason: rule.reason,
        owner: rule.owner,
        reviewedAt: rule.reviewedAt,
        docAnchor: rule.docAnchor,
      }
    }
  }
  return null
}

export function matchRbacException(
  roleName: string,
  checkId: string,
  namespace = "",
  kind = "",
): AcceptedMatch | null {
  if (kind !== "Role" && kind !== "ClusterRole") return null
  if (kind === "Role" && namespace === "") return null
  if (kind === "ClusterRole" && namespace !== "") return null
  const scope: AcceptanceScope = kind === "ClusterRole" ? "cluster" : "namespace"
  const rules = rbacRuleIndex.get(`${scope}\0${namespace}\0${kind}\0${roleName}`)
  if (!rules) return null
  const normalizedCheck = normalizeCheckId(checkId)
  for (const rule of rules) {
    if (rule.checks.has(normalizedCheck)) {
      return {
        ruleId: rule.id,
        class: rule.class,
        reasonKey: rule.reasonKey,
        reason: rule.reason,
        owner: rule.owner,
        reviewedAt: rule.reviewedAt,
        docAnchor: rule.docAnchor,
      }
    }
  }
  return null
}

export function isBootstrapRbacRole(kind: string, namespace: string, roleName: string): boolean {
  if (kind !== "Role" && kind !== "ClusterRole") return false
  const scope: AcceptanceScope = kind === "ClusterRole" ? "cluster" : "namespace"
  const rules = rbacRuleIndex.get(`${scope}\0${namespace}\0${kind}\0${roleName}`)
  if (rules?.some((r) => r.reasonKey === "compliance.acceptedReason.verifiedBootstrap")) return true
  return ACCEPTED_RBAC_BINDING_RULES.some(
    (r) => r.roleRefKind === kind && r.roleRefName === roleName && (kind === "ClusterRole" || r.namespace === namespace),
  )
}

function canonicalizeRbacSubjects(
  subjects: ReadonlyArray<{ kind: string; name: string; namespace?: string }>,
): string[] {
  return subjects
    .map((s) => `${s.kind}\0${s.namespace ?? ""}\0${s.name}`)
    .sort()
}

function subjectsMatchExact(
  expected: ReadonlyArray<RbacSubject>,
  actual: ReadonlyArray<{ kind: string; name: string; namespace?: string }>,
): boolean {
  if (expected.length !== actual.length) return false
  const exp = canonicalizeRbacSubjects(expected)
  const act = canonicalizeRbacSubjects(actual)
  return exp.every((v, idx) => v === act[idx])
}

export function hasRbacBindingRule(binding: {
  kind: string
  namespace: string
  name: string
}): boolean {
  if (binding.kind !== "ClusterRoleBinding" && binding.kind !== "RoleBinding") return false
  const scope: AcceptanceScope = binding.kind === "ClusterRoleBinding" ? "cluster" : "namespace"
  return ACCEPTED_RBAC_BINDING_RULES.some(
    (r) => r.scope === scope && r.namespace === binding.namespace && r.kind === binding.kind && r.name === binding.name,
  )
}

export function matchRbacBindingException(binding: {
  kind: string
  namespace: string
  name: string
  roleRefKind: string
  roleRefName: string
  subjects: ReadonlyArray<{ kind: string; name: string; namespace?: string }>
  checkIds: ReadonlyArray<string>
  labels?: Record<string, string>
  annotations?: Record<string, string>
}): AcceptedMatch | null {
  if (binding.kind !== "ClusterRoleBinding" && binding.kind !== "RoleBinding") return null
  if (binding.kind === "ClusterRoleBinding" && binding.namespace !== "") return null
  if (binding.kind === "RoleBinding" && binding.namespace === "") return null
  if (!Array.isArray(binding.subjects) || !Array.isArray(binding.checkIds) || binding.checkIds.length === 0) {
    return null
  }
  const normalizedChecks = binding.checkIds.map(normalizeCheckId)
  const scope: AcceptanceScope = binding.kind === "ClusterRoleBinding" ? "cluster" : "namespace"
  const rules = rbacBindingRuleIndex.get(
    `${scope}\0${binding.namespace}\0${binding.kind}\0${binding.name}\0${binding.roleRefKind}\0${binding.roleRefName}`,
  )
  if (!rules) return null
  for (const rule of rules) {
    if (rule.requireBootstrapMetadata) {
      const bootstrapping = binding.labels?.["kubernetes.io/bootstrapping"]
      const autoupdate = binding.annotations?.["rbac.authorization.kubernetes.io/autoupdate"]
      if (bootstrapping !== "rbac-defaults" || autoupdate !== "true") {
        continue
      }
    }
    if (!subjectsMatchExact(rule.subjects, binding.subjects)) {
      continue
    }
    if (!normalizedChecks.every((cid) => rule.checks.has(cid))) {
      continue
    }
    return {
      ruleId: rule.id,
      class: rule.class,
      reasonKey: rule.reasonKey,
      reason: rule.reason,
      owner: rule.owner,
      reviewedAt: rule.reviewedAt,
      docAnchor: rule.docAnchor,
    }
  }
  return null
}

export function matchInfraException(node: string, checkId: string): AcceptedMatch | null {
  if (node.trim() === "" || checkId.trim() === "") return null
  const normalizedCheck = normalizeCheckId(checkId)
  const rules = infraRuleIndex.get(node)
  if (!rules) return null
  for (const rule of rules) {
    if (rule.checks.has(normalizedCheck)) {
      return {
        ruleId: rule.id,
        class: rule.class,
        reasonKey: rule.reasonKey,
        reason: rule.reason,
        owner: rule.owner,
        reviewedAt: rule.reviewedAt,
        docAnchor: rule.docAnchor,
      }
    }
  }
  return null
}
