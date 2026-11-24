// Pure version math and image tag/repo normalization for the OSS updates view.
// No I/O so every rule (gap class, cooling, tag normalization, repo matching) is unit-testable.

/** Repo rule: an upstream version younger than this is "cooling" and must not be adopted yet
 * (.agents/skills/narwhal-version-upgrade, docs/common/oss-update-process.md). */
export const COOLING_DAYS = 7

export interface Semver {
  major: number
  minor: number
  patch: number
}

export type GapKind = "none" | "patch" | "minor" | "major" | "ahead" | "unknown"

// Require end-of-string or `-`/`+` after major.minor(.patch) so "1.2.3.4" or "1.2.3abc" is rejected.
const SEMVER_RE = /^v?(\d+)\.(\d+)(?:\.(\d+))?(?:$|[-+])/
const STRICT_BASE_SEMVER_RE = /^v?(\d+)\.(\d+)(?:\.(\d+))?(.*)$/
const DIGEST_SUFFIX_RE = /@([A-Za-z0-9_+.-]+:[0-9a-fA-F]+)$/

/** Lenient parse: `v1.35.7`, `4.34` (2-part, patch=0), `1.30.1-distroless`; rejects `1.2.3.4`, `latest`, `HEAD`. */
export function parseSemver(input: string | null | undefined): Semver | null {
  if (!input) return null
  const m = SEMVER_RE.exec(input.trim())
  if (!m) return null
  return { major: Number(m[1]), minor: Number(m[2]), patch: Number(m[3] ?? 0) }
}

export function compareSemver(a: Semver, b: Semver): number {
  return a.major - b.major || a.minor - b.minor || a.patch - b.patch
}

/** How far `declared` trails `latest`. Never throws; unparsable input is "unknown". */
export function gapKind(declared: string | null | undefined, latest: string | null | undefined): GapKind {
  const d = parseSemver(declared)
  const l = parseSemver(latest)
  if (!d || !l) return "unknown"
  const cmp = compareSemver(d, l)
  if (cmp === 0) return "none"
  if (cmp > 0) return "ahead"
  if (d.major !== l.major) return "major"
  if (d.minor !== l.minor) return "minor"
  return "patch"
}

const DAY_MS = 24 * 60 * 60 * 1000

/** Whole days from `iso` to `now`; null for a missing/invalid timestamp. Clamped at 0. */
export function ageDays(iso: string | null | undefined, now: number): number | null {
  if (!iso) return null
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return null
  return Math.max(0, Math.floor((now - t) / DAY_MS))
}

export function isCooling(publishedAt: string | null | undefined, now: number): boolean {
  const age = ageDays(publishedAt, now)
  return age !== null && age < COOLING_DAYS
}

/** Strip a leading `v` so `v1.20.2` (git tag) and `1.20.2` (chart targetRevision) compare equal. */
export function normalizeVersion(v: string | null | undefined): string | null {
  if (!v) return null
  const s = v.trim().replace(/^v/, "")
  return s === "" ? null : s
}

/** Same version ignoring `v` and trailing `.0` padding (`4.34` == `4.34.0`).
 * Suffixes are NOT stripped unless explicitly listed in `allowedSuffixes`, so
 * `3.15.0-rc1`, `3.15.0-preview1`, and `3.15.0-custom` never match `3.15.0`. */
export function sameVersion(
  a: string | null | undefined,
  b: string | null | undefined,
  allowedSuffixes?: readonly string[],
): boolean {
  const na = normalizeTag(a, allowedSuffixes)
  const nb = normalizeTag(b, allowedSuffixes)
  if (na !== null && nb !== null) {
    const pa = parseSemver(na)
    const pb = parseSemver(nb)
    if (pa && pb) return compareSemver(pa, pb) === 0
  }
  return normalizeVersion(a) === normalizeVersion(b)
}

export interface ParsedImageRef {
  repo: string
  rawTag: string | null
  digest: string | null
}

/** Extracts repository, raw tag, and `@algorithm:hex` digest from an OCI image string,
 * ignoring registry port colons (e.g. `192.168.56.1:5001/gitea:1.26.2-rootless`). */
export function extractImageRef(image: string): ParsedImageRef {
  const trimmed = image.trim()
  const digestMatch = DIGEST_SUFFIX_RE.exec(trimmed)
  const digest = digestMatch ? digestMatch[1] : null
  const withoutDigest = digestMatch ? trimmed.slice(0, digestMatch.index) : trimmed
  const lastSlash = withoutDigest.lastIndexOf("/")
  const colonIdx = withoutDigest.indexOf(":", lastSlash + 1)
  if (colonIdx > lastSlash) {
    const repo = withoutDigest.slice(0, colonIdx)
    const tag = withoutDigest.slice(colonIdx + 1)
    return { repo, rawTag: tag || null, digest }
  }
  return { repo: withoutDigest, rawTag: null, digest }
}

