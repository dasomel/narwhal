export type Severity = "Critical" | "High" | "Medium" | "Low" | "Unknown"

export type SeverityCounts = Record<Severity, number>

export interface Vulnerability {
  id: string
  severity: Severity
  score?: number
  title: string
  primaryLink?: string
  fixedVersion?: string
  installedVersion?: string
  target?: string
  publishedDate?: string
}

export interface ImageVulnReport {
  image: string
  digest?: string
  namespace: string
  workload: {
    kind: string
    name: string
  }
  summary: SeverityCounts
  vulnerabilities: Vulnerability[]
}

export interface WorkloadVulnRow {
  namespace: string
  kind: "Deployment" | "DaemonSet" | "StatefulSet" | "Job" | "Pod" | string
  name: string
  image: string
  summary: SeverityCounts
}

export interface VulnDbFreshness {
  lastSyncTime: string
  dbAgeDays: number
  status: "fresh" | "stale" | "critical"
  dbRegistry?: string
}

/** "pinned" = present, but the source has no age SLO (versioned upstream line). */
export type TrivyDbArtifactStatus = "fresh" | "pinned" | "stale" | "missing" | "unknown"

export interface TrivyDbArtifact {
  /** Source name in scripts/airgap/security-db.txt, e.g. "trivy-db". */
  name: string
  /** Scanner component that reads it: "vulnScan" | "javaScan" | "configAudit". */
  consumer: "vulnScan" | "javaScan" | "configAudit"
  /** Age limit in days; null when the source is versioned rather than rolling. */
  sloDays: number | null
  /** Harbor repository under the trivy-db project, e.g. "aquasecurity/trivy-db". */
  repository: string
  /** Tag the scanner is configured to pull (trivy-db:2, trivy-java-db:1, trivy-checks:1). */
  tag: string
  status: TrivyDbArtifactStatus
  /** Harbor push_time of the tagged artifact. */
  pushedAt?: string
  /** OCI org.opencontainers.image.created annotation (upstream build time) when present. */
  createdAt?: string
  /** Whole days since createdAt, else pushedAt. */
  ageDays?: number
  digest?: string
}

export type TrivyScannerState = "ok" | "failing" | "no-reports"

export interface TrivyDbStatus {
  observedAt: string
  sloDays: number
  /** false when Harbor could not be read: every artifact is then "unknown", not "missing". */
  harborReachable: boolean
  overall: TrivyDbArtifactStatus
  dbRegistry: string
  artifacts: TrivyDbArtifact[]
  scanner: {
    state: TrivyScannerState
    reportCount: number
    newestReportAt: string | null
    /** Scan-job failure events still retained by Kubernetes (events expire, so this is a floor). */
    recentScanFailures: number
    lastFailureAt: string | null
  }
}

export interface SecuritySummary {
  totals: SeverityCounts
  scannedImages: number
  scannedWorkloads: number
  lastUpdated: string
  vulnDb?: VulnDbFreshness
  /** true when the VulnerabilityReport walk ended early; totals are an undercount. */
  partial?: boolean
  partialReason?: "max-pages" | "expired" | "error"
}

export type FalcoEventPriority =
  | "Emergency"
  | "Alert"
  | "Critical"
  | "Error"
  | "Warning"
  | "Notice"
  | "Informational"
  | "Debug"

export interface FalcoEvent {
  id: string
  time: string
  priority: FalcoEventPriority
  rule: string
  output: string
  source: string
  tags?: string[]
  pod?: string
  namespace?: string
  container?: string
  image?: string
}
