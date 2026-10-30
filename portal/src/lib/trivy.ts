import "server-only"
import { listBounded } from "./k8s-client"
import { cacheGet, cacheSet } from "./valkey"
import { cacheKeys, cacheTtl } from "./cache-keys"
import type { SecuritySummary, WorkloadVulnRow, ImageVulnReport, Vulnerability, Severity, VulnDbFreshness } from "@/types/security"

// --- K8s bounded pagination helper ---
//
// On Kakao (109 VulnerabilityReports), an unbounded list is 10.76 MB and exceeds
// http-client's 8 MiB DEFAULT_MAX_RESPONSE_BYTES; the largest single report is
// 343,228 bytes (20 largest together ~4.2 MB). Even 20 x the largest report
// (~6.9 MB, 82% of the cap) fits one page, and 50 pages cover up to 1,000 reports.
const TRIVY_VULNERABILITY_REPORTS_PATH = "/apis/aquasecurity.github.io/v1alpha1/vulnerabilityreports"
const TRIVY_REPORT_PAGE_LIMIT = 20
const TRIVY_REPORT_MAX_PAGES = 50

type PartialReason = NonNullable<SecuritySummary["partialReason"]>

interface ReportList {
  items: TrivyVulnerabilityReport[]
  partial: boolean
  partialReason?: PartialReason
}

// D-TRIVY-RETRY (#505): a page that gets 429/5xx is retried by the shared
// http-client (Retry-After honoured, full-jitter backoff). 3 attempts, delays
// capped at 2s => at most ~4s of sleeping per page, so a page cannot hang the
// request. If it still fails after pages were collected, the walk ends as a
// flagged partial (partialOnError) instead of dropping everything.
// D-TRIVY-410 (#505): an expired continue token is handled in listBounded: the
// list restarts once from the beginning, and a second 410 returns the items so
// far as partial (reason "expired"). No unbounded restart loop.
// Partial results are never cached (callers below) so the next request re-walks.
const TRIVY_PAGE_RETRY = { maxAttempts: 3, baseDelayMs: 250, maxDelayMs: 2_000 } as const

async function listVulnerabilityReports(): Promise<ReportList> {
  const { items, truncated, reason, pages } = await listBounded<TrivyVulnerabilityReport>(
    TRIVY_VULNERABILITY_REPORTS_PATH,
    {
      limit: TRIVY_REPORT_PAGE_LIMIT,
      maxPages: TRIVY_REPORT_MAX_PAGES,
      pageRetry: TRIVY_PAGE_RETRY,
      partialOnError: true,
    },
  )
  if (truncated) {
    console.warn(
      `[trivy] VulnerabilityReports list truncated (${reason}) after ${pages} pages — returning ${items.length} fetched reports`,
    )
  }
  return { items, partial: truncated, partialReason: truncated ? reason : undefined }
}

// --- Trivy CRD types ---

interface TrivyVulnerabilityReport {
  metadata: {
    name: string
    namespace: string
    creationTimestamp?: string
    labels?: Record<string, string>
    ownerReferences?: Array<{ kind: string; name: string }>
  }
  report: {
    artifact: { repository: string; tag?: string; digest?: string }
    registry: { server: string }
    summary: {
      criticalCount: number
      highCount: number
      mediumCount: number
      lowCount: number
      unknownCount: number
      noneCount?: number
    }
    vulnerabilities: Array<{
      vulnerabilityID: string
      severity: string
      score?: number
      title: string
      description?: string
      primaryLink?: string
      fixedVersion?: string
      installedVersion?: string
      target?: string
      publishedDate?: string
      lastModifiedDate?: string
    }>
    scanner?: { name: string; vendor: string; version: string }
  }
}

// --- Mapping helpers ---

function mapSeverity(raw: string): Severity {
  const s = raw.charAt(0).toUpperCase() + raw.slice(1).toLowerCase()
  if (s === "Critical" || s === "High" || s === "Medium" || s === "Low") return s
  return "Unknown"
}