const OCI_TAG_RE = /^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$/
const OCI_PORT_RE = /^\d{1,5}$/
const OCI_PATH_SEGMENT_RE = /^[^\s:@]+$/

/** Strict OCI image reference parser: returns `null` when the input is empty, contains
 * whitespace, has an invalid `@digest` or `:tag`, empty path segments, or an invalid
 * registry `host:port`. Reused by KISA-IMG-02 so unparseable refs stay in scope. */
export function parseImageRefStrict(image: string): ParsedImageRef | null {
  const trimmed = image.trim()
  if (!trimmed || /\s/.test(trimmed)) return null

  const digestMatch = DIGEST_SUFFIX_RE.exec(trimmed)
  const digest = digestMatch ? digestMatch[1] : null
  const withoutDigest = digestMatch ? trimmed.slice(0, digestMatch.index) : trimmed
  if (!withoutDigest || withoutDigest.includes("@")) return null

  const lastSlash = withoutDigest.lastIndexOf("/")
  const colonIdx = withoutDigest.indexOf(":", lastSlash + 1)
  let repo = withoutDigest
  let rawTag: string | null = null
  if (colonIdx > lastSlash) {
    repo = withoutDigest.slice(0, colonIdx)
    rawTag = withoutDigest.slice(colonIdx + 1)
    if (!rawTag || !OCI_TAG_RE.test(rawTag)) return null
  }

  const segments = repo.split("/")
  if (segments.some((s) => !s)) return null
  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i]
    if (i === 0 && segments.length > 1 && seg.includes(":")) {
      const portColon = seg.indexOf(":")
      const host = seg.slice(0, portColon)
      const port = seg.slice(portColon + 1)
      if (!host || !OCI_PATH_SEGMENT_RE.test(host) || !OCI_PORT_RE.test(port)) return null
      const portNum = Number(port)
      if (portNum < 1 || portNum > 65535) return null
    } else if (!OCI_PATH_SEGMENT_RE.test(seg)) {
      return null
    }
  }

  return { repo, rawTag, digest }
}

/** Shortens `sha256:0123456789abcdef...` to `sha256:0123456789ab` for display. */
export function shortenDigest(digest: string): string {
  const idx = digest.indexOf(":")
  if (idx === -1) return digest.slice(0, 12)
  const alg = digest.slice(0, idx)
  const hex = digest.slice(idx + 1)
  return `${alg}:${hex.slice(0, 12)}`
}

/** Normalizes Docker Hub aliases (`index.docker.io`, bare `org/app`, bare `app`) to `docker.io/...`. */
export function normalizeImageRepo(repo: string): string {
  const clean = repo.trim().replace(DIGEST_SUFFIX_RE, "")
  const parts = clean.split("/")
  if (parts.length === 1) return `docker.io/library/${clean}`
  const first = parts[0]
  if (first === "index.docker.io") return `docker.io/${parts.slice(1).join("/")}`
  if (!first.includes(".") && !first.includes(":") && first !== "localhost") {
    return `docker.io/${clean}`
  }
  return clean
}

/** Exact repository match (never bare substring): `cilium/cilium` will NOT match `cilium/cilium-envoy`,
 * and `apache/apisix` will NOT match `apache/apisix-ingress-controller`. */
export function repoMatches(image: string, pattern: string): boolean {
  const normImage = normalizeImageRepo(extractImageRef(image).repo)
  const firstSeg = pattern.split("/")[0] ?? ""
  const hasRegistry = firstSeg.includes(".") || firstSeg.includes(":") || firstSeg === "localhost"
  if (hasRegistry) return normImage === normalizeImageRepo(pattern)
  return normImage === pattern || normImage.endsWith(`/${pattern}`)
}

/** Explicit tag normalization for running versions (D-TAG-ALLOWLIST): accepts optional leading `v`,
 * `major.minor(.patch)`, and ONLY an exact suffix in `allowedSuffixes` (e.g. `["-rootless"]` for
 * Gitea, `["-ubuntu"]` for APISIX, `["-distroless"]` for Istio). Any unlisted suffix (`-preview1`,
 * `-rc1`, `-custom`, `-0`, `+build`) or non-semver tag (`latest`, `HEAD`, `8-alpine`) returns `null`
 * so it is shown verbatim as unknown/drift and never treated as a match. */
export function normalizeTag(
  rawTag: string | null | undefined,
  allowedSuffixes?: readonly string[],
): string | null {
  if (!rawTag) return null
  const cleaned = rawTag.trim().replace(DIGEST_SUFFIX_RE, "")
  if (!cleaned) return null
  const m = STRICT_BASE_SEMVER_RE.exec(cleaned)
  if (!m) return null
  const suffix = m[4]
  if (suffix !== "") {
    if (!allowedSuffixes || !allowedSuffixes.includes(suffix)) return null
  }
  return m[3] !== undefined ? `${m[1]}.${m[2]}.${m[3]}` : `${m[1]}.${m[2]}`
}
