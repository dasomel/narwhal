import "server-only"
import { getK8sApiServer } from "./config"
import { getK8sBearerToken } from "./k8s-token"
import { fetchWithPolicy, readJsonWithPolicy } from "./http-client"
import { K8S_POLICY } from "./k8s-client"
import { cacheGet, cacheSet } from "./valkey"
import { cacheKeys, cacheTtl } from "./cache-keys"
import { en } from "./i18n"
import {
  type AcceptedReasonKey,
  COMPLIANCE_EXCEPTIONS_DOC_ANCHOR,
  matchConfigAuditException,
  matchInfraException,
  matchRbacException,
} from "./compliance-accepted"
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
  type ComplianceSummary,
} from "@/types/compliance"

// --- K8s API helpers ---

async function complianceK8sFetch<T>(path: string): Promise<T> {
  const apiServer = getK8sApiServer()
  const headers: Record<string, string> = { "Content-Type": "application/json" }
  if (apiServer.startsWith("https://")) {
    const token = getK8sBearerToken()
    if (token.length > 0) headers["Authorization"] = `Bearer ${token}`
  }
  const res = await fetchWithPolicy(`${apiServer}${path}`, { headers }, { ...K8S_POLICY, timeoutMs: 60_000 })
  if (!res.ok) {
    throw new Error(`K8s API ${res.status} ${res.statusText} for ${path}`)
  }
  return readJsonWithPolicy<T>(res)
}

// System namespaces whose config-audit findings are inherent to K8s static pods / CNI / mesh
// (hostNetwork, root, hostPath on apiserver/etcd/cilium/istio) and are NOT actionable by the
// platform team — see docs/common/compliance-hardening.md#risk-accepted-exceptions.
export const SYSTEM_NAMESPACES = ["kube-system", "kube-public", "kube-node-lease", "istio-system"] as const

function isSystemNamespace(namespace: string): boolean {
  return (SYSTEM_NAMESPACES as readonly string[]).includes(namespace)
}

// K8s default ClusterRoles bound cluster-wide by kubeadm/aggregation — inherently broad, not
// authored by the platform team.
const K8S_DEFAULT_CLUSTER_ROLES = new Set(["cluster-admin", "admin", "edit", "view"])

// Upstream controller/chart-owned RBAC role name patterns (case-insensitive substring match) —
// these roles need broad permissions inherent to their function (cert-manager manages secrets,
// kyverno manages webhooks, etc.) and are NOT actionable by the platform team — see
// docs/common/compliance-hardening.md#risk-accepted-exceptions.
const UPSTREAM_RBAC_ROLE_PATTERNS = [
  "cert-manager", "kyverno", "cilium", "metallb", "prometheus", "kube-prom", "trivy",
  "velero", "openbao", "seaweedfs", "argocd", "istio", "harbor", "keycloak", "loki",
  "tempo", "alloy", "k8s-monitoring", "nfs-", "headlamp", "apisix", "cnpg", "narwhal-db",
  "gitea", "ghost-pod", "coredns", "metrics-server", "csi-", "snapshot", "node-",
  "gatekeeper", "kubelet",
] as const

export type RbacRoleKind = "Role" | "ClusterRole"

// Exact upstream (namespace, kind, roleName) triples for charts whose prefix/name could match
// platform-authored roles (e.g. "platform-admin-chaos-mesh" or "developer-kubernetes-dashboard-admin").
// Derived from live cluster roles plus narwhal-airgap-bundle-arm64/charts/chaos-mesh-2.8.3.tgz
// (namespace: chaos-testing for Role, "" for ClusterRole) and
// gitops/charts/kubernetes-dashboard/templates/rbac/ (namespace: devtools for Role, "" for ClusterRole).
const UPSTREAM_EXACT_RBAC_ROLES = new Set([
  "chaos-testing\0Role\0chaos-mesh-chaos-controller-manager-control-plane",
  "\0ClusterRole\0chaos-mesh-chaos-controller-manager-target-namespace",
  "\0ClusterRole\0chaos-mesh-chaos-controller-manager-cluster-level",
  "\0ClusterRole\0chaos-mesh-chaos-dashboard-cluster-level",
  "\0ClusterRole\0chaos-mesh-chaos-dashboard-target-namespace",
  "\0ClusterRole\0chaos-mesh-chaos-dns-server-target-namespace",
  "\0ClusterRole\0chaos-mesh-chaos-dns-server-cluster-level",
  "chaos-testing\0Role\0chaos-mesh-chaos-dns-server-control-plane",
  "\0ClusterRole\0chaos-mesh-chaos-daemon-psp",
  "\0ClusterRole\0chaos-mesh-chaos-prometheus",
  "devtools\0Role\0kubernetes-dashboard-api",
  "devtools\0Role\0kubernetes-dashboard-web",
  "\0ClusterRole\0kubernetes-dashboard-metrics-scraper",
])

