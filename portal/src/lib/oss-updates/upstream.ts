import "server-only"
import { cacheGet, cacheSet } from "../valkey"
import { cacheKeys, cacheTtl } from "../cache-keys"
import { fetchWithPolicy, readJsonWithPolicy } from "../http-client"
import { compareSemver, parseSemver } from "./version"

// Latest-upstream lookup for the OSS updates view.
//
// D-OSS-UPSTREAM-OFF: the platform may be air-gapped (Kakao nodes have no upstream egress), so the
// lookup is OFF unless OSS_UPDATES_UPSTREAM=true. Off, or unreachable, every row shows "unknown"
// and the rest of the page (declared/running/drift/CVE) still works. Cost: an operator must opt in
// on connected clusters. Escape hatch: set the flag.
//
// Privacy: only unauthenticated GET https://api.github.com/repos/<owner>/<repo>/releases. No cookie,
// token, user, cluster or version data is sent; <owner>/<repo> comes from the static catalog.
// Unauthenticated GitHub allows 60 req/h per IP; ~24 catalog repos x one refresh per 6h is far
// below that. A rate-limit answer is treated like any failure (cached 10 min as "unknown").

const GITHUB_API = "https://api.github.com"
const FAILURE_TTL_SECONDS = 600
const REPO_RE = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/
const PRERELEASE_TAG_RE = /-(?:rc|alpha|beta|pre|dev|snapshot)/i

export interface UpstreamRelease {
  tag: string
  publishedAt: string
  url: string
}

interface CachedLookup {
  /** null = lookup failed (unknown); [] = repo has no usable stable release. */
  releases: UpstreamRelease[] | null
}

interface GithubRelease {
  tag_name?: unknown
  draft?: unknown
  prerelease?: unknown
  published_at?: unknown
  html_url?: unknown
}

export function isUpstreamLookupEnabled(): boolean {
  return process.env.OSS_UPDATES_UPSTREAM === "true"
}

/** Stable, parseable releases only, newest-first by version. Exported for tests. */
export function normalizeReleases(raw: unknown): UpstreamRelease[] {
  if (!Array.isArray(raw)) return []
  const out: UpstreamRelease[] = []
  for (const r of raw as GithubRelease[]) {
    if (typeof r?.tag_name !== "string" || typeof r.published_at !== "string") continue
    if (r.draft === true || r.prerelease === true) continue
    if (PRERELEASE_TAG_RE.test(r.tag_name) || !parseSemver(r.tag_name)) continue
    out.push({
      tag: r.tag_name,
      publishedAt: r.published_at,
      url: typeof r.html_url === "string" && r.html_url.startsWith("https://") ? r.html_url : "",
    })
  }
  return out.sort((a, b) => compareSemver(parseSemver(b.tag)!, parseSemver(a.tag)!))
}

/** Releases for `repo`, or null when unknown (flag off, unreachable, rate-limited, bad repo). */
export async function getUpstreamReleases(repo: string): Promise<UpstreamRelease[] | null> {
  if (!isUpstreamLookupEnabled() || !REPO_RE.test(repo)) return null

  const key = cacheKeys.ossUpstreamReleases(repo)
  const cached = await cacheGet<CachedLookup>(key)
  if (cached) return cached.releases

  try {
    const res = await fetchWithPolicy(
      `${GITHUB_API}/repos/${repo}/releases?per_page=30`,
      { headers: { Accept: "application/vnd.github+json", "User-Agent": "narwhal-portal-oss-updates" } },
      { timeoutMs: 4000, retry: false },
    )
    if (!res.ok) throw new Error(`GitHub releases HTTP ${res.status}`)
    const releases = normalizeReleases(await readJsonWithPolicy<unknown>(res))
    await cacheSet(key, { releases } satisfies CachedLookup, cacheTtl("ossUpstreamReleases"))
    return releases
  } catch (err) {
    console.warn(`[oss-updates] upstream lookup failed for ${repo}:`, err instanceof Error ? err.message : err)
    await cacheSet(key, { releases: null } satisfies CachedLookup, FAILURE_TTL_SECONDS)
    return null
  }
}
