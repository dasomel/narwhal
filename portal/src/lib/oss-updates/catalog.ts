// Curated list of platform components the OSS updates view tracks. VERSIONS.md stays the source of
// truth for the *declared* version (via the generated JSON); this file only adds what VERSIONS.md
// cannot say: where upstream releases live, which Argo CD Application carries the chart, which
// running image identifies the component in Trivy reports, and which minor line is deliberately held.

export interface OssComponent {
  /** Exact `Component` cell text in VERSIONS.md. */
  name: string
  /** GitHub `owner/repo` whose releases carry the upstream app version. */
  repo: string
  /** Argo CD Application whose source.targetRevision is the running chart version. */
  argoApp?: string
  /** Substring of the image repository in Trivy VulnerabilityReports for the running workload. */
  imageMatch?: string
  /** Declared minor line is deliberate (VERSIONS.md "NOT 3.16", "capped below 26.6.x", ...): the
   * "latest" shown is the newest release on the same major.minor, not the newest overall. */
  trackMinor?: boolean
  /** Upgrade needs a mandatory ordering/migration step: shown as a warning in the request. */
  highRisk?: boolean
}

export const OSS_COMPONENTS: readonly OssComponent[] = [
  { name: "Kubernetes", repo: "kubernetes/kubernetes", trackMinor: true, highRisk: true },
  { name: "Cilium", repo: "cilium/cilium", imageMatch: "cilium/cilium", highRisk: true },
  { name: "APISIX", repo: "apache/apisix", argoApp: "apisix", imageMatch: "apache/apisix", trackMinor: true },
  { name: "MetalLB", repo: "metallb/metallb", argoApp: "metallb", imageMatch: "metallb/controller" },
  { name: "Gateway API CRDs", repo: "kubernetes-sigs/gateway-api" },
  { name: "kube-vip", repo: "kube-vip/kube-vip", imageMatch: "kube-vip/kube-vip" },
  { name: "Istio", repo: "istio/istio", argoApp: "istiod", imageMatch: "istio/pilot", highRisk: true },
  { name: "csi-driver-nfs", repo: "kubernetes-csi/csi-driver-nfs", imageMatch: "nfsplugin" },
  { name: "SeaweedFS", repo: "seaweedfs/seaweedfs", argoApp: "seaweedfs", imageMatch: "chrislusf/seaweedfs" },
  { name: "CloudNative-PG", repo: "cloudnative-pg/cloudnative-pg", imageMatch: "cloudnative-pg/cloudnative-pg" },
  { name: "Keycloak", repo: "keycloak/keycloak", imageMatch: "keycloak/keycloak", trackMinor: true, highRisk: true },
  { name: "cert-manager", repo: "cert-manager/cert-manager", argoApp: "cert-manager", imageMatch: "cert-manager-controller" },
  { name: "OpenBao", repo: "openbao/openbao", argoApp: "openbao", imageMatch: "openbao/openbao", highRisk: true },
  { name: "Kyverno", repo: "kyverno/kyverno", argoApp: "kyverno", imageMatch: "kyverno/kyverno" },
  { name: "Falco", repo: "falcosecurity/falco", argoApp: "falco", imageMatch: "falcosecurity/falco" },
  { name: "Velero", repo: "vmware-tanzu/velero", argoApp: "velero", imageMatch: "velero/velero" },
  { name: "Loki", repo: "grafana/loki", argoApp: "loki", imageMatch: "grafana/loki" },
  { name: "Tempo", repo: "grafana/tempo", argoApp: "tempo", imageMatch: "grafana/tempo", trackMinor: true },
  { name: "Gitea", repo: "go-gitea/gitea", imageMatch: "gitea/gitea" },
  { name: "ArgoCD", repo: "argoproj/argo-cd", imageMatch: "argoproj/argocd", highRisk: true },
  { name: "Harbor", repo: "goharbor/harbor", argoApp: "harbor", imageMatch: "goharbor/harbor-core" },
  { name: "Headlamp", repo: "headlamp-k8s/headlamp", argoApp: "headlamp", imageMatch: "headlamp" },
  { name: "Helm", repo: "helm/helm" },
  { name: "metrics-server", repo: "kubernetes-sigs/metrics-server", imageMatch: "metrics-server" },
]
