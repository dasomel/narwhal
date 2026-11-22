import type { GapKind } from "@/lib/oss-updates/version"

export type OssDrift = "in-sync" | "drift" | "unknown" | "not-applicable"

export type OssRunningReason =
  | "not-applicable"
  | "no-permission"
  | "workload-not-found"
  | "container-not-found"
  | "no-running-pod"
  | "rollout-mixed"
  | "digest-only"
  | "tag-unparseable"
  | "k8s-unavailable"

export type OssUpstreamReason =
  | "upstream-off"
  | "upstream-failed"
  | "no-matching-release"

export type OssCveReason =
  | "not-applicable"
  | "trivy-unavailable"
  | "no-running-image"
  | "no-scan-for-running"

export interface OssRunningSibling {
  /** Companion label (e.g. "cilium-envoy", "apisix-ingress-controller", "falcosidekick", "velero-plugin-for-aws"). */
  label: string
  /** Exact workload/container source (e.g. "ds/kube-system/cilium-envoy (cilium-envoy)"). */
  source: string
  /** Raw image tag (digest-stripped), null when untagged or unreadable. */
  rawTag: string | null
  /** Normalized semver when parseable under the sibling's allowed suffixes, null otherwise. */
  version: string | null
  /** Running tag(s) or shortened digest(s) observed across Running pods. */
  imageTags: string[]
  /** Explicit reason when unreadable, digest-only, rollout-mixed, or tag-unparseable. */
  reason: OssRunningReason | null
  /** Extra context for `reason` (e.g. shortened digest, mixed tags, missing resource/container). */
  reasonDetail?: string | null
  /** Trivy counts strictly for this sibling's running image(s); null when no report or unreadable. */
  cves: { critical: number; high: number } | null
  /** Explicit reason when sibling `cves` is null. */
  cveReason: OssCveReason | null
}

export interface OssUpstreamInfo {
  /** Newest stable release (on the held minor line when the catalog says trackMinor). */
  latest: string
  latestPublishedAt: string
  latestUrl: string
  latestAgeDays: number | null
  /** latest is younger than COOLING_DAYS: do not adopt yet. */
  cooling: boolean
  /** Newest release that has already cooled and is newer than declared; null when none. */
  target: string | null
  targetPublishedAt: string | null
  gap: GapKind
  /** Days between the declared release and latest, when the declared tag is in the fetched list. */
  daysBehind: number | null
}

export interface OssUpdateRow {
  component: string
  section: string
  declared: { version: string; chartVersion: string | null }
  running: {
    /** Normalized running application version from the authoritative workload/endpoint/CRD; null when unknown, mixed, or CLI. */
    version: string | null
    /** Raw tag of the authoritative running container/endpoint before normalization (e.g. "1.26.2-rootless", "latest"). */
    rawTag: string | null
    /** Exact workload/container, endpoint, or CRD annotation defining this row's running version. */
    source: string | null
    /** Argo CD Application source.targetRevision (chart version), null when not an Argo chart app. */
    chartVersion: string | null
    /** Authoritative running image tag(s) or shortened digest(s) for this component (never includes sibling/init/sidecar images). */
    imageTags: string[]
    /** Non-authoritative companion containers/workloads in the same release, shown separately and never mixed into drift. */
    siblings: OssRunningSibling[]
    /** Explicit reason when `version` is null (or "not-applicable" for CLI tools). */
    reason: OssRunningReason | null
    /** Extra context for `reason` (e.g. missing RBAC resource, missing workload, unparseable tag, mixed tags, digest). */
    reasonDetail?: string | null
    /** Optional non-authoritative hint from Trivy when K8s workload cannot be read; never drives drift. */
    trivyHint?: string | null
  }
  drift: OssDrift
  /** Explicit reason when `drift` is "unknown", "not-applicable", or rollout-mixed drift. */
  driftReason: OssRunningReason | null
  /** "ok" = looked up; "disabled" = flag off (air-gap default); "unavailable" = lookup failed. */
  upstreamState: "ok" | "disabled" | "unavailable"
  /** Explicit reason when `upstream` is null. */
  upstreamReason: OssUpstreamReason | null
  upstream: OssUpstreamInfo | null
  /** Trivy counts strictly for the running primary image of this component; null = no scan data or not applicable. */
  cves: { critical: number; high: number } | null
  /** Explicit reason when `cves` is null. */
  cveReason: OssCveReason | null
  /** Declared minor line is deliberately held (see VERSIONS.md); "latest" is branch-latest. */
  heldMinor: boolean
  highRisk: boolean
}

export interface OssUpdatesResponse {
  generatedAt: string
  coolingDays: number
  upstreamLookup: "enabled" | "disabled"
  argocd: "ok" | "unavailable"
  trivy: "ok" | "partial" | "unavailable"
  k8s?: "ok" | "partial" | "unavailable"
  /** "copy" = the portal only generates issue text (no tracker token configured). */
  requestMode: "copy"
  issueRepo: string
  rows: OssUpdateRow[]
}

export interface OssUpgradeRequestResponse {
  component: string
  from: string
  target: string
  /** true when this component+target was already requested (idempotent replay). */
  duplicate: boolean
  requestedAt: string
  requestedBy: string
  issue: { title: string; body: string; newIssueUrl: string }
}