function buildImageString(report: TrivyVulnerabilityReport): string {
  const { registry, artifact } = report.report
  const server = registry.server.replace(/\/$/, "")
  const tag = artifact.tag ?? "latest"
  return `${server}/${artifact.repository}:${tag}`
}

function buildSummary(s: TrivyVulnerabilityReport["report"]["summary"]) {
  return {
    Critical: s.criticalCount,
    High: s.highCount,
    Medium: s.mediumCount,
    Low: s.lowCount,
    Unknown: s.unknownCount,
  }
}

function reportToWorkloadRow(item: TrivyVulnerabilityReport): WorkloadVulnRow {
  const labels = item.metadata.labels ?? {}
  const ownerRefs = item.metadata.ownerReferences ?? []
  const kind =
    labels["trivy-operator.resource.kind"] ??
    (ownerRefs.length > 0 ? ownerRefs[0].kind : "Pod")
  const name =
    labels["trivy-operator.resource.name"] ??
    (ownerRefs.length > 0 ? ownerRefs[0].name : item.metadata.name)
  return {
    namespace: item.metadata.namespace,
    kind,
    name,
    image: buildImageString(item),
    summary: buildSummary(item.report.summary),
  }
}

function reportToVulnerabilities(item: TrivyVulnerabilityReport): Vulnerability[] {
  return (item.report.vulnerabilities ?? []).map((v) => ({
    id: v.vulnerabilityID,
    severity: mapSeverity(v.severity),
    score: v.score,
    title: v.title,
    primaryLink: v.primaryLink,
    fixedVersion: v.fixedVersion,
    installedVersion: v.installedVersion,
    target: v.target,
    publishedDate: v.publishedDate,
  }))
}

function scoreRow(row: WorkloadVulnRow): number {
  return row.summary.Critical * 1000 + row.summary.High * 100 + row.summary.Medium * 10 + row.summary.Low
}

export function computeVulnDbFreshness(lastSyncIso?: string): VulnDbFreshness {
  const syncTime = lastSyncIso || process.env.VULN_DB_LAST_SYNC || new Date().toISOString()
  const syncDate = new Date(syncTime)
  const diffMs = Math.max(0, Date.now() - (isNaN(syncDate.getTime()) ? Date.now() : syncDate.getTime()))
  const dbAgeDays = Math.floor(diffMs / (1000 * 60 * 60 * 24))

  let status: "fresh" | "stale" | "critical" = "fresh"
  if (dbAgeDays >= 30) {
    status = "critical"
  } else if (dbAgeDays >= 14) {
    status = "stale"
  }

  return {
    lastSyncTime: syncTime,
    dbAgeDays,
    status,
    dbRegistry:
      process.env.TRIVY_DB_REGISTRY ||
      `harbor.${process.env.CLUSTER_BASE_DOMAIN ?? process.env.NEXT_PUBLIC_CLUSTER_BASE_DOMAIN ?? "local.narwhal.internal"}/library/trivy-db`,
  }
}

const emptySummary: SecuritySummary = {
  totals: { Critical: 0, High: 0, Medium: 0, Low: 0, Unknown: 0 },
  scannedImages: 0,
  scannedWorkloads: 0,
  lastUpdated: new Date().toISOString(),
  vulnDb: computeVulnDbFreshness(),
}

// --- Exported functions (signatures unchanged) ---

