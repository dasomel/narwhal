import "server-only"
import declaredJson from "@/data/oss-versions.generated.json"
import { getArgoAppsOrThrow } from "../argocd"
import { getWorkloadVulnerabilitiesWithMeta } from "../trivy"
import type { WorkloadVulnRow } from "@/types/security"
import type { OssDrift, OssUpdateRow, OssUpdatesResponse, OssUpstreamInfo } from "@/types/oss-updates"
import { OSS_COMPONENTS, type OssComponent } from "./catalog"
import { getUpstreamReleases, isUpstreamLookupEnabled, type UpstreamRelease } from "./upstream"
import { ageDays, compareSemver, COOLING_DAYS, gapKind, isCooling, parseSemver, sameVersion } from "./version"

interface DeclaredRow {
  component: string
  section: string
  version: string
  chartVersion: string | null
}

export function getIssueRepo(): string {
  const v = process.env.OSS_UPDATES_ISSUE_REPO
  return v && /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(v) ? v : "dasomel/narwhal"
}

export function declaredRow(component: string): DeclaredRow | undefined {
  return (declaredJson.rows as DeclaredRow[]).find((r) => r.component === component)
}

/** Pure: picks latest / target / gap from a release list. `now` injected for tests. */
export function summarizeUpstream(
  releases: UpstreamRelease[],
  declared: string,
  heldMinor: boolean,
  now: number,
): OssUpstreamInfo | null {
  const d = parseSemver(declared)
  const pool = heldMinor && d ? releases.filter((r) => {
    const p = parseSemver(r.tag)
    return p && p.major === d.major && p.minor === d.minor
  }) : releases
  const latest = pool[0] // normalizeReleases sorts newest-first
  if (!latest) return null

  const newer = pool.filter((r) => d && compareSemver(parseSemver(r.tag)!, d) > 0)
  const target = newer.find((r) => !isCooling(r.publishedAt, now)) ?? null
  const declaredRelease = releases.find((r) => sameVersion(r.tag, declared))
  const latestMs = Date.parse(latest.publishedAt)
  const declaredMs = declaredRelease ? Date.parse(declaredRelease.publishedAt) : NaN

  return {
    latest: latest.tag,
    latestPublishedAt: latest.publishedAt,
    latestUrl: latest.url,
    latestAgeDays: ageDays(latest.publishedAt, now),
    cooling: isCooling(latest.publishedAt, now),
    target: target?.tag ?? null,
    targetPublishedAt: target?.publishedAt ?? null,
    gap: gapKind(declared, latest.tag),
    daysBehind: Number.isNaN(declaredMs) || Number.isNaN(latestMs) ? null : Math.max(0, Math.round((latestMs - declaredMs) / 86_400_000)),
  }
}

const IMAGE_TAG_RE = /:([^:/@]+)(?:@sha256:[0-9a-f]+)?$/

/** Pure: Trivy rows -> distinct matching images, their tags and Critical/High totals. */
export function summarizeImages(rows: WorkloadVulnRow[], imageMatch: string | undefined) {
  if (!imageMatch) return { tags: [] as string[], cves: null }
  const byImage = new Map<string, WorkloadVulnRow["summary"]>()
  for (const r of rows) if (r.image.includes(imageMatch)) byImage.set(r.image, r.summary)
  if (byImage.size === 0) return { tags: [] as string[], cves: null }
  const tags = new Set<string>()
  let critical = 0
  let high = 0
  for (const [image, s] of byImage) {
    const m = IMAGE_TAG_RE.exec(image)
    if (m && parseSemver(m[1])) tags.add(m[1])
    critical += s.Critical
    high += s.High
  }
  return { tags: [...tags].sort(), cves: { critical, high } }
}

/** Pure: declared vs running. Any running signal that differs from declared = drift. */
export function computeDrift(
  declared: { version: string; chartVersion: string | null },
  chartVersion: string | null,
  imageTags: string[],
): OssDrift {
  const signals: boolean[] = []
  if (chartVersion && parseSemver(chartVersion)) signals.push(sameVersion(chartVersion, declared.chartVersion ?? declared.version))
  for (const t of imageTags) signals.push(sameVersion(t, declared.version))
  if (signals.length === 0) return "unknown"
  return signals.every(Boolean) ? "in-sync" : "drift"
}

export async function getOssUpdateStatus(now: number = Date.now()): Promise<OssUpdatesResponse> {
  const lookupOn = isUpstreamLookupEnabled()

  let argoState: OssUpdatesResponse["argocd"] = "ok"
  const chartByApp = new Map<string, string>()
  try {
    for (const app of await getArgoAppsOrThrow()) {
      const rev = app.spec.source?.targetRevision
      if (rev) chartByApp.set(app.metadata.name, rev)
    }
  } catch (err) {
    argoState = "unavailable"
    console.warn("[oss-updates] Argo CD read failed:", err instanceof Error ? err.message : err)
  }

  const trivy = await getWorkloadVulnerabilitiesWithMeta()
  const trivyState: OssUpdatesResponse["trivy"] = trivy.rows.length === 0 ? "unavailable" : trivy.partial ? "partial" : "ok"

  const rows = await Promise.all(
    OSS_COMPONENTS.map(async (c: OssComponent): Promise<OssUpdateRow | null> => {
      const declared = declaredRow(c.name)
      if (!declared) return null // catalog entry whose VERSIONS.md row was renamed: skipped, the sync test flags it
      const chart = c.argoApp ? chartByApp.get(c.argoApp) ?? null : null
      const imgs = summarizeImages(trivy.rows, c.imageMatch)
      const releases = lookupOn ? await getUpstreamReleases(c.repo) : null
      return {
        component: c.name,
        section: declared.section,
        declared: { version: declared.version, chartVersion: declared.chartVersion },
        running: { chartVersion: chart && parseSemver(chart) ? chart : null, imageTags: imgs.tags },
        drift: computeDrift(declared, chart, imgs.tags),
        upstreamState: !lookupOn ? "disabled" : releases === null ? "unavailable" : "ok",
        upstream: releases ? summarizeUpstream(releases, declared.version, c.trackMinor === true, now) : null,
        cves: imgs.cves,
        heldMinor: c.trackMinor === true,
        highRisk: c.highRisk === true,
      }
    }),
  )

  return {
    generatedAt: new Date(now).toISOString(),
    coolingDays: COOLING_DAYS,
    upstreamLookup: lookupOn ? "enabled" : "disabled",
    argocd: argoState,
    trivy: trivyState,
    requestMode: "copy",
    issueRepo: getIssueRepo(),
    rows: rows.filter((r): r is OssUpdateRow => r !== null),
  }
}
