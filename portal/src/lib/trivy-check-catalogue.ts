import rawCatalogue from "@/data/trivy-check-catalogue.generated.json"
import { normalizeCheckId } from "./compliance-accepted"
import type { Severity } from "@/types/compliance"

export type CatalogueReportFamily = "configAudit" | "rbac" | "infra"
export type CatalogueNodeType = "master" | "worker"

export interface TrivyCheckCatalogueEntry {
  id: string
  avdId: string
  reportFamily: CatalogueReportFamily
  kinds: readonly string[]
  nodeTypes?: readonly CatalogueNodeType[]
  requiredNamespaces?: readonly string[]
  scopeNamespaces?: readonly string[]
  severity: Severity
  title: string
  legacyId?: string
  aliases?: readonly string[]
}

export interface TrivyCheckCatalogueSource {
  trivyOperatorVersion: string
  trivyOperatorChartVersion: string
  trivyChecksModule: string
  trivyChecksVersion: string
  policyMode: string
  excludedPaths: readonly string[]
}

export interface TrivyCheckCatalogue {
  source: TrivyCheckCatalogueSource
  checks: Readonly<Record<string, TrivyCheckCatalogueEntry>>
}

const CATALOGUE = rawCatalogue as unknown as TrivyCheckCatalogue

export const TRIVY_CHECK_CATALOGUE_SOURCE: TrivyCheckCatalogueSource = CATALOGUE.source
export const TRIVY_CHECK_CATALOGUE: Readonly<Record<string, TrivyCheckCatalogueEntry>> = CATALOGUE.checks

// D-CHECK-ALIASES: build two alias lookup maps from the generated catalogue:
// 1. exactAliasToCanonical: exact uppercased alias -> canonical id (e.g. "KSV0012" -> "KCV0012",
//    "KCV0135" -> "KCV0035", "KSV-0124" -> "KSV124"). Evaluated BEFORE `normalizeCheckId` so
//    "KSV0012" (CIS 1.2.12 AlwaysPullImages) never collides with distinct check "KSV012" (runs as root).
// 2. normalizedAliasToCanonical: normalized alias -> canonical id ONLY when the normalized form is
//    not itself a distinct canonical check in the catalogue (e.g. "AVD-KCV-0135" -> "KCV0135" -> "KCV0035").
// Cost: O(N) once at module load. Escape hatch: regenerate catalogue when trivy-checks fixes legacy IDs.
const EXACT_ALIAS_TO_CANONICAL = new Map<string, string>()
const NORMALIZED_ALIAS_TO_CANONICAL = new Map<string, string>()

for (const entry of Object.values(TRIVY_CHECK_CATALOGUE)) {
  const candidates = new Set<string>()
  if (entry.legacyId) candidates.add(entry.legacyId)
  for (const a of entry.aliases ?? []) candidates.add(a)
  for (const rawAlias of candidates) {
    const upper = rawAlias.trim().toUpperCase()
    if (!upper || upper === entry.id || Object.hasOwn(TRIVY_CHECK_CATALOGUE, upper)) continue
    EXACT_ALIAS_TO_CANONICAL.set(upper, entry.id)
    const normAlias = normalizeCheckId(upper)
    if (normAlias && normAlias !== entry.id && !Object.hasOwn(TRIVY_CHECK_CATALOGUE, normAlias)) {
      NORMALIZED_ALIAS_TO_CANONICAL.set(normAlias, entry.id)
    }
  }
}

/**
 * Resolves any raw check ID, AVD ID, or catalogued legacy/alias ID to its canonical catalogue ID.
 * Preserves distinct checks when a raw alias would otherwise normalize to another check ID
 * (e.g. `"KSV0012"` -> `"KCV0012"`, while `"KSV012"` / `"AVD-KSV-0012"` stays `"KSV012"`).
 */
export function resolveCanonicalCheckId(rawId: string): string {
  const upper = rawId.trim().toUpperCase()
  if (!upper) return ""
  const exact = EXACT_ALIAS_TO_CANONICAL.get(upper)
  if (exact) return exact
  const norm = normalizeCheckId(upper)
  if (Object.hasOwn(TRIVY_CHECK_CATALOGUE, norm)) return norm
  return NORMALIZED_ALIAS_TO_CANONICAL.get(norm) ?? norm
}

/**
 * Looks up a Trivy Kubernetes check by raw, normalized, or catalogued alias ID (e.g. `AVD-KSV-0014`,
 * `KSV014`, `AVD-KCV-0012`, `KSV0012`, `KCV0135`). Returns `undefined` for unknown, optional, or
 * deprecated IDs that are not loaded by the deployed trivy-operator embedded policy set.
 */
export function getCatalogueCheck(rawId: string): TrivyCheckCatalogueEntry | undefined {
  const canonical = resolveCanonicalCheckId(rawId)
  if (!canonical || !Object.hasOwn(TRIVY_CHECK_CATALOGUE, canonical)) return undefined
  return TRIVY_CHECK_CATALOGUE[canonical]
}
