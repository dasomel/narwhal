// Curated list of platform components the OSS updates view tracks. VERSIONS.md stays the source of
// truth for the *declared* version (via the generated JSON); this file pins the exact upstream repo,
// Argo CD Application (when Helm-chart tracked via Argo), authoritative running version source
// (endpoint, workload+container, static pod, CRD annotation, or CLI), sibling companions (never mixed
// into drift), and exact image repository for Trivy CVE matching.

export type WorkloadKind = "Deployment" | "DaemonSet" | "StatefulSet"

export type OssRunningSource =
  | { kind: "server-version" }
  | { kind: "workload"; resource: WorkloadKind; namespace: string; name: string; container: string }
  | { kind: "static-pod"; namespace: string; namePrefix: string; container: string }
  | { kind: "crd-annotation"; crdName: string; annotation: string }
  | { kind: "cli" }

export interface OssSiblingSpec {
  label: string
  resource: WorkloadKind
  namespace: string
  name: string
  container: string
  init?: boolean
  /** Exact tag suffixes accepted for this sibling (optional leading `v` is always allowed). */
  allowedTagSuffixes?: readonly string[]
}

export interface OssComponent {
  /** Exact `Component` cell text in VERSIONS.md. */
  name: string
  /** GitHub `owner/repo` whose releases carry the upstream app version. */
  repo: string
  /** Argo CD Application whose source.targetRevision is the running chart version. */
  argoApp?: string
  /** Authoritative source for the running version in the cluster. */
  runningSource: OssRunningSource
  /** Exact tag suffixes accepted for this component (optional leading `v` is always allowed). */
  allowedTagSuffixes?: readonly string[]
  /** Companion containers/workloads in the same release: rendered separately, never mixed into drift. */
  siblings?: readonly OssSiblingSpec[]
  /** Exact repository (or `org/repo`) in Trivy VulnerabilityReports for the primary image. */
  imageMatch?: string
  /** Optional namespace constraint when joining Trivy VulnerabilityReports for CVEs. */
  cveNamespace?: string
  /** Declared minor line is deliberate (VERSIONS.md "NOT 3.16", "capped below 26.6.x", ...): the
   * "latest" shown is the newest release on the same major.minor, not the newest overall. */
  trackMinor?: boolean
  /** Upgrade needs a mandatory ordering/migration step: shown as a warning in the request. */
  highRisk?: boolean
}