function exactRbacRoleKey(
  namespace: string | undefined,
  kind: RbacRoleKind | string | undefined,
  roleName: string,
): string | null {
  if (kind !== "Role" && kind !== "ClusterRole") return null
  const ns = namespace ?? ""
  if (kind === "Role" && ns === "") return null
  if (kind === "ClusterRole" && ns !== "") return null
  return `${ns}\0${kind}\0${roleName}`
}

// Our own authored roles whose name happens to contain an upstream pattern as a substring
// (e.g. "velero-ui-admin" contains "velero") — must be checked BEFORE the upstream patterns so
// they aren't swept into "accepted".
const OWN_ROLE_EXCEPTIONS = ["velero-ui"] as const

function isBuiltInRbacRole(roleName: string): boolean {
  return (
    roleName.startsWith("system:") ||
    roleName.startsWith("kubeadm:") ||
    K8S_DEFAULT_CLUSTER_ROLES.has(roleName)
  )
}

// RBAC findings owned by Kubernetes built-in roles (system:*/kubeadm:*, default cluster-admin/
// admin/edit/view) or upstream controller/chart roles are inherent to what those roles do and are
// NOT actionable by the platform team. Only our own authored roles (platform-admin, developer,
// narwhal-portal*, velero-ui-*, etc.) are actionable unless covered by ACCEPTED_RBAC_RULES.
export function isAcceptedRbacRole(
  roleName: string,
  namespace?: string,
  kind?: RbacRoleKind | string,
): boolean {
  if (isBuiltInRbacRole(roleName)) return true
  const exactKey = exactRbacRoleKey(namespace, kind, roleName)
  if (exactKey !== null && UPSTREAM_EXACT_RBAC_ROLES.has(exactKey)) return true
  const lower = roleName.toLowerCase()
  if (OWN_ROLE_EXCEPTIONS.some((exception) => lower.includes(exception))) return false
  return UPSTREAM_RBAC_ROLE_PATTERNS.some((pattern) => lower.includes(pattern))
}

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

