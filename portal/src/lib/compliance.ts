import "server-only"
import { getK8sApiServer } from "./config"
import { getK8sBearerToken } from "./k8s-token"
import { fetchWithPolicy, readJsonWithPolicy } from "./http-client"
import { K8S_POLICY } from "./k8s-client"
import { cacheGet, cacheSet } from "./valkey"
import { cacheKeys, cacheTtl } from "./cache-keys"
import {
  ACCEPTED_INFRA_RULES,
  type AcceptedReasonKey,
  findUnusedAcceptanceRules,
  matchConfigAuditException,
  matchInfraException,
  matchRbacException,
} from "./compliance-accepted"
import { getCatalogueCheck, resolveCanonicalCheckId, type TrivyCheckCatalogueEntry } from "./trivy-check-catalogue"
import {
  BENCHMARK_CONFIGMAP_LIST_PATH,
  buildBenchmarkStore,
  mergeCisControlWithBenchmark,
  type BenchmarkStore,
  type RawConfigMapList,
} from "./kube-bench"
import {
  sumCheckSummary,
  type AcceptedClass,
  type AcceptedScope,
  type Severity,
  type CheckSummary,
  type AuditCheck,
  type ConfigAuditRow,
  type ConfigAuditDetail,
  type RbacAuditRow,
  type RbacAuditDetail,
  type InfraAuditRow,
  type InfraAuditDetail,
  type ComplianceFramework,
  type ComplianceFrameworkDetail,
  type ComplianceControl,
  type ComplianceSourceKey,
  type ComplianceSourceStatus,
  type ComplianceSummary,
  type ControlEvaluationState,
  type ControlNotEvaluatedReason,
} from "@/types/compliance"

// --- K8s API helpers ---

export class ComplianceDataUnavailableError extends Error {
  readonly source: ComplianceSourceKey
  constructor(source: ComplianceSourceKey, message: string) {
    super(message)
    this.name = "ComplianceDataUnavailableError"
    this.source = source
  }
}

export type ConfigAuditScope = "cluster" | "namespace"

export interface ConfigAuditTarget {
  scope: ConfigAuditScope
  namespace: string
  kind: string
  name: string
}

export class ComplianceDetailAmbiguousError extends Error {
  readonly scope: ConfigAuditScope
  readonly namespace: string
  readonly kind: string
  readonly resourceName: string
  readonly candidates: ReadonlyArray<ConfigAuditTarget>

  constructor(
    target: ConfigAuditTarget,
    candidates: ReadonlyArray<ConfigAuditTarget> = [],
    message?: string,
  ) {
    const scopeLabel = target.scope === "cluster" ? "cluster" : `namespace "${target.namespace}"`
    const kindLabel = target.kind ? ` kind "${target.kind}"` : " (missing kind)"
    const candidateSuffix =
      candidates.length > 0
        ? ` (${candidates.length} matches: ${candidates
            .map((c) => `${c.scope}/${c.namespace || "_cluster"}/${c.kind}/${c.name}`)
            .join(", ")})`
        : ""
    super(
      message ??
        `Ambiguous config audit detail lookup for ${scopeLabel}${kindLabel} name "${target.name}"${candidateSuffix}`,
    )
    this.name = "ComplianceDetailAmbiguousError"
    this.scope = target.scope
    this.namespace = target.namespace
    this.kind = target.kind
    this.resourceName = target.name
    this.candidates = candidates
  }
}

class K8sHttpError extends Error {
  readonly status: number
  constructor(status: number, statusText: string, path: string) {
    super(`K8s API ${status} ${statusText} for ${path}`)
    this.status = status
  }
}

async function complianceK8sFetch<T>(path: string): Promise<T> {
  const apiServer = getK8sApiServer()
  const headers: Record<string, string> = { "Content-Type": "application/json" }
  if (apiServer.startsWith("https://")) {
    const token = getK8sBearerToken()
    if (token.length > 0) headers["Authorization"] = `Bearer ${token}`
  }
  const res = await fetchWithPolicy(`${apiServer}${path}`, { headers }, { ...K8S_POLICY, timeoutMs: 60_000 })
  if (!res.ok) {
    throw new K8sHttpError(res.status, res.statusText, path)
  }
  return readJsonWithPolicy<T>(res)
}

type TryFetchResult<T> = { ok: true; data: T } | { ok: false; error: string; status?: number }

async function tryComplianceK8sFetch<T>(path: string): Promise<TryFetchResult<T>> {
  try {
    const data = await complianceK8sFetch<T>(path)
    return { ok: true, data }
  } catch (err) {
    return {
      ok: false,
      error: err instanceof Error ? err.message : String(err),
      ...(err instanceof K8sHttpError ? { status: err.status } : {}),
    }
  }
}

export type RbacRoleKind = "Role" | "ClusterRole"

// --- CRD raw types ---

interface RawCheck {
  checkID: string
  title: string
  description?: string
  severity: string
  category?: string
  success: boolean
  messages?: string[]
  remediation?: string
}

interface RawAuditSummary {
  criticalCount: number
  highCount: number
  mediumCount: number
  lowCount: number
}

interface RawAuditReport {
  metadata: {
    name: string
    namespace?: string
    creationTimestamp?: string
    labels?: Record<string, string>
    annotations?: Record<string, string>
    ownerReferences?: Array<{ kind: string; name: string }>
  }
  report: {
    summary: RawAuditSummary
    checks: RawCheck[]
  }
}

interface RawAuditList {
  items: RawAuditReport[]
}

interface RawReplicaSet {
  metadata: {
    name: string
    namespace?: string
    annotations?: Record<string, string>
    ownerReferences?: Array<{ kind: string; name: string }>
  }
  spec?: {
    replicas?: number
  }
  status?: {
    replicas?: number
    // Pods of this ReplicaSet that are terminating; status.replicas does NOT count them.
    terminatingReplicas?: number
  }
}

interface RawReplicaSetList {
  items?: RawReplicaSet[]
}

interface ReplicaSetSummary {
  specReplicas: number | null
  statusReplicas: number | null
  revision: number | null
  deploymentOwners: string[]
  superseded: boolean
}

interface RawSpecControl {
  id: string
  name: string
  severity: string
  description?: string
  nodeType?: string
  checks?: Array<{ id?: string }>
  commands?: Array<{ id?: string; nodeType?: string }>
}

interface RawComplianceReport {
  metadata: {
    name: string
    creationTimestamp?: string
  }
  spec: {
    compliance: {
      id: string
      title: string
      controls?: RawSpecControl[]
    }
  }
  status?: {
    updateTimestamp?: string
    summary?: {
      passCount?: number
      failCount?: number
    }
    detailReport?: {
      // trivy-operator (reportType=all) emits `results`, each control carrying a
      // `checks[]` array with `checkID`, `target`, and `success` — NOT `controls[]`/passTotal/failTotal.
      results?: Array<{
        id: string
        name: string
        severity: string
        description?: string
        checks?: Array<{
          checkID?: string
          target?: string
          success?: boolean
        }>
      }>
    }
  }
}

interface RawComplianceList {
  items: RawComplianceReport[]
}

interface RawNodeList {
  items?: Array<{
    metadata?: {
      name?: string
      labels?: Record<string, string>
    }
  }>
}

interface RawDeployment {
  spec?: {
    template?: {
      spec?: {
        containers?: Array<{
          env?: Array<{ name?: string; value?: string }>
        }>
      }
    }
  }
}

interface RawConfigMap {
  data?: Record<string, string>
}

// --- Mapping helpers ---

function mapSeverity(raw: string): Severity {
  const s = raw.charAt(0).toUpperCase() + raw.slice(1).toLowerCase()
  if (s === "Critical" || s === "High" || s === "Medium" || s === "Low") return s
  return "Unknown"
}

function buildCheckSummaryFromCounts(raw: RawAuditSummary): CheckSummary {
  return {
    Critical: raw.criticalCount,
    High: raw.highCount,
    Medium: raw.mediumCount,
    Low: raw.lowCount,
  }
}

interface AcceptanceAnnotation {
  accepted: boolean
  acceptedRuleId?: string
  acceptedClass?: AcceptedClass
  acceptedReasonKey?: AcceptedReasonKey
  acceptedReason?: string
  acceptedOwner?: string
  acceptedReviewedAt?: string
  acceptedDocAnchor?: string
}

type AcceptanceMatcher = (checkId: string) => AcceptanceAnnotation

function mapChecks(rawChecks: RawCheck[], matcher?: AcceptanceMatcher): AuditCheck[] {
  // Trivy가 동일 checkID + success 조합을 여러 번 emit하는 경우가 있어 dedup.
  // (Pod 안 여러 컨테이너가 같은 정책에 동시 실패하면 동일 항목 반복)
  const seen = new Set<string>()
  const deduped: RawCheck[] = []
  for (const c of rawChecks) {
    const key = `${c.checkID}|${c.success}|${(c.messages ?? []).join("\n")}`
    if (seen.has(key)) continue
    seen.add(key)
    deduped.push(c)
  }
  return deduped.map((c) => {
    const base: AuditCheck = {
      id: c.checkID,
      title: c.title,
      description: c.description,
      severity: mapSeverity(c.severity),
      category: c.category,
      success: c.success,
      messages: c.messages,
      remediation: c.remediation,
    }
    if (!c.success && matcher) {
      const ann = matcher(c.checkID)
      base.accepted = ann.accepted
      if (ann.accepted) {
        base.acceptedRuleId = ann.acceptedRuleId
        base.acceptedClass = ann.acceptedClass
        base.acceptedReasonKey = ann.acceptedReasonKey
        base.acceptedReason = ann.acceptedReason
        base.acceptedOwner = ann.acceptedOwner
        base.acceptedReviewedAt = ann.acceptedReviewedAt
        base.acceptedDocAnchor = ann.acceptedDocAnchor
      }
    }
    return base
  })
}

function addSummaries(a: CheckSummary, b: CheckSummary): CheckSummary {
  return {
    Critical: a.Critical + b.Critical,
    High: a.High + b.High,
    Medium: a.Medium + b.Medium,
    Low: a.Low + b.Low,
  }
}

const emptyCheckSummary: CheckSummary = { Critical: 0, High: 0, Medium: 0, Low: 0 }
const REPORT_SEVERITIES = ["Critical", "High", "Medium", "Low"] as const

function splitSummariesByChecks(
  rawChecks: RawCheck[],
  rawSummary: RawAuditSummary,
  matcher: AcceptanceMatcher,
): {
  summary: CheckSummary
  actionableSummary: CheckSummary
  acceptedSummary: CheckSummary
  matchedClasses: Set<AcceptedClass>
  matchedRuleIds: Set<string>
  totalsMatchSummary: boolean
} {
  const summary = buildCheckSummaryFromCounts(rawSummary)
  const actionableSummary: CheckSummary = { ...emptyCheckSummary }
  const acceptedSummary: CheckSummary = { ...emptyCheckSummary }
  const matchedClasses = new Set<AcceptedClass>()
  const matchedRuleIds = new Set<string>()

  if (rawChecks.length === 0) {
    // Fail closed when raw checks are omitted while summary reports failures.
    const hasAnySummaryFailure = REPORT_SEVERITIES.some((sev) => summary[sev] > 0)
    return {
      summary,
      actionableSummary: { ...summary },
      acceptedSummary,
      matchedClasses,
      matchedRuleIds,
      totalsMatchSummary: !hasAnySummaryFailure,
    }
  }

  const acceptedCounts: CheckSummary = { ...emptyCheckSummary }
  const unacceptedCounts: CheckSummary = { ...emptyCheckSummary }
  let hasUnknownSeverityFailure = false

  for (const c of rawChecks) {
    if (c.success) continue
    const sev = mapSeverity(c.severity)
    if (sev === "Unknown") {
      hasUnknownSeverityFailure = true
      continue
    }
    const ann = matcher(c.checkID)
    if (ann.accepted) {
      acceptedCounts[sev]++
      if (ann.acceptedClass) matchedClasses.add(ann.acceptedClass)
      if (ann.acceptedRuleId) matchedRuleIds.add(ann.acceptedRuleId)
    } else {
      unacceptedCounts[sev]++
    }
  }

  let totalsMatchSummary = !hasUnknownSeverityFailure
  for (const sev of REPORT_SEVERITIES) {
    if (acceptedCounts[sev] + unacceptedCounts[sev] !== summary[sev]) {
      totalsMatchSummary = false
    }
    // Fail closed: any summary failing count not matched by an accepted check stays actionable.
    acceptedSummary[sev] = Math.min(acceptedCounts[sev], summary[sev])
    actionableSummary[sev] = Math.max(unacceptedCounts[sev], summary[sev] - acceptedSummary[sev])
  }

  return { summary, actionableSummary, acceptedSummary, matchedClasses, matchedRuleIds, totalsMatchSummary }
}

function resolveScopeFromClasses(classes: Set<AcceptedClass>): AcceptedScope | undefined {
  if (classes.size === 0) return undefined
  if (classes.size === 1) return classes.values().next().value
  return "mixed"
}

