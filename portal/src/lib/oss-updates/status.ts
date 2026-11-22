import "server-only"
import declaredJson from "@/data/oss-versions.generated.json"
import { getArgoAppsOrThrow } from "../argocd"
import { getOssClusterInventory, type OssClusterInventory, type OssWorkloadSpecItem } from "../k8s-client"
import { getWorkloadVulnerabilitiesWithMeta } from "../trivy"
import type { WorkloadVulnRow } from "@/types/security"
import type {
  OssCveReason,
  OssDrift,
  OssRunningReason,
  OssRunningSibling,
  OssUpdateRow,
  OssUpdatesResponse,
  OssUpstreamInfo,
  OssUpstreamReason,
} from "@/types/oss-updates"
import { OSS_COMPONENTS, type OssComponent, type OssSiblingSpec, type WorkloadKind } from "./catalog"
import { getUpstreamReleases, isUpstreamLookupEnabled, type UpstreamRelease } from "./upstream"
import {
  ageDays,
  compareSemver,
  COOLING_DAYS,
  extractImageRef,
  gapKind,
  isCooling,
  normalizeTag,
  parseSemver,
  repoMatches,
  sameVersion,
} from "./version"

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

/** Pure: Trivy rows -> distinct exact-repo matching images, their normalized tags and Critical/High totals.
 * Uses exact repository boundaries (`repoMatches`), never substring `.includes()`, so sibling images
 * such as `cilium-envoy`, `apisix-ingress-controller`, `falcosidekick`, or `velero-plugin-for-aws`
 * are never mixed into the primary component. */
export function summarizeImages(
  rows: WorkloadVulnRow[],
  imageMatch: string | undefined,
  preferredNamespace?: string,
) {
  if (!imageMatch) return { tags: [] as string[], rawTags: [] as string[], cves: null }
  const repoRows = rows.filter((r) => repoMatches(r.image, imageMatch))
  if (repoRows.length === 0) return { tags: [] as string[], rawTags: [] as string[], cves: null }
  const nsRows = preferredNamespace ? repoRows.filter((r) => r.namespace === preferredNamespace) : []
  const scoped = nsRows.length > 0 ? nsRows : repoRows

  const byImage = new Map<string, WorkloadVulnRow["summary"]>()
  for (const r of scoped) byImage.set(r.image, r.summary)
  const tags = new Set<string>()
  const rawTags = new Set<string>()
  let critical = 0
  let high = 0
  for (const [image, s] of byImage) {
    const { rawTag } = extractImageRef(image)
    if (rawTag) rawTags.add(rawTag)
    const norm = normalizeTag(rawTag)
    if (norm) tags.add(norm)
    critical += s.Critical
    high += s.High
  }
  return { tags: [...tags].sort(), rawTags: [...rawTags].sort(), cves: { critical, high } }
}

/** Pure: declared vs running. Unparseable tags are always "unknown" (never "in-sync"),
 * while chart mismatch still flags "drift". */
export function computeDrift(
  declared: { version: string; chartVersion: string | null },
  chartVersion: string | null,
  imageTags: string[],
): OssDrift {
  const hasUnparseableTag = imageTags.some((t) => normalizeTag(t) === null)
  const chartKnown = Boolean(chartVersion && parseSemver(chartVersion))
  const chartOk = chartKnown ? sameVersion(chartVersion, declared.chartVersion ?? declared.version) : null

  if (hasUnparseableTag) {
    return chartOk === false ? "drift" : "unknown"
  }

  const signals: boolean[] = []
  if (chartOk !== null) signals.push(chartOk)
  for (const t of imageTags) signals.push(sameVersion(t, declared.version))
  if (signals.length === 0) return "unknown"
  return signals.every(Boolean) ? "in-sync" : "drift"
}

const WORKLOAD_PREFIX: Record<WorkloadKind, string> = {
  Deployment: "deploy",
  DaemonSet: "ds",
  StatefulSet: "sts",
}

function workloadListFor(k8s: OssClusterInventory, resource: WorkloadKind) {
  if (resource === "Deployment") return k8s.deployments
  if (resource === "DaemonSet") return k8s.daemonsets
  return k8s.statefulsets
}