interface RawComplianceReport {
  metadata: {
    name: string
    creationTimestamp?: string
  }
  spec: {
    compliance: {
      id: string
      title: string
      controls?: Array<{
        id: string
        name: string
        severity: string
        description?: string
      }>
    }
  }
  status?: {
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
  acceptedClass?: AcceptedClass
  acceptedReasonKey?: AcceptedReasonKey
  acceptedReason?: string
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
        base.acceptedClass = ann.acceptedClass
        base.acceptedReasonKey = ann.acceptedReasonKey
        base.acceptedReason = ann.acceptedReason
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
  totalsMatchSummary: boolean
} {
  const summary = buildCheckSummaryFromCounts(rawSummary)
  const actionableSummary: CheckSummary = { ...emptyCheckSummary }
  const acceptedSummary: CheckSummary = { ...emptyCheckSummary }
  const matchedClasses = new Set<AcceptedClass>()

  if (rawChecks.length === 0) {
    // Fail closed when raw checks are omitted while summary reports failures.
    const hasAnySummaryFailure = REPORT_SEVERITIES.some((sev) => summary[sev] > 0)
    return {
      summary,
      actionableSummary: { ...summary },
      acceptedSummary,
      matchedClasses,
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

  return { summary, actionableSummary, acceptedSummary, matchedClasses, totalsMatchSummary }
}

function resolveScopeFromClasses(classes: Set<AcceptedClass>): AcceptedScope | undefined {
  if (classes.size === 0) return undefined
  if (classes.size === 1) return classes.values().next().value
  return "mixed"
}

function configAuditMatcher(
  namespace: string,
  kind: string,
  name: string,
  deploymentOwners?: readonly string[],
): AcceptanceMatcher {
  if (isSystemNamespace(namespace)) {
    const reasonKey: AcceptedReasonKey = "compliance.acceptedReason.systemNamespace"
    return () => ({
      accepted: true,
      acceptedClass: "accept",
      acceptedReasonKey: reasonKey,
      acceptedReason: en[reasonKey],
      acceptedDocAnchor: COMPLIANCE_EXCEPTIONS_DOC_ANCHOR,
    })
  }
  return (checkId: string) => {
    const match = matchConfigAuditException(namespace, kind, name, checkId, deploymentOwners)
    if (!match) return { accepted: false }
    return {
      accepted: true,
      acceptedClass: match.class,
      acceptedReasonKey: match.reasonKey,
      acceptedReason: match.reason,
      acceptedDocAnchor: match.docAnchor,
    }
  }
}

function classifyConfigAuditItem(
  item: RawAuditReport,
  deploymentOwners?: readonly string[],
): {
  row: ConfigAuditRow
  matcher: AcceptanceMatcher
} {
  const labels = item.metadata.labels ?? {}
  const kind = labels["trivy-operator.resource.kind"] ?? "Unknown"
  const name = labels["trivy-operator.resource.name"] ?? item.metadata.name
  const namespace = item.metadata.namespace ?? ""
  const matcher = configAuditMatcher(namespace, kind, name, deploymentOwners)

  if (isSystemNamespace(namespace)) {
    const summary = buildCheckSummaryFromCounts(item.report.summary)
    return {
      row: {
        namespace,
        kind,
        name,
        summary,
        actionableSummary: { ...emptyCheckSummary },
        acceptedSummary: { ...summary },
        accepted: true,
        acceptedScope: "system",
      },
      matcher,
    }
  }

  const { summary, actionableSummary, acceptedSummary, matchedClasses, totalsMatchSummary } =
    splitSummariesByChecks(item.report.checks ?? [], item.report.summary, matcher)
  const nonLowTotal = summary.Critical + summary.High + summary.Medium
  const nonLowActionable =
    actionableSummary.Critical + actionableSummary.High + actionableSummary.Medium
  // A non-system row is accepted only when every failing check is present (totalsMatchSummary),
  // it has non-LOW failing checks, and ALL non-LOW checks are covered by reviewed exceptions.
  const accepted = totalsMatchSummary && nonLowTotal > 0 && nonLowActionable === 0
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
  }
}

function rbacAuditMatcher(namespace: string, kind: string, roleName: string): AcceptanceMatcher {
  if (isAcceptedRbacRole(roleName, namespace, kind)) {
    const builtIn = isBuiltInRbacRole(roleName)
    const reasonKey: AcceptedReasonKey = builtIn
      ? "compliance.acceptedReason.k8sBuiltinRole"
      : "compliance.acceptedReason.upstreamRbacRole"
    return () => ({
      accepted: true,
      acceptedClass: builtIn ? "accept" : "upstream",
      acceptedReasonKey: reasonKey,
      acceptedReason: en[reasonKey],
      acceptedDocAnchor: COMPLIANCE_EXCEPTIONS_DOC_ANCHOR,
    })
  }
  return (checkId: string) => {
    const match = matchRbacException(roleName, checkId, namespace, kind)
    if (!match) return { accepted: false }
    return {
      accepted: true,
      acceptedClass: match.class,
      acceptedReasonKey: match.reasonKey,
      acceptedReason: match.reason,
      acceptedDocAnchor: match.docAnchor,
    }
  }
}

function classifyRbacAuditItem(item: RawAuditReport): {
  row: RbacAuditRow
  matcher: AcceptanceMatcher
} {
  const labels = item.metadata.labels ?? {}
  const ownerRefs = item.metadata.ownerReferences ?? []
  const kind = labels["trivy-operator.resource.kind"] ?? ownerRefs[0]?.kind ?? "Unknown"
  const name = resolveRbacRoleName(item)
  const namespace = item.metadata.namespace ?? ""
  const matcher = rbacAuditMatcher(namespace, kind, name)

  if (isAcceptedRbacRole(name, namespace, kind)) {
    const summary = buildCheckSummaryFromCounts(item.report.summary)
    return {
      row: {
        namespace,
        kind,
        name,
        summary,
        actionableSummary: { ...emptyCheckSummary },
        acceptedSummary: { ...summary },
        accepted: true,
        acceptedScope: isBuiltInRbacRole(name) ? "builtin" : "upstream",
      },
      matcher,
    }
  }

  const { summary, actionableSummary, acceptedSummary, matchedClasses, totalsMatchSummary } =
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
      acceptedClass: match.class,
      acceptedReasonKey: match.reasonKey,
      acceptedReason: match.reason,
      acceptedDocAnchor: match.docAnchor,
    }
  }
}