// D-P2-1: Config Audit acceptance matches ONLY exact (scope, namespace, kind, name, checkId)
// rules in compliance-acceptance-rules.json — never namespace-blanket acceptance.
function configAuditMatcher(
  namespace: string,
  kind: string,
  name: string,
  deploymentOwners?: readonly string[],
): AcceptanceMatcher {
  return (checkId: string) => {
    const match = matchConfigAuditException(namespace, kind, name, checkId, deploymentOwners)
    if (!match) return { accepted: false }
    return {
      accepted: true,
      acceptedRuleId: match.ruleId,
      acceptedClass: match.class,
      acceptedReasonKey: match.reasonKey,
      acceptedReason: match.reason,
      acceptedOwner: match.owner,
      acceptedReviewedAt: match.reviewedAt,
      acceptedDocAnchor: match.docAnchor,
    }
  }
}

function resolveConfigAuditResourceKind(item: RawAuditReport): string {
  const labels = item.metadata.labels ?? {}
  const ownerRefs = item.metadata.ownerReferences ?? []
  return labels["trivy-operator.resource.kind"] ?? ownerRefs[0]?.kind ?? "Unknown"
}

function resolveConfigAuditResourceName(item: RawAuditReport): string {
  const labels = item.metadata.labels ?? {}
  const ownerRefs = item.metadata.ownerReferences ?? []
  return (
    labels["trivy-operator.resource.name"] ??
    ownerRefs[0]?.name ??
    item.metadata.annotations?.["trivy-operator.resource.name"] ??
    item.metadata.name
  )
}

function classifyConfigAuditItem(
  item: RawAuditReport,
  deploymentOwners?: readonly string[],
): {
  row: ConfigAuditRow
  matcher: AcceptanceMatcher
  matchedRuleIds: Set<string>
} {
  const kind = resolveConfigAuditResourceKind(item)
  const name = resolveConfigAuditResourceName(item)
  const namespace = item.metadata.namespace ?? ""
  const scopeField = namespace === "" ? { scope: "cluster" as const } : { scope: "namespace" as const }
  const matcher = configAuditMatcher(namespace, kind, name, deploymentOwners)

  const { summary, actionableSummary, acceptedSummary, matchedClasses, matchedRuleIds, totalsMatchSummary } =
    splitSummariesByChecks(item.report.checks ?? [], item.report.summary, matcher)
  const nonLowTotal = summary.Critical + summary.High + summary.Medium
  const nonLowActionable =
    actionableSummary.Critical + actionableSummary.High + actionableSummary.Medium
  // A row is accepted only when every failing check is present (totalsMatchSummary),
  // it has non-LOW failing checks, and ALL non-LOW checks are covered by exact reviewed rules.
  const accepted = totalsMatchSummary && nonLowTotal > 0 && nonLowActionable === 0
  return {
    row: {
      namespace,
      ...scopeField,
      kind,
      name,
      summary,
      actionableSummary,
      acceptedSummary,
      accepted,
      acceptedScope: accepted ? resolveScopeFromClasses(matchedClasses) : undefined,
    },
    matcher,
    matchedRuleIds,
  }
}

// D-P2-1: RBAC acceptance matches ONLY exact (scope, namespace, kind, roleName, checkId)
// rules in compliance-acceptance-rules.json — never substring, prefix, or check-less role rules.
function rbacAuditMatcher(namespace: string, kind: string, roleName: string): AcceptanceMatcher {
  return (checkId: string) => {
    const match = matchRbacException(roleName, checkId, namespace, kind)
    if (!match) return { accepted: false }
    return {
      accepted: true,
      acceptedRuleId: match.ruleId,
      acceptedClass: match.class,
      acceptedReasonKey: match.reasonKey,
      acceptedReason: match.reason,
      acceptedOwner: match.owner,
      acceptedReviewedAt: match.reviewedAt,
      acceptedDocAnchor: match.docAnchor,
    }
  }
}

function classifyRbacAuditItem(item: RawAuditReport): {
  row: RbacAuditRow
  matcher: AcceptanceMatcher
  matchedRuleIds: Set<string>
} {
  const labels = item.metadata.labels ?? {}
  const ownerRefs = item.metadata.ownerReferences ?? []
  const kind = labels["trivy-operator.resource.kind"] ?? ownerRefs[0]?.kind ?? "Unknown"
  const name = resolveRbacRoleName(item)
  const namespace = item.metadata.namespace ?? ""
  const matcher = rbacAuditMatcher(namespace, kind, name)

  const { summary, actionableSummary, acceptedSummary, matchedClasses, matchedRuleIds, totalsMatchSummary } =
    splitSummariesByChecks(item.report.checks ?? [], item.report.summary, matcher)
  const totalFailures = summary.Critical + summary.High + summary.Medium + summary.Low
  const actionableTotal =
    actionableSummary.Critical +
    actionableSummary.High +
    actionableSummary.Medium +
    actionableSummary.Low
  const accepted = totalsMatchSummary && totalFailures > 0 && actionableTotal === 0
  return {
    row: {
      namespace,
      kind,
      name,
      summary,
      actionableSummary,
      acceptedSummary,
      accepted,
      acceptedScope: accepted ? resolveScopeFromClasses(matchedClasses) : undefined,
    },
    matcher,
    matchedRuleIds,
  }
}

// rbacassessmentreport/clusterrbacassessmentreport metadata.name is a content hash, not the real
// Role/ClusterRole name — the real name is the report's owning object (ownerReferences[0].name).
// Falls back to the resource-name label / metadata.name if ownerReferences is absent.
function resolveRbacRoleName(item: RawAuditReport): string {
  const labels = item.metadata.labels ?? {}
  const ownerRefs = item.metadata.ownerReferences ?? []
  return ownerRefs[0]?.name ?? labels["trivy-operator.resource.name"] ?? item.metadata.name
}

function infraAuditMatcher(node: string): AcceptanceMatcher {
  return (checkId: string) => {
    const match = matchInfraException(node, checkId)
    if (!match) return { accepted: false }
    return {
      accepted: true,
      acceptedRuleId: match.ruleId,
      acceptedClass: match.class,
      acceptedReasonKey: match.reasonKey,
      acceptedReason: match.reason,
      acceptedOwner: match.owner,
      acceptedReviewedAt: match.reviewedAt,
      acceptedDocAnchor: match.docAnchor,
    }
  }
}

function classifyInfraAuditItem(item: RawAuditReport): {
  row: InfraAuditRow
  matcher: AcceptanceMatcher
  matchedRuleIds: Set<string>
} {
  const labels = item.metadata.labels ?? {}
  const node = labels["trivy-operator.resource.name"] ?? item.metadata.name
  const matcher = infraAuditMatcher(node)

  const { summary, actionableSummary, acceptedSummary, matchedClasses, matchedRuleIds, totalsMatchSummary } =
    splitSummariesByChecks(item.report.checks ?? [], item.report.summary, matcher)
  const totalFailures = sumCheckSummary(summary)
  const actionableTotal = sumCheckSummary(actionableSummary)
  const accepted = totalsMatchSummary && totalFailures > 0 && actionableTotal === 0
  return {
    row: {
      node,
      summary,
      actionableSummary,
      acceptedSummary,
      accepted,
      acceptedScope: accepted ? resolveScopeFromClasses(matchedClasses) : undefined,
    },
    matcher,
    matchedRuleIds,
  }
}

// --- ReplicaSet lookup for superseded-revision filtering & exact Deployment ownership (narwhal#727) ---

const DEPLOYMENT_REVISION_ANNOTATION = "deployment.kubernetes.io/revision"

function parseDeploymentRevision(annotations?: Record<string, string>): number | null {
  const raw = annotations?.[DEPLOYMENT_REVISION_ANNOTATION]
  if (typeof raw !== "string" || !/^\d+$/.test(raw)) return null
  const rev = Number(raw)
  return Number.isSafeInteger(rev) ? rev : null
}

// Fetch in the same request path as configauditreports with no separate ReplicaSet cache so
// cached 0-replica state can never outlive or poison a subsequent report computation.
async function fetchReplicaSetMap(): Promise<Map<string, ReplicaSetSummary> | null> {
  try {
    const list = await complianceK8sFetch<RawReplicaSetList>("/apis/apps/v1/replicasets")
    const entries: Array<{
      key: string
      namespace: string
      specReplicas: number | null
      statusReplicas: number | null
      terminatingReplicas: number | null
      revision: number | null
      deploymentOwners: string[]
    }> = []
    const maxRevisionByDeployment = new Map<string, number>()

    for (const rs of list?.items ?? []) {
      const ns = rs.metadata?.namespace ?? ""
      const name = rs.metadata?.name ?? ""
      if (!ns || !name) continue
      const deploymentOwners = (rs.metadata?.ownerReferences ?? [])
        .filter((ref) => ref.kind === "Deployment" && typeof ref.name === "string" && ref.name.length > 0)
        .map((ref) => ref.name)
      const revision = parseDeploymentRevision(rs.metadata?.annotations)
      if (revision !== null) {
        for (const owner of deploymentOwners) {
          const depKey = `${ns}/${owner}`
          const prev = maxRevisionByDeployment.get(depKey)
          if (prev === undefined || revision > prev) {
            maxRevisionByDeployment.set(depKey, revision)
          }
        }
      }
      entries.push({
        key: `${ns}/${name}`,
        namespace: ns,
        specReplicas: typeof rs.spec?.replicas === "number" ? rs.spec.replicas : null,
        statusReplicas: typeof rs.status?.replicas === "number" ? rs.status.replicas : null,
        terminatingReplicas:
          typeof rs.status?.terminatingReplicas === "number" ? rs.status.terminatingReplicas : null,
        revision,
        deploymentOwners,
      })
    }

    const map = new Map<string, ReplicaSetSummary>()
    for (const entry of entries) {
      // Drop ONLY fully drained superseded Deployment revisions:
      // (a) owned by a Deployment, (b) spec.replicas == 0 AND status.replicas == 0 AND
      // status.terminatingReplicas == 0 (reported explicitly), and
      // (c) the same Deployment in the same namespace owns another RS with a higher revision.
      // Standalone ReplicaSets, scaled-to-zero Deployments (current revision), terminating pods,
      // and any unknown field are kept so live findings are never hidden.
      const hasHigherRevisionSibling =
        entry.revision !== null &&
        entry.deploymentOwners.some((owner) => {
          const maxRev = maxRevisionByDeployment.get(`${entry.namespace}/${owner}`)
          return maxRev !== undefined && maxRev > entry.revision!
        })
      const superseded =
        entry.deploymentOwners.length > 0 &&
        entry.specReplicas === 0 &&
        entry.statusReplicas === 0 &&
        // status.replicas excludes terminating pods, so require an explicit 0 here too. A cluster
        // that does not report the field (null) keeps the report: unknown never drops.
        entry.terminatingReplicas === 0 &&
        hasHigherRevisionSibling
      map.set(entry.key, {
        specReplicas: entry.specReplicas,
        statusReplicas: entry.statusReplicas,
        revision: entry.revision,
        deploymentOwners: entry.deploymentOwners,
        superseded,
      })
    }
    return map
  } catch (err) {
    console.warn(
      "[compliance] failed to list ReplicaSets, keeping all ReplicaSet reports:",
      err instanceof Error ? err.message : err,
    )
    return null
  }
}

function resolveReplicaSetByName(
  namespace: string,
  resourceName: string | undefined,
  fallbackId: string,
  rsMap: Map<string, ReplicaSetSummary> | null,
): { drop: boolean; deploymentOwners?: readonly string[]; missingKey?: string } {
  if (rsMap === null) {
    return { drop: false }
  }
  const rsKey = namespace && resourceName ? `${namespace}/${resourceName}` : ""
  const rsMeta = rsKey ? rsMap.get(rsKey) : undefined
  if (!rsMeta) {
    return { drop: false, missingKey: rsKey || `${namespace}/${fallbackId}` }
  }
  if (rsMeta.superseded) {
    return { drop: true }
  }
  return { drop: false, deploymentOwners: rsMeta.deploymentOwners }
}

function resolveReplicaSetMetadata(
  item: RawAuditReport,
  rsMap: Map<string, ReplicaSetSummary> | null,
): { drop: boolean; deploymentOwners?: readonly string[]; missingKey?: string } {
  const labels = item.metadata.labels ?? {}
  const kind = labels["trivy-operator.resource.kind"] ?? "Unknown"
  if (kind !== "ReplicaSet") {
    return { drop: false }
  }
  return resolveReplicaSetByName(
    item.metadata.namespace ?? "",
    labels["trivy-operator.resource.name"],
    item.metadata.name,
    rsMap,
  )
}

function extractTimestampRange(
  items: ReadonlyArray<{ metadata: { creationTimestamp?: string }; status?: { updateTimestamp?: string } }>,
  preferUpdateTimestamp = false,
): { newest: string | null; oldest: string | null } {
  const ts = items
    .map((i) => (preferUpdateTimestamp ? i.status?.updateTimestamp ?? i.metadata.creationTimestamp : i.metadata.creationTimestamp) ?? "")
    .filter(Boolean)
    .sort()
  if (ts.length === 0) return { newest: null, oldest: null }
  return { newest: ts[ts.length - 1], oldest: ts[0] }
}

