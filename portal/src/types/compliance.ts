import type { Severity } from "./security"
export type { Severity }

export interface CheckSummary {
  Critical: number
  High: number
  Medium: number
  Low: number
}

/** Sums the four severity counts of a CheckSummary (safe for undefined or partial cached summaries). */
export function sumCheckSummary(summary: Partial<CheckSummary> | undefined): number {
  if (!summary) return 0
  return (summary.Critical ?? 0) + (summary.High ?? 0) + (summary.Medium ?? 0) + (summary.Low ?? 0)
}

/**
 * Formats a [0, 1] pass rate for display with 1 decimal place.
 * When actionable failures remain or controls were not evaluated, floors to 0.1% and caps at
 * 99.9% so incomplete/failing frameworks never round up to 100%.
 */
export function formatPassRatePct(
  rate: number,
  opts?: { hasActionableFail?: boolean; hasNotEvaluated?: boolean },
): string {
  const clamped = Math.max(0, Math.min(1, Number.isFinite(rate) ? rate : 0))
  const pct = clamped * 100
  if (opts?.hasActionableFail || opts?.hasNotEvaluated) {
    const floored = Math.min(99.9, Math.floor(pct * 10 + 1e-9) / 10)
    return `${floored.toFixed(1)}%`
  }
  const rounded = Math.round(pct * 10) / 10
  return rounded === 100 ? "100%" : `${rounded.toFixed(1)}%`
}

export type AcceptedClass = "accept" | "upstream"

export type AcceptedScope = "system" | "builtin" | "accept" | "upstream" | "mixed"

export interface AuditCheck {
  id: string
  title: string
  description?: string
  severity: Severity
  category?: string
  success: boolean
  messages?: string[]
  remediation?: string
  /** Set when a failing check is covered by system/built-in acceptance or a reviewed exception. */
  accepted?: boolean
  acceptedClass?: AcceptedClass
  acceptedReasonKey?: string
  acceptedReason?: string
  acceptedDocAnchor?: string
}

export interface ConfigAuditRow {
  namespace: string
  kind: string
  name: string
  summary: CheckSummary
  /** Actionable (non-accepted) failing check counts for this workload. */
  actionableSummary?: CheckSummary
  /** Accepted failing check counts for this workload (system-namespace or allowlisted). */
  acceptedSummary?: CheckSummary
  /** true when `namespace` is a system namespace OR ALL non-LOW failing checks on this
   *  workload are covered by reviewed allowlist entries (see lib/compliance-accepted.ts). */
  accepted: boolean
  acceptedScope?: AcceptedScope
}

export interface ConfigAuditDetail extends ConfigAuditRow {
  checks: AuditCheck[]
}

export interface RbacAuditRow {
  namespace: string
  kind: string
  name: string
  summary: CheckSummary
  /** Actionable (non-accepted) failing check counts for this role. */
  actionableSummary?: CheckSummary
  /** Accepted failing check counts for this role (built-in/upstream or allowlisted). */
  acceptedSummary?: CheckSummary
  /** true when `name` is a built-in/upstream role OR ALL failing checks on this authored role
   *  are covered by ACCEPTED_RBAC_RULES (see lib/compliance-accepted.ts). */
  accepted: boolean
  acceptedScope?: AcceptedScope
}

export interface RbacAuditDetail extends RbacAuditRow {
  checks: AuditCheck[]
}

export interface InfraAuditRow {
  node: string
  summary: CheckSummary
  /** Actionable (non-accepted) failing check counts for this node. */
  actionableSummary?: CheckSummary
  /** Accepted failing check counts for this node (covered by ACCEPTED_INFRA_RULES). */
  acceptedSummary?: CheckSummary
  /** true when ALL failing checks on this node are covered by ACCEPTED_INFRA_RULES
   *  (see lib/compliance-accepted.ts). */
  accepted?: boolean
  acceptedScope?: AcceptedScope
}

export interface InfraAuditDetail extends InfraAuditRow {
  checks: AuditCheck[]
}

export type ControlEvaluationState = "passed" | "failed" | "not_evaluated"

export type ControlNotEvaluatedReason =
  | "manual"
  | "excluded_namespace"
  | "unsupported_kind"
  | "missing_node_type"
  | "unlinked_check_id"
  | "scanner_disabled"
  | "summary_only"
  | "empty_checks"
  | "no_evidence_in_report"
  | "absent_from_report"
  | "coverage_unknown"

export interface ComplianceFramework {
  id: string
  title: string
  /** Raw Trivy control pass count (`status.summary.passCount`, includes placeholder passes). */
  passCount: number
  /** Alias for raw Trivy control pass count (`status.summary.passCount`). */
  rawPassCount?: number
  /** Controls verified as evaluated-pass (excludes not-evaluated controls). */
  evaluatedPassCount?: number
  /** Controls that could not be evaluated on this cluster (excluded scope, absent node type, manual/unlinked IDs, or unknown coverage). */
  notEvaluatedCount?: number
  failCount: number
  /** Actionable failed controls count (controls with >=1 actionable failing check). */
  actionableFailCount?: number
  /** Accepted failed controls count (controls with >=1 failing check and all non-superseded failures accepted). */
  acceptedFailCount?: number
  /** Failed controls whose every failing check was dropped as a drained superseded ReplicaSet. */
  droppedFailCount?: number
  /** Total failing checks dropped across all controls in this framework due to drained superseded ReplicaSets. */
  droppedCheckCount?: number
  totalControls: number
  /** Raw Trivy control pass rate: passCount / totalControls. */
  passRate: number
  /** Evaluated control pass rate: evaluatedPassCount / totalControls. */
  evaluatedPassRate?: number
  /** Exception-adjusted pass rate: (evaluatedPassCount + acceptedFailCount) / totalControls. Never counts not-evaluated controls as passed. */
  effectivePassRate?: number
  /** True when cluster config, node list, or report lists needed to verify coverage failed to load. */
  coverageUnknown?: boolean
  /** Per-framework report timestamp (`status.updateTimestamp ?? metadata.creationTimestamp`). */
  lastUpdated?: string
}

