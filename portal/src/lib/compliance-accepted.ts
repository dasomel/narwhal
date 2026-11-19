import { en, type TranslationKey } from "./i18n"
import type { AcceptedClass } from "@/types/compliance"

export const COMPLIANCE_EXCEPTIONS_DOC_ANCHOR = "docs/common/compliance-hardening.md#risk-accepted-exceptions"

export type WorkloadKind = "ReplicaSet" | "DaemonSet" | "StatefulSet" | "Pod"

export type AcceptedReasonKey = Extract<TranslationKey, `compliance.acceptedReason.${string}`>

export interface AcceptedConfigAuditRule {
  namespace: string
  kind: WorkloadKind
  name: string
  checks: ReadonlySet<string>
  class: AcceptedClass
  reasonKey: AcceptedReasonKey
  reason: string
  docAnchor: string
}

export interface AcceptedRbacRule {
  /** Empty string "" for ClusterRole, exact namespace for namespaced Role. */
  namespace: string
  kind: "ClusterRole" | "Role"
  roleName: string
  checks: ReadonlySet<string>
  class: AcceptedClass
  reasonKey: AcceptedReasonKey
  reason: string
  docAnchor: string
}

export interface AcceptedMatch {
  class: AcceptedClass
  reasonKey: AcceptedReasonKey
  reason: string
  docAnchor: string
}

/**
 * Normalizes Trivy KSV check IDs so equivalent forms match identically:
 * - "AVD-KSV-0017" -> "KSV017"
 * - "AVD-KSV-0108" -> "KSV108"
 * - "ksv17"        -> "KSV017"
 * - "KSV017"       -> "KSV017"
 */
export function normalizeCheckId(raw: string): string {
  const upper = raw.trim().toUpperCase()
  const avd = /^AVD-KSV-0*(\d+)$/.exec(upper)
  if (avd) return `KSV${avd[1].padStart(3, "0")}`
  const ksv = /^KSV0*(\d+)$/.exec(upper)
  if (ksv) return `KSV${ksv[1].padStart(3, "0")}`
  return upper
}

/**
 * Strips the single Kubernetes pod-template-hash suffix from a ReplicaSet name so
 * rollout-induced hash changes stay matched without loosening name matching to substrings.
 * Matches only the Kubernetes SafeEncodeString alphabet (no vowels, no 0/1/3, 5-10 chars)
 * so standalone names like "-abc12345" or hyphenated words like "-controller" are never stripped.
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

function checksSet(ids: readonly string[]): ReadonlySet<string> {
  return new Set(ids.map(normalizeCheckId))
}

function configRule(
  namespace: string,
  kind: WorkloadKind,
  name: string,
  ids: readonly string[],
  cls: AcceptedClass,
  reasonKey: AcceptedReasonKey,
): AcceptedConfigAuditRule {
  return {
    namespace,
    kind,
    name,
    checks: checksSet(ids),
    class: cls,
    reasonKey,
    reason: en[reasonKey],
    docAnchor: COMPLIANCE_EXCEPTIONS_DOC_ANCHOR,
  }
}

function rbacRule(
  namespace: string,
  kind: "ClusterRole" | "Role",
  roleName: string,
  ids: readonly string[],
  cls: AcceptedClass,
  reasonKey: AcceptedReasonKey,
): AcceptedRbacRule {
  return {
    namespace,
    kind,
    roleName,
    checks: checksSet(ids),
    class: cls,
    reasonKey,
    reason: en[reasonKey],
    docAnchor: COMPLIANCE_EXCEPTIONS_DOC_ANCHOR,
  }
}

/**
 * Reviewed per-workload, per-check Config Audit exceptions. Exact match on
 * (namespace, kind, normalized name, normalized check ID); any unlisted check or
 * workload stays actionable (fail closed).
 *
 * Adding or expanding an entry requires documented justification and review in
 * docs/common/compliance-hardening.md#risk-accepted-exceptions.
 */