interface ConfigAuditListWithMeta {
  rows: ConfigAuditRow[]
  matchedRuleIds: Set<string>
  droppedCount: number
  droppedSummary: CheckSummary
  lastUpdated: string | null
  oldestUpdated: string | null
  partial: boolean
  partialSources: string[]
}

// D-CONFIG-AUDIT-CLUSTER-SCOPE: Fetch ClusterConfigAuditReports alongside namespaced
// ConfigAuditReports so cluster-scoped findings (e.g. ClusterRoleBinding KSV111/KSV122)
// appear in the Config Audit list, counters, summary totals, and KISA CFG-01. Either list
// failing throws ComplianceDataUnavailableError (never an empty success); cluster-scoped
// rows keep namespace "" with scope "cluster" and stay fail-closed in configAuditMatcher.
// Cost: one extra cluster-scoped list read (already allowed by ClusterRole/narwhal-portal).
// Escape hatch: split into separate source status if cluster CRDs are ever restricted.
async function fetchConfigAuditListWithMeta(): Promise<ConfigAuditListWithMeta> {
  let namespaced: RawAuditList
  let clustered: RawAuditList
  try {
    ;[namespaced, clustered] = await Promise.all([
      complianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/configauditreports"),
      complianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/clusterconfigauditreports"),
    ])
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    console.warn("[compliance] getConfigAuditList failed:", msg)
    throw new ComplianceDataUnavailableError("configAudit", msg)
  }

  const rsMap = await fetchReplicaSetMap()
  const missingReplicaSets: string[] = []
  const rows: ConfigAuditRow[] = []
  const matchedRuleIds = new Set<string>()
  const keptItems: RawAuditReport[] = []
  let droppedCount = 0
  let droppedSummary: CheckSummary = { ...emptyCheckSummary }

  const allItems = [...(namespaced.items ?? []), ...(clustered.items ?? [])]
  for (const item of allItems) {
    const rsState = resolveReplicaSetMetadata(item, rsMap)
    if (rsState.missingKey) missingReplicaSets.push(rsState.missingKey)
    if (rsState.drop) {
      droppedCount++
      droppedSummary = addSummaries(droppedSummary, buildCheckSummaryFromCounts(item.report.summary))
      continue
    }
    keptItems.push(item)
    const classified = classifyConfigAuditItem(item, rsState.deploymentOwners)
    rows.push(classified.row)
    for (const id of classified.matchedRuleIds) matchedRuleIds.add(id)
  }
  if (missingReplicaSets.length > 0) {
    console.warn(
      `[compliance] ReplicaSet(s) missing from cluster list, keeping reports fail-safe: ${missingReplicaSets.join(", ")}`,
    )
  }
  const { newest, oldest } = extractTimestampRange(keptItems)
  return {
    rows,
    matchedRuleIds,
    droppedCount,
    droppedSummary,
    lastUpdated: newest,
    oldestUpdated: oldest,
    partial: rsMap === null,
    partialSources: rsMap === null ? ["replicasets"] : [],
  }
}

// --- getConfigAuditList ---

export async function getConfigAuditList(): Promise<ConfigAuditRow[]> {
  const cacheKey = cacheKeys.complianceConfigAuditList()
  const cached = await cacheGet<ConfigAuditRow[]>(cacheKey)
  if (cached) return cached

  const meta = await fetchConfigAuditListWithMeta()
  if (!meta.partial) {
    await cacheSet(cacheKey, meta.rows, cacheTtl("complianceConfigAuditList"))
  }
  return meta.rows
}

// --- getConfigAuditDetail ---

// D-CONFIG-AUDIT-DETAIL-IDENTITY: Identify Config Audit details strictly by
// (scope, namespace, kind, name) end to end so same-name resources of different kinds
// (e.g. clean ClusterRole vs failing ClusterRoleBinding, or namespaced Deployment vs Service)
// never share a cache entry or return another resource's checks. Missing kind or multiple active
// matches fail closed with ComplianceDetailAmbiguousError.
// Cost: kind and scope must be passed on every detail lookup; escape hatch: none (guessing hides findings).
export async function getConfigAuditDetail(
  scope: ConfigAuditScope,
  namespace: string,
  kind: string,
  name: string,
): Promise<ConfigAuditDetail | null> {
  if (scope !== "cluster" && scope !== "namespace") return null
  if (scope === "cluster" && namespace !== "") return null
  if (scope === "namespace" && namespace === "") return null
  if (name.trim() === "") return null
  if (kind.trim() === "") {
    throw new ComplianceDetailAmbiguousError({ scope, namespace, kind: "", name })
  }

  const cacheKey = cacheKeys.complianceConfigAuditDetail(scope, namespace, kind, name)
  const cached = await cacheGet<ConfigAuditDetail>(cacheKey)
  if (cached) return cached

  let namespaced: RawAuditList
  let clustered: RawAuditList
  try {
    ;[namespaced, clustered] = await Promise.all([
      complianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/configauditreports"),
      complianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/clusterconfigauditreports"),
    ])
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    console.warn("[compliance] getConfigAuditDetail failed:", msg)
    throw new ComplianceDataUnavailableError("configAudit", msg)
  }

  const rsMap = await fetchReplicaSetMap()
  const scopedItems = scope === "cluster" ? (clustered.items ?? []) : (namespaced.items ?? [])
  const matchedItems = scopedItems.filter((i) => {
    const rNamespace = i.metadata.namespace ?? ""
    if (scope === "cluster" ? rNamespace !== "" : rNamespace !== namespace) return false
    const rKind = resolveConfigAuditResourceKind(i)
    const rName = resolveConfigAuditResourceName(i)
    return rKind === kind && rName === name
  })

  const activeMatches: Array<{ item: RawAuditReport; rsState: ReturnType<typeof resolveReplicaSetMetadata> }> = []
  for (const candidate of matchedItems) {
    const rsState = resolveReplicaSetMetadata(candidate, rsMap)
    if (rsState.missingKey) {
      console.warn(
        `[compliance] ReplicaSet missing from cluster list, keeping report detail fail-safe: ${rsState.missingKey}`,
      )
    }
    if (!rsState.drop) {
      activeMatches.push({ item: candidate, rsState })
    }
  }

  if (activeMatches.length === 0) return null
  if (activeMatches.length > 1) {
    throw new ComplianceDetailAmbiguousError(
      { scope, namespace, kind, name },
      activeMatches.map(({ item: m }) => ({
        scope,
        namespace: m.metadata.namespace ?? "",
        kind: resolveConfigAuditResourceKind(m),
        name: resolveConfigAuditResourceName(m),
      })),
    )
  }

  const { item, rsState } = activeMatches[0]
  const { row, matcher } = classifyConfigAuditItem(item, rsState.deploymentOwners)
  const detail: ConfigAuditDetail = {
    ...row,
    checks: mapChecks(item.report.checks ?? [], matcher),
  }
  if (rsMap !== null) {
    await cacheSet(cacheKey, detail, cacheTtl("complianceConfigAuditDetail"))
  }
  return detail
}

// --- getRbacAuditList ---

interface RbacAuditListWithMeta {
  rows: RbacAuditRow[]
  matchedRuleIds: Set<string>
  lastUpdated: string | null
  oldestUpdated: string | null
}

async function fetchRbacAuditListWithMeta(): Promise<RbacAuditListWithMeta> {
  let namespaced: RawAuditList
  let clustered: RawAuditList
  try {
    ;[namespaced, clustered] = await Promise.all([
      complianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/rbacassessmentreports"),
      complianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/clusterrbacassessmentreports"),
    ])
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    console.warn("[compliance] getRbacAuditList failed:", msg)
    throw new ComplianceDataUnavailableError("rbac", msg)
  }

  const allItems = [...(namespaced.items ?? []), ...(clustered.items ?? [])]
  const rows: RbacAuditRow[] = []
  const matchedRuleIds = new Set<string>()
  for (const item of allItems) {
    const classified = classifyRbacAuditItem(item)
    rows.push(classified.row)
    for (const id of classified.matchedRuleIds) matchedRuleIds.add(id)
  }
  const { newest, oldest } = extractTimestampRange(allItems)
  return { rows, matchedRuleIds, lastUpdated: newest, oldestUpdated: oldest }
}

export async function getRbacAuditList(): Promise<RbacAuditRow[]> {
  const cacheKey = cacheKeys.complianceRbacAuditList()
  const cached = await cacheGet<RbacAuditRow[]>(cacheKey)
  if (cached) return cached

  const meta = await fetchRbacAuditListWithMeta()
  await cacheSet(cacheKey, meta.rows, cacheTtl("complianceRbacAuditList"))
  return meta.rows
}

// --- getRbacAuditDetail ---

export async function getRbacAuditDetail(
  namespace: string,
  name: string,
): Promise<RbacAuditDetail | null> {
  const cacheKey = cacheKeys.complianceRbacAuditDetail(namespace, name)
  const cached = await cacheGet<RbacAuditDetail>(cacheKey)
  if (cached) return cached

  let namespaced: RawAuditList
  let clustered: RawAuditList
  try {
    ;[namespaced, clustered] = await Promise.all([
      complianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/rbacassessmentreports"),
      complianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/clusterrbacassessmentreports"),
    ])
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    console.warn("[compliance] getRbacAuditDetail failed:", msg)
    throw new ComplianceDataUnavailableError("rbac", msg)
  }

  const allItems = [...(namespaced.items ?? []), ...(clustered.items ?? [])]
  const item = allItems.find((i) => {
    const rName = resolveRbacRoleName(i)
    const rNamespace = i.metadata.namespace ?? ""
    return rNamespace === namespace && rName === name
  })
  if (!item) return null

  const { row, matcher } = classifyRbacAuditItem(item)
  const detail: RbacAuditDetail = {
    ...row,
    checks: mapChecks(item.report.checks ?? [], matcher),
  }
  await cacheSet(cacheKey, detail, cacheTtl("complianceRbacAuditDetail"))
  return detail
}

// --- getInfraAuditList ---

interface InfraAuditListWithMeta {
  rows: InfraAuditRow[]
  matchedRuleIds: Set<string>
  lastUpdated: string | null
  oldestUpdated: string | null
}

async function fetchInfraAuditListWithMeta(): Promise<InfraAuditListWithMeta> {
  let namespaced: RawAuditList
  let clustered: RawAuditList
  try {
    ;[namespaced, clustered] = await Promise.all([
      complianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/infraassessmentreports"),
      complianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/clusterinfraassessmentreports"),
    ])
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    console.warn("[compliance] getInfraAuditList failed:", msg)
    throw new ComplianceDataUnavailableError("infra", msg)
  }

  const allItems = [...(namespaced.items ?? []), ...(clustered.items ?? [])]
  const rows: InfraAuditRow[] = []
  const matchedRuleIds = new Set<string>()
  for (const item of allItems) {
    const classified = classifyInfraAuditItem(item)
    rows.push(classified.row)
    for (const id of classified.matchedRuleIds) matchedRuleIds.add(id)
  }
  const { newest, oldest } = extractTimestampRange(allItems)
  return { rows, matchedRuleIds, lastUpdated: newest, oldestUpdated: oldest }
}

export async function getInfraAuditList(): Promise<InfraAuditRow[]> {
  const cacheKey = cacheKeys.complianceInfraAuditList()
  const cached = await cacheGet<InfraAuditRow[]>(cacheKey)
  if (cached) return cached

  const meta = await fetchInfraAuditListWithMeta()
  await cacheSet(cacheKey, meta.rows, cacheTtl("complianceInfraAuditList"))
  return meta.rows
}

// --- getInfraAuditDetail ---

export async function getInfraAuditDetail(node: string): Promise<InfraAuditDetail | null> {
  const cacheKey = cacheKeys.complianceInfraAuditDetail(node)
  const cached = await cacheGet<InfraAuditDetail>(cacheKey)
  if (cached) return cached

  let namespaced: RawAuditList
  let clustered: RawAuditList
  try {
    ;[namespaced, clustered] = await Promise.all([
      complianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/infraassessmentreports"),
      complianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/clusterinfraassessmentreports"),
    ])
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    console.warn("[compliance] getInfraAuditDetail failed:", msg)
    throw new ComplianceDataUnavailableError("infra", msg)
  }

  const allItems = [...(namespaced.items ?? []), ...(clustered.items ?? [])]
  const item = allItems.find((i) => {
    const labels = i.metadata.labels ?? {}
    const rName = labels["trivy-operator.resource.name"] ?? i.metadata.name
    return rName === node
  })
  if (!item) return null

  const { row, matcher } = classifyInfraAuditItem(item)
  const checks = mapChecks(item.report.checks ?? [], matcher)
  const detail: InfraAuditDetail = {
    ...row,
    checks,
  }
  await cacheSet(cacheKey, detail, cacheTtl("complianceInfraAuditDetail"))
  return detail
}

// --- Framework target parsing & check-level acceptance ---

export interface ParsedComplianceTarget {
  namespace: string
  kind: string
  name: string
}

