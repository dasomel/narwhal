import type { GapKind } from "@/lib/oss-updates/version"

export type OssDrift = "in-sync" | "drift" | "unknown"

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
    /** Argo CD Application source.targetRevision (chart version), null when not an Argo chart app. */
    chartVersion: string | null
    /** Distinct image tags of running workloads Trivy has scanned for this component. */
    imageTags: string[]
  }
  drift: OssDrift
  /** "ok" = looked up; "disabled" = flag off (air-gap default); "unavailable" = lookup failed. */
  upstreamState: "ok" | "disabled" | "unavailable"
  upstream: OssUpstreamInfo | null
  /** Trivy counts for the running images of this component; null = no scan data. */
  cves: { critical: number; high: number } | null
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