export async function getSecuritySummary(): Promise<SecuritySummary> {
  const cacheKey = cacheKeys.trivySummary()
  const cached = await cacheGet<SecuritySummary>(cacheKey)
  if (cached) return cached

  try {
    const { items, partial, partialReason } = await listVulnerabilityReports()

    const totals = { Critical: 0, High: 0, Medium: 0, Low: 0, Unknown: 0 }
    const imageSet = new Set<string>()
    const workloadSet = new Set<string>()
    let latestTs = ""

    for (const item of items) {
      const s = item.report.summary
      totals.Critical += s.criticalCount
      totals.High += s.highCount
      totals.Medium += s.mediumCount
      totals.Low += s.lowCount
      totals.Unknown += s.unknownCount

      imageSet.add(buildImageString(item))

      const row = reportToWorkloadRow(item)
      workloadSet.add(`${row.kind}/${row.namespace}/${row.name}`)

      const ts = item.metadata.creationTimestamp ?? ""
      if (ts > latestTs) latestTs = ts
    }

    const result: SecuritySummary = {
      totals,
      scannedImages: imageSet.size,
      scannedWorkloads: workloadSet.size,
      lastUpdated: latestTs || new Date().toISOString(),
      vulnDb: computeVulnDbFreshness(latestTs),
      ...(partial ? { partial, partialReason } : {}),
    }

    if (!partial) await cacheSet(cacheKey, result, cacheTtl("trivySummary"))
    return result
  } catch (err) {
    console.warn("[trivy] getSecuritySummary failed:", err instanceof Error ? err.message : err)
    return emptySummary
  }
}

export interface WorkloadVulnerabilityList {
  rows: WorkloadVulnRow[]
  partial: boolean
  partialReason?: PartialReason
}

export async function getWorkloadVulnerabilitiesWithMeta(): Promise<WorkloadVulnerabilityList> {
  const cacheKey = cacheKeys.trivyWorkloads()
  const cached = await cacheGet<WorkloadVulnRow[]>(cacheKey)
  if (cached) return { rows: cached, partial: false }

  try {
    const { items, partial, partialReason } = await listVulnerabilityReports()
    const rows = items.map(reportToWorkloadRow)

    if (!partial) await cacheSet(cacheKey, rows, cacheTtl("trivyWorkloads"))
    return { rows, partial, partialReason }
  } catch (err) {
    console.warn("[trivy] getWorkloadVulnerabilities failed:", err instanceof Error ? err.message : err)
    return { rows: [], partial: false }
  }
}

export async function getWorkloadVulnerabilities(): Promise<WorkloadVulnRow[]> {
  return (await getWorkloadVulnerabilitiesWithMeta()).rows
}

export async function getImageVulnReport(image: string): Promise<ImageVulnReport | null> {
  const cacheKey = cacheKeys.trivyImage(image)
  const cached = await cacheGet<ImageVulnReport>(cacheKey)
  if (cached) return cached

  try {
    const { items, partial } = await listVulnerabilityReports()

    const item = items.find((i) => buildImageString(i) === image)
    if (!item) return null

    const row = reportToWorkloadRow(item)
    const report: ImageVulnReport = {
      image,
      digest: item.report.artifact.digest,
      namespace: row.namespace,
      workload: { kind: row.kind, name: row.name },
      summary: row.summary,
      vulnerabilities: reportToVulnerabilities(item),
    }

    if (!partial) await cacheSet(cacheKey, report, cacheTtl("trivyImage"))
    return report
  } catch (err) {
    console.warn("[trivy] getImageVulnReport failed:", err instanceof Error ? err.message : err)
    return null
  }
}

export async function getTopVulnerableImages(limit = 5): Promise<WorkloadVulnRow[]> {
  const cacheKey = cacheKeys.trivyTopVulnerable(limit)
  const cached = await cacheGet<WorkloadVulnRow[]>(cacheKey)
  if (cached) return cached

  try {
    const { rows, partial } = await getWorkloadVulnerabilitiesWithMeta()
    const sorted = [...rows].sort((a, b) => scoreRow(b) - scoreRow(a))
    const result = sorted.slice(0, limit)

    if (!partial) await cacheSet(cacheKey, result, cacheTtl("trivyTopVulnerable"))
    return result
  } catch (err) {
    console.warn("[trivy] getTopVulnerableImages failed:", err instanceof Error ? err.message : err)
    return []
  }
}

// Re-export Severity for API route filtering
export type { Severity }