function classifyInfraAuditItem(item: RawAuditReport): {
  row: InfraAuditRow
  matcher: AcceptanceMatcher
} {
  const labels = item.metadata.labels ?? {}
  const node = labels["trivy-operator.resource.name"] ?? item.metadata.name
  const matcher = infraAuditMatcher(node)

  const { summary, actionableSummary, acceptedSummary, matchedClasses, totalsMatchSummary } =
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

// --- getConfigAuditList ---

export async function getConfigAuditList(): Promise<ConfigAuditRow[]> {
  const cacheKey = cacheKeys.complianceConfigAuditList()
  const cached = await cacheGet<ConfigAuditRow[]>(cacheKey)
  if (cached) return cached

  try {
    const [list, rsMap] = await Promise.all([
      complianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/configauditreports"),
      fetchReplicaSetMap(),
    ])
    const missingReplicaSets: string[] = []
    const rows: ConfigAuditRow[] = []
    for (const item of list.items ?? []) {
      const rsState = resolveReplicaSetMetadata(item, rsMap)
      if (rsState.missingKey) missingReplicaSets.push(rsState.missingKey)
      if (rsState.drop) continue
      rows.push(classifyConfigAuditItem(item, rsState.deploymentOwners).row)
    }
    if (missingReplicaSets.length > 0) {
      console.warn(
        `[compliance] ReplicaSet(s) missing from cluster list, keeping reports fail-safe: ${missingReplicaSets.join(", ")}`,
      )
    }
    await cacheSet(cacheKey, rows, cacheTtl("complianceConfigAuditList"))
    return rows
  } catch (err) {
    console.warn("[compliance] getConfigAuditList failed:", err instanceof Error ? err.message : err)
    return []
  }
}

// --- getConfigAuditDetail ---

export async function getConfigAuditDetail(
  namespace: string,
  name: string,
): Promise<ConfigAuditDetail | null> {
  const cacheKey = cacheKeys.complianceConfigAuditDetail(namespace, name)
  const cached = await cacheGet<ConfigAuditDetail>(cacheKey)
  if (cached) return cached

  try {
    const [list, rsMap] = await Promise.all([
      complianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/configauditreports"),
      fetchReplicaSetMap(),
    ])
    const item = (list.items ?? []).find((i) => {
      const labels = i.metadata.labels ?? {}
      const rName = labels["trivy-operator.resource.name"] ?? i.metadata.name
      return i.metadata.namespace === namespace && rName === name
    })
    if (!item) return null

    const rsState = resolveReplicaSetMetadata(item, rsMap)
    if (rsState.missingKey) {
      console.warn(
        `[compliance] ReplicaSet missing from cluster list, keeping report detail fail-safe: ${rsState.missingKey}`,
      )
    }
    if (rsState.drop) return null

    const { row, matcher } = classifyConfigAuditItem(item, rsState.deploymentOwners)
    const detail: ConfigAuditDetail = {
      ...row,
      checks: mapChecks(item.report.checks ?? [], matcher),
    }
    await cacheSet(cacheKey, detail, cacheTtl("complianceConfigAuditDetail"))
    return detail
  } catch (err) {
    console.warn("[compliance] getConfigAuditDetail failed:", err instanceof Error ? err.message : err)
    return null
  }
}

// --- getRbacAuditList ---

export async function getRbacAuditList(): Promise<RbacAuditRow[]> {
  const cacheKey = cacheKeys.complianceRbacAuditList()
  const cached = await cacheGet<RbacAuditRow[]>(cacheKey)
  if (cached) return cached

  try {
    const [namespaced, clustered] = await Promise.all([
      complianceK8sFetch<RawAuditList>(
        "/apis/aquasecurity.github.io/v1alpha1/rbacassessmentreports",
      ).catch(() => ({ items: [] as RawAuditReport[] })),
      complianceK8sFetch<RawAuditList>(
        "/apis/aquasecurity.github.io/v1alpha1/clusterrbacassessmentreports",
      ).catch(() => ({ items: [] as RawAuditReport[] })),
    ])

    const allItems = [...(namespaced.items ?? []), ...(clustered.items ?? [])]
    const rows: RbacAuditRow[] = allItems.map((item) => classifyRbacAuditItem(item).row)
    await cacheSet(cacheKey, rows, cacheTtl("complianceRbacAuditList"))
    return rows
  } catch (err) {
    console.warn("[compliance] getRbacAuditList failed:", err instanceof Error ? err.message : err)
    return []
  }
}

// --- getRbacAuditDetail ---

export async function getRbacAuditDetail(
  namespace: string,
  name: string,
): Promise<RbacAuditDetail | null> {
  const cacheKey = cacheKeys.complianceRbacAuditDetail(namespace, name)
  const cached = await cacheGet<RbacAuditDetail>(cacheKey)
  if (cached) return cached

  try {
    const [namespaced, clustered] = await Promise.all([
      complianceK8sFetch<RawAuditList>(
        "/apis/aquasecurity.github.io/v1alpha1/rbacassessmentreports",
      ).catch(() => ({ items: [] as RawAuditReport[] })),
      complianceK8sFetch<RawAuditList>(
        "/apis/aquasecurity.github.io/v1alpha1/clusterrbacassessmentreports",
      ).catch(() => ({ items: [] as RawAuditReport[] })),
    ])

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
  } catch (err) {
    console.warn("[compliance] getRbacAuditDetail failed:", err instanceof Error ? err.message : err)
    return null
  }
}

// --- getInfraAuditList ---

export async function getInfraAuditList(): Promise<InfraAuditRow[]> {
  const cacheKey = cacheKeys.complianceInfraAuditList()
  const cached = await cacheGet<InfraAuditRow[]>(cacheKey)
  if (cached) return cached

  try {
    const [namespaced, clustered] = await Promise.all([
      complianceK8sFetch<RawAuditList>(
        "/apis/aquasecurity.github.io/v1alpha1/infraassessmentreports",
      ).catch(() => ({ items: [] as RawAuditReport[] })),
      complianceK8sFetch<RawAuditList>(
        "/apis/aquasecurity.github.io/v1alpha1/clusterinfraassessmentreports",
      ).catch(() => ({ items: [] as RawAuditReport[] })),
    ])

    const allItems = [...(namespaced.items ?? []), ...(clustered.items ?? [])]
    const rows: InfraAuditRow[] = allItems.map((item) => classifyInfraAuditItem(item).row)
    await cacheSet(cacheKey, rows, cacheTtl("complianceInfraAuditList"))
    return rows
  } catch (err) {
    console.warn("[compliance] getInfraAuditList failed:", err instanceof Error ? err.message : err)
    return []
  }
}

// --- getInfraAuditDetail ---

export async function getInfraAuditDetail(node: string): Promise<InfraAuditDetail | null> {
  const cacheKey = cacheKeys.complianceInfraAuditDetail(node)
  const cached = await cacheGet<InfraAuditDetail>(cacheKey)
  if (cached) return cached

  try {
    const [namespaced, clustered] = await Promise.all([
      complianceK8sFetch<RawAuditList>(
        "/apis/aquasecurity.github.io/v1alpha1/infraassessmentreports",
      ).catch(() => ({ items: [] as RawAuditReport[] })),
      complianceK8sFetch<RawAuditList>(
        "/apis/aquasecurity.github.io/v1alpha1/clusterinfraassessmentreports",
      ).catch(() => ({ items: [] as RawAuditReport[] })),
    ])

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
  } catch (err) {
    console.warn("[compliance] getInfraAuditDetail failed:", err instanceof Error ? err.message : err)
    return null
  }
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
  node: "Node",
}

const CLUSTER_SCOPED_TARGET_KINDS = new Set(["ClusterRole", "Node"])
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
    if (isSystemNamespace(resolved.namespace)) {
      return { status: "accepted", scope: "system" }
    }
    return { status: "accepted", scope: scopeForAcceptedClass(ann.acceptedClass) }
  }

  if (RBAC_TARGET_KINDS.has(resolved.kind)) {
    const matcher = rbacAuditMatcher(resolved.namespace, resolved.kind, resolved.name)
    const ann = matcher(checkId)
    if (!ann.accepted) return { status: "actionable" }
    if (isAcceptedRbacRole(resolved.name, resolved.namespace, resolved.kind)) {
      return { status: "accepted", scope: isBuiltInRbacRole(resolved.name) ? "builtin" : "upstream" }
    }
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

function resolveFrameworkAcceptedScope(scopes: Set<FrameworkAcceptedToken>): AcceptedScope | undefined {
  if (scopes.size === 0) return undefined
  if (scopes.size === 1) return scopes.values().next().value
  return "mixed"
}

function buildComplianceFrameworkData(
  item: RawComplianceReport,
  rsMap: Map<string, ReplicaSetSummary> | null,
  reportTargetMap: ReadonlyMap<string, ParsedComplianceTarget>,
  missingReplicaSets: Set<string>,
): { summary: ComplianceFramework; detail: ComplianceFrameworkDetail } {
  const passCount = item.status?.summary?.passCount ?? 0
  const failCount = item.status?.summary?.failCount ?? 0

  // status.detailReport.results 가 있으면 per-control 사용, 없으면 spec.compliance.controls 폴백.
  // (Trivy reportType=summary 이면 detailReport 비어있음 — 정의만 표시, pass/fail은 0으로.
  //  reportType=all 이면 results[].checks[].success 로 컨트롤별 pass/fail 집계.)
  const detailControls = item.status?.detailReport?.results ?? []
  const specControls = item.spec.compliance.controls ?? []

  let controls: ComplianceControl[]
  if (detailControls.length > 0) {
    controls = detailControls.map((c) => {
      const checks = c.checks ?? []
      let ctrlPass = 0
      let ctrlFail = 0
      let actionableFailCount = 0
      let acceptedFailCount = 0
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
        }
      }

      const accepted = ctrlFail > 0 && actionableFailCount === 0 && acceptedFailCount > 0
      return {
        id: c.id,
        name: c.name,
        severity: mapSeverity(c.severity),
        passCount: ctrlPass,
        failCount: ctrlFail,
        actionableFailCount,
        acceptedFailCount,
        accepted,
        ...(accepted ? { acceptedScope: resolveFrameworkAcceptedScope(scopes) } : {}),
        description: c.description,
      }
    })
  } else {
    controls = specControls.map((c) => ({
      id: c.id,
      name: c.name,
      severity: mapSeverity(c.severity),
      passCount: 0,
      failCount: 0,
      actionableFailCount: 0,
      acceptedFailCount: 0,
      accepted: false,
      description: c.description,
    }))
  }

  let actionableFailCount: number
  let acceptedFailCount: number
  if (detailControls.length > 0) {
    const detailFailControls = controls.filter((c) => c.failCount > 0).length
    const detailActionableFail = controls.filter((c) => (c.actionableFailCount ?? 0) > 0).length
    const detailAcceptedFail = controls.filter((c) => c.accepted === true).length
    // Fail closed if summary.failCount exceeds failing controls present in detailReport.results.
    const unaccountedFail = Math.max(0, failCount - detailFailControls)
    actionableFailCount = detailActionableFail + unaccountedFail
    acceptedFailCount = detailAcceptedFail
  } else {
    // reportType=summary lacks per-check targets to verify exceptions — fail closed.
    actionableFailCount = failCount
    acceptedFailCount = 0
  }

  // totalControls는 spec 정의 기준이 정확 (summary는 평가된 항목만 카운트)
  const totalControls = specControls.length || controls.length || passCount + failCount
  const evaluated = passCount + failCount
  const passRate = evaluated > 0 ? passCount / evaluated : 0
  const effectivePassRate =
    evaluated > 0 ? Math.max(0, evaluated - actionableFailCount) / evaluated : 0

  const summary: ComplianceFramework = {
    id: item.spec.compliance.id,
    title: item.spec.compliance.title,
    passCount,
    failCount,
    actionableFailCount,
    acceptedFailCount,
    totalControls,
    passRate,
    effectivePassRate,
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
}> {
  const [list, rsMap, configList, rbacNs, rbacCluster] = await Promise.all([
    complianceK8sFetch<RawComplianceList>("/apis/aquasecurity.github.io/v1alpha1/clustercompliancereports"),
    fetchReplicaSetMap(),
    complianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/configauditreports")
      .catch(() => ({ items: [] as RawAuditReport[] })),
    complianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/rbacassessmentreports")
      .catch(() => ({ items: [] as RawAuditReport[] })),
    complianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/clusterrbacassessmentreports")
      .catch(() => ({ items: [] as RawAuditReport[] })),
  ])
  return {
    list,
    rsMap,
    reportTargetMap: buildReportTargetMap(
      configList.items ?? [],
      rbacNs.items ?? [],
      rbacCluster.items ?? [],
    ),
  }
}

