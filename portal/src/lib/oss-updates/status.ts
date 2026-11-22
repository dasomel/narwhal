import "server-only"
import declaredJson from "@/data/oss-versions.generated.json"
import { getArgoAppsOrThrow } from "../argocd"
import {
  getOssClusterInventory,
  type OssClusterInventory,
  type OssOwnerRef,
  type OssPodContainerSpec,
  type OssPodSpecItem,
  type OssResourceFetch,
  type OssWorkloadSpecItem,
} from "../k8s-client"
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
  normalizeImageRepo,
  normalizeTag,
  parseSemver,
  repoMatches,
  sameVersion,
  shortenDigest,
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

export interface RunningArtifactRef {
  repo: string
  /** True when `repo` is an exact-boundary pattern (e.g. `kube-apiserver` from server-version) rather than a container image repo. */
  patternMatch?: boolean
  rawTag: string | null
  /** Explicit `@sha256:...` digest pinned on the image spec, if any. */
  pinnedDigest: string | null
  /** Best known digest (spec `@sha256:...` or containerStatus `imageID` digest). */
  effectiveDigest: string | null
}

/** Pure: sums Critical/High CVEs strictly for Trivy reports whose artifact (repository AND tag,
 * or repository AND digest when digest-only) equals one of `runningArtifacts`. Reports for older
 * or other tags of the same repository are never attributed to the running row. */
export function summarizeCvesForArtifacts(
  rows: WorkloadVulnRow[],
  runningArtifacts: readonly RunningArtifactRef[],
  preferredNamespace?: string,
): { critical: number; high: number } | null {
  if (runningArtifacts.length === 0) return null
  const scoped = preferredNamespace ? rows.filter((r) => r.namespace === preferredNamespace) : rows
  if (scoped.length === 0) return null

  const matchedByReport = new Map<string, WorkloadVulnRow["summary"]>()
  for (const r of scoped) {
    const ref = extractImageRef(r.image)
    const rRepo = normalizeImageRepo(ref.repo)
    const rDigest = r.digest ?? ref.digest

    const hits = runningArtifacts.some((art) => {
      const repoOk = art.patternMatch
        ? repoMatches(r.image, art.repo)
        : rRepo === normalizeImageRepo(art.repo)
      if (!repoOk) return false
      if (art.rawTag !== null) {
        if (ref.rawTag !== art.rawTag) return false
        const targetDigest = art.pinnedDigest ?? art.effectiveDigest
        if (targetDigest && rDigest && rDigest !== targetDigest) return false
        return true
      }
      // Digest-only running image: match strictly by digest, never by repo alone or :latest.
      return Boolean(art.effectiveDigest && rDigest && rDigest === art.effectiveDigest)
    })
    if (!hits) continue
    matchedByReport.set(`${rRepo}|${ref.rawTag ?? ""}|${rDigest ?? ""}`, r.summary)
  }

  if (matchedByReport.size === 0) return null
  let critical = 0
  let high = 0
  for (const s of matchedByReport.values()) {
    critical += s.Critical
    high += s.High
  }
  return { critical, high }
}

/** Pure: non-authoritative hint of raw tags seen in Trivy for `imageMatch` (never drives drift). */
function findTrivyHint(rows: WorkloadVulnRow[], imageMatch: string | undefined, preferredNamespace?: string): string | null {
  if (!imageMatch) return null
  const repoRows = rows.filter((r) => repoMatches(r.image, imageMatch))
  if (repoRows.length === 0) return null
  const nsRows = preferredNamespace ? repoRows.filter((r) => r.namespace === preferredNamespace) : []
  const scoped = nsRows.length > 0 ? nsRows : repoRows
  const tags = [...new Set(scoped.map((r) => extractImageRef(r.image).rawTag).filter((t): t is string => Boolean(t)))].sort()
  return tags[0] ?? null
}

/** Pure: declared vs running. Match (`in-sync`) requires at least one verified running tag from
 * the authoritative workload/API source and a non-mixed rollout. Chart version alone never yields
 * `in-sync`; unparseable or disallowed-suffix tags are always `unknown` (or `drift` on chart mismatch). */