export interface ComplianceControl {
  id: string
  name: string
  severity: Severity
  passCount: number
  failCount: number
  /** Actionable failing checks count for this control. */
  actionableFailCount?: number
  /** Accepted failing checks count for this control. */
  acceptedFailCount?: number
  /** Superseded-ReplicaSet failing checks dropped for this control. */
  droppedFailCount?: number
  /** Three-state evaluation classification for this control. */
  evaluationState?: ControlEvaluationState
  /** Why a control was classified as `not_evaluated`. */
  notEvaluatedReason?: ControlNotEvaluatedReason
  /** Human/diagnostic context for `not_evaluated` (e.g. excluded namespace, unsupported kind, missing node type). */
  notEvaluatedDetail?: string
  /** true when failCount > 0, actionableFailCount === 0, and acceptedFailCount > 0. */
  accepted?: boolean
  acceptedScope?: AcceptedScope
  description?: string
}

export interface ComplianceFrameworkDetail extends ComplianceFramework {
  controls: ComplianceControl[]
}

export type ComplianceSourceKey = "configAudit" | "rbac" | "infra" | "frameworks"

export interface ComplianceSourceStatus {
  status: "ok" | "unavailable"
  /** Newest timestamp observed on the underlying CRD objects, or null when unavailable/empty. */
  lastUpdated: string | null
  /** Oldest timestamp observed across underlying CRD objects (when multiple reports exist). */
  oldestUpdated?: string | null
  /** Scheduled rebuild cadence in hours (e.g. 6 for ClusterComplianceReport 6-hour cron). */
  rebuildIntervalHours?: number
  /** True when auxiliary metadata (e.g. ReplicaSets, Nodes, operator Deploy/ConfigMap) failed to load. */
  partial?: boolean
  /** Names of failed auxiliary K8s resources when `partial` is true. */
  partialSources?: string[]
  /** Sanitized error summary when status is "unavailable". */
  error?: string
}

export interface ComplianceSummary {
  /** ACTIONABLE config-audit failures only — unaccepted MEDIUM and above (Critical/High/Medium) in
   *  non-system namespaces (excludes system namespaces, per-check reviewed exceptions, and LOW).
   *  Field name kept for API compat. */
  totalConfigAuditFailures: CheckSummary
  /** Unaccepted LOW-severity config-audit failures in non-system namespaces (no acceptance rule). */
  unacceptedLowConfigAuditFailures?: CheckSummary
  /** Accepted LOW-severity config-audit failures (hygiene) in non-system namespaces, kept
   *  distinct from unaccepted LOW and non-LOW accepted counts so totals never overlap. */
  lowSeverityConfigAuditFailures: CheckSummary
  /** Accepted config-audit failures: system namespaces (all severities) plus non-LOW reviewed
   *  exceptions (accept/upstream) in non-system namespaces. Field name kept for API compat. */
  acceptedSystemConfigAuditFailures: CheckSummary
  /** Count of superseded-ReplicaSet ConfigAuditReports dropped from active totals. */
  droppedConfigAuditReports?: number
  /** Sum of severity counts across dropped superseded-ReplicaSet ConfigAuditReports. */
  droppedConfigAuditFailures?: CheckSummary
  /** ACTIONABLE RBAC-audit failures only — excludes findings owned by Kubernetes built-in roles,
   *  upstream controller/chart roles, and per-check reviewed exceptions on authored roles (see
   *  isAcceptedRbacRole / ACCEPTED_RBAC_RULES). Field name kept for API compat. */
  totalRbacFailures: CheckSummary
  /** Accepted RBAC-audit failures: built-in K8s roles, upstream controllers/charts, and reviewed
   *  check exceptions on authored roles — kept visible separately rather than dropped. */
  acceptedRbacFailures: CheckSummary
  /** ACTIONABLE Infra-audit failures only — excludes findings covered by ACCEPTED_INFRA_RULES
   *  (see lib/compliance-accepted.ts). Field name kept for API compat. */
  totalInfraFailures: CheckSummary
  /** Accepted Infra-audit failures covered by reviewed node-check exceptions — kept visible
   *  separately rather than dropped. */
  acceptedInfraFailures: CheckSummary
  frameworks: ComplianceFramework[]
  scannedWorkloads: number
  scannedRbacObjects: number
  scannedNodes: number
  /** Legacy global newest timestamp (empty string "" when all sources are unavailable). */
  lastUpdated: string
  /** Per-source availability and freshness metadata. */
  sources?: Record<ComplianceSourceKey, ComplianceSourceStatus>
  /**
   * True when at least one primary compliance source (`configAudit`, `rbac`, `infra`, `frameworks`)
   * failed to load (`sources[key].status === "unavailable"`). Auxiliary-only failures (e.g. 403 on
   * `replicasets`, `nodes`, `deploy/trivy-operator`, or `cm/trivy-operator-trivy-config`) set
   * `partial: true` and `sources[key].partial: true` rather than `dataUnavailable` so callers can
   * distinguish missing primary data from degraded coverage/metadata; when ALL four primary sources
   * fail, `GET /api/compliance/summary` returns HTTP 503 `{ unavailable: true }`.
   */
  dataUnavailable?: boolean
  /** True when at least one source loaded with failed auxiliary metadata reads (never cached). */
  partial?: boolean
}
