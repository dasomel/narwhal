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

/**
 * Looks up a Trivy Kubernetes check by raw or normalized ID (e.g. `AVD-KSV-0014`, `KSV014`,
 * `AVD-KCV-0048`, `KCV0048`). Returns `undefined` for unknown, optional, or deprecated IDs
 * that are not loaded by the deployed trivy-operator embedded policy set.
 */
export function getCatalogueCheck(rawId: string): TrivyCheckCatalogueEntry | undefined {
  const norm = normalizeCheckId(rawId)
  if (!norm || !Object.hasOwn(TRIVY_CHECK_CATALOGUE, norm)) return undefined
  return TRIVY_CHECK_CATALOGUE[norm]
}