function findContainerImage(
  items: OssWorkloadSpecItem[],
  namespace: string,
  name: string,
  container: string,
  init = false,
): string | null {
  const item = items.find((w) => w.namespace === namespace && w.name === name)
  if (!item) return null
  const list = init ? item.initContainers : item.containers
  return list.find((c) => c.name === container)?.image ?? null
}

function resolveSiblings(specs: readonly OssSiblingSpec[] | undefined, k8s: OssClusterInventory): OssRunningSibling[] {
  if (!specs?.length) return []
  const out: OssRunningSibling[] = []
  for (const s of specs) {
    const fetch = workloadListFor(k8s, s.resource)
    if (fetch.status !== "ok") continue
    const image = findContainerImage(fetch.data, s.namespace, s.name, s.container, s.init === true)
    if (!image) continue
    const { rawTag } = extractImageRef(image)
    out.push({
      label: s.label,
      source: `${WORKLOAD_PREFIX[s.resource]}/${s.namespace}/${s.name} (${s.container})`,
      rawTag,
      version: normalizeTag(rawTag),
    })
  }
  return out
}

interface ResolvedRunning {
  version: string | null
  rawTag: string | null
  source: string | null
  primaryImageRepo: string | null
  reason: OssRunningReason | null
  reasonDetail: string | null
  siblings: OssRunningSibling[]
}