// --- getComplianceFrameworks ---

export async function getComplianceFrameworks(): Promise<ComplianceFramework[]> {
  const cacheKey = cacheKeys.complianceFrameworksList()
  const cached = await cacheGet<ComplianceFramework[]>(cacheKey)
  if (cached) return cached

  try {
    const { list, rsMap, reportTargetMap } = await fetchFrameworkContext()
    const missingReplicaSets = new Set<string>()
    const frameworks: ComplianceFramework[] = (list.items ?? []).map(
      (item) => buildComplianceFrameworkData(item, rsMap, reportTargetMap, missingReplicaSets).summary,
    )
    if (missingReplicaSets.size > 0) {
      console.warn(
        `[compliance] ReplicaSet(s) missing from cluster list, keeping framework checks fail-safe: ${Array.from(missingReplicaSets).join(", ")}`,
      )
    }
    await cacheSet(cacheKey, frameworks, cacheTtl("complianceFrameworksList"))
    return frameworks
  } catch (err) {
    console.warn("[compliance] getComplianceFrameworks failed:", err instanceof Error ? err.message : err)
    return []
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
    const { list, rsMap, reportTargetMap } = await fetchFrameworkContext()
    const item = (list.items ?? []).find((i) => i.spec.compliance.id === id)
    if (!item) return null

    const missingReplicaSets = new Set<string>()
    const { detail } = buildComplianceFrameworkData(item, rsMap, reportTargetMap, missingReplicaSets)
    if (missingReplicaSets.size > 0) {
      console.warn(
        `[compliance] ReplicaSet(s) missing from cluster list, keeping framework checks fail-safe: ${Array.from(missingReplicaSets).join(", ")}`,
      )
    }
    await cacheSet(cacheKey, detail, cacheTtl("complianceFrameworksDetail"))
    return detail
  } catch (err) {
    console.warn("[compliance] getComplianceFrameworkDetail failed:", err instanceof Error ? err.message : err)
    return null
  }
}

