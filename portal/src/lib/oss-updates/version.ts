// Pure version math for the OSS updates view. No I/O so every rule (gap class, cooling,
// "branch latest" filtering) is unit-testable.

/** Repo rule: an upstream version younger than this is "cooling" and must not be adopted yet
 * (.agents/skills/narwhal-version-upgrade, docs/common/oss-update-process.md). */
export const COOLING_DAYS = 7

export interface Semver {
  major: number
  minor: number
  patch: number
}

export type GapKind = "none" | "patch" | "minor" | "major" | "ahead" | "unknown"

const SEMVER_RE = /^v?(\d+)\.(\d+)(?:\.(\d+))?/

/** Lenient parse: `v1.35.7`, `4.34` (2-part, patch=0), `kube-prometheus-stack-1.2.3` is NOT accepted. */
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

/** Same version ignoring `v` and trailing `.0` padding (`4.34` == `4.34.0`). */
export function sameVersion(a: string | null | undefined, b: string | null | undefined): boolean {
  const pa = parseSemver(a)
  const pb = parseSemver(b)
  if (pa && pb) return compareSemver(pa, pb) === 0
  return normalizeVersion(a) === normalizeVersion(b)
}