function resolveRunning(
  c: OssComponent,
  k8s: OssClusterInventory,
  trivyImgs: ReturnType<typeof summarizeImages>,
): ResolvedRunning {
  const src = c.runningSource
  const siblings = resolveSiblings(c.siblings, k8s)

  if (src.kind === "cli") {
    return { version: null, rawTag: null, source: null, primaryImageRepo: null, reason: "not-applicable", reasonDetail: null, siblings }
  }

  if (src.kind === "server-version") {
    if (k8s.serverVersion.status === "no-permission") {
      return { version: null, rawTag: null, source: "/version", primaryImageRepo: null, reason: "no-permission", reasonDetail: k8s.serverVersion.resource, siblings }
    }
    if (k8s.serverVersion.status !== "ok") {
      return { version: null, rawTag: null, source: "/version", primaryImageRepo: null, reason: "k8s-unavailable", reasonDetail: null, siblings }
    }
    const rawTag = k8s.serverVersion.data
    const version = normalizeTag(rawTag)
    if (!version) {
      return { version: null, rawTag, source: "/version", primaryImageRepo: null, reason: "tag-unparseable", reasonDetail: rawTag, siblings }
    }
    return { version, rawTag, source: "/version", primaryImageRepo: null, reason: null, reasonDetail: null, siblings }
  }

  if (src.kind === "crd-annotation") {
    const label = `crd/${src.crdName}`
    if (k8s.gatewayApiCrdAnnotations.status === "no-permission") {
      return { version: null, rawTag: null, source: label, primaryImageRepo: null, reason: "no-permission", reasonDetail: k8s.gatewayApiCrdAnnotations.resource, siblings }
    }
    if (k8s.gatewayApiCrdAnnotations.status === "not-found") {
      return { version: null, rawTag: null, source: label, primaryImageRepo: null, reason: "workload-not-found", reasonDetail: src.crdName, siblings }
    }
    if (k8s.gatewayApiCrdAnnotations.status !== "ok") {
      return { version: null, rawTag: null, source: label, primaryImageRepo: null, reason: "k8s-unavailable", reasonDetail: null, siblings }
    }
    const rawTag = k8s.gatewayApiCrdAnnotations.data[src.annotation] ?? null
    if (!rawTag) {
      return { version: null, rawTag: null, source: label, primaryImageRepo: null, reason: "workload-not-found", reasonDetail: src.annotation, siblings }
    }
    const version = normalizeTag(rawTag)
    if (!version) {
      return { version: null, rawTag, source: label, primaryImageRepo: null, reason: "tag-unparseable", reasonDetail: rawTag, siblings }
    }
    return { version, rawTag, source: `${label} (${src.annotation})`, primaryImageRepo: null, reason: null, reasonDetail: null, siblings }
  }

  if (src.kind === "static-pod") {
    const label = `pod/${src.namespace}/${src.namePrefix}* (${src.container})`
    const fetch = k8s.kubeSystemPods
    if (fetch.status === "no-permission") {
      return { version: null, rawTag: null, source: label, primaryImageRepo: null, reason: "no-permission", reasonDetail: fetch.resource, siblings }
    }
    if (fetch.status === "ok") {
      const pod = fetch.data.find((p) => p.namespace === src.namespace && p.name.startsWith(src.namePrefix))
      const image = pod?.containers.find((x) => x.name === src.container)?.image ?? null
      if (!image) {
        return { version: null, rawTag: null, source: label, primaryImageRepo: null, reason: "workload-not-found", reasonDetail: `${src.namespace}/${src.namePrefix}*`, siblings }
      }
      const { repo, rawTag } = extractImageRef(image)
      const version = normalizeTag(rawTag)
      if (!version) {
        return { version: null, rawTag, source: `pod/${pod!.namespace}/${pod!.name} (${src.container})`, primaryImageRepo: repo, reason: "tag-unparseable", reasonDetail: rawTag ?? image, siblings }
      }
      return { version, rawTag, source: `pod/${pod!.namespace}/${pod!.name} (${src.container})`, primaryImageRepo: repo, reason: null, reasonDetail: null, siblings }
    }
    // K8s unreachable: allow strict exact-repo Trivy fallback if available.
    if (trivyImgs.tags.length > 0) {
      return { version: trivyImgs.tags[0], rawTag: trivyImgs.rawTags[0] ?? trivyImgs.tags[0], source: "trivy", primaryImageRepo: null, reason: null, reasonDetail: null, siblings }
    }
    return { version: null, rawTag: null, source: label, primaryImageRepo: null, reason: "k8s-unavailable", reasonDetail: null, siblings }
  }

  // src.kind === "workload"
  const prefix = WORKLOAD_PREFIX[src.resource]
  const label = `${prefix}/${src.namespace}/${src.name} (${src.container})`
  const fetch = workloadListFor(k8s, src.resource)
  if (fetch.status === "no-permission") {
    return { version: null, rawTag: null, source: label, primaryImageRepo: null, reason: "no-permission", reasonDetail: fetch.resource, siblings }
  }
  if (fetch.status === "ok") {
    const image = findContainerImage(fetch.data, src.namespace, src.name, src.container)
    if (!image) {
      return { version: null, rawTag: null, source: label, primaryImageRepo: null, reason: "workload-not-found", reasonDetail: `${prefix}/${src.namespace}/${src.name}`, siblings }
    }
    const { repo, rawTag } = extractImageRef(image)
    const version = normalizeTag(rawTag)
    if (!version) {
      return { version: null, rawTag, source: label, primaryImageRepo: repo, reason: "tag-unparseable", reasonDetail: rawTag ?? image, siblings }
    }
    return { version, rawTag, source: label, primaryImageRepo: repo, reason: null, reasonDetail: null, siblings }
  }

  // K8s unreachable: allow strict exact-repo Trivy fallback if available.
  if (trivyImgs.tags.length > 0) {
    return { version: trivyImgs.tags[0], rawTag: trivyImgs.rawTags[0] ?? trivyImgs.tags[0], source: "trivy", primaryImageRepo: null, reason: null, reasonDetail: null, siblings }
  }
  if (trivyImgs.rawTags.length > 0) {
    return { version: null, rawTag: trivyImgs.rawTags[0], source: "trivy", primaryImageRepo: null, reason: "tag-unparseable", reasonDetail: trivyImgs.rawTags[0], siblings }
  }
  return { version: null, rawTag: null, source: label, primaryImageRepo: null, reason: "k8s-unavailable", reasonDetail: null, siblings }
}

function summarizeK8sState(k8s: OssClusterInventory): OssUpdatesResponse["k8s"] {
  const core = [k8s.serverVersion, k8s.deployments, k8s.daemonsets, k8s.statefulsets, k8s.kubeSystemPods]
  const okCount = core.filter((r) => r.status === "ok").length
  if (okCount === core.length) return "ok"
  if (okCount === 0) return "unavailable"
  return "partial"
}