export const ACCEPTED_CONFIG_AUDIT_RULES: readonly AcceptedConfigAuditRule[] = [
  // chaos-testing
  configRule(
    "chaos-testing",
    "DaemonSet",
    "chaos-daemon",
    ["KSV001", "KSV003", "KSV010", "KSV012", "KSV017", "KSV020", "KSV021", "KSV022", "KSV023", "KSV106", "KSV121"],
    "accept",
    "compliance.acceptedReason.chaosDaemonAccept",
  ),
  configRule(
    "chaos-testing",
    "DaemonSet",
    "chaos-daemon",
    ["KSV014"],
    "upstream",
    "compliance.acceptedReason.chaosDaemonReadOnlyRootFs",
  ),
  configRule(
    "chaos-testing",
    "ReplicaSet",
    "chaos-controller-manager",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.chaosControllerUid",
  ),
  configRule(
    "chaos-testing",
    "ReplicaSet",
    "chaos-controller-manager",
    ["KSV001", "KSV003", "KSV012", "KSV014", "KSV106"],
    "upstream",
    "compliance.acceptedReason.chaosControllerSecurityContext",
  ),

  // database
  configRule(
    "database",
    "Pod",
    "narwhal-db-1",
    ["KSV036"],
    "accept",
    "compliance.acceptedReason.cnpgInstanceManagerToken",
  ),

  // devtools
  configRule(
    "devtools",
    "ReplicaSet",
    "argocd-applicationset-controller",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.argocdUid999",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "argocd-dex-server",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.argocdCopyutilUid999",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "argocd-notifications-controller",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.argocdUid999",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "argocd-redis",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.valkey8Uid999",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "argocd-repo-server",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.argocdRepoCopyutilUid999",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "argocd-server",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.argocdUid999",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "gitea",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.giteaUid1000",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "gitea",
    ["KSV014"],
    "upstream",
    "compliance.acceptedReason.giteaReadOnlyRootFs",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "harbor-core",
    ["KSV011", "KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.harborCoreProbes",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "harbor-core",
    ["KSV014"],
    "upstream",
    "compliance.acceptedReason.harborPhotonReadOnlyRootFs",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "harbor-jobservice",
    ["KSV011", "KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.harborJobserviceProbes",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "harbor-jobservice",
    ["KSV014"],
    "upstream",
    "compliance.acceptedReason.harborPhotonReadOnlyRootFs",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "harbor-nginx",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.harborPhotonUid10000",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "harbor-nginx",
    ["KSV014"],
    "upstream",
    "compliance.acceptedReason.harborNginxReadOnlyRootFs",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "harbor-portal",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.harborPhotonUid10000",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "harbor-portal",
    ["KSV014"],
    "upstream",
    "compliance.acceptedReason.harborPortalReadOnlyRootFs",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "harbor-registry",
    ["KSV011", "KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.harborRegistryProbes",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "harbor-registry",
    ["KSV014"],
    "upstream",
    "compliance.acceptedReason.harborRegistryReadOnlyRootFs",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "headlamp",
    ["KSV011", "KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.headlampUidAndProbes",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "kubernetes-dashboard-api",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.k8sDashboardUid",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "kubernetes-dashboard-auth",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.k8sDashboardUid",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "kubernetes-dashboard-kong",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.k8sDashboardKongUid",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "kubernetes-dashboard-metrics-scraper",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.k8sDashboardUid",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "kubernetes-dashboard-web",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.k8sDashboardUid",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "narwhal-portal",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.portalUid1001",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "narwhal-portal-valkey",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.valkey8DefaultUid",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "waypoint",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.waypointUid1337",
  ),
  configRule(
    "devtools",
    "StatefulSet",
    "argocd-application-controller",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.argocdUid999",
  ),
  configRule(
    "devtools",
    "StatefulSet",
    "harbor-redis",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.harborRedisUid999",
  ),
  configRule(
    "devtools",
    "StatefulSet",
    "harbor-redis",
    ["KSV014"],
    "upstream",
    "compliance.acceptedReason.harborRedisReadOnlyRootFs",
  ),

  // iam
  configRule(
    "iam",
    "ReplicaSet",
    "keycloak-operator",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.keycloakOperatorUid1000",
  ),
  configRule(
    "iam",
    "StatefulSet",
    "keycloak",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.keycloakUid1000",
  ),
  configRule(
    "iam",
    "StatefulSet",
    "keycloak",
    ["KSV014"],
    "upstream",
    "compliance.acceptedReason.keycloakReadOnlyRootFs",
  ),

  // monitoring
  configRule(
    "monitoring",
    "DaemonSet",
    "k8s-monitoring-alloy-logs",
    ["KSV012", "KSV020", "KSV021", "KSV023"],
    "accept",
    "compliance.acceptedReason.alloyLogsAccept",
  ),
  configRule(
    "monitoring",
    "DaemonSet",
    "k8s-monitoring-alloy-logs",
    ["KSV118"],
    "upstream",
    "compliance.acceptedReason.alloyLogsPodSecurityContext",
  ),
  configRule(
    "monitoring",
    "DaemonSet",
    "prometheus-stack-prometheus-node-exporter",
    ["KSV009", "KSV010", "KSV023", "KSV121"],
    "accept",
    "compliance.acceptedReason.nodeExporterAccept",
  ),
  configRule(
    "monitoring",
    "ReplicaSet",
    "k8s-monitoring-alloy-operator",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.alloyOperatorUid4242",
  ),
  configRule(
    "monitoring",
    "ReplicaSet",
    "prometheus-stack-grafana",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.grafanaUid472",
  ),
  configRule(
    "monitoring",
    "ReplicaSet",
    "waypoint",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.waypointUid1337",
  ),
  configRule(
    "monitoring",
    "StatefulSet",
    "alertmanager-prometheus-stack-kube-prom-alertmanager",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.alertmanagerUid",
  ),
  configRule(
    "monitoring",
    "StatefulSet",
    "loki",
    ["KSV011"],
    "accept",
    "compliance.acceptedReason.lokiCpuLimit",
  ),
  configRule(
    "monitoring",
    "StatefulSet",
    "prometheus-prometheus-stack-kube-prom-prometheus",
    ["KSV011", "KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.prometheusUidAndCpuLimit",
  ),

  // nfs-quota-agent
  configRule(
    "nfs-quota-agent",
    "DaemonSet",
    "nfs-quota-agent",
    ["KSV001", "KSV012", "KSV017", "KSV023", "KSV121"],
    "accept",
    "compliance.acceptedReason.nfsQuotaAgentAccept",
  ),

  // platform-system
  configRule(
    "platform-system",
    "DaemonSet",
    "metallb-speaker",
    ["KSV009", "KSV012", "KSV020", "KSV021", "KSV022", "KSV106", "KSV119"],
    "accept",
    "compliance.acceptedReason.metallbSpeakerAccept",
  ),
  configRule(
    "platform-system",
    "ReplicaSet",
    "apisix",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.apisixUid636",
  ),
  configRule(
    "platform-system",
    "ReplicaSet",
    "apisix-ingress-controller",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.apisixControllerUid65532",
  ),
  configRule(
    "platform-system",
    "ReplicaSet",
    "apisix-ingress-controller",
    ["KSV011", "KSV015", "KSV016", "KSV018"],
    "upstream",
    "compliance.acceptedReason.apisixControllerInitResources",
  ),
  configRule(
    "platform-system",
    "ReplicaSet",
    "cert-manager",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.certManagerUid",
  ),
  configRule(
    "platform-system",
    "ReplicaSet",
    "cert-manager-cainjector",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.certManagerCainjectorUid",
  ),
  configRule(
    "platform-system",
    "ReplicaSet",
    "cert-manager-webhook",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.certManagerWebhookUid",
  ),
  configRule(
    "platform-system",
    "ReplicaSet",
    "metallb-controller",
    ["KSV021"],
    "accept",
    "compliance.acceptedReason.metallbControllerGid",
  ),
  configRule(
    "platform-system",
    "ReplicaSet",
    "waypoint",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.waypointUid1337",
  ),

  // security-system
  configRule(
    "security-system",
    "DaemonSet",
    "falco",
    ["KSV001", "KSV003", "KSV012", "KSV017", "KSV020", "KSV021", "KSV023", "KSV030", "KSV104", "KSV106", "KSV121"],
    "accept",
    "compliance.acceptedReason.falcoAccept",
  ),
  configRule(
    "security-system",
    "ReplicaSet",
    "falco-falcosidekick",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.falcosidekickUid",
  ),
  configRule(
    "security-system",
    "ReplicaSet",
    "trivy-operator",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.trivyOperatorUid",
  ),
  configRule(
    "security-system",
    "StatefulSet",
    "trivy-server",
    ["KSV021"],
    "accept",
    "compliance.acceptedReason.trivyServerGid",
  ),

  // storage
  configRule(
    "storage",
    "DaemonSet",
    "node-agent",
    ["KSV001", "KSV003", "KSV012", "KSV020", "KSV021", "KSV023", "KSV105", "KSV106"],
    "accept",
    "compliance.acceptedReason.veleroNodeAgentAccept",
  ),
  configRule(
    "storage",
    "ReplicaSet",
    "openbao-agent-injector",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.openbaoInjectorUid",
  ),
  configRule(
    "storage",
    "ReplicaSet",
    "seaweedfs-s3",
    ["KSV011", "KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.seaweedfsS3",
  ),
  configRule(
    "storage",
    "ReplicaSet",
    "velero",
    ["KSV012", "KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.veleroNonNumericUser",
  ),
  configRule(
    "storage",
    "ReplicaSet",
    "velero-ui",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.veleroUiUid",
  ),
  configRule(
    "storage",
    "StatefulSet",
    "openbao",
    ["KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.openbaoUid",
  ),
  configRule(
    "storage",
    "StatefulSet",
    "seaweedfs-filer",
    ["KSV011", "KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.seaweedfsStateful",
  ),
  configRule(
    "storage",
    "StatefulSet",
    "seaweedfs-master",
    ["KSV011", "KSV020", "KSV021"],
    "accept",
    "compliance.acceptedReason.seaweedfsStateful",
  ),
  configRule(
    "storage",
    "StatefulSet",
    "seaweedfs-volume",
    ["KSV011", "KSV012", "KSV020", "KSV021", "KSV023"],
    "accept",
    "compliance.acceptedReason.seaweedfsVolume",
  ),
]

/**
 * Reviewed per-role, per-check RBAC exceptions for platform-authored roles.
 * Exact match on (namespace, kind, roleName) plus normalized check ID (Role requires a
 * non-empty namespace; ClusterRole requires an empty namespace; unknown kind fails closed);
 * any unlisted check (e.g. KSV042 on platform-admin) or unlisted role stays actionable.
 */
export const ACCEPTED_RBAC_RULES: readonly AcceptedRbacRule[] = [
  rbacRule(
    "",
    "ClusterRole",
    "platform-admin",
    ["KSV041", "KSV048", "KSV049", "KSV053", "KSV056"],
    "accept",
    "compliance.acceptedReason.rbacPlatformAdmin",
  ),
  rbacRule(
    "",
    "ClusterRole",
    "developer-workload-admin",
    ["KSV048", "KSV049", "KSV053", "KSV056"],
    "accept",
    "compliance.acceptedReason.rbacDeveloperWorkloadAdmin",
  ),
  rbacRule(
    "devtools",
    "Role",
    "narwhal-portal-jobs",
    ["KSV048"],
    "accept",
    "compliance.acceptedReason.rbacPortalJobs",
  ),
  rbacRule(
    "monitoring",
    "Role",
    "narwhal-portal-notification-channels",
    ["KSV113"],
    "accept",
    "compliance.acceptedReason.rbacPortalNotificationChannels",
  ),
]

const configRuleIndex = new Map<string, AcceptedConfigAuditRule[]>()
for (const rule of ACCEPTED_CONFIG_AUDIT_RULES) {
  const key = `${rule.namespace}\0${rule.kind}\0${rule.name}`
  const bucket = configRuleIndex.get(key)
  if (bucket) bucket.push(rule)
  else configRuleIndex.set(key, [rule])
}

const rbacRuleIndex = new Map<string, AcceptedRbacRule[]>()
for (const rule of ACCEPTED_RBAC_RULES) {
  const key = `${rule.namespace}\0${rule.kind}\0${rule.roleName}`
  const bucket = rbacRuleIndex.get(key)
  if (bucket) bucket.push(rule)
  else rbacRuleIndex.set(key, [rule])
}

export function matchConfigAuditException(
  namespace: string,
  kind: string,
  name: string,
  checkId: string,
  deploymentOwners?: readonly string[],
): AcceptedMatch | null {
  const normalizedName = normalizeWorkloadName(kind, name, deploymentOwners)
  const rules = configRuleIndex.get(`${namespace}\0${kind}\0${normalizedName}`)
  if (!rules) return null
  const normalizedCheck = normalizeCheckId(checkId)
  for (const rule of rules) {
    if (rule.checks.has(normalizedCheck)) {
      return {
        class: rule.class,
        reasonKey: rule.reasonKey,
        reason: rule.reason,
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
  const rules = rbacRuleIndex.get(`${namespace}\0${kind}\0${roleName}`)
  if (!rules) return null
  const normalizedCheck = normalizeCheckId(checkId)
  for (const rule of rules) {
    if (rule.checks.has(normalizedCheck)) {
      return {
        class: rule.class,
        reasonKey: rule.reasonKey,
        reason: rule.reason,
        docAnchor: rule.docAnchor,
      }
    }
  }
  return null
}