const COMPLIANCE_TARGET_KINDS: Readonly<Record<string, string>> = {
  replicaset: "ReplicaSet",
  daemonset: "DaemonSet",
  statefulset: "StatefulSet",
  pod: "Pod",
  deployment: "Deployment",
  cronjob: "CronJob",
  job: "Job",
  role: "Role",
  clusterrole: "ClusterRole",
  rolebinding: "RoleBinding",
  clusterrolebinding: "ClusterRoleBinding",
  networkpolicy: "NetworkPolicy",
  service: "Service",
  configmap: "ConfigMap",
  node: "Node",
}

const CLUSTER_SCOPED_TARGET_KINDS = new Set(["ClusterRole", "ClusterRoleBinding", "Node"])
const WORKLOAD_TARGET_KINDS = new Set([
  "ReplicaSet",
  "DaemonSet",
  "StatefulSet",
  "Pod",
  "Deployment",
  "CronJob",
  "Job",
])
const RBAC_TARGET_KINDS = new Set(["Role", "ClusterRole"])

/**
 * Parses a Trivy ClusterComplianceReport check target (`<namespace>/<kind>-<name>`,
 * e.g. `"devtools/replicaset-gitea-67f4766475"`, `"/clusterrole-cluster-admin"`,
 * `"/node-narwhal-master-1"`). Fails closed (`null`) on missing/extra slashes,
 * unknown kinds, empty names, or namespace/scope mismatches.
 */
export function parseComplianceTarget(target: unknown): ParsedComplianceTarget | null {
  if (typeof target !== "string" || target.length === 0) return null
  const parts = target.split("/")
  if (parts.length !== 2) return null
  const [namespace, rest] = parts
  const dashIdx = rest.indexOf("-")
  if (dashIdx <= 0) return null
  const rawKind = rest.slice(0, dashIdx).toLowerCase()
  const name = rest.slice(dashIdx + 1)
  const kind = Object.hasOwn(COMPLIANCE_TARGET_KINDS, rawKind) ? COMPLIANCE_TARGET_KINDS[rawKind] : undefined
  if (!kind || name.length === 0) return null
  if (CLUSTER_SCOPED_TARGET_KINDS.has(kind)) {
    if (namespace !== "") return null
  } else if (namespace.length === 0) {
    return null
  }
  return { namespace, kind, name }
}

// D-FW-REPORT-MAP: Trivy ClusterComplianceReport targets use the underlying report's
// `metadata.namespace/metadata.name`, which Trivy hashes for names >63 chars or containing `:`
// (e.g. `monitoring/statefulset-78785dbd5c`, `/clusterrole-54cdc9b678`). Resolving from the
// live ConfigAudit/RBAC report lists recovers the real object name while failing closed to raw
// target parsing whenever the report list is unavailable or lacks explicit kind/name labels.
function buildReportTargetMap(
  configItems: readonly RawAuditReport[],
  configClusterItems: readonly RawAuditReport[],
  rbacNsItems: readonly RawAuditReport[],
  rbacClusterItems: readonly RawAuditReport[],
): Map<string, ParsedComplianceTarget> {
  const map = new Map<string, ParsedComplianceTarget>()
  for (const item of configItems) {
    const ns = item.metadata.namespace ?? ""
    const reportName = item.metadata.name
    if (!ns || !reportName) continue
    const labels = item.metadata.labels ?? {}
    const kind = labels["trivy-operator.resource.kind"]
    const name = labels["trivy-operator.resource.name"]
    if (!kind || !WORKLOAD_TARGET_KINDS.has(kind) || !name) continue
    map.set(`${ns}/${reportName}`, { namespace: ns, kind, name })
  }
  for (const item of configClusterItems) {
    const ns = item.metadata.namespace ?? ""
    const reportName = item.metadata.name
    if (ns !== "" || !reportName) continue
    const labels = item.metadata.labels ?? {}
    const ownerRefs = item.metadata.ownerReferences ?? []
    const kind = labels["trivy-operator.resource.kind"] ?? ownerRefs[0]?.kind
    const name = ownerRefs[0]?.name ?? labels["trivy-operator.resource.name"]
    if (!kind || !name) continue
    map.set(`/${reportName}`, { namespace: "", kind, name })
  }
  for (const item of rbacNsItems) {
    const ns = item.metadata.namespace ?? ""
    const reportName = item.metadata.name
    if (!ns || !reportName) continue
    const labels = item.metadata.labels ?? {}
    const ownerRefs = item.metadata.ownerReferences ?? []
    const kind = labels["trivy-operator.resource.kind"] ?? ownerRefs[0]?.kind
    const name = ownerRefs[0]?.name ?? labels["trivy-operator.resource.name"]
    if (kind !== "Role" || !name) continue
    map.set(`${ns}/${reportName}`, { namespace: ns, kind: "Role", name })
  }
  for (const item of rbacClusterItems) {
    const ns = item.metadata.namespace ?? ""
    const reportName = item.metadata.name
    if (ns !== "" || !reportName) continue
    const labels = item.metadata.labels ?? {}
    const ownerRefs = item.metadata.ownerReferences ?? []
    const kind = labels["trivy-operator.resource.kind"] ?? ownerRefs[0]?.kind
    const name = ownerRefs[0]?.name ?? labels["trivy-operator.resource.name"]
    if (kind !== "ClusterRole" || !name) continue
    map.set(`/${reportName}`, { namespace: "", kind: "ClusterRole", name })
  }
  return map
}

type FrameworkAcceptedToken = "system" | "builtin" | "accept" | "upstream"

type FrameworkCheckDisposition =
  | { status: "dropped" }
  | { status: "actionable" }
  | { status: "accepted"; scope: FrameworkAcceptedToken }

function scopeForAcceptedClass(acceptedClass: AcceptedClass | undefined): FrameworkAcceptedToken {
  return acceptedClass === "upstream" ? "upstream" : "accept"
}

function resolveFrameworkAcceptedScope(scopes: Set<FrameworkAcceptedToken>): AcceptedScope | undefined {
  if (scopes.size === 0) return undefined
  if (scopes.size === 1) return scopes.values().next().value
  return "mixed"
}

function classifyFrameworkCheck(
  check: { checkID?: string; target?: string },
  rsMap: Map<string, ReplicaSetSummary> | null,
  reportTargetMap: ReadonlyMap<string, ParsedComplianceTarget>,
  missingReplicaSets: Set<string>,
): FrameworkCheckDisposition {
  const rawTarget = typeof check.target === "string" ? check.target : ""
  const resolved = reportTargetMap.get(rawTarget) ?? parseComplianceTarget(rawTarget)
  if (!resolved) return { status: "actionable" }

  const checkId = typeof check.checkID === "string" ? check.checkID.trim() : ""
  if (!checkId) return { status: "actionable" }

  if (WORKLOAD_TARGET_KINDS.has(resolved.kind)) {
    let deploymentOwners: readonly string[] | undefined
    if (resolved.kind === "ReplicaSet") {
      const rsState = resolveReplicaSetByName(resolved.namespace, resolved.name, resolved.name, rsMap)
      if (rsState.missingKey) missingReplicaSets.add(rsState.missingKey)
      if (rsState.drop) return { status: "dropped" }
      deploymentOwners = rsState.deploymentOwners
    }
    const matcher = configAuditMatcher(resolved.namespace, resolved.kind, resolved.name, deploymentOwners)
    const ann = matcher(checkId)
    if (!ann.accepted) return { status: "actionable" }
    return { status: "accepted", scope: scopeForAcceptedClass(ann.acceptedClass) }
  }

  if (RBAC_TARGET_KINDS.has(resolved.kind)) {
    const matcher = rbacAuditMatcher(resolved.namespace, resolved.kind, resolved.name)
    const ann = matcher(checkId)
    if (!ann.accepted) return { status: "actionable" }
    return { status: "accepted", scope: scopeForAcceptedClass(ann.acceptedClass) }
  }

  if (resolved.kind === "Node") {
    const matcher = infraAuditMatcher(resolved.name)
    const ann = matcher(checkId)
    if (!ann.accepted) return { status: "actionable" }
    return { status: "accepted", scope: scopeForAcceptedClass(ann.acceptedClass) }
  }

  return { status: "actionable" }
}

const WORKLOAD_KINDS_LOWER = new Set([
  "pod",
  "replicaset",
  "replicationcontroller",
  "deployment",
  "statefulset",
  "daemonset",
  "cronjob",
  "job",
])

interface FrameworkEvalContext {
  excludedNamespaces: ReadonlySet<string> | "unknown"
  targetNamespaces: ReadonlySet<string> | "unknown"
  kubeSystemExcluded: boolean | "unknown"
  supportedConfigAuditKinds: ReadonlySet<string> | "unknown"
  configAuditScannerEnabled: boolean | "unknown"
  rbacScannerEnabled: boolean | "unknown"
  infraScannerEnabled: boolean | "unknown"
  hasMasterNodes: boolean | "unknown"
  hasWorkerNodes: boolean | "unknown"
  hasConfigAuditReports: boolean | "unknown"
  hasRbacReports: boolean | "unknown"
  hasInfraReports: boolean | "unknown"
  nodeRoles: ReadonlyMap<string, "master" | "worker">
  evidenceReports: { config: readonly RawAuditReport[]; rbac: readonly RawAuditReport[]; infra: readonly RawAuditReport[] }
  benchmarkStore: BenchmarkStore
  operatorConfigError: boolean
  hasHttpError: boolean
  failedAuxSources: string[]
}

function parseCsvSet(raw: string | undefined): Set<string> {
  if (!raw) return new Set()
  return new Set(
    raw
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean),
  )
}

function isControlPlaneNodeLabels(labels: Record<string, string> | undefined): boolean {
  if (!labels) return false
  return (
    Object.hasOwn(labels, "node-role.kubernetes.io/control-plane") ||
    Object.hasOwn(labels, "node-role.kubernetes.io/master")
  )
}

function isNamespaceExcluded(ns: string, ctx: FrameworkEvalContext): boolean | "unknown" {
  if (ctx.excludedNamespaces === "unknown" || ctx.targetNamespaces === "unknown") {
    return ns === "kube-system" ? ctx.kubeSystemExcluded : "unknown"
  }
  return ctx.excludedNamespaces.has(ns) || (ctx.targetNamespaces.size > 0 && !ctx.targetNamespaces.has(ns))
}

function matchesSupportedKind(checkKind: string, supported: ReadonlySet<string>): boolean {
  const lower = checkKind.toLowerCase()
  if (lower === "workload") {
    return supported.has("workload") || Array.from(WORKLOAD_KINDS_LOWER).some((k) => supported.has(k))
  }
  if (WORKLOAD_KINDS_LOWER.has(lower)) {
    return supported.has(lower) || supported.has("workload")
  }
  return supported.has(lower)
}

function reportResourceKind(r: RawAuditReport): string {
  return r.metadata.labels?.["trivy-operator.resource.kind"] ?? r.metadata.ownerReferences?.[0]?.kind ?? ""
}

function reportMatchesKind(r: RawAuditReport, targetKind: string): boolean {
  const rk = reportResourceKind(r).toLowerCase()
  if (!rk) return false
  const tk = targetKind.toLowerCase()
  if (tk === "workload") return WORKLOAD_KINDS_LOWER.has(rk)
  return rk === tk
}

function isNodeInfraReport(r: RawAuditReport): boolean {
  const rk = reportResourceKind(r).toLowerCase()
  if (rk === "node") return true
  return !r.metadata.namespace && r.metadata.name.startsWith("node-")
}

// D-FAILURE-FIRST-JOIN: join failures across all report families (namespaced and cluster-scoped
// ConfigAudit, RBAC, and Infra) and across catalogued aliases/legacy IDs via resolveCanonicalCheckId
// BEFORE deciding any non-failing state, independent of nodeRoles (so a failed /api/v1/nodes read
// never filters out or hides an existing failing record).
// Cost: linear scan over loaded reports; escape hatch: index failures by canonical check ID.
function controlEvidenceFailures(spec: RawSpecControl | undefined, ctx: FrameworkEvalContext) {
  const allReports = [
    ...ctx.evidenceReports.config,
    ...ctx.evidenceReports.rbac,
    ...ctx.evidenceReports.infra,
  ]
  const seenCanonicalIds = new Set<string>()
  const failures: Array<RawCheck & { target: string }> = []
  for (const { id } of spec?.checks ?? []) {
    const canonical = resolveCanonicalCheckId(id ?? "")
    if (!canonical || seenCanonicalIds.has(canonical)) continue
    seenCanonicalIds.add(canonical)
    for (const r of allReports) {
      for (const c of r.report?.checks ?? []) {
        if (c.success !== false) continue
        if (resolveCanonicalCheckId(c.checkID ?? "") !== canonical) continue
        const labels = r.metadata.labels ?? {}
        const owner = r.metadata.ownerReferences?.[0]
        const kind = labels["trivy-operator.resource.kind"] ?? owner?.kind ?? "Unknown"
        const name = owner?.name ?? labels["trivy-operator.resource.name"] ?? r.metadata.name
        failures.push({ ...c, target: `${r.metadata.namespace ?? ""}/${kind.toLowerCase()}-${name}` })
      }
    }
  }
  return failures
}