export async function getOssUpdateStatus(now: number = Date.now()): Promise<OssUpdatesResponse> {
  const lookupOn = isUpstreamLookupEnabled()

  let argoState: OssUpdatesResponse["argocd"] = "ok"
  const chartByApp = new Map<string, string>()
  const argoPromise = getArgoAppsOrThrow()
    .then((apps) => {
      for (const app of apps) {
        const rev = app.spec.source?.targetRevision
        if (rev) chartByApp.set(app.metadata.name, rev)
      }
    })
    .catch((err) => {
      argoState = "unavailable"
      console.warn("[oss-updates] Argo CD read failed:", err instanceof Error ? err.message : err)
    })

  const [, trivy, k8s] = await Promise.all([
    argoPromise,
    getWorkloadVulnerabilitiesWithMeta(),
    getOssClusterInventory(),
  ])
  const trivyState: OssUpdatesResponse["trivy"] = trivy.rows.length === 0 ? "unavailable" : trivy.partial ? "partial" : "ok"

  const rows = await Promise.all(
    OSS_COMPONENTS.map(async (c: OssComponent): Promise<OssUpdateRow | null> => {
      const declared = declaredRow(c.name)
      if (!declared) return null // catalog entry whose VERSIONS.md row was renamed: skipped, the sync test flags it

      const chart = c.argoApp ? chartByApp.get(c.argoApp) ?? null : null
      const validChart = chart && parseSemver(chart) ? chart : null
      const preliminaryTrivy = summarizeImages(trivy.rows, c.imageMatch, c.cveNamespace)
      const resolved = resolveRunning(c, k8s, preliminaryTrivy)
      // When the live primary container repo is known, also match Trivy by that exact repo
      // (covers mirror/registry rewrites such as registry.istio.io/release/pilot).
      const cveSummary = resolved.primaryImageRepo
        ? summarizeImages(trivy.rows, resolved.primaryImageRepo, c.cveNamespace)
        : preliminaryTrivy
      const cves = c.runningSource.kind === "cli" || c.runningSource.kind === "crd-annotation" ? null : cveSummary.cves
      const cveReason: OssCveReason | null = cves !== null
        ? null
        : c.runningSource.kind === "cli" || c.runningSource.kind === "crd-annotation"
          ? "not-applicable"
          : trivyState === "unavailable"
            ? "trivy-unavailable"
            : "no-scan"

      let drift: OssDrift
      let driftReason: OssRunningReason | null = null
      if (c.runningSource.kind === "cli") {
        drift = "not-applicable"
        driftReason = "not-applicable"
      } else if (resolved.reason === "tag-unparseable") {
        drift = computeDrift(declared, validChart, [resolved.rawTag ?? "__unparseable__"])
        driftReason = drift === "unknown" ? "tag-unparseable" : null
      } else {
        const runningTags = resolved.version ? [resolved.version] : []
        drift = computeDrift(declared, validChart, runningTags)
        if (drift === "unknown") driftReason = resolved.reason ?? "workload-not-found"
      }

      const imageTags = resolved.version
        ? [resolved.version]
        : resolved.rawTag
          ? [resolved.rawTag]
          : []

      const releases = lookupOn ? await getUpstreamReleases(c.repo) : null
      const upstream = releases ? summarizeUpstream(releases, declared.version, c.trackMinor === true, now) : null
      const upstreamState: OssUpdateRow["upstreamState"] = !lookupOn ? "disabled" : releases === null ? "unavailable" : "ok"
      const upstreamReason: OssUpstreamReason | null = upstream !== null
        ? null
        : !lookupOn
          ? "upstream-off"
          : releases === null
            ? "upstream-failed"
            : "no-matching-release"

      return {
        component: c.name,
        section: declared.section,
        declared: { version: declared.version, chartVersion: declared.chartVersion },
        running: {
          version: resolved.version,
          rawTag: resolved.rawTag,
          source: resolved.source,
          chartVersion: validChart,
          imageTags,
          siblings: resolved.siblings,
          reason: resolved.reason,
          reasonDetail: resolved.reasonDetail,
        },
        drift,
        driftReason,
        upstreamState,
        upstreamReason,
        upstream,
        cves,
        cveReason,
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
    k8s: summarizeK8sState(k8s),
    requestMode: "copy",
    issueRepo: getIssueRepo(),
    rows: rows.filter((r): r is OssUpdateRow => r !== null),
  }
}
