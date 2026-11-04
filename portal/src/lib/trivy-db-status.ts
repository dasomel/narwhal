import "server-only"
import { getEvents } from "./k8s-client"
import { cacheGet, cacheSet } from "./valkey"
import { cacheKeys, cacheTtl } from "./cache-keys"
import { fetchWithPolicy, readJsonWithPolicy } from "./http-client"
import { computeVulnDbFreshness, getSecuritySummary } from "./trivy"
import type {
  TrivyDbArtifact,
  TrivyDbArtifactStatus,
  TrivyDbStatus,
  TrivyScannerState,
} from "@/types/security"

// Trivy DB status (read-only).
//
// D-TRIVYDB-ANON: Harbor project `trivy-db` is public (08-5-registry.sh creates it
// public so scan Jobs pull without credentials), and Harbor's v2 API serves a public
// project's repositories/artifacts to anonymous callers. So this reads Harbor with NO
// credential: no new Secret, no widened Harbor permission. If the project is ever made
// private the calls return 401/403 and the card shows "unknown" (Harbor unreadable)
// instead of guessing "missing". Escape hatch: add a read-only robot account then.
//
// D-TRIVYDB-SLO: 7 days, same as scripts/airgap/lib/check-security-db-freshness.py.
// Age is measured from the OCI `created` annotation (upstream build time) when present,
// else Harbor push_time. That check measures intake time; this one measures the data the
// scanner would actually load, so it can read a few hours older than the script.
//
// The portal never runs the update: fetching needs internet and promotion needs a named
// approver (see scripts/airgap/README.md). The UI only prints the commands.

export const TRIVY_DB_SLO_DAYS = 7

// Must match trivy-operator.yaml (dbRepository / javaDbRepository / policiesBundle) and
// scripts/airgap/security-db.txt.
const TRIVY_DB_ARTIFACTS = [
  { repository: "aquasecurity/trivy-db", tag: "2" },
  { repository: "aquasecurity/trivy-java-db", tag: "1" },
  { repository: "aquasecurity/trivy-checks", tag: "1" },
] as const

const HARBOR_PROJECT = "trivy-db"
const SCANNER_NAMESPACE = "security-system"
const SCAN_JOB_PREFIX = "scan-vulnerabilityreport"
const DAY_MS = 86_400_000
const ORDER: Record<TrivyDbArtifactStatus, number> = { fresh: 0, stale: 1, unknown: 2, missing: 3 }

interface HarborArtifact {
  digest?: string
  push_time?: string
  tags?: Array<{ name: string }> | null
  annotations?: Record<string, string> | null
}

// Read at call time (see config.ts getDependencyUrl contract). Not a DependencyUrlEnvVar:
// it is an anonymous read of a public project, so an unset var must not fail production.
function getHarborUrl(): string {
  return (process.env.HARBOR_URL || "http://harbor-core.devtools.svc.cluster.local").replace(/\/+$/, "")
}

function validTime(iso: string | undefined): string | undefined {
  return iso && !Number.isNaN(Date.parse(iso)) ? iso : undefined
}

export function classifyArtifact(
  base: { repository: string; tag: string },
  found: HarborArtifact | null,
  now: number,
): TrivyDbArtifact {
  if (!found) return { ...base, status: "missing" }
  const pushedAt = validTime(found.push_time)
  const createdAt = validTime(found.annotations?.["org.opencontainers.image.created"])
  const ref = createdAt ?? pushedAt
  const ageDays = ref === undefined ? undefined : Math.max(0, Math.floor((now - Date.parse(ref)) / DAY_MS))
  const status: TrivyDbArtifactStatus = ageDays === undefined ? "unknown" : ageDays > TRIVY_DB_SLO_DAYS ? "stale" : "fresh"
  return { ...base, status, pushedAt, createdAt, ageDays, digest: found.digest?.slice(0, 19) }
}