export const OSS_COMPONENTS: readonly OssComponent[] = [
  {
    name: "Kubernetes",
    repo: "kubernetes/kubernetes",
    runningSource: { kind: "server-version" },
    imageMatch: "kube-apiserver",
    cveNamespace: "kube-system",
    trackMinor: true,
    highRisk: true,
  },
  {
    name: "Cilium",
    repo: "cilium/cilium",
    runningSource: { kind: "workload", resource: "DaemonSet", namespace: "kube-system", name: "cilium", container: "cilium-agent" },
    siblings: [
      { label: "cilium-envoy", resource: "DaemonSet", namespace: "kube-system", name: "cilium-envoy", container: "cilium-envoy" },
      { label: "cilium-operator", resource: "Deployment", namespace: "kube-system", name: "cilium-operator", container: "cilium-operator" },
    ],
    imageMatch: "cilium/cilium",
    cveNamespace: "kube-system",
    highRisk: true,
  },
  {
    name: "APISIX",
    repo: "apache/apisix",
    argoApp: "apisix",
    runningSource: { kind: "workload", resource: "Deployment", namespace: "platform-system", name: "apisix", container: "apisix" },
    allowedTagSuffixes: ["-ubuntu"],
    siblings: [
      { label: "apisix-ingress-controller", resource: "Deployment", namespace: "platform-system", name: "apisix-ingress-controller", container: "apisix-ingress-controller" },
    ],
    imageMatch: "apache/apisix",
    cveNamespace: "platform-system",
    trackMinor: true,
  },
  {
    name: "MetalLB",
    repo: "metallb/metallb",
    argoApp: "metallb",
    runningSource: { kind: "workload", resource: "Deployment", namespace: "platform-system", name: "metallb-controller", container: "controller" },
    siblings: [
      { label: "speaker", resource: "DaemonSet", namespace: "platform-system", name: "metallb-speaker", container: "speaker" },
    ],
    imageMatch: "metallb/controller",
    cveNamespace: "platform-system",
  },
  {
    name: "Gateway API CRDs",
    repo: "kubernetes-sigs/gateway-api",
    runningSource: {
      kind: "crd-annotation",
      crdName: "gateways.gateway.networking.k8s.io",
      annotation: "gateway.networking.k8s.io/bundle-version",
    },
  },
  {
    name: "kube-vip",
    repo: "kube-vip/kube-vip",
    runningSource: { kind: "static-pod", namespace: "kube-system", namePrefix: "kube-vip-", container: "kube-vip" },
    imageMatch: "kube-vip/kube-vip",
    cveNamespace: "kube-system",
  },
  {
    name: "Istio",
    repo: "istio/istio",
    argoApp: "istiod",
    runningSource: { kind: "workload", resource: "Deployment", namespace: "istio-system", name: "istiod", container: "discovery" },
    allowedTagSuffixes: ["-distroless"],
    siblings: [
      { label: "istio-cni-node", resource: "DaemonSet", namespace: "istio-system", name: "istio-cni-node", container: "install-cni", allowedTagSuffixes: ["-distroless"] },
      { label: "ztunnel", resource: "DaemonSet", namespace: "istio-system", name: "ztunnel", container: "istio-proxy" },
    ],
    imageMatch: "registry.istio.io/release/pilot",
    cveNamespace: "istio-system",
    highRisk: true,
  },
  {
    name: "csi-driver-nfs",
    repo: "kubernetes-csi/csi-driver-nfs",
    runningSource: { kind: "workload", resource: "Deployment", namespace: "kube-system", name: "csi-nfs-controller", container: "nfs" },
    siblings: [
      { label: "csi-provisioner", resource: "Deployment", namespace: "kube-system", name: "csi-nfs-controller", container: "csi-provisioner" },
      { label: "csi-resizer", resource: "Deployment", namespace: "kube-system", name: "csi-nfs-controller", container: "csi-resizer" },
      { label: "csi-snapshotter", resource: "Deployment", namespace: "kube-system", name: "csi-nfs-controller", container: "csi-snapshotter" },
      { label: "liveness-probe", resource: "Deployment", namespace: "kube-system", name: "csi-nfs-controller", container: "liveness-probe" },
    ],
    imageMatch: "sig-storage/nfsplugin",
    cveNamespace: "kube-system",
  },
  {
    name: "SeaweedFS",
    repo: "seaweedfs/seaweedfs",
    argoApp: "seaweedfs",
    runningSource: { kind: "workload", resource: "StatefulSet", namespace: "storage", name: "seaweedfs-master", container: "seaweedfs" },
    imageMatch: "chrislusf/seaweedfs",
    cveNamespace: "storage",
  },
  {
    name: "CloudNative-PG",
    repo: "cloudnative-pg/cloudnative-pg",
    runningSource: { kind: "workload", resource: "Deployment", namespace: "platform-system", name: "cnpg-cloudnative-pg", container: "manager" },
    imageMatch: "cloudnative-pg/cloudnative-pg",
    cveNamespace: "platform-system",
  },
  {
    name: "Keycloak",
    repo: "keycloak/keycloak",
    runningSource: { kind: "workload", resource: "StatefulSet", namespace: "iam", name: "keycloak", container: "keycloak" },
    siblings: [
      { label: "keycloak-operator", resource: "Deployment", namespace: "iam", name: "keycloak-operator", container: "keycloak-operator" },
    ],
    imageMatch: "keycloak/keycloak",
    cveNamespace: "iam",
    trackMinor: true,
    highRisk: true,
  },
  {
    name: "cert-manager",
    repo: "cert-manager/cert-manager",
    argoApp: "cert-manager",
    runningSource: { kind: "workload", resource: "Deployment", namespace: "platform-system", name: "cert-manager", container: "cert-manager-controller" },
    siblings: [
      { label: "cainjector", resource: "Deployment", namespace: "platform-system", name: "cert-manager-cainjector", container: "cert-manager-cainjector" },
      { label: "webhook", resource: "Deployment", namespace: "platform-system", name: "cert-manager-webhook", container: "cert-manager-webhook" },
    ],
    imageMatch: "jetstack/cert-manager-controller",
    cveNamespace: "platform-system",
  },
  {
    name: "OpenBao",
    repo: "openbao/openbao",
    argoApp: "openbao",
    runningSource: { kind: "workload", resource: "StatefulSet", namespace: "storage", name: "openbao", container: "openbao" },
    imageMatch: "openbao/openbao",
    cveNamespace: "storage",
    highRisk: true,
  },
  {
    name: "Kyverno",
    repo: "kyverno/kyverno",
    argoApp: "kyverno",
    runningSource: { kind: "workload", resource: "Deployment", namespace: "platform-system", name: "kyverno-admission-controller", container: "kyverno" },
    siblings: [
      { label: "kyverno-pre", resource: "Deployment", namespace: "platform-system", name: "kyverno-admission-controller", container: "kyverno-pre", init: true },
    ],
    imageMatch: "kyverno/kyverno",
    cveNamespace: "platform-system",
  },
  {
    name: "Falco",
    repo: "falcosecurity/falco",
    argoApp: "falco",
    runningSource: { kind: "workload", resource: "DaemonSet", namespace: "security-system", name: "falco", container: "falco" },
    siblings: [
      { label: "falcosidekick", resource: "Deployment", namespace: "security-system", name: "falco-falcosidekick", container: "falcosidekick" },
    ],
    imageMatch: "falcosecurity/falco",
    cveNamespace: "security-system",
  },
  {
    name: "Velero",
    repo: "vmware-tanzu/velero",
    argoApp: "velero",
    runningSource: { kind: "workload", resource: "Deployment", namespace: "storage", name: "velero", container: "velero" },
    siblings: [
      { label: "velero-plugin-for-aws", resource: "Deployment", namespace: "storage", name: "velero", container: "velero-plugin-for-aws", init: true },
      { label: "velero-ui", resource: "Deployment", namespace: "storage", name: "velero-ui", container: "velero-ui" },
    ],
    imageMatch: "velero/velero",
    cveNamespace: "storage",
  },
  {
    name: "Loki",
    repo: "grafana/loki",
    argoApp: "loki",
    runningSource: { kind: "workload", resource: "StatefulSet", namespace: "monitoring", name: "loki", container: "loki" },
    siblings: [
      { label: "loki-sc-rules", resource: "StatefulSet", namespace: "monitoring", name: "loki", container: "loki-sc-rules" },
    ],
    imageMatch: "grafana/loki",
    cveNamespace: "monitoring",
  },
  {
    name: "Tempo",
    repo: "grafana/tempo",
    argoApp: "tempo",
    runningSource: { kind: "workload", resource: "StatefulSet", namespace: "monitoring", name: "tempo", container: "tempo" },
    imageMatch: "grafana/tempo",
    cveNamespace: "monitoring",
    trackMinor: true,
  },
  {
    name: "Gitea",
    repo: "go-gitea/gitea",
    runningSource: { kind: "workload", resource: "Deployment", namespace: "devtools", name: "gitea", container: "gitea" },
    allowedTagSuffixes: ["-rootless"],
    imageMatch: "docker.gitea.com/gitea",
    cveNamespace: "devtools",
  },
  {
    name: "ArgoCD",
    repo: "argoproj/argo-cd",
    runningSource: { kind: "workload", resource: "Deployment", namespace: "devtools", name: "argocd-server", container: "argocd-server" },
    imageMatch: "argoproj/argocd",
    cveNamespace: "devtools",
    highRisk: true,
  },
  {
    name: "Harbor",
    repo: "goharbor/harbor",
    argoApp: "harbor",
    runningSource: { kind: "workload", resource: "Deployment", namespace: "devtools", name: "harbor-core", container: "core" },
    imageMatch: "goharbor/harbor-core",
    cveNamespace: "devtools",
  },
  {
    name: "Headlamp",
    repo: "headlamp-k8s/headlamp",
    argoApp: "headlamp",
    runningSource: { kind: "workload", resource: "Deployment", namespace: "devtools", name: "headlamp", container: "headlamp" },
    imageMatch: "headlamp-k8s/headlamp",
    cveNamespace: "devtools",
  },
  {
    name: "Helm",
    repo: "helm/helm",
    runningSource: { kind: "cli" },
  },
  {
    name: "metrics-server",
    repo: "kubernetes-sigs/metrics-server",
    runningSource: { kind: "workload", resource: "Deployment", namespace: "kube-system", name: "metrics-server", container: "metrics-server" },
    imageMatch: "metrics-server/metrics-server",
    cveNamespace: "kube-system",
  },
]