function normalizeNodeType(raw: string | undefined): "master" | "worker" | null {
  if (typeof raw !== "string") return null
  const lower = raw.trim().toLowerCase()
  if (lower === "master" || lower === "control-plane") return "master"
  if (lower === "worker") return "worker"
  return null
}

function buildFrameworkEvalContext(opts: {
  configRes: TryFetchResult<RawAuditList>
  configClusterRes: TryFetchResult<RawAuditList>
  rbacNsRes: TryFetchResult<RawAuditList>
  rbacClusterRes: TryFetchResult<RawAuditList>
  infraNsRes: TryFetchResult<RawAuditList>
  infraClusterRes: TryFetchResult<RawAuditList>
  nodesRes: TryFetchResult<RawNodeList>
  operatorDeployRes: TryFetchResult<RawDeployment>
  trivyConfigRes: TryFetchResult<RawConfigMap>
  benchmarkCmRes: TryFetchResult<RawConfigMapList>
  rsOk: boolean
}): FrameworkEvalContext {
  const {
    configRes,
    configClusterRes,
    rbacNsRes,
    rbacClusterRes,
    infraNsRes,
    infraClusterRes,
    nodesRes,
    operatorDeployRes,
    trivyConfigRes,
    benchmarkCmRes,
    rsOk,
  } = opts

  const failedAuxSources: string[] = []
  if (!rsOk) failedAuxSources.push("replicasets")
  if (!configRes.ok) failedAuxSources.push("configauditreports")
  if (!configClusterRes.ok) failedAuxSources.push("clusterconfigauditreports")
  if (!rbacNsRes.ok) failedAuxSources.push("rbacassessmentreports")
  if (!rbacClusterRes.ok) failedAuxSources.push("clusterrbacassessmentreports")
  if (!infraNsRes.ok) failedAuxSources.push("infraassessmentreports")
  if (!infraClusterRes.ok) failedAuxSources.push("clusterinfraassessmentreports")
  if (!nodesRes.ok) failedAuxSources.push("nodes")
  if (!operatorDeployRes.ok) failedAuxSources.push("deploy/trivy-operator")
  if (!trivyConfigRes.ok) failedAuxSources.push("cm/trivy-operator-trivy-config")

  const operatorConfigError = !operatorDeployRes.ok || !trivyConfigRes.ok
  const hasHttpError = failedAuxSources.length > 0

  const configItems = [
    ...(configRes.ok ? configRes.data.items ?? [] : []),
    ...(configClusterRes.ok ? configClusterRes.data.items ?? [] : []),
  ]
  const rbacItems = [
    ...(rbacNsRes.ok ? rbacNsRes.data.items ?? [] : []),
    ...(rbacClusterRes.ok ? rbacClusterRes.data.items ?? [] : []),
  ]
  const infraItems = [
    ...(infraNsRes.ok ? infraNsRes.data.items ?? [] : []),
    ...(infraClusterRes.ok ? infraClusterRes.data.items ?? [] : []),
  ]

  // Derive trivy-operator effective env config (Deployments get/list is in current RBAC).
  const envList = operatorDeployRes.ok
    ? operatorDeployRes.data.spec?.template?.spec?.containers?.[0]?.env
    : undefined
  const operatorEnv = envList
    ? new Map(envList.filter((e) => typeof e.name === "string").map((e) => [e.name!, e.value ?? ""]))
    : null

  const excludedNamespaces: ReadonlySet<string> | "unknown" = operatorEnv
    ? parseCsvSet(operatorEnv.get("OPERATOR_EXCLUDE_NAMESPACES"))
    : "unknown"
  const targetNamespaces: ReadonlySet<string> | "unknown" = operatorEnv
    ? parseCsvSet(operatorEnv.get("OPERATOR_TARGET_NAMESPACES"))
    : "unknown"

  let kubeSystemExcluded: boolean | "unknown" = "unknown"
  if (excludedNamespaces !== "unknown" && targetNamespaces !== "unknown") {
    kubeSystemExcluded =
      excludedNamespaces.has("kube-system") || (targetNamespaces.size > 0 && !targetNamespaces.has("kube-system"))
  } else if (operatorDeployRes.ok && configRes.ok) {
    const hasKubeSystemReport = configItems.some((i) => i.metadata.namespace === "kube-system")
    kubeSystemExcluded = hasKubeSystemReport ? false : "unknown"
  }

  const parseScannerFlag = (envKey: string): boolean | "unknown" => {
    if (!operatorEnv) return "unknown"
    const v = operatorEnv.get(envKey)
    if (v === "false") return false
    if (v === "true") return true
    return "unknown"
  }

  // Derive supportedConfigAuditKinds from trivy-operator-trivy-config ConfigMap (current RBAC).
  const rawSupportedKinds = trivyConfigRes.ok
    ? trivyConfigRes.data.data?.["trivy.supportedConfigAuditKinds"]
    : undefined
  const supportedConfigAuditKinds: ReadonlySet<string> | "unknown" =
    typeof rawSupportedKinds === "string" && rawSupportedKinds.trim().length > 0
      ? new Set(
          rawSupportedKinds
            .split(",")
            .map((s) => s.trim().toLowerCase())
            .filter(Boolean),
        )
      : "unknown"

  // Derive node roles and per-node-type mapping from /api/v1/nodes (current RBAC).
  let hasMasterNodes: boolean | "unknown" = "unknown"
  let hasWorkerNodes: boolean | "unknown" = "unknown"
  const nodeRoleByName = new Map<string, "master" | "worker">()
  if (nodesRes.ok && Array.isArray(nodesRes.data.items)) {
    const nodes = nodesRes.data.items
    let masterCount = 0
    let workerCount = 0
    for (const n of nodes) {
      const role: "master" | "worker" = isControlPlaneNodeLabels(n.metadata?.labels) ? "master" : "worker"
      if (role === "master") masterCount++
      else workerCount++
      if (typeof n.metadata?.name === "string" && n.metadata.name.length > 0) {
        nodeRoleByName.set(n.metadata.name, role)
      }
    }
    hasMasterNodes = masterCount > 0
    hasWorkerNodes = workerCount > 0
  }

  const benchmarkStore = buildBenchmarkStore({
    configMapRes: benchmarkCmRes,
    nodesAvailable: nodesRes.ok,
    clusterNodes: nodeRoleByName,
  })

  return {
    nodeRoles: nodeRoleByName,
    evidenceReports: { config: configItems, rbac: rbacItems, infra: infraItems },
    benchmarkStore,
    excludedNamespaces,
    targetNamespaces,
    kubeSystemExcluded,
    supportedConfigAuditKinds,
    configAuditScannerEnabled: parseScannerFlag("OPERATOR_CONFIG_AUDIT_SCANNER_ENABLED"),
    rbacScannerEnabled: parseScannerFlag("OPERATOR_RBAC_ASSESSMENT_SCANNER_ENABLED"),
    infraScannerEnabled: parseScannerFlag("OPERATOR_INFRA_ASSESSMENT_SCANNER_ENABLED"),
    hasMasterNodes,
    hasWorkerNodes,
    hasConfigAuditReports: configRes.ok && configClusterRes.ok ? configItems.length > 0 : "unknown",
    hasRbacReports: rbacNsRes.ok && rbacClusterRes.ok ? rbacItems.length > 0 : "unknown",
    hasInfraReports: infraNsRes.ok && infraClusterRes.ok ? infraItems.length > 0 : "unknown",
    operatorConfigError,
    hasHttpError,
    failedAuxSources,
  }
}

interface NonFailingControlOutcome {
  evaluationState: ControlEvaluationState
  notEvaluatedReason?: ControlNotEvaluatedReason
  notEvaluatedDetail?: string
}

// D-CHECK-SCOPE-NS: Enforce both `requiredNamespaces` and `scopeNamespaces` from the check
// catalogue when filtering evidence reports. A check scoped to specific namespaces (e.g.
// KSV110 -> `default`) cannot reach the positive "no failure recorded" state from reports
// in unrelated namespaces (e.g. `dev`). Cost: one namespace check per report; escape hatch:
// regenerate the catalogue if an upstream Rego check drops its namespace predicate.
function getApplicableNamespaces(entry: TrivyCheckCatalogueEntry): readonly string[] | null {
  const req = entry.requiredNamespaces?.length ? entry.requiredNamespaces : null
  const scope = entry.scopeNamespaces?.length ? entry.scopeNamespaces : null
  if (req && scope) return req.filter((ns) => scope.includes(ns))
  return req ?? scope
}