export function computeDrift(
  declared: { version: string; chartVersion: string | null },
  chartVersion: string | null,
  imageTags: string[],
  allowedSuffixes?: readonly string[],
  rolloutMixed = false,
): OssDrift {
  if (rolloutMixed) return "drift"
  if (imageTags.length === 0) return "unknown"

  const hasUnparseableTag = imageTags.some((t) => normalizeTag(t, allowedSuffixes) === null)
  const chartKnown = Boolean(chartVersion && normalizeTag(chartVersion) !== null)
  const chartOk = chartKnown ? sameVersion(chartVersion, declared.chartVersion ?? declared.version) : null

  if (hasUnparseableTag) {
    return chartOk === false ? "drift" : "unknown"
  }

  const signals: boolean[] = []
  if (chartOk !== null) signals.push(chartOk)
  for (const t of imageTags) signals.push(sameVersion(t, declared.version, allowedSuffixes))
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

function fetchFailureReason<T>(
  fetch: OssResourceFetch<T>,
  fallbackResource: string,
): { reason: OssRunningReason; reasonDetail: string | null } | null {
  if (fetch.status === "no-permission") {
    return { reason: "no-permission", reasonDetail: fetch.resource }
  }
  if (fetch.status === "truncated") {
    return { reason: "list-truncated", reasonDetail: fetch.resource }
  }
  if (fetch.status !== "ok") {
    return { reason: "k8s-unavailable", reasonDetail: null }
  }
  if ((fetch as { truncated?: boolean }).truncated) {
    return { reason: "list-truncated", reasonDetail: fallbackResource }
  }
  return null
}

// D-OSS-OWNERSHIP: a Pod counts only when owned by the defining workload via
// metadata.ownerReferences (Deployment -> ReplicaSet -> Pod; DaemonSet/StatefulSet -> Pod).
// Overlapping label selectors without ownership (e.g. a Job pod) are ignored.
function isOwnedBy(
  refs: readonly OssOwnerRef[] | undefined,
  kind: string,
  name: string,
  uid?: string,
): boolean {
  if (!refs?.length) return false
  return refs.some(
    (r) => r.kind === kind && r.name === name && r.controller !== false && (!uid || r.uid === uid),
  )
}

function isActiveRunningPod(pod: OssPodSpecItem): boolean {
  return pod.phase === "Running" && !pod.terminating
}

function isMirrorStaticPod(pod: OssPodSpecItem, namespace: string, namePrefix: string): boolean {
  if (pod.namespace !== namespace || !pod.name.startsWith(namePrefix)) return false
  if (!pod.mirrorAnnotation || !pod.nodeName) return false
  if (!pod.ownerReferences?.length) return true
  return pod.ownerReferences.some(
    (r) => r.kind === "Node" && r.name === pod.nodeName && r.controller !== false,
  )
}

function isContainerRunning(c: OssPodContainerSpec, init: boolean): boolean {
  if (!c.image) return false
  if (init) return c.running === true || c.completed === true
  return c.running === true
}

interface ContainerInspection {
  version: string | null
  rawTag: string | null
  imageTags: string[]
  reason: OssRunningReason | null
  reasonDetail: string | null
  runningArtifacts: RunningArtifactRef[]
}

function inspectContainerAcrossRunningPods(
  activePods: OssPodSpecItem[],
  container: string,
  init: boolean,
  allowedSuffixes?: readonly string[],
): ContainerInspection {
  interface ObservedImage {
    image: string
    repo: string
    rawTag: string | null
    pinnedDigest: string | null
    effectiveDigest: string | null
  }

  const distinct = new Map<string, ObservedImage>()
  let missingContainer = false
  let runningCount = 0

  for (const pod of activePods) {
    const list = init ? pod.initContainers : pod.containers
    const c = list.find((item) => item.name === container)
    if (!c) {
      missingContainer = true
      continue
    }
    if (!isContainerRunning(c, init)) continue
    runningCount++
    const parsed = extractImageRef(c.image)
    const normRepo = normalizeImageRepo(parsed.repo)
    const effectiveDigest = parsed.digest ?? c.digest ?? null
    // Dedupe by effective digest as well as repo/tag so same-tag pods running different
    // digests are surfaced as rollout-mixed drift rather than silently collapsed.
    const key = `${normRepo}|${parsed.rawTag ?? ""}|${effectiveDigest ?? ""}`
    if (!distinct.has(key)) {
      distinct.set(key, {
        image: c.image,
        repo: parsed.repo,
        rawTag: parsed.rawTag,
        pinnedDigest: parsed.digest,
        effectiveDigest,
      })
    }
  }

  if (missingContainer) {
    return {
      version: null,
      rawTag: null,
      imageTags: [],
      reason: "container-not-found",
      reasonDetail: container,
      runningArtifacts: [],
    }
  }

  if (runningCount === 0) {
    return {
      version: null,
      rawTag: null,
      imageTags: [],
      reason: "no-running-pod",
      reasonDetail: null,
      runningArtifacts: [],
    }
  }

  const observed = [...distinct.values()]
  const runningArtifacts: RunningArtifactRef[] = observed.map((o) => ({
    repo: o.repo,
    rawTag: o.rawTag,
    pinnedDigest: o.pinnedDigest,
    effectiveDigest: o.effectiveDigest,
  }))

  if (observed.length > 1) {
    const tagCounts = new Map<string, number>()
    const resolvedTagLabels = observed.map((o) =>
      o.rawTag ? (normalizeTag(o.rawTag, allowedSuffixes) ?? o.rawTag) : null,
    )
    for (const lbl of resolvedTagLabels) {
      if (lbl !== null) tagCounts.set(lbl, (tagCounts.get(lbl) ?? 0) + 1)
    }
    const displayTags = observed.map((o, idx) => {
      const lbl = resolvedTagLabels[idx]
      if (lbl !== null) {
        if ((tagCounts.get(lbl) ?? 0) > 1) {
          return `${lbl} (${o.effectiveDigest ? shortenDigest(o.effectiveDigest) : "no-digest"})`
        }
        return lbl
      }
      return o.effectiveDigest ? shortenDigest(o.effectiveDigest) : o.image
    })
    return {
      version: null,
      rawTag: null,
      imageTags: displayTags,
      reason: "rollout-mixed",
      reasonDetail: displayTags.join(", "),
      runningArtifacts,
    }
  }

  const only = observed[0]
  if (only.rawTag === null) {
    if (only.effectiveDigest) {
      const short = shortenDigest(only.effectiveDigest)
      return {
        version: null,
        rawTag: null,
        imageTags: [short],
        reason: "digest-only",
        reasonDetail: short,
        runningArtifacts,
      }
    }
    return {
      version: null,
      rawTag: null,
      imageTags: [],
      reason: "tag-unparseable",
      reasonDetail: only.image,
      runningArtifacts,
    }
  }

  const norm = normalizeTag(only.rawTag, allowedSuffixes)
  if (!norm) {
    return {
      version: null,
      rawTag: only.rawTag,
      imageTags: [only.rawTag],
      reason: "tag-unparseable",
      reasonDetail: only.rawTag,
      runningArtifacts,
    }
  }

  return {
    version: norm,
    rawTag: only.rawTag,
    imageTags: [norm],
    reason: null,
    reasonDetail: null,
    runningArtifacts,
  }
}

function resolveWorkloadTarget(
  resource: WorkloadKind,
  namespace: string,
  name: string,
  container: string,
  init: boolean,
  allowedSuffixes: readonly string[] | undefined,
  k8s: OssClusterInventory,
): ContainerInspection & { source: string } {
  const prefix = WORKLOAD_PREFIX[resource]
  const source = `${prefix}/${namespace}/${name} (${container})`
  const fetch = workloadListFor(k8s, resource)

  const workloadErr = fetchFailureReason(fetch, `${resource.toLowerCase()}s.apps`)
  if (workloadErr) {
    return { source, version: null, rawTag: null, imageTags: [], ...workloadErr, runningArtifacts: [] }
  }
  if (fetch.status !== "ok") {
    return { source, version: null, rawTag: null, imageTags: [], reason: "k8s-unavailable", reasonDetail: null, runningArtifacts: [] }
  }

  const workload: OssWorkloadSpecItem | undefined = fetch.data.find((w) => w.namespace === namespace && w.name === name)
  if (!workload) {
    return { source, version: null, rawTag: null, imageTags: [], reason: "workload-not-found", reasonDetail: `${prefix}/${namespace}/${name}`, runningArtifacts: [] }
  }

  const templateContainers = init ? workload.initContainers : workload.containers
  const templateHasContainer = templateContainers.some((c) => c.name === container)

  let ownedReplicaSets: Array<{ name: string; uid?: string }> = []
  if (resource === "Deployment") {
    const rsErr = fetchFailureReason(k8s.replicasets, "replicasets.apps")
    if (rsErr) {
      return { source, version: null, rawTag: null, imageTags: [], ...rsErr, runningArtifacts: [] }
    }
    if (k8s.replicasets.status !== "ok") {
      return { source, version: null, rawTag: null, imageTags: [], reason: "k8s-unavailable", reasonDetail: null, runningArtifacts: [] }
    }
    ownedReplicaSets = k8s.replicasets.data
      .filter((rs) => rs.namespace === namespace && isOwnedBy(rs.ownerReferences, "Deployment", workload.name, workload.uid))
      .map((rs) => ({ name: rs.name, uid: rs.uid }))
  }

  const podsErr = fetchFailureReason(k8s.pods, "pods")
  if (podsErr) {
    return { source, version: null, rawTag: null, imageTags: [], ...podsErr, runningArtifacts: [] }
  }
  if (k8s.pods.status !== "ok") {
    return { source, version: null, rawTag: null, imageTags: [], reason: "k8s-unavailable", reasonDetail: null, runningArtifacts: [] }
  }

  const ownedPods = k8s.pods.data.filter((p) => {
    if (p.namespace !== namespace) return false
    if (resource === "Deployment") {
      return ownedReplicaSets.some((rs) => isOwnedBy(p.ownerReferences, "ReplicaSet", rs.name, rs.uid))
    }
    return isOwnedBy(p.ownerReferences, resource, workload.name, workload.uid)
  })
  const activePods = ownedPods.filter(isActiveRunningPod)

  if (activePods.length === 0) {
    if (!templateHasContainer) {
      return { source, version: null, rawTag: null, imageTags: [], reason: "container-not-found", reasonDetail: container, runningArtifacts: [] }
    }
    return { source, version: null, rawTag: null, imageTags: [], reason: "no-running-pod", reasonDetail: `${prefix}/${namespace}/${name}`, runningArtifacts: [] }
  }

  const inspected = inspectContainerAcrossRunningPods(activePods, container, init, allowedSuffixes)
  if (inspected.reason === "no-running-pod") {
    if (!templateHasContainer) {
      return { source, version: null, rawTag: null, imageTags: [], reason: "container-not-found", reasonDetail: container, runningArtifacts: [] }
    }
    return { source, ...inspected, reasonDetail: `${prefix}/${namespace}/${name}` }
  }
  return { source, ...inspected }
}

function cveForArtifacts(
  rows: WorkloadVulnRow[],
  artifacts: readonly RunningArtifactRef[],
  namespace: string | undefined,
  trivyState: OssUpdatesResponse["trivy"],
): { cves: { critical: number; high: number } | null; cveReason: OssCveReason | null } {
  if (artifacts.length === 0) {
    return { cves: null, cveReason: trivyState === "unavailable" ? "trivy-unavailable" : "no-running-image" }
  }
  const cves = summarizeCvesForArtifacts(rows, artifacts, namespace)
  if (cves !== null) return { cves, cveReason: null }
  return { cves: null, cveReason: trivyState === "unavailable" ? "trivy-unavailable" : "no-scan-for-running" }
}

function resolveSiblings(
  specs: readonly OssSiblingSpec[] | undefined,
  k8s: OssClusterInventory,
  trivyRows: WorkloadVulnRow[],
  trivyState: OssUpdatesResponse["trivy"],
): OssRunningSibling[] {
  if (!specs?.length) return []
  return specs.map((s) => {
    const res = resolveWorkloadTarget(s.resource, s.namespace, s.name, s.container, s.init === true, s.allowedTagSuffixes, k8s)
    const { cves, cveReason } = cveForArtifacts(trivyRows, res.runningArtifacts, s.namespace, trivyState)
    return {
      label: s.label,
      source: res.source,
      rawTag: res.rawTag,
      version: res.version,
      imageTags: res.imageTags,
      reason: res.reason,
      reasonDetail: res.reasonDetail,
      cves,
      cveReason,
    }
  })
}

interface ResolvedRunning {
  version: string | null
  rawTag: string | null
  imageTags: string[]
  source: string | null
  reason: OssRunningReason | null
  reasonDetail: string | null
  trivyHint: string | null
  runningArtifacts: RunningArtifactRef[]
  siblings: OssRunningSibling[]
}

function resolveRunning(
  c: OssComponent,
  k8s: OssClusterInventory,
  trivyRows: WorkloadVulnRow[],
  trivyState: OssUpdatesResponse["trivy"],
): ResolvedRunning {
  const src = c.runningSource
  const siblings = resolveSiblings(c.siblings, k8s, trivyRows, trivyState)
  const trivyHint = findTrivyHint(trivyRows, c.imageMatch, c.cveNamespace)

  if (src.kind === "cli") {
    return {
      version: null,
      rawTag: null,
      imageTags: [],
      source: null,
      reason: "not-applicable",
      reasonDetail: null,
      trivyHint: null,
      runningArtifacts: [],
      siblings,
    }
  }

  if (src.kind === "server-version") {
    if (k8s.serverVersion.status === "no-permission") {
      return { version: null, rawTag: null, imageTags: [], source: "/version", reason: "no-permission", reasonDetail: k8s.serverVersion.resource, trivyHint, runningArtifacts: [], siblings }
    }
    if (k8s.serverVersion.status !== "ok" || !k8s.serverVersion.data) {
      return { version: null, rawTag: null, imageTags: [], source: "/version", reason: "k8s-unavailable", reasonDetail: null, trivyHint, runningArtifacts: [], siblings }
    }
    const rawTag = k8s.serverVersion.data
    const version = normalizeTag(rawTag, c.allowedTagSuffixes)
    const artifacts: RunningArtifactRef[] = c.imageMatch
      ? [{ repo: c.imageMatch, patternMatch: true, rawTag, pinnedDigest: null, effectiveDigest: null }]
      : []
    if (!version) {
      return { version: null, rawTag, imageTags: [rawTag], source: "/version", reason: "tag-unparseable", reasonDetail: rawTag, trivyHint: null, runningArtifacts: artifacts, siblings }
    }
    return { version, rawTag, imageTags: [version], source: "/version", reason: null, reasonDetail: null, trivyHint: null, runningArtifacts: artifacts, siblings }
  }

  if (src.kind === "crd-annotation") {
    const label = `crd/${src.crdName}`
    if (k8s.gatewayApiCrdAnnotations.status === "no-permission") {
      return { version: null, rawTag: null, imageTags: [], source: label, reason: "no-permission", reasonDetail: k8s.gatewayApiCrdAnnotations.resource, trivyHint: null, runningArtifacts: [], siblings }
    }
    if (k8s.gatewayApiCrdAnnotations.status === "not-found") {
      return { version: null, rawTag: null, imageTags: [], source: label, reason: "workload-not-found", reasonDetail: src.crdName, trivyHint: null, runningArtifacts: [], siblings }
    }
    if (k8s.gatewayApiCrdAnnotations.status !== "ok") {
      return { version: null, rawTag: null, imageTags: [], source: label, reason: "k8s-unavailable", reasonDetail: null, trivyHint: null, runningArtifacts: [], siblings }
    }
    const rawTag = k8s.gatewayApiCrdAnnotations.data[src.annotation] ?? null
    if (!rawTag) {
      return { version: null, rawTag: null, imageTags: [], source: label, reason: "workload-not-found", reasonDetail: src.annotation, trivyHint: null, runningArtifacts: [], siblings }
    }
    const version = normalizeTag(rawTag, c.allowedTagSuffixes)
    if (!version) {
      return { version: null, rawTag, imageTags: [rawTag], source: label, reason: "tag-unparseable", reasonDetail: rawTag, trivyHint: null, runningArtifacts: [], siblings }
    }
    return { version, rawTag, imageTags: [version], source: `${label} (${src.annotation})`, reason: null, reasonDetail: null, trivyHint: null, runningArtifacts: [], siblings }
  }

  if (src.kind === "static-pod") {
    const label = `pod/${src.namespace}/${src.namePrefix}* (${src.container})`
    const fetch = k8s.pods
    const podsErr = fetchFailureReason(fetch, "pods")
    if (podsErr) {
      return { version: null, rawTag: null, imageTags: [], source: label, ...podsErr, trivyHint, runningArtifacts: [], siblings }
    }
    if (fetch.status !== "ok") {
      return { version: null, rawTag: null, imageTags: [], source: label, reason: "k8s-unavailable", reasonDetail: null, trivyHint, runningArtifacts: [], siblings }
    }
    const matchingPods = fetch.data.filter((p) => isMirrorStaticPod(p, src.namespace, src.namePrefix))
    if (matchingPods.length === 0) {
      return { version: null, rawTag: null, imageTags: [], source: label, reason: "workload-not-found", reasonDetail: `${src.namespace}/${src.namePrefix}*`, trivyHint, runningArtifacts: [], siblings }
    }
    const activePods = matchingPods.filter(isActiveRunningPod)
    if (activePods.length === 0) {
      return { version: null, rawTag: null, imageTags: [], source: label, reason: "no-running-pod", reasonDetail: `${src.namespace}/${src.namePrefix}*`, trivyHint, runningArtifacts: [], siblings }
    }
    const inspected = inspectContainerAcrossRunningPods(activePods, src.container, false, c.allowedTagSuffixes)
    const exactSource = activePods.length === 1
      ? `pod/${activePods[0].namespace}/${activePods[0].name} (${src.container})`
      : `pod/${src.namespace}/${src.namePrefix}* (${activePods.length} pods, ${src.container})`
    return {
      version: inspected.version,
      rawTag: inspected.rawTag,
      imageTags: inspected.imageTags,
      source: exactSource,
      reason: inspected.reason,
      reasonDetail: inspected.reason === "no-running-pod" ? `${src.namespace}/${src.namePrefix}*` : inspected.reasonDetail,
      trivyHint: null,
      runningArtifacts: inspected.runningArtifacts,
      siblings,
    }
  }

  // src.kind === "workload"
  const res = resolveWorkloadTarget(src.resource, src.namespace, src.name, src.container, false, c.allowedTagSuffixes, k8s)
  return {
    version: res.version,
    rawTag: res.rawTag,
    imageTags: res.imageTags,
    source: res.source,
    reason: res.reason,
    reasonDetail: res.reasonDetail,
    trivyHint: res.reason === "k8s-unavailable" || res.reason === "no-permission" ? trivyHint : null,
    runningArtifacts: res.runningArtifacts,
    siblings,
  }
}

function summarizeK8sState(k8s: OssClusterInventory): OssUpdatesResponse["k8s"] {
  const core = [k8s.serverVersion, k8s.deployments, k8s.daemonsets, k8s.statefulsets, k8s.replicasets, k8s.pods]
  const okCount = core.filter((r) => r.status === "ok" && !(r as { truncated?: boolean }).truncated).length
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
      const validChart = chart && normalizeTag(chart) !== null ? chart : null
      const resolved = resolveRunning(c, k8s, trivy.rows, trivyState)

      let cves: { critical: number; high: number } | null = null
      let cveReason: OssCveReason | null = null
      if (c.runningSource.kind === "cli" || c.runningSource.kind === "crd-annotation") {
        cveReason = "not-applicable"
      } else {
        const cveRes = cveForArtifacts(trivy.rows, resolved.runningArtifacts, c.cveNamespace, trivyState)
        cves = cveRes.cves
        cveReason = cveRes.cveReason
      }

      let drift: OssDrift
      let driftReason: OssRunningReason | null = null
      if (c.runningSource.kind === "cli") {
        drift = "not-applicable"
        driftReason = "not-applicable"
      } else if (resolved.reason === "rollout-mixed") {
        drift = computeDrift(declared, validChart, resolved.imageTags, c.allowedTagSuffixes, true)
        driftReason = "rollout-mixed"
      } else if (resolved.reason === "tag-unparseable" || resolved.reason === "digest-only") {
        drift = computeDrift(declared, validChart, resolved.imageTags.length > 0 ? resolved.imageTags : ["__unparseable__"], c.allowedTagSuffixes)
        driftReason = drift === "unknown" ? resolved.reason : null
      } else if (resolved.version !== null) {
        drift = computeDrift(declared, validChart, [resolved.version], c.allowedTagSuffixes)
      } else {
        // K1: Workload/container/pods not read -> never match on chart alone.
        drift = computeDrift(declared, validChart, [], c.allowedTagSuffixes)
        driftReason = resolved.reason ?? "workload-not-found"
      }

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
          imageTags: resolved.imageTags,
          siblings: resolved.siblings,
          reason: resolved.reason,
          reasonDetail: resolved.reasonDetail,
          ...(resolved.trivyHint ? { trivyHint: resolved.trivyHint } : {}),
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
