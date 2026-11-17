import type { AcceptedClass } from "@/types/compliance"

export const COMPLIANCE_EXCEPTIONS_DOC_ANCHOR = "docs/common/compliance-hardening.md#risk-accepted-exceptions"

export type WorkloadKind = "ReplicaSet" | "DaemonSet" | "StatefulSet"

export interface AcceptedConfigAuditRule {
  namespace: string
  kind: WorkloadKind
  name: string
  checks: ReadonlySet<string>
  class: AcceptedClass
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
  reason: string
  docAnchor: string
}

export interface AcceptedMatch {
  class: AcceptedClass
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
 * Requires either at least one digit in the 6-10 char suffix or Kubernetes' consonant-only
 * SafeEncodeString alphabet (8-10 chars) so real hyphenated suffixes like "-controller"
 * or "-cainjector" are never stripped.
 */
const REPLICASET_HASH_SUFFIX_RE =
  /^(.+)-((?=[a-z0-9]*[0-9])[a-z0-9]{6,10}|[bcdfghjklmnpqrstvwxz]{8,10})$/

export function normalizeWorkloadName(kind: string, name: string): string {
  if (kind !== "ReplicaSet") return name
  const m = REPLICASET_HASH_SUFFIX_RE.exec(name)
  return m ? m[1] : name
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
  reason: string,
): AcceptedConfigAuditRule {
  return {
    namespace,
    kind,
    name,
    checks: checksSet(ids),
    class: cls,
    reason,
    docAnchor: COMPLIANCE_EXCEPTIONS_DOC_ANCHOR,
  }
}

function rbacRule(
  namespace: string,
  kind: "ClusterRole" | "Role",
  roleName: string,
  ids: readonly string[],
  cls: AcceptedClass,
  reason: string,
): AcceptedRbacRule {
  return {
    namespace,
    kind,
    roleName,
    checks: checksSet(ids),
    class: cls,
    reason,
    docAnchor: COMPLIANCE_EXCEPTIONS_DOC_ANCHOR,
  }
}

/**
 * Reviewed per-workload, per-check Config Audit exceptions reconciled from the hardening lanes
 * (.omc/live/compliance/accept-tables.md). Exact match on (namespace, kind, normalized name,
 * normalized check ID); any unlisted check or workload stays actionable (fail closed).
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
    "Requires privileged, hostPID, /sys and containerd.sock hostPath, root UID 0, and capabilities to inject pod netns and I/O faults",
  ),
  configRule(
    "chaos-testing",
    "DaemonSet",
    "chaos-daemon",
    ["KSV014"],
    "upstream",
    "Upstream chaos-mesh 2.8.3 DaemonSet template does not expose readOnlyRootFilesystem",
  ),
  configRule(
    "chaos-testing",
    "ReplicaSet",
    "chaos-controller-manager",
    ["KSV020", "KSV021"],
    "accept",
    "Uses upstream chaos-mesh image default non-root UID/GID",
  ),
  configRule(
    "chaos-testing",
    "ReplicaSet",
    "chaos-controller-manager",
    ["KSV001", "KSV003", "KSV012", "KSV014", "KSV106"],
    "upstream",
    "Upstream chaos-mesh 2.8.3 controller-manager template does not expose containerSecurityContext",
  ),

  // devtools
  configRule(
    "devtools",
    "ReplicaSet",
    "argocd-applicationset-controller",
    ["KSV020", "KSV021"],
    "accept",
    "Uses upstream ArgoCD image non-root UID/GID 999",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "argocd-dex-server",
    ["KSV020", "KSV021"],
    "accept",
    "Uses upstream ArgoCD dex-server and copyutil non-root UID/GID 999",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "argocd-notifications-controller",
    ["KSV020", "KSV021"],
    "accept",
    "Uses upstream ArgoCD image non-root UID/GID 999",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "argocd-redis",
    ["KSV020", "KSV021"],
    "accept",
    "Uses upstream Valkey 8 Alpine image default UID/GID 999",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "argocd-repo-server",
    ["KSV020", "KSV021"],
    "accept",
    "Uses upstream ArgoCD repo-server and copyutil non-root UID/GID 999",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "argocd-server",
    ["KSV020", "KSV021"],
    "accept",
    "Uses upstream ArgoCD image non-root UID/GID 999",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "gitea",
    ["KSV020", "KSV021"],
    "accept",
    "Uses upstream Gitea rootless image default git UID/GID 1000",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "gitea",
    ["KSV012", "KSV014"],
    "upstream",
    "Gitea rootless requires writable paths for SSH host keys, LevelDB queues, and git hooks; readOnlyRootFilesystem unsupported",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "harbor-core",
    ["KSV011", "KSV020", "KSV021"],
    "accept",
    "Uses Harbor Photon OS UID/GID 10000; omits CPU limit to prevent probe timeouts/502 during scan job bursts (D-HARBOR-PROBES)",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "harbor-core",
    ["KSV014"],
    "upstream",
    "Harbor Photon OS image and chart 1.19.1 template do not support readOnlyRootFilesystem",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "harbor-jobservice",
    ["KSV011", "KSV020", "KSV021"],
    "accept",
    "Uses Harbor Photon OS UID/GID 10000; omits CPU limit to prevent throttling during job execution (D-HARBOR-PROBES)",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "harbor-jobservice",
    ["KSV014"],
    "upstream",
    "Harbor Photon OS image and chart 1.19.1 template do not support readOnlyRootFilesystem",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "harbor-nginx",
    ["KSV020", "KSV021"],
    "accept",
    "Uses Harbor Photon OS UID/GID 10000",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "harbor-nginx",
    ["KSV014"],
    "upstream",
    "Nginx temporary writes and Harbor chart 1.19.1 template do not support readOnlyRootFilesystem",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "harbor-portal",
    ["KSV020", "KSV021"],
    "accept",
    "Uses Harbor Photon OS UID/GID 10000",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "harbor-portal",
    ["KSV014"],
    "upstream",
    "Nginx-based web UI and Harbor chart 1.19.1 template do not support readOnlyRootFilesystem",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "harbor-registry",
    ["KSV011", "KSV020", "KSV021"],
    "accept",
    "Uses Harbor Photon OS UID/GID 10000; omits CPU limit to prevent probe timeouts on large image pushes (D-HARBOR-PROBES)",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "harbor-registry",
    ["KSV014"],
    "upstream",
    "Harbor registry/registryctl chart 1.19.1 template does not support readOnlyRootFilesystem",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "headlamp",
    ["KSV011", "KSV020", "KSV021"],
    "accept",
    "Uses upstream Headlamp hardcoded UID 100/GID 101; omits CPU limit to avoid 1s probe timeouts under VM load",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "kubernetes-dashboard-api",
    ["KSV020", "KSV021"],
    "accept",
    "Uses Kubernetes Dashboard 7.14.0 chart default UID 1001/GID 2001",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "kubernetes-dashboard-auth",
    ["KSV020", "KSV021"],
    "accept",
    "Uses Kubernetes Dashboard 7.14.0 chart default UID 1001/GID 2001",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "kubernetes-dashboard-kong",
    ["KSV020", "KSV021"],
    "accept",
    "Uses Kong 2.52.0 chart default UID/GID 1000",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "kubernetes-dashboard-metrics-scraper",
    ["KSV020", "KSV021"],
    "accept",
    "Uses Kubernetes Dashboard 7.14.0 chart default UID 1001/GID 2001",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "kubernetes-dashboard-web",
    ["KSV020", "KSV021"],
    "accept",
    "Uses Kubernetes Dashboard 7.14.0 chart default UID 1001/GID 2001",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "narwhal-portal",
    ["KSV020", "KSV021"],
    "accept",
    "Portal image fixes non-root nextjs:nodejs UID/GID 1001 in portal/Dockerfile",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "narwhal-portal-valkey",
    ["KSV020", "KSV021"],
    "accept",
    "Relies on upstream valkey:8-alpine default non-root UID/GID 999",
  ),
  configRule(
    "devtools",
    "ReplicaSet",
    "waypoint",
    ["KSV020", "KSV021"],
    "accept",
    "Istio ambient waypoint proxy requires fixed non-root UID/GID 1337",
  ),
  configRule(
    "devtools",
    "StatefulSet",
    "argocd-application-controller",
    ["KSV020", "KSV021"],
    "accept",
    "Uses upstream ArgoCD image non-root UID/GID 999",
  ),
  configRule(
    "devtools",
    "StatefulSet",
    "harbor-redis",
    ["KSV020", "KSV021"],
    "accept",
    "Uses Redis image default UID/GID 999",
  ),
  configRule(
    "devtools",
    "StatefulSet",
    "harbor-redis",
    ["KSV014"],
    "upstream",
    "Harbor chart 1.19.1 redis StatefulSet template does not expose readOnlyRootFilesystem",
  ),

  // iam
  configRule(
    "iam",
    "ReplicaSet",
    "keycloak-operator",
    ["KSV014", "KSV020", "KSV021"],
    "accept",
    "Uses upstream keycloak-operator:26.5.7 UID 1000; KSV014 deferred pending Quarkus /tmp emptyDir live validation",
  ),
  configRule(
    "iam",
    "StatefulSet",
    "keycloak",
    ["KSV014", "KSV020", "KSV021"],
    "accept",
    "Uses upstream keycloak:26.5.7 UID 1000; KSV014 deferred pending /opt/keycloak/data and $HOME/.keycloak live validation",
  ),

  // monitoring
  configRule(
    "monitoring",
    "DaemonSet",
    "k8s-monitoring-alloy-logs",
    ["KSV012", "KSV020", "KSV021", "KSV023"],
    "accept",
    "Requires root UID 0, recommended UID/GID ownership, and /var/log hostPath to collect node and pod logs",
  ),
  configRule(
    "monitoring",
    "DaemonSet",
    "k8s-monitoring-alloy-logs",
    ["KSV118"],
    "upstream",
    "Upstream chart schema does not expose DaemonSet pod-level securityContext",
  ),
  configRule(
    "monitoring",
    "DaemonSet",
    "prometheus-stack-prometheus-node-exporter",
    ["KSV009", "KSV010", "KSV023", "KSV121"],
    "accept",
    "Requires hostNetwork, hostPID, and root/proc/sys hostPath mounts to collect host metrics",
  ),
  configRule(
    "monitoring",
    "ReplicaSet",
    "k8s-monitoring-alloy-operator",
    ["KSV020", "KSV021"],
    "accept",
    "Relies on upstream image fixed non-root UID/GID 4242",
  ),
  configRule(
    "monitoring",
    "ReplicaSet",
    "prometheus-stack-grafana",
    ["KSV020", "KSV021"],
    "accept",
    "Relies on upstream Grafana UID 472 and NFS persistent volume file ownership",
  ),
  configRule(
    "monitoring",
    "ReplicaSet",
    "waypoint",
    ["KSV020", "KSV021"],
    "accept",
    "Istio ambient waypoint proxy requires fixed non-root UID/GID 1337",
  ),
  configRule(
    "monitoring",
    "StatefulSet",
    "alertmanager-prometheus-stack-kube-prom-alertmanager",
    ["KSV020", "KSV021"],
    "accept",
    "Relies on upstream Alertmanager UID 1000 and config-reloader UID 65534",
  ),
  configRule(
    "monitoring",
    "StatefulSet",
    "loki",
    ["KSV011"],
    "accept",
    "Omits main CPU limit intentionally to prevent CFS throttling timeouts on ingest/queries (D-MON-QOS-LOKI)",
  ),
  configRule(
    "monitoring",
    "StatefulSet",
    "prometheus-prometheus-stack-kube-prom-prometheus",
    ["KSV011", "KSV020", "KSV021"],
    "accept",
    "Relies on Prometheus UID 1000/reloader UID 65534; omits CPU limit to avoid TSDB compaction probe timeouts (D-MON-QOS)",
  ),

  // platform-system
  configRule(
    "platform-system",
    "DaemonSet",
    "metallb-speaker",
    ["KSV009", "KSV012", "KSV020", "KSV021", "KSV022", "KSV106", "KSV119"],
    "accept",
    "Requires hostNetwork, NET_RAW/NET_ADMIN, and UID/GID 0 raw socket access for L2 ARP/NDP announcements",
  ),
  configRule(
    "platform-system",
    "ReplicaSet",
    "apisix",
    ["KSV020", "KSV021"],
    "accept",
    "Uses upstream APISIX image fixed UID/GID 636",
  ),
  configRule(
    "platform-system",
    "ReplicaSet",
    "apisix",
    ["KSV014"],
    "upstream",
    "Dynamically generates /usr/local/apisix/conf/nginx.conf at startup; emptyDir shadows packaged assets without initContainer copy",
  ),
  configRule(
    "platform-system",
    "ReplicaSet",
    "apisix-ingress-controller",
    ["KSV020", "KSV021"],
    "accept",
    "Uses upstream image UID/GID 65532",
  ),
  configRule(
    "platform-system",
    "ReplicaSet",
    "apisix-ingress-controller",
    ["KSV011", "KSV015", "KSV016", "KSV018"],
    "upstream",
    "Upstream chart 0.14.1 template does not expose resources fields for initContainers",
  ),
  configRule(
    "platform-system",
    "ReplicaSet",
    "cert-manager",
    ["KSV020", "KSV021"],
    "accept",
    "Uses upstream cert-manager controller UID 1000/GID 65532",
  ),
  configRule(
    "platform-system",
    "ReplicaSet",
    "cert-manager-cainjector",
    ["KSV020", "KSV021"],
    "accept",
    "Uses upstream cert-manager cainjector UID 1000/GID 65532",
  ),
  configRule(
    "platform-system",
    "ReplicaSet",
    "cert-manager-webhook",
    ["KSV020", "KSV021"],
    "accept",
    "Uses upstream cert-manager webhook UID 1000/GID 65532",
  ),
  configRule(
    "platform-system",
    "ReplicaSet",
    "metallb-controller",
    ["KSV021"],
    "accept",
    "Uses upstream MetalLB controller nobody GID 65534",
  ),
  configRule(
    "platform-system",
    "ReplicaSet",
    "waypoint",
    ["KSV020", "KSV021"],
    "accept",
    "Istio ambient waypoint proxy requires fixed non-root UID/GID 1337",
  ),
  configRule(
    "platform-system",
    "ReplicaSet",
    "waypoint",
    ["KSV030", "KSV104"],
    "upstream",
    "Kyverno excludes platform-system webhooks to prevent self-lockout; istiod gateways.seccompProfile is outside lane scope",
  ),

  // security-system
  configRule(
    "security-system",
    "DaemonSet",
    "falco",
    ["KSV001", "KSV003", "KSV012", "KSV014", "KSV017", "KSV020", "KSV021", "KSV023", "KSV030", "KSV104", "KSV106", "KSV121"],
    "accept",
    "Requires privileged, root, hostPath (/boot,/etc,/proc,/usr,containerd.sock), and disabled seccomp for modern_ebpf instrumentation",
  ),
  configRule(
    "security-system",
    "ReplicaSet",
    "falco-falcosidekick",
    ["KSV020", "KSV021"],
    "accept",
    "Keeps upstream falco 9.2.1 / falcosidekick 0.14.1 default UID/GID 1234",
  ),
  configRule(
    "security-system",
    "ReplicaSet",
    "trivy-operator",
    ["KSV020", "KSV021"],
    "accept",
    "Keeps upstream trivy-operator:0.25.0 default UID/GID 10000",
  ),
  configRule(
    "security-system",
    "StatefulSet",
    "trivy-server",
    ["KSV021"],
    "accept",
    "Upstream chart sets fsGroup 65534 with runAsUser 65534 and omits runAsGroup",
  ),

  // storage
  configRule(
    "storage",
    "DaemonSet",
    "node-agent",
    ["KSV001", "KSV003", "KSV012", "KSV014", "KSV020", "KSV021", "KSV023", "KSV105", "KSV106"],
    "accept",
    "Requires privileged/UID 0 and /var/lib/kubelet hostPath mounts for kopia fs-backup",
  ),
  configRule(
    "storage",
    "ReplicaSet",
    "openbao-agent-injector",
    ["KSV020", "KSV021"],
    "accept",
    "Uses upstream OpenBao image/chart default UID/GID 100/1000",
  ),
  configRule(
    "storage",
    "ReplicaSet",
    "seaweedfs-s3",
    ["KSV011", "KSV020", "KSV021"],
    "accept",
    "Omits CPU limit to prevent S3 upload throttling (D-sw-qos) and uses seaweed UID/GID 1000",
  ),
  configRule(
    "storage",
    "ReplicaSet",
    "velero",
    ["KSV012", "KSV020", "KSV021"],
    "accept",
    "Upstream velero:v1.18.1 non-numeric USER cnb (GID 65532) fails CreateContainerConfigError with runAsNonRoot: true",
  ),
  configRule(
    "storage",
    "ReplicaSet",
    "velero-ui",
    ["KSV020", "KSV021"],
    "accept",
    "Uses upstream node image UID/GID 1000",
  ),
  configRule(
    "storage",
    "StatefulSet",
    "openbao",
    ["KSV020", "KSV021"],
    "accept",
    "Uses upstream OpenBao UID/GID 100/1000 matching raft PVC ownership",
  ),
  configRule(
    "storage",
    "StatefulSet",
    "seaweedfs-filer",
    ["KSV011", "KSV020", "KSV021"],
    "accept",
    "Omits CPU limit to prevent upload throttling (D-sw-qos) and uses seaweed UID/GID 1000",
  ),
  configRule(
    "storage",
    "StatefulSet",
    "seaweedfs-master",
    ["KSV011", "KSV020", "KSV021"],
    "accept",
    "Omits CPU limit to prevent upload throttling (D-sw-qos) and uses seaweed UID/GID 1000",
  ),
  configRule(
    "storage",
    "StatefulSet",
    "seaweedfs-volume",
    ["KSV011", "KSV012", "KSV020", "KSV021", "KSV023"],
    "accept",
    "Requires root and hostPath for existing root-owned /ssd/object_store data; omits CPU limit (D-sw-qos) with UID/GID <= 10000",
  ),
]

/**
 * Reviewed per-role, per-check RBAC exceptions for platform-authored roles.
 * Exact match on roleName (and namespace when namespaced Role) plus normalized check ID;
 * any unlisted check (e.g. KSV042 on platform-admin) or unlisted role stays actionable.
 */
export const ACCEPTED_RBAC_RULES: readonly AcceptedRbacRule[] = [
  rbacRule(
    "",
    "ClusterRole",
    "platform-admin",
    ["KSV041", "KSV048", "KSV049", "KSV053", "KSV056"],
    "accept",
    "Administrative privileges required by OIDC platform-admin role",
  ),
  rbacRule(
    "",
    "ClusterRole",
    "developer-workload-admin",
    ["KSV048", "KSV049", "KSV053", "KSV056"],
    "accept",
    "Developer workload management privileges scoped solely via dev RoleBinding",
  ),
  rbacRule(
    "devtools",
    "Role",
    "narwhal-portal-jobs",
    ["KSV048"],
    "accept",
    "Tuning and Kaniko build Jobs guarded by Kyverno admission policies",
  ),
  rbacRule(
    "monitoring",
    "Role",
    "narwhal-portal-notification-channels",
    ["KSV113"],
    "accept",
    "Scoped to a single Secret name with get/update/patch verbs only",
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
  const key = `${rule.namespace}\0${rule.roleName}`
  const bucket = rbacRuleIndex.get(key)
  if (bucket) bucket.push(rule)
  else rbacRuleIndex.set(key, [rule])
}

export function matchConfigAuditException(
  namespace: string,
  kind: string,
  name: string,
  checkId: string,
): AcceptedMatch | null {
  const normalizedName = normalizeWorkloadName(kind, name)
  const rules = configRuleIndex.get(`${namespace}\0${kind}\0${normalizedName}`)
  if (!rules) return null
  const normalizedCheck = normalizeCheckId(checkId)
  for (const rule of rules) {
    if (rule.checks.has(normalizedCheck)) {
      return { class: rule.class, reason: rule.reason, docAnchor: rule.docAnchor }
    }
  }
  return null
}

export function matchRbacException(
  roleName: string,
  checkId: string,
  namespace = "",
): AcceptedMatch | null {
  const rules = rbacRuleIndex.get(`${namespace}\0${roleName}`)
  if (!rules) return null
  const normalizedCheck = normalizeCheckId(checkId)
  for (const rule of rules) {
    if (rule.checks.has(normalizedCheck)) {
      return { class: rule.class, reason: rule.reason, docAnchor: rule.docAnchor }
    }
  }
  return null
}