// null = repository or tag absent. Throws on anything that is not a clean 200/404.
async function readHarborArtifact(repository: string, tag: string): Promise<HarborArtifact | null> {
  // Harbor requires a repository name containing "/" to be double URL-encoded.
  const repo = encodeURIComponent(encodeURIComponent(repository))
  const url = `${getHarborUrl()}/api/v2.0/projects/${HARBOR_PROJECT}/repositories/${repo}/artifacts?with_tag=true&page_size=50`
  const res = await fetchWithPolicy(url, { headers: { Accept: "application/json" } }, { timeoutMs: 5_000, retry: false })
  if (res.status === 404) return null
  if (!res.ok) throw new Error(`Harbor ${res.status}`)
  const items = await readJsonWithPolicy<HarborArtifact[]>(res)
  const tagged = items.filter((a) => a.tags?.some((x) => x.name === tag))
  tagged.sort((a, b) => Date.parse(b.push_time ?? "") - Date.parse(a.push_time ?? ""))
  return tagged[0] ?? null
}

async function readScanner(): Promise<TrivyDbStatus["scanner"]> {
  const [summary, events] = await Promise.all([getSecuritySummary(), getEvents(SCANNER_NAMESPACE)])
  const failures = events.filter(
    (e) => e.reason === "BackoffLimitExceeded" && e.involvedObject.name.startsWith(SCAN_JOB_PREFIX),
  )
  const times = failures.map((e) => e.lastTimestamp ?? e.firstTimestamp).filter((x): x is string => Boolean(x)).sort()
  const lastFailureAt = times.length ? times[times.length - 1] : null
  const reportCount = summary.scannedWorkloads
  // getSecuritySummary() degrades to an empty summary stamped "now"; that is not a report time.
  const newestReportAt = reportCount > 0 ? summary.lastUpdated : null
  let state: TrivyScannerState = reportCount > 0 ? "ok" : "no-reports"
  if (failures.length > 0 && (!newestReportAt || (lastFailureAt !== null && lastFailureAt > newestReportAt))) state = "failing"
  return { state, reportCount, newestReportAt, recentScanFailures: failures.length, lastFailureAt }
}

export async function getTrivyDbStatus(): Promise<TrivyDbStatus> {
  const cacheKey = cacheKeys.trivyDbStatus()
  const cached = await cacheGet<TrivyDbStatus>(cacheKey)
  if (cached) return cached

  const now = Date.now()
  let harborReachable = true
  const artifacts = await Promise.all(
    TRIVY_DB_ARTIFACTS.map(async (base): Promise<TrivyDbArtifact> => {
      try {
        return classifyArtifact(base, await readHarborArtifact(base.repository, base.tag), now)
      } catch (err) {
        harborReachable = false
        console.warn(`[trivy-db] Harbor read failed for ${base.repository}:`, err instanceof Error ? err.message : err)
        return { ...base, status: "unknown" }
      }
    }),
  )
  const scanner = await readScanner().catch((err): TrivyDbStatus["scanner"] => {
    console.warn("[trivy-db] scanner health read failed:", err instanceof Error ? err.message : err)
    return { state: "no-reports", reportCount: 0, newestReportAt: null, recentScanFailures: 0, lastFailureAt: null }
  })

  const result: TrivyDbStatus = {
    observedAt: new Date(now).toISOString(),
    sloDays: TRIVY_DB_SLO_DAYS,
    harborReachable,
    overall: artifacts.reduce<TrivyDbArtifactStatus>((w, a) => (ORDER[a.status] > ORDER[w] ? a.status : w), "fresh"),
    dbRegistry: computeVulnDbFreshness().dbRegistry ?? "",
    artifacts,
    scanner,
  }
  // A Harbor outage is transient: do not pin "unknown" for the TTL.
  if (harborReachable) await cacheSet(cacheKey, result, cacheTtl("trivyDbStatus"))
  return result
}