// D-CHECK-CATALOGUE: trivy-operator 0.25.0 drops all `success: true` checks in `evaluate()`
// because passed Rego rules carry empty `Messages` (confirmed even with `recordFailedChecksOnly=false`).
// Therefore positive success records never exist in this deployment. A non-failing control reaches
// `"passed"` (displayed as "no failure recorded — scanner records failures only") ONLY when EVERY
// check ID is in the pinned embedded `trivy-checks` catalogue, the operator scope covers at least
// one applicable kind/node type/namespace, and at least one in-scope report of the right family and
// kind exists. Cost: regenerate `trivy-check-catalogue.generated.json` on trivy-operator/chart bumps
// (enforced by `gen-trivy-check-catalogue.mjs --check` and R391). Escape hatch: restore positive
// success-record evidence once an upstream operator version emits `success: true` checks.
function evaluateCheckAgainstCatalogue(
  normId: string,
  specControl: RawSpecControl,
  evalCtx: FrameworkEvalContext,
): NonFailingControlOutcome {
  const entry = getCatalogueCheck(normId)
  if (!entry) {
    return {
      evaluationState: "not_evaluated",
      notEvaluatedReason: "not_in_catalogue",
      notEvaluatedDetail: `${normId} not in trivy-checks catalogue`,
    }
  }

  if (entry.reportFamily === "infra") {
    if (evalCtx.infraScannerEnabled === false) {
      return {
        evaluationState: "not_evaluated",
        notEvaluatedReason: "scanner_disabled",
        notEvaluatedDetail: "infraAssessmentScannerEnabled=false",
      }
    }
    if (evalCtx.operatorConfigError || evalCtx.hasInfraReports === "unknown") {
      return {
        evaluationState: "not_evaluated",
        notEvaluatedReason: "coverage_unknown",
        notEvaluatedDetail: "infra report or operator config lookup failed",
      }
    }

    if (entry.kinds.includes("Node")) {
      if (evalCtx.hasMasterNodes === "unknown" || evalCtx.hasWorkerNodes === "unknown") {
        return {
          evaluationState: "not_evaluated",
          notEvaluatedReason: "coverage_unknown",
          notEvaluatedDetail: "node list unavailable",
        }
      }
      const declaredNodeTypes = new Set<"master" | "worker">()
      const ctrlNodeType = normalizeNodeType(specControl.nodeType)
      if (ctrlNodeType) declaredNodeTypes.add(ctrlNodeType)
      for (const cmd of specControl.commands ?? []) {
        const nt = normalizeNodeType(cmd.nodeType)
        if (nt) declaredNodeTypes.add(nt)
      }
      const catalogueNodeTypes = entry.nodeTypes ?? ["master", "worker"]
      const applicableNodeTypes =
        declaredNodeTypes.size > 0
          ? catalogueNodeTypes.filter((nt) => declaredNodeTypes.has(nt))
          : [...catalogueNodeTypes]
      const effectiveNodeTypes = applicableNodeTypes.length > 0 ? applicableNodeTypes : [...declaredNodeTypes]

      if (evalCtx.hasMasterNodes === false && evalCtx.hasWorkerNodes === false) {
        return {
          evaluationState: "not_evaluated",
          notEvaluatedReason: "missing_node_type",
          notEvaluatedDetail: "no nodes on cluster",
        }
      }
      const presentNodeTypes = effectiveNodeTypes.filter((nt) =>
        nt === "master" ? evalCtx.hasMasterNodes === true : evalCtx.hasWorkerNodes === true,
      )
      if (presentNodeTypes.length === 0) {
        const missingLabel =
          effectiveNodeTypes.length === 1 && effectiveNodeTypes[0] === "worker"
            ? "no worker nodes on cluster"
            : effectiveNodeTypes.length === 1 && effectiveNodeTypes[0] === "master"
              ? "no control-plane nodes on cluster"
              : "no matching node types on cluster"
        return {
          evaluationState: "not_evaluated",
          notEvaluatedReason: "missing_node_type",
          notEvaluatedDetail: missingLabel,
        }
      }

      const hasMatchingNodeReport = evalCtx.evidenceReports.infra.some((r) => {
        if (!isNodeInfraReport(r)) return false
        const nodeName = r.metadata.labels?.["trivy-operator.resource.name"] ?? r.metadata.name.replace(/^node-/, "")
        const role = evalCtx.nodeRoles.get(nodeName)
        return role !== undefined && presentNodeTypes.includes(role)
      })
      if (!hasMatchingNodeReport) {
        return {
          evaluationState: "not_evaluated",
          notEvaluatedReason: "no_report_for_family",
          notEvaluatedDetail: `no ClusterInfraAssessmentReport for ${presentNodeTypes.join("/")} node (${normId})`,
        }
      }
      // D-INFRA-NO-PER-CHECK-EVIDENCE: node-collector commands that return empty output leave no
      // trace in ClusterInfraAssessmentReport. Infra checks therefore NEVER get the positive state
      // from report existence — failed when a failing record exists, else not_evaluated.
      // Cost: most CIS node-level controls display as not evaluated by the scanner (verified on-node
      // via scripts/test/verify-cluster.sh). Escape hatch: re-enable positive state only when an
      // upstream scanner records explicit per-check evaluated/passed results.
      return {
        evaluationState: "not_evaluated",
        notEvaluatedReason: "infra_no_per_check_evidence",
        notEvaluatedDetail: `node/control-plane check (${normId}): the scanner leaves no per-check evidence`,
      }
    }

    // Pod-level InfraAssessment checks (control-plane static pods in kube-system).
    for (const ns of entry.requiredNamespaces ?? []) {
      const excl = isNamespaceExcluded(ns, evalCtx)
      if (excl === true) {
        return {
          evaluationState: "not_evaluated",
          notEvaluatedReason: "excluded_namespace",
          notEvaluatedDetail: `${ns} excluded by OPERATOR_EXCLUDE_NAMESPACES`,
        }
      }
      if (excl === "unknown") {
        return {
          evaluationState: "not_evaluated",
          notEvaluatedReason: "coverage_unknown",
          notEvaluatedDetail: `${ns} namespace scope unknown`,
        }
      }
    }
    if (evalCtx.supportedConfigAuditKinds === "unknown") {
      return {
        evaluationState: "not_evaluated",
        notEvaluatedReason: "coverage_unknown",
        notEvaluatedDetail: "trivy.supportedConfigAuditKinds unavailable",
      }
    }
    const auditedKinds = entry.kinds.filter((k) => matchesSupportedKind(k, evalCtx.supportedConfigAuditKinds as ReadonlySet<string>))
    if (auditedKinds.length === 0) {
      return {
        evaluationState: "not_evaluated",
        notEvaluatedReason: "unsupported_kind",
        notEvaluatedDetail: `${entry.kinds.join("/")} omitted from trivy.supportedConfigAuditKinds`,
      }
    }
    const matchingInfraReports = evalCtx.evidenceReports.infra.filter((r) =>
      auditedKinds.some((k) => reportMatchesKind(r, k)),
    )
    const inScopeInfraReports = matchingInfraReports.filter((r) => {
      const ns = r.metadata.namespace ?? ""
      if (entry.requiredNamespaces && entry.requiredNamespaces.length > 0) {
        return entry.requiredNamespaces.includes(ns) && isNamespaceExcluded(ns, evalCtx) === false
      }
      return ns === "" || isNamespaceExcluded(ns, evalCtx) === false
    })
    if (inScopeInfraReports.length === 0) {
      if (matchingInfraReports.length > 0 && matchingInfraReports.every((r) => isNamespaceExcluded(r.metadata.namespace ?? "", evalCtx) === true)) {
        return {
          evaluationState: "not_evaluated",
          notEvaluatedReason: "excluded_namespace",
          notEvaluatedDetail: `only excluded-namespace InfraAssessmentReports exist for ${normId}`,
        }
      }
      return {
        evaluationState: "not_evaluated",
        notEvaluatedReason: "no_report_for_family",
        notEvaluatedDetail: `no in-scope ${auditedKinds.join("/")} InfraAssessmentReport for ${normId}`,
      }
    }
    // D-INFRA-NO-PER-CHECK-EVIDENCE: control-plane components without their own report leave no
    // trace in Pod InfraAssessmentReports, so existence of any kube-system Pod infra report cannot
    // prove a specific component's check ran. Never mark infra checks positive from report existence.
    return {
      evaluationState: "not_evaluated",
      notEvaluatedReason: "infra_no_per_check_evidence",
      notEvaluatedDetail: `node/control-plane check (${normId}): the scanner leaves no per-check evidence`,
    }
  }

  if (entry.reportFamily === "rbac") {
    if (evalCtx.rbacScannerEnabled === false) {
      return {
        evaluationState: "not_evaluated",
        notEvaluatedReason: "scanner_disabled",
        notEvaluatedDetail: "rbacAssessmentScannerEnabled=false",
      }
    }
    if (evalCtx.operatorConfigError || evalCtx.hasRbacReports === "unknown" || evalCtx.supportedConfigAuditKinds === "unknown") {
      return {
        evaluationState: "not_evaluated",
        notEvaluatedReason: "coverage_unknown",
        notEvaluatedDetail: "RBAC assessment report or operator config lookup failed",
      }
    }
    const auditedKinds = entry.kinds.filter((k) => matchesSupportedKind(k, evalCtx.supportedConfigAuditKinds as ReadonlySet<string>))
    if (auditedKinds.length === 0) {
      return {
        evaluationState: "not_evaluated",
        notEvaluatedReason: "unsupported_kind",
        notEvaluatedDetail: `${entry.kinds.join("/")} omitted from trivy.supportedConfigAuditKinds`,
      }
    }
    const applicableRbacNamespaces = getApplicableNamespaces(entry)
    const matchingRbacReports = evalCtx.evidenceReports.rbac.filter((r) =>
      auditedKinds.some((k) => reportMatchesKind(r, k)),
    )
    const inScopeRbacReports = matchingRbacReports.filter((r) => {
      const ns = r.metadata.namespace ?? ""
      if (applicableRbacNamespaces !== null) {
        return applicableRbacNamespaces.includes(ns) && isNamespaceExcluded(ns, evalCtx) === false
      }
      const kind = reportResourceKind(r)
      if (kind === "ClusterRole" || !ns) return true
      return isNamespaceExcluded(ns, evalCtx) === false
    })
    if (inScopeRbacReports.length === 0) {
      if (applicableRbacNamespaces !== null) {
        return {
          evaluationState: "not_evaluated",
          notEvaluatedReason: "no_report_in_scope_namespace",
          notEvaluatedDetail: `no report in the namespaces this check applies to (${applicableRbacNamespaces.join(", ")}) for ${normId}`,
        }
      }
      if (matchingRbacReports.length > 0 && matchingRbacReports.every((r) => r.metadata.namespace && isNamespaceExcluded(r.metadata.namespace, evalCtx) === true)) {
        return {
          evaluationState: "not_evaluated",
          notEvaluatedReason: "excluded_namespace",
          notEvaluatedDetail: `only excluded-namespace RbacAssessmentReports exist for ${normId}`,
        }
      }
      return {
        evaluationState: "not_evaluated",
        notEvaluatedReason: "no_report_for_family",
        notEvaluatedDetail: `no in-scope ${auditedKinds.join("/")} RbacAssessmentReport for ${normId}`,
      }
    }
    return { evaluationState: "passed" }
  }

  // ConfigAudit family
  if (evalCtx.configAuditScannerEnabled === false) {
    return {
      evaluationState: "not_evaluated",
      notEvaluatedReason: "scanner_disabled",
      notEvaluatedDetail: "configAuditScannerEnabled=false",
    }
  }
  if (evalCtx.operatorConfigError || evalCtx.hasConfigAuditReports === "unknown" || evalCtx.supportedConfigAuditKinds === "unknown") {
    return {
      evaluationState: "not_evaluated",
      notEvaluatedReason: "coverage_unknown",
      notEvaluatedDetail: "ConfigAudit report or operator config lookup failed",
    }
  }
  for (const ns of [...(entry.requiredNamespaces ?? []), ...(entry.scopeNamespaces ?? [])]) {
    const excl = isNamespaceExcluded(ns, evalCtx)
    if (excl === true) {
      return {
        evaluationState: "not_evaluated",
        notEvaluatedReason: "excluded_namespace",
        notEvaluatedDetail: `${ns} excluded by OPERATOR_EXCLUDE_NAMESPACES`,
      }
    }
    if (excl === "unknown") {
      return {
        evaluationState: "not_evaluated",
        notEvaluatedReason: "coverage_unknown",
        notEvaluatedDetail: `${ns} namespace scope unknown`,
      }
    }
  }
  const auditedKinds = entry.kinds.filter((k) => matchesSupportedKind(k, evalCtx.supportedConfigAuditKinds as ReadonlySet<string>))
  if (auditedKinds.length === 0) {
    return {
      evaluationState: "not_evaluated",
      notEvaluatedReason: "unsupported_kind",
      notEvaluatedDetail: `${entry.kinds.join("/")} omitted from trivy.supportedConfigAuditKinds`,
    }
  }
  const applicableNamespaces = getApplicableNamespaces(entry)
  const matchingConfigReports = evalCtx.evidenceReports.config.filter((r) =>
    auditedKinds.some((k) => reportMatchesKind(r, k)),
  )
  const inScopeConfigReports = matchingConfigReports.filter((r) => {
    const ns = r.metadata.namespace ?? ""
    if (applicableNamespaces !== null) {
      return applicableNamespaces.includes(ns) && isNamespaceExcluded(ns, evalCtx) === false
    }
    if (!ns) return true
    return isNamespaceExcluded(ns, evalCtx) === false
  })
  if (inScopeConfigReports.length === 0) {
    if (applicableNamespaces !== null) {
      return {
        evaluationState: "not_evaluated",
        notEvaluatedReason: "no_report_in_scope_namespace",
        notEvaluatedDetail: `no report in the namespaces this check applies to (${applicableNamespaces.join(", ")}) for ${normId}`,
      }
    }
    if (matchingConfigReports.length > 0 && matchingConfigReports.every((r) => r.metadata.namespace && isNamespaceExcluded(r.metadata.namespace, evalCtx) === true)) {
      return {
        evaluationState: "not_evaluated",
        notEvaluatedReason: "excluded_namespace",
        notEvaluatedDetail: `only excluded-namespace ${auditedKinds.join("/")} ConfigAuditReports exist for ${normId}`,
      }
    }
    return {
      evaluationState: "not_evaluated",
      notEvaluatedReason: "no_report_for_family",
      notEvaluatedDetail: `no in-scope ${auditedKinds.join("/")} ConfigAuditReport for ${normId}`,
    }
  }
  return { evaluationState: "passed" }
}

function classifyNonFailingControl(
  resultControl: NonNullable<NonNullable<RawComplianceReport["status"]>["detailReport"]>["results"] extends Array<infer R> | undefined ? R : never,
  specControl: RawSpecControl | undefined,
  evalCtx: FrameworkEvalContext,
  specHasAutomatedChecks: boolean,
): NonFailingControlOutcome {
  if (evalCtx.hasHttpError) {
    return { evaluationState: "not_evaluated", notEvaluatedReason: "coverage_unknown", notEvaluatedDetail: evalCtx.failedAuxSources.join(", ") }
  }
  const checks = resultControl.checks ?? []
  if (checks.length === 0 && !specControl?.checks?.length) {
    return {
      evaluationState: "not_evaluated",
      notEvaluatedReason: "empty_checks",
      notEvaluatedDetail: "detailReport.results[].checks is empty",
    }
  }

  if (!specControl) {
    return {
      evaluationState: "not_evaluated",
      notEvaluatedReason: "coverage_unknown",
      notEvaluatedDetail: evalCtx.operatorConfigError
        ? "auxiliary operator config lookup failed"
        : "spec.compliance.controls[] metadata unavailable",
    }
  }

  // Kubernetes YAML serialization (`omitempty`) drops empty `checks` on manual controls
  // (e.g. CIS 3.1.1, NSA 3.0). When other controls in the same benchmark carry spec checks
  // and this control has neither checks nor commands, classify it as `manual` rather than
  // `coverage_unknown`; missing check linkage cannot establish report family or scope.
  if (!Array.isArray(specControl.checks)) {
    const hasCommands = Array.isArray(specControl.commands) && specControl.commands.length > 0
    if (specHasAutomatedChecks && !hasCommands) {
      return {
        evaluationState: "not_evaluated",
        notEvaluatedReason: "manual",
        notEvaluatedDetail: "no automated Trivy check IDs in spec",
      }
    }
    return {
      evaluationState: "not_evaluated",
      notEvaluatedReason: "coverage_unknown",
      notEvaluatedDetail: evalCtx.operatorConfigError
        ? "auxiliary operator config lookup failed"
        : "spec.compliance.controls[].checks metadata unavailable",
    }
  }

  if (specControl.checks.length === 0) {
    return {
      evaluationState: "not_evaluated",
      notEvaluatedReason: "manual",
      notEvaluatedDetail: "no automated Trivy check IDs in spec",
    }
  }

  const rawIds = specControl.checks.map((c) => (c.id ?? "").trim()).filter(Boolean)
  if (rawIds.length === 0) {
    return {
      evaluationState: "not_evaluated",
      notEvaluatedReason: "manual",
      notEvaluatedDetail: "no automated Trivy check IDs in spec",
    }
  }

  // Lower-case check IDs in spec (e.g. avd-ksv-*) never match Trivy report IDs.
  const nonUpperIds = rawIds.filter((id) => id !== id.toUpperCase())
  if (nonUpperIds.length > 0) {
    return {
      evaluationState: "not_evaluated",
      notEvaluatedReason: "unlinked_check_id",
      notEvaluatedDetail: nonUpperIds.join(", "),
    }
  }

  const normIds = rawIds.map(resolveCanonicalCheckId)
  for (const id of normIds) {
    const outcome = evaluateCheckAgainstCatalogue(id, specControl, evalCtx)
    if (outcome.evaluationState !== "passed") {
      return outcome
    }
  }
  return { evaluationState: "passed" }
}

