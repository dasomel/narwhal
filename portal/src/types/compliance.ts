import type { Severity } from "./security"
export type { Severity }

export interface CheckSummary {
  Critical: number
  High: number
  Medium: number
  Low: number
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

export interface ComplianceFramework {
  id: string
  title: string
  passCount: number
  failCount: number
  /** Actionable failed controls count (controls with >=1 actionable failing check). */
  actionableFailCount?: number
  /** Accepted failed controls count (controls with >=1 failing check and all non-superseded failures accepted). */
  acceptedFailCount?: number
  totalControls: number
  /** Raw Trivy control pass rate: passCount / (passCount + failCount). */
  passRate: number
  /** Effective pass rate treating accepted-only and superseded-only controls as non-failing. */
  effectivePassRate?: number
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
  /** true when failCount > 0, actionableFailCount === 0, and acceptedFailCount > 0. */
  accepted?: boolean
  acceptedScope?: AcceptedScope
  description?: string
}

export interface ComplianceFrameworkDetail extends ComplianceFramework {
  controls: ComplianceControl[]
}

export interface ComplianceSummary {
  /** ACTIONABLE config-audit failures only — excludes system namespaces (see SYSTEM_NAMESPACES
   *  in lib/compliance.ts), per-check reviewed exceptions (see ACCEPTED_CONFIG_AUDIT_RULES in
   *  lib/compliance-accepted.ts), AND LOW severity (see lowSeverityConfigAuditFailures below;
   *  per docs/common/compliance-hardening.md). Field name kept for API compat. */
  totalConfigAuditFailures: CheckSummary
  /** LOW-severity config-audit failures in non-system namespaces — risk-accepted hygiene
   *  checks, shown separately so they don't inflate the actionable headline. Only the `Low` field
   *  is ever non-zero here (Critical/High/Medium always 0 by construction). */
  lowSeverityConfigAuditFailures: CheckSummary
  /** Accepted config-audit failures: system namespaces (all severities) plus non-LOW reviewed
   *  exceptions (accept/upstream) in non-system namespaces (non-system LOW stays in
   *  lowSeverityConfigAuditFailures so accepted LOW IDs are never double-counted). Field name kept
   *  for API compat. */
  acceptedSystemConfigAuditFailures: CheckSummary
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
  lastUpdated: string
}