// --- getComplianceSummary ---

export async function getComplianceSummary(): Promise<ComplianceSummary> {
  const cacheKey = cacheKeys.complianceSummary()
  const cached = await cacheGet<ComplianceSummary>(cacheKey)
  if (cached) return cached

  try {
    const [configAuditList, rbacAuditList, infraAuditList, frameworks] = await Promise.all([
      getConfigAuditList(),
      getRbacAuditList(),
      getInfraAuditList(),
      getComplianceFrameworks(),
    ])

    // Headline number reflects ACTIONABLE findings only: non-system namespace, not covered by
    // ACCEPTED_CONFIG_AUDIT_RULES, AND Critical/High/Medium severity. LOW-severity checks stay
    // in lowSeverityConfigAuditFailures (hygiene) and are NOT double-counted in accepted counts.
    // System-namespace failures (all severities) and non-system accepted Critical/High/Medium
    // failures are surfaced in acceptedSystemConfigAuditFailures rather than dropped.
    const totalConfigAuditFailures: CheckSummary = { ...emptyCheckSummary }
    const lowSeverityConfigAuditFailures: CheckSummary = { ...emptyCheckSummary }
    const acceptedSystemConfigAuditFailures: CheckSummary = { ...emptyCheckSummary }

    for (const row of configAuditList) {
      if (isSystemNamespace(row.namespace)) {
        acceptedSystemConfigAuditFailures.Critical += row.summary.Critical
        acceptedSystemConfigAuditFailures.High += row.summary.High
        acceptedSystemConfigAuditFailures.Medium += row.summary.Medium
        acceptedSystemConfigAuditFailures.Low += row.summary.Low
        continue
      }
      const actionable = row.actionableSummary ?? (row.accepted ? emptyCheckSummary : row.summary)
      const accepted = row.acceptedSummary ?? (row.accepted ? row.summary : emptyCheckSummary)
      totalConfigAuditFailures.Critical += actionable.Critical
      totalConfigAuditFailures.High += actionable.High
      totalConfigAuditFailures.Medium += actionable.Medium
      lowSeverityConfigAuditFailures.Low += row.summary.Low
      acceptedSystemConfigAuditFailures.Critical += accepted.Critical
      acceptedSystemConfigAuditFailures.High += accepted.High
      acceptedSystemConfigAuditFailures.Medium += accepted.Medium
    }

    // Headline RBAC number reflects ACTIONABLE findings only: excludes findings owned by K8s
    // built-in roles, upstream controller/chart roles, and per-check reviewed exceptions on
    // authored roles (see isAcceptedRbacRole / ACCEPTED_RBAC_RULES).
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
    // Headline Infra number reflects ACTIONABLE findings only: excludes findings covered by
    // reviewed node-check exceptions (see ACCEPTED_INFRA_RULES).
    let totalInfraFailures: CheckSummary = { ...emptyCheckSummary }
    let acceptedInfraFailures: CheckSummary = { ...emptyCheckSummary }
    for (const row of infraAuditList) {
      const actionable = row.actionableSummary ?? (row.accepted ? emptyCheckSummary : row.summary)
      const accepted = row.acceptedSummary ?? (row.accepted ? row.summary : emptyCheckSummary)
      totalInfraFailures = addSummaries(totalInfraFailures, actionable)
      acceptedInfraFailures = addSummaries(acceptedInfraFailures, accepted)
    }

    // Derive lastUpdated from K8s timestamps via a separate fetch
    let lastUpdated = new Date().toISOString()
    try {
      const [configList, rbacNs, rbacCluster, infraNs, infraCluster, ccList] = await Promise.all([
        complianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/configauditreports").catch(() => ({ items: [] as RawAuditReport[] })),
        complianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/rbacassessmentreports").catch(() => ({ items: [] as RawAuditReport[] })),
        complianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/clusterrbacassessmentreports").catch(() => ({ items: [] as RawAuditReport[] })),
        complianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/infraassessmentreports").catch(() => ({ items: [] as RawAuditReport[] })),
        complianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/clusterinfraassessmentreports").catch(() => ({ items: [] as RawAuditReport[] })),
        complianceK8sFetch<RawComplianceList>("/apis/aquasecurity.github.io/v1alpha1/clustercompliancereports").catch(() => ({ items: [] as RawComplianceReport[] })),
      ])
      const allTs = [
        ...(configList.items ?? []),
        ...(rbacNs.items ?? []),
        ...(rbacCluster.items ?? []),
        ...(infraNs.items ?? []),
        ...(infraCluster.items ?? []),
      ]
        .map((i) => i.metadata.creationTimestamp ?? "")
        .concat((ccList.items ?? []).map((i) => i.metadata.creationTimestamp ?? ""))
        .filter(Boolean)
        .sort()
      if (allTs.length > 0) lastUpdated = allTs[allTs.length - 1]
    } catch {
      // lastUpdated stays as now
    }

    const result: ComplianceSummary = {
      totalConfigAuditFailures,
      lowSeverityConfigAuditFailures,
      acceptedSystemConfigAuditFailures,
      totalRbacFailures,
      acceptedRbacFailures,
      totalInfraFailures,
      acceptedInfraFailures,
      frameworks,
      scannedWorkloads: configAuditList.length,
      scannedRbacObjects: rbacAuditList.length,
      scannedNodes: infraAuditList.length,
      lastUpdated,
    }
    await cacheSet(cacheKey, result, cacheTtl("complianceSummary"))
    return result
  } catch (err) {
    console.warn("[compliance] getComplianceSummary failed:", err instanceof Error ? err.message : err)
    return {
      totalConfigAuditFailures: { ...emptyCheckSummary },
      lowSeverityConfigAuditFailures: { ...emptyCheckSummary },
      acceptedSystemConfigAuditFailures: { ...emptyCheckSummary },
      totalRbacFailures: { ...emptyCheckSummary },
      acceptedRbacFailures: { ...emptyCheckSummary },
      totalInfraFailures: { ...emptyCheckSummary },
      acceptedInfraFailures: { ...emptyCheckSummary },
      frameworks: [],
      scannedWorkloads: 0,
      scannedRbacObjects: 0,
      scannedNodes: 0,
      lastUpdated: new Date().toISOString(),
    }
  }
}