function buildComplianceFrameworkData(
  item: RawComplianceReport,
  rsMap: Map<string, ReplicaSetSummary> | null,
  reportTargetMap: ReadonlyMap<string, ParsedComplianceTarget>,
  missingReplicaSets: Set<string>,
  evalCtx: FrameworkEvalContext,
): { summary: ComplianceFramework; detail: ComplianceFrameworkDetail } {
  const passCount = item.status?.summary?.passCount ?? 0
  const failCount = item.status?.summary?.failCount ?? 0

  // status.detailReport.results 가 있으면 per-control 사용, 없으면 spec.compliance.controls 폴백.
  // (Trivy reportType=summary 이면 detailReport 비어있음 — 실패 증거 없는 컨트롤은 not_evaluated.
  //  reportType=all 이면 results[].checks[].success 및 spec/cluster coverage로 3-상태 집계.)
  const reportedControls = item.status?.detailReport?.results ?? []
  const detailControls = [...reportedControls]
  const specControls = item.spec.compliance.controls ?? []
  const specControlMap = new Map(specControls.map((sc) => [sc.id, sc]))
  const specHasAutomatedChecks = specControls.some((sc) => Array.isArray(sc.checks) && sc.checks.length > 0)

  // D2: Underlying failures outrank the 6-hourly CCR, including omitted controls.
  // Cost: check-ID join; escape hatch: remove once CCR freshness is guaranteed upstream.
  for (const sc of specControls) {
    if (!detailControls.some((c) => c.id === sc.id) && controlEvidenceFailures(sc, evalCtx).length > 0) {
      detailControls.push({ ...sc, checks: [] })
    }
  }
  let controls: ComplianceControl[]
  if (detailControls.length > 0) {
    controls = detailControls.map((c) => {
      const checks = [...(c.checks ?? [])]
      const specControl = specControlMap.get(c.id)
      const evidenceSpec = specControl?.checks ? specControl : { ...specControl, ...c, checks: checks.map((k) => ({ id: k.checkID })) }
      for (const failure of controlEvidenceFailures(evidenceSpec, evalCtx)) {
        const target = parseComplianceTarget(failure.target)
        const alreadyReported = checks.some((k) => {
          if (k.success !== false) return false
          if (resolveCanonicalCheckId(k.checkID ?? "") !== resolveCanonicalCheckId(failure.checkID)) return false
          const resolved = reportTargetMap.get(k.target ?? "") ?? parseComplianceTarget(k.target)
          if (resolved && target) {
            return resolved.namespace === target.namespace && resolved.kind === target.kind && resolved.name === target.name
          }
          return (k.target ?? "") === failure.target
        })
        if (!alreadyReported) checks.push(failure)
      }
      let ctrlPass = 0
      let ctrlFail = 0
      let actionableFailCount = 0
      let acceptedFailCount = 0
      let droppedFailCount = 0
      const scopes = new Set<FrameworkAcceptedToken>()

      for (const k of checks) {
        if (k.success === true) {
          ctrlPass++
          continue
        }
        ctrlFail++
        const disp = classifyFrameworkCheck(k, rsMap, reportTargetMap, missingReplicaSets)
        if (disp.status === "accepted") {
          acceptedFailCount++
          scopes.add(disp.scope)
        } else if (disp.status === "actionable") {
          actionableFailCount++
        } else if (disp.status === "dropped") {
          droppedFailCount++
        }
      }

      if (ctrlFail > 0) {
        const accepted = ctrlFail > 0 && actionableFailCount === 0 && acceptedFailCount > 0
        return {
          id: c.id,
          name: c.name,
          severity: mapSeverity(c.severity),
          passCount: ctrlPass,
          failCount: ctrlFail,
          actionableFailCount,
          acceptedFailCount,
          droppedFailCount,
          evaluationState: "failed" as const,
          accepted,
          ...(accepted ? { acceptedScope: resolveFrameworkAcceptedScope(scopes) } : {}),
          description: c.description,
        }
      }

      const evalOutcome = classifyNonFailingControl(c, specControlMap.get(c.id), evalCtx, specHasAutomatedChecks)
      return {
        id: c.id,
        name: c.name,
        severity: mapSeverity(c.severity),
        passCount: evalOutcome.evaluationState === "passed" ? ctrlPass : 0,
        failCount: 0,
        actionableFailCount: 0,
        acceptedFailCount: 0,
        droppedFailCount: 0,
        evaluationState: evalOutcome.evaluationState,
        ...(evalOutcome.notEvaluatedReason ? { notEvaluatedReason: evalOutcome.notEvaluatedReason } : {}),
        ...(evalOutcome.notEvaluatedDetail ? { notEvaluatedDetail: evalOutcome.notEvaluatedDetail } : {}),
        accepted: false,
        description: c.description,
      }
    })
    // Materialise any spec control omitted from status.detailReport.results so it stays visible
    // in the UI and in notEvaluatedCount instead of silently disappearing.
    const detailControlIds = new Set(detailControls.map((c) => c.id))
    for (const sc of specControls) {
      if (detailControlIds.has(sc.id)) continue
      controls.push({
        id: sc.id,
        name: sc.name,
        severity: mapSeverity(sc.severity),
        passCount: 0,
        failCount: 0,
        actionableFailCount: 0,
        acceptedFailCount: 0,
        droppedFailCount: 0,
        evaluationState: "not_evaluated",
        notEvaluatedReason: "absent_from_report",
        notEvaluatedDetail: "absent from status.detailReport.results",
        ...(evalCtx.hasHttpError ? { notEvaluatedReason: "coverage_unknown" as const, notEvaluatedDetail: evalCtx.failedAuxSources.join(", ") } : {}),
        accepted: false,
        description: sc.description,
      })
    }
  } else {
    controls = specControls.map((c) => ({
      id: c.id,
      name: c.name,
      severity: mapSeverity(c.severity),
      passCount: 0,
      failCount: 0,
      actionableFailCount: 0,
      acceptedFailCount: 0,
      droppedFailCount: 0,
      evaluationState: "not_evaluated" as const,
      notEvaluatedReason: "summary_only" as const,
      notEvaluatedDetail: "ClusterComplianceReport detailReport.results absent (reportType=summary)",
      ...(evalCtx.hasHttpError ? { notEvaluatedReason: "coverage_unknown" as const, notEvaluatedDetail: evalCtx.failedAuxSources.join(", ") } : {}),
      accepted: false,
      description: c.description,
    }))
  }

  if (item.spec.compliance.id === "k8s-cis-1.23") {
    controls = controls.map((ctrl) => mergeCisControlWithBenchmark(ctrl, evalCtx.benchmarkStore))
  }

  let actionableFailCount: number
  let acceptedFailCount: number
  let droppedFailCount: number
  let droppedCheckCount: number
  let evaluatedPassCount: number
  let benchmarkTestedPassCount: number
  let benchmarkTestedFailCount: number
  let noFailureRecordedPassCount: number
  let notEvaluatedCount: number
  let effectiveFailCount = failCount
  let unaccountedFailTotal = 0

  const totalControls = Math.max(controls.length, specControls.length) || passCount + failCount

  if (detailControls.length > 0) {
    const detailFailControls = controls.filter((c) => c.failCount > 0).length
    const detailActionableFail = controls.filter((c) => (c.actionableFailCount ?? 0) > 0).length
    const detailAcceptedFail = controls.filter((c) => c.accepted === true).length
    const detailDroppedOnlyFail = controls.filter(
      (c) =>
        c.failCount > 0 &&
        (c.actionableFailCount ?? 0) === 0 &&
        (c.acceptedFailCount ?? 0) === 0 &&
        (c.droppedFailCount ?? 0) > 0,
    ).length
    // Fail closed if summary.failCount exceeds failing controls present in detailReport.results.
    const unaccountedFail = Math.max(0, failCount - detailFailControls)
    unaccountedFailTotal = unaccountedFail
    effectiveFailCount = Math.max(failCount, detailFailControls)
    actionableFailCount = detailActionableFail + unaccountedFail
    acceptedFailCount = detailAcceptedFail
    droppedFailCount = detailDroppedOnlyFail
    droppedCheckCount = controls.reduce((acc, c) => acc + (c.droppedFailCount ?? 0), 0)
    evaluatedPassCount = controls.filter((c) => c.evaluationState === "passed").length
    benchmarkTestedPassCount = controls.filter((c) => c.evaluationState === "passed" && c.testedByBenchmark === true).length
    benchmarkTestedFailCount = controls.filter((c) => c.evaluationState === "failed" && c.benchmarkEvidence?.verdict === "failed").length
    noFailureRecordedPassCount = controls.filter((c) => c.evaluationState === "passed" && c.testedByBenchmark !== true).length
    notEvaluatedCount = controls.filter((c) => c.evaluationState === "not_evaluated").length
  } else {
    // reportType=summary lacks per-check targets to verify exceptions or control evaluation — fail closed.
    actionableFailCount = failCount
    acceptedFailCount = 0
    droppedFailCount = 0
    droppedCheckCount = 0
    evaluatedPassCount = 0
    benchmarkTestedPassCount = 0
    benchmarkTestedFailCount = 0
    noFailureRecordedPassCount = 0
    notEvaluatedCount = Math.max(0, totalControls - failCount)
  }

  const passRate = totalControls > 0 ? Math.min(1, passCount / totalControls) : 0
  const evaluatedPassRate = totalControls > 0 ? Math.min(1, evaluatedPassCount / totalControls) : 0
  const effectiveNumerator = Math.max(0, evaluatedPassCount + acceptedFailCount + droppedFailCount - unaccountedFailTotal)
  const effectivePassRate = totalControls > 0 ? Math.min(1, effectiveNumerator / totalControls) : 0
  const reportTimestamp = item.status?.updateTimestamp ?? item.metadata.creationTimestamp

  const summary: ComplianceFramework = {
    id: item.spec.compliance.id,
    title: item.spec.compliance.title,
    passCount,
    rawPassCount: passCount,
    evaluatedPassCount,
    benchmarkTestedPassCount,
    benchmarkTestedFailCount,
    noFailureRecordedPassCount,
    notEvaluatedCount,
    failCount: effectiveFailCount,
    actionableFailCount,
    acceptedFailCount,
    droppedFailCount,
    droppedCheckCount,
    totalControls,
    passRate,
    evaluatedPassRate,
    effectivePassRate,
    ...(evalCtx.hasHttpError ||
    (item.spec.compliance.id === "k8s-cis-1.23" && evalCtx.benchmarkStore.listFailed) ||
    controls.some((c) => c.notEvaluatedReason === "coverage_unknown")
      ? { coverageUnknown: true }
      : {}),
    ...(reportTimestamp ? { lastUpdated: reportTimestamp } : {}),
  }
  return {
    summary,
    detail: {
      ...summary,
      controls,
    },
  }
}

async function fetchFrameworkContext(): Promise<{
  list: RawComplianceList
  rsMap: Map<string, ReplicaSetSummary> | null
  reportTargetMap: Map<string, ParsedComplianceTarget>
  evalCtx: FrameworkEvalContext
}> {
  let list: RawComplianceList
  try {
    list = await complianceK8sFetch<RawComplianceList>(
      "/apis/aquasecurity.github.io/v1alpha1/clustercompliancereports",
    )
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    throw new ComplianceDataUnavailableError("frameworks", msg)
  }

  const [
    rsMap,
    configRes,
    configClusterRes,
    rbacNsRes,
    rbacClusterRes,
    infraNsRes,
    infraClusterRes,
    nodesRes,
    operatorDeployRes,
    trivyConfigRes,
    benchmarkCmRes,
  ] = await Promise.all([
    fetchReplicaSetMap(),
    tryComplianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/configauditreports"),
    tryComplianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/clusterconfigauditreports"),
    tryComplianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/rbacassessmentreports"),
    tryComplianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/clusterrbacassessmentreports"),
    tryComplianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/infraassessmentreports"),
    tryComplianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/clusterinfraassessmentreports"),
    tryComplianceK8sFetch<RawNodeList>("/api/v1/nodes"),
    tryComplianceK8sFetch<RawDeployment>("/apis/apps/v1/namespaces/security-system/deployments/trivy-operator"),
    tryComplianceK8sFetch<RawConfigMap>("/api/v1/namespaces/security-system/configmaps/trivy-operator-trivy-config"),
    tryComplianceK8sFetch<RawConfigMapList>(BENCHMARK_CONFIGMAP_LIST_PATH),
  ])

  const evalCtx = buildFrameworkEvalContext({
    configRes,
    configClusterRes,
    rbacNsRes,
    rbacClusterRes,
    infraNsRes,
    infraClusterRes,
    nodesRes,
    operatorDeployRes,
    trivyConfigRes,
    benchmarkCmRes,
    rsOk: rsMap !== null,
  })

  return {
    list,
    rsMap,
    reportTargetMap: buildReportTargetMap(
      configRes.ok ? configRes.data.items ?? [] : [],
      configClusterRes.ok ? configClusterRes.data.items ?? [] : [],
      rbacNsRes.ok ? rbacNsRes.data.items ?? [] : [],
      rbacClusterRes.ok ? rbacClusterRes.data.items ?? [] : [],
    ),
    evalCtx,
  }
}

// --- getComplianceFrameworks ---

interface FrameworksWithMeta {
  frameworks: ComplianceFramework[]
  lastUpdated: string | null
  oldestUpdated: string | null
  partial: boolean
  partialSources: string[]
  matchedBenchmarkRuleIds: Set<string>
  benchmarkListFailed: boolean
}

async function fetchComplianceFrameworksWithMeta(): Promise<FrameworksWithMeta> {
  const { list, rsMap, reportTargetMap, evalCtx } = await fetchFrameworkContext()
  const missingReplicaSets = new Set<string>()
  const frameworks: ComplianceFramework[] = (list.items ?? []).map(
    (item) => buildComplianceFrameworkData(item, rsMap, reportTargetMap, missingReplicaSets, evalCtx).summary,
  )
  if (missingReplicaSets.size > 0) {
    console.warn(
      `[compliance] ReplicaSet(s) missing from cluster list, keeping framework checks fail-safe: ${Array.from(missingReplicaSets).join(", ")}`,
    )
  }
  const { newest, oldest } = extractTimestampRange(list.items ?? [], true)
  const partialSources = evalCtx.benchmarkStore.listFailed
    ? [...evalCtx.failedAuxSources, "kube-bench-configmaps"]
    : evalCtx.failedAuxSources
  return {
    frameworks,
    lastUpdated: newest,
    oldestUpdated: oldest,
    partial: evalCtx.hasHttpError || evalCtx.benchmarkStore.listFailed,
    partialSources,
    matchedBenchmarkRuleIds: evalCtx.benchmarkStore.matchedRuleIds,
    benchmarkListFailed: evalCtx.benchmarkStore.listFailed,
  }
}

export async function getComplianceFrameworks(): Promise<ComplianceFramework[]> {
  const cacheKey = cacheKeys.complianceFrameworksList()
  const cached = await cacheGet<ComplianceFramework[]>(cacheKey)
  if (cached) return cached

  try {
    const meta = await fetchComplianceFrameworksWithMeta()
    if (!meta.partial) {
      await cacheSet(cacheKey, meta.frameworks, cacheTtl("complianceFrameworksList"))
    }
    return meta.frameworks
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    console.warn("[compliance] getComplianceFrameworks failed:", msg)
    if (err instanceof ComplianceDataUnavailableError) throw err
    throw new ComplianceDataUnavailableError("frameworks", msg)
  }
}

// --- getComplianceFrameworkDetail ---

export async function getComplianceFrameworkDetail(
  id: string,
): Promise<ComplianceFrameworkDetail | null> {
  const cacheKey = cacheKeys.complianceFrameworksDetail(id)
  const cached = await cacheGet<ComplianceFrameworkDetail>(cacheKey)
  if (cached) return cached

  try {
    const { list, rsMap, reportTargetMap, evalCtx } = await fetchFrameworkContext()
    const item = (list.items ?? []).find((i) => i.spec.compliance.id === id)
    if (!item) return null

    const missingReplicaSets = new Set<string>()
    const { detail } = buildComplianceFrameworkData(item, rsMap, reportTargetMap, missingReplicaSets, evalCtx)
    if (missingReplicaSets.size > 0) {
      console.warn(
        `[compliance] ReplicaSet(s) missing from cluster list, keeping framework checks fail-safe: ${Array.from(missingReplicaSets).join(", ")}`,
      )
    }
    if (!evalCtx.hasHttpError && !evalCtx.benchmarkStore.listFailed) {
      await cacheSet(cacheKey, detail, cacheTtl("complianceFrameworksDetail"))
    }
    return detail
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    console.warn("[compliance] getComplianceFrameworkDetail failed:", msg)
    if (err instanceof ComplianceDataUnavailableError) throw err
    throw new ComplianceDataUnavailableError("frameworks", msg)
  }
}

// --- getComplianceSummary ---

export async function getComplianceSummary(): Promise<ComplianceSummary> {
  const cacheKey = cacheKeys.complianceSummary()
  const cached = await cacheGet<ComplianceSummary>(cacheKey)
  if (cached) return cached

  const [carRes, rbacRes, infraRes, fwRes] = await Promise.allSettled([
    fetchConfigAuditListWithMeta(),
    fetchRbacAuditListWithMeta(),
    fetchInfraAuditListWithMeta(),
    fetchComplianceFrameworksWithMeta(),
  ])

  const sources: Record<ComplianceSourceKey, ComplianceSourceStatus> = {
    configAudit:
      carRes.status === "fulfilled"
        ? {
            status: "ok",
            lastUpdated: carRes.value.lastUpdated,
            oldestUpdated: carRes.value.oldestUpdated,
            ...(carRes.value.partial
              ? { partial: true, partialSources: carRes.value.partialSources }
              : {}),
          }
        : {
            status: "unavailable",
            lastUpdated: null,
            error: "K8s ConfigAuditReport list failed",
          },
    rbac:
      rbacRes.status === "fulfilled"
        ? {
            status: "ok",
            lastUpdated: rbacRes.value.lastUpdated,
            oldestUpdated: rbacRes.value.oldestUpdated,
          }
        : {
            status: "unavailable",
            lastUpdated: null,
            error: "K8s RBACAssessmentReport list failed",
          },
    infra:
      infraRes.status === "fulfilled"
        ? {
            status: "ok",
            lastUpdated: infraRes.value.lastUpdated,
            oldestUpdated: infraRes.value.oldestUpdated,
            rebuildIntervalHours: 6,
          }
        : {
            status: "unavailable",
            lastUpdated: null,
            rebuildIntervalHours: 6,
            error: "K8s InfraAssessmentReport list failed",
          },
    frameworks:
      fwRes.status === "fulfilled"
        ? {
            status: "ok",
            lastUpdated: fwRes.value.lastUpdated,
            oldestUpdated: fwRes.value.oldestUpdated,
            rebuildIntervalHours: 6,
            ...(fwRes.value.partial
              ? { partial: true, partialSources: fwRes.value.partialSources }
              : {}),
          }
        : {
            status: "unavailable",
            lastUpdated: null,
            rebuildIntervalHours: 6,
            error: "K8s ClusterComplianceReport list failed",
          },
  }

  const configAuditList = carRes.status === "fulfilled" ? carRes.value.rows : []
  const rbacAuditList = rbacRes.status === "fulfilled" ? rbacRes.value.rows : []
  const infraAuditList = infraRes.status === "fulfilled" ? infraRes.value.rows : []
  const frameworks = fwRes.status === "fulfilled" ? fwRes.value.frameworks : []

  // Headline number reflects ACTIONABLE (unaccepted MEDIUM+) findings only across ALL namespaces
  // (including kube-system/istio-system): not covered by ACCEPTED_CONFIG_AUDIT_RULES AND
  // Critical/High/Medium severity. LOW checks are split into unacceptedLowConfigAuditFailures
  // (no exact rule) vs lowSeverityConfigAuditFailures (accepted hygiene) without double-counting.
  const totalConfigAuditFailures: CheckSummary = { ...emptyCheckSummary }
  const unacceptedLowConfigAuditFailures: CheckSummary = { ...emptyCheckSummary }
  const lowSeverityConfigAuditFailures: CheckSummary = { ...emptyCheckSummary }
  const acceptedSystemConfigAuditFailures: CheckSummary = { ...emptyCheckSummary }

  for (const row of configAuditList) {
    const actionable = row.actionableSummary ?? (row.accepted ? emptyCheckSummary : row.summary)
    const accepted = row.acceptedSummary ?? (row.accepted ? row.summary : emptyCheckSummary)
    totalConfigAuditFailures.Critical += actionable.Critical
    totalConfigAuditFailures.High += actionable.High
    totalConfigAuditFailures.Medium += actionable.Medium
    unacceptedLowConfigAuditFailures.Low += actionable.Low
    lowSeverityConfigAuditFailures.Low += accepted.Low
    acceptedSystemConfigAuditFailures.Critical += accepted.Critical
    acceptedSystemConfigAuditFailures.High += accepted.High
    acceptedSystemConfigAuditFailures.Medium += accepted.Medium
  }

  const totalRbacFailures: CheckSummary = { ...emptyCheckSummary }
  const acceptedRbacFailures: CheckSummary = { ...emptyCheckSummary }
  for (const row of rbacAuditList) {
    const actionable = row.actionableSummary ?? (row.accepted ? emptyCheckSummary : row.summary)
    const accepted = row.acceptedSummary ?? (row.accepted ? row.summary : emptyCheckSummary)
    totalRbacFailures.Critical += actionable.Critical
    totalRbacFailures.High += actionable.High
    totalRbacFailures.Medium += actionable.Medium
    totalRbacFailures.Low += actionable.Low
    acceptedRbacFailures.Critical += accepted.Critical
    acceptedRbacFailures.High += accepted.High
    acceptedRbacFailures.Medium += accepted.Medium
    acceptedRbacFailures.Low += accepted.Low
  }

  let totalInfraFailures: CheckSummary = { ...emptyCheckSummary }
  let acceptedInfraFailures: CheckSummary = { ...emptyCheckSummary }
  for (const row of infraAuditList) {
    const actionable = row.actionableSummary ?? (row.accepted ? emptyCheckSummary : row.summary)
    const accepted = row.acceptedSummary ?? (row.accepted ? row.summary : emptyCheckSummary)
    totalInfraFailures = addSummaries(totalInfraFailures, actionable)
    acceptedInfraFailures = addSummaries(acceptedInfraFailures, accepted)
  }

  const sourceTimestamps = Object.values(sources)
    .map((s) => s.lastUpdated ?? "")
    .filter(Boolean)
    .sort()
  const lastUpdated = sourceTimestamps.length > 0 ? sourceTimestamps[sourceTimestamps.length - 1] : ""

  const matchedRuleIds = new Set<string>()
  if (carRes.status === "fulfilled") {
    for (const id of carRes.value.matchedRuleIds) matchedRuleIds.add(id)
  }
  if (rbacRes.status === "fulfilled") {
    for (const id of rbacRes.value.matchedRuleIds) matchedRuleIds.add(id)
  }
  if (infraRes.status === "fulfilled") {
    for (const id of infraRes.value.matchedRuleIds) matchedRuleIds.add(id)
  }
  if (fwRes.status === "fulfilled") {
    for (const id of fwRes.value.matchedBenchmarkRuleIds) matchedRuleIds.add(id)
  }
  // D-P2-4: Surface exact acceptance rules that matched nothing across fulfilled families.
  // Unavailable sources are excluded from unused checks so outages never look like stale rules.
  const unusedAcceptanceRules = findUnusedAcceptanceRules(
    matchedRuleIds,
    {
      configAudit: carRes.status === "fulfilled",
      rbac: rbacRes.status === "fulfilled",
      infra: infraRes.status === "fulfilled",
      benchmark: fwRes.status === "fulfilled" && !fwRes.value.benchmarkListFailed,
    },
    ACCEPTED_INFRA_RULES,
  )

  const dataUnavailable = Object.values(sources).some((s) => s.status === "unavailable")
  const hasPartial = Object.values(sources).some((s) => Boolean(s.partial))

  const result: ComplianceSummary = {
    totalConfigAuditFailures,
    unacceptedLowConfigAuditFailures,
    lowSeverityConfigAuditFailures,
    acceptedSystemConfigAuditFailures,
    droppedConfigAuditReports: carRes.status === "fulfilled" ? carRes.value.droppedCount : 0,
    droppedConfigAuditFailures:
      carRes.status === "fulfilled" ? carRes.value.droppedSummary : { ...emptyCheckSummary },
    totalRbacFailures,
    acceptedRbacFailures,
    totalInfraFailures,
    acceptedInfraFailures,
    frameworks,
    scannedWorkloads: configAuditList.length,
    scannedRbacObjects: rbacAuditList.length,
    scannedNodes: infraAuditList.length,
    lastUpdated,
    sources,
    dataUnavailable,
    ...(hasPartial ? { partial: true } : {}),
    unusedAcceptanceRules,
    unusedAcceptanceRuleCount: unusedAcceptanceRules.length,
  }

  // Never cache failed or partial results as a valid snapshot (cache-keys contract: cachesPartial=false).
  if (!dataUnavailable && !hasPartial) {
    await cacheSet(cacheKey, result, cacheTtl("complianceSummary"))
  }
  return result
}
