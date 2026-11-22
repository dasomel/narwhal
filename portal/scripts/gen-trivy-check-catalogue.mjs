#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
//
// Generates portal/src/data/trivy-check-catalogue.generated.json from the pinned
// github.com/aquasecurity/trivy-checks Go module embedded in the deployed
// aquasec/trivy-operator binary (`trivy.useEmbeddedRegoPolicies: "true"`,
// `trivy.useBuiltinRegoPolicies: "false"`).
//
// Source-of-truth pins verified by `--check` (offline, no network):
//   - gitops/charts/narwhal-apps/templates/trivy-operator.yaml (chart targetRevision + useBuiltinRegoPolicies)
//   - scripts/airgap/images.txt (trivy-operator image tag)
//   - portal/src/data/trivy-check-catalogue.generated.sha256.json (canonical SHA-256 + pinned source versions)
//
// Changing the catalogue requires regenerating from the upstream trivy-checks source (network
// or `--from-zip`) with the pinned versions bumped consciously — never hand-edit the JSON.
//
// Usage:
//   node portal/scripts/gen-trivy-check-catalogue.mjs --check
//   node portal/scripts/gen-trivy-check-catalogue.mjs [--from-zip /path/to/trivy-checks-v1.7.1.zip]

import { execFileSync } from "node:child_process"
import { createHash } from "node:crypto"
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const here = dirname(fileURLToPath(import.meta.url))
export const REPO_ROOT = resolve(here, "../..")
export const OUT_JSON = resolve(here, "../src/data/trivy-check-catalogue.generated.json")
export const OUT_DIGEST_JSON = resolve(here, "../src/data/trivy-check-catalogue.generated.sha256.json")
export const OPERATOR_APP_YAML = resolve(REPO_ROOT, "gitops/charts/narwhal-apps/templates/trivy-operator.yaml")
export const AIRGAP_IMAGES_TXT = resolve(REPO_ROOT, "scripts/airgap/images.txt")

/** Deterministic JSON serialization matching Python `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`. */
export function canonicalJson(value) {
  if (value === null || typeof value !== "object") {
    return JSON.stringify(value)
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item)).join(",")}]`
  }
  const keys = Object.keys(value).sort()
  const parts = []
  for (const k of keys) {
    if (value[k] === undefined) continue
    parts.push(`${JSON.stringify(k)}:${canonicalJson(value[k])}`)
  }
  return `{${parts.join(",")}}`
}

export function computeCanonicalCatalogueDigest(catalogue) {
  return createHash("sha256").update(canonicalJson(catalogue), "utf8").digest("hex")
}

export function buildDigestManifest(catalogue) {
  return {
    algorithm: "sha256",
    canonicalDigest: computeCanonicalCatalogueDigest(catalogue),
    source: { ...catalogue.source },
    checkCount: Object.keys(catalogue.checks ?? {}).length,
    regenerationNote:
      "Generated from upstream trivy-checks source; do not hand-edit. Changing the catalogue requires regenerating from source (network) via `node portal/scripts/gen-trivy-check-catalogue.mjs` with pinned versions bumped consciously.",
  }
}

// Embedded module version pinned in github.com/aquasecurity/trivy-operator@v0.25.0 go.mod
// (verified against live /usr/local/bin/trivy-operator buildinfo).
export const PINNED_OPERATOR_TO_CHECKS = {
  "0.25.0": "v1.7.1",
}

const CHECK_ID_PAD_WIDTH = { KSV: 3, KCV: 4 }
const CHECK_ID_RE = /^(?:AVD-(KSV|KCV)-|(KSV|KCV))0*(\d+)$/

export function normalizeCheckId(raw) {
  const upper = String(raw ?? "").trim().toUpperCase()
  const match = CHECK_ID_RE.exec(upper)
  if (!match) return upper
  const family = match[1] ?? match[2]
  return `${family}${match[3].padStart(CHECK_ID_PAD_WIDTH[family], "0")}`
}

function mapSeverity(raw) {
  const s = String(raw ?? "").trim()
  if (!s) return "Unknown"
  const norm = s.charAt(0).toUpperCase() + s.slice(1).toLowerCase()
  if (norm === "Critical" || norm === "High" || norm === "Medium" || norm === "Low") return norm
  return "Unknown"
}

export function readRepoPins() {
  const opYaml = readFileSync(OPERATOR_APP_YAML, "utf8")
  const imagesTxt = readFileSync(AIRGAP_IMAGES_TXT, "utf8")

  const chartMatch = /^\s*targetRevision:\s*["']?([0-9]+\.[0-9]+\.[0-9]+)["']?/m.exec(opYaml)
  if (!chartMatch) {
    throw new Error(`Could not find trivy-operator chart targetRevision in ${OPERATOR_APP_YAML}`)
  }
  const trivyOperatorChartVersion = chartMatch[1]

  const imgMatch = /aquasec\/trivy-operator:v?([0-9]+\.[0-9]+\.[0-9]+)/m.exec(imagesTxt)
  if (!imgMatch) {
    throw new Error(`Could not find aquasec/trivy-operator image tag in ${AIRGAP_IMAGES_TXT}`)
  }
  const trivyOperatorVersion = imgMatch[1]

  // Chart 0.27.0 defaults to useBuiltinRegoPolicies=false & useEmbeddedRegoPolicies=true.
  const useBuiltinTrue = /^\s*useBuiltinRegoPolicies:\s*true\b/m.test(opYaml)
  const useEmbeddedFalse = /^\s*useEmbeddedRegoPolicies:\s*false\b/m.test(opYaml)
  const policyMode = !useBuiltinTrue && !useEmbeddedFalse ? "embedded" : "bundle"

  const trivyChecksVersion = PINNED_OPERATOR_TO_CHECKS[trivyOperatorVersion] ?? null
  return {
    trivyOperatorVersion,
    trivyOperatorChartVersion,
    trivyChecksVersion,
    policyMode,
  }
}

/**
 * Parses a Rego `# METADATA` header block + policy body from trivy-checks into a catalogue entry.
 * Mirrors trivy v0.60.0 LoadEmbeddedPolicies() + Scanner: skips `advanced/optional/`, deprecated
 * checks, and modules without an `avd_id` (e.g. `dynamic/outdated_api.rego`).
 */
export function parseKubernetesRegoCheck(relPath, text) {
  if (relPath.startsWith("advanced/optional/")) return null

  const metaMatch = /^# METADATA\r?\n((?:#.*\r?\n)+)/m.exec(text)
  if (!metaMatch) return null
  const metaLines = metaMatch[1].split(/\r?\n/).map((line) => (line.startsWith("# ") ? line.slice(2) : line.slice(1)))

  const titleMatch = /^title:\s*(.+)$/m.exec(metaLines.join("\n"))
  const avdMatch = /^\s*avd_id:\s*["']?([^"'\s]+)["']?/m.exec(metaLines.join("\n"))
  const idMatch = /^\s*id:\s*["']?([^"'\s]+)["']?/m.exec(metaLines.join("\n"))
  const sevMatch = /^\s*severity:\s*["']?([^"'\s]+)["']?/m.exec(metaLines.join("\n"))
  const depMatch = /^\s*deprecated:\s*true\b/m.test(metaLines.join("\n"))

  if (depMatch) return null
  const avdId = avdMatch ? avdMatch[1].trim() : ""
  if (!avdId) return null

  const rawTitle = titleMatch ? titleMatch[1].trim() : ""
  const title = rawTitle.replace(/^"(.*)"$/, "$1").replace(/^'(.*)'$/, "$1").replace(/\\"/g, '"')
  const id = normalizeCheckId(avdId)
  const severity = mapSeverity(sevMatch ? sevMatch[1] : "Unknown")
  const legacyId = idMatch ? idMatch[1].trim() : undefined

  // D-CHECK-ALIASES: capture every distinct non-canonical ID emitted by the Rego metadata
  // (`id:` and any `aliases:` list items) so failure joins match legacy/mismatched check IDs
  // (e.g. KSV0012 -> KCV0012, KCV0135 -> KCV0035, KSV-0124 -> KSV124) without normalizing
  // distinct checks into each other.
  const aliasSet = new Set()
  const addAlias = (candidate) => {
    const trimmed = String(candidate ?? "").trim().toUpperCase()
    if (!trimmed) return
    if (trimmed === id || trimmed === avdId.toUpperCase()) return
    aliasSet.add(trimmed)
  }
  if (legacyId) addAlias(legacyId)
  const aliasesBlockMatch = /^\s*aliases:\s*\r?\n((?:\s+-\s+.+\r?\n?)+)/m.exec(metaLines.join("\n"))
  if (aliasesBlockMatch) {
    for (const line of aliasesBlockMatch[1].split(/\r?\n/)) {
      const itemMatch = /^\s+-\s+["']?([^"'\s]+)["']?/.exec(line)
      if (itemMatch) addAlias(itemMatch[1])
    }
  }
  const aliases = [...aliasSet].sort()

  const pkgIdx = text.indexOf("\npackage ")
  const body = pkgIdx >= 0 ? text.slice(pkgIdx) : text
  const bodyNoComments = body
    .split(/\r?\n/)
    .map((line) => line.split("#")[0])
    .join("\n")

  const isKcv = avdId.toUpperCase().startsWith("AVD-KCV-")
  let reportFamily
  let kinds
  let nodeTypes
  let requiredNamespaces
  let scopeNamespaces

  if (isKcv) {
    reportFamily = "infra"
    if (bodyNoComments.includes("NodeInfo")) {
      kinds = ["Node"]
      const ntSet = new Set()
      for (const m of bodyNoComments.matchAll(/sp\.type\s*==\s*"(master|worker)"/g)) {
        ntSet.add(m[1])
      }
      nodeTypes = ntSet.size > 0 ? [...ntSet].sort() : ["master", "worker"]
    } else {
      // Pod-level CIS control-plane check: trivy-operator writes KCV checks on
      // k8sCoreComponent (kube-system apiserver/controller-manager/scheduler/etcd Pods)
      // into namespaced InfraAssessmentReport.
      kinds = ["Pod"]
      requiredNamespaces = ["kube-system"]
    }
  } else if (
    (/"ClusterRole"|"Role"/.test(bodyNoComments)) &&
    !(/is_role_binding_kind|RoleBinding|ClusterRoleBinding/.test(bodyNoComments))
  ) {
    reportFamily = "rbac"
    kinds = []
    if (bodyNoComments.includes('"Role"')) kinds.push("Role")
    if (bodyNoComments.includes('"ClusterRole"')) kinds.push("ClusterRole")
  } else if (
    bodyNoComments.includes("is_role_binding_kind") ||
    relPath.startsWith("rolebinding/") ||
    /"(?:Cluster)?RoleBinding"/i.test(bodyNoComments)
  ) {
    reportFamily = "configAudit"
    kinds = ["RoleBinding", "ClusterRoleBinding"]
  } else if (/(?:kubernetes|input)\.kind\s*==\s*"ConfigMap"/.test(bodyNoComments)) {
    reportFamily = "configAudit"
    kinds = ["ConfigMap"]
  } else if (/(?:kubernetes|input)\.kind\s*==\s*"Service"/.test(bodyNoComments)) {
    reportFamily = "configAudit"
    kinds = ["Service"]
  } else if (relPath.includes("network_policies") || /"networkpolicy"/i.test(bodyNoComments)) {
    reportFamily = "configAudit"
    kinds = ["NetworkPolicy"]
  } else {
    reportFamily = "configAudit"
    kinds = ["Workload"]
    if (bodyNoComments.includes('kubernetes.namespace == "kube-system"')) {
      requiredNamespaces = ["kube-system"]
    } else if (bodyNoComments.includes('kubernetes.namespace == "default"')) {
      scopeNamespaces = ["default"]
    }
  }

  const entry = {
    id,
    avdId,
    reportFamily,
    kinds,
    ...(nodeTypes ? { nodeTypes } : {}),
    ...(requiredNamespaces ? { requiredNamespaces } : {}),
    ...(scopeNamespaces ? { scopeNamespaces } : {}),
    severity,
    title,
    ...(legacyId && normalizeCheckId(legacyId) !== id ? { legacyId } : {}),
    ...(aliases.length > 0 ? { aliases } : {}),
  }
  return entry
}

function generateFromZip(zipPath, pins) {
  const listOut = execFileSync("unzip", ["-Z1", zipPath], { encoding: "utf8", maxBuffer: 16 * 1024 * 1024 })
  const regoEntries = listOut
    .split(/\r?\n/)
    .filter((n) => n.includes("/checks/kubernetes/") && n.endsWith(".rego") && !n.endsWith("_test.rego"))
    .sort()

  const checksList = []
  for (const zipEntry of regoEntries) {
    const relPath = zipEntry.split("/checks/kubernetes/")[1]
    const content = execFileSync("unzip", ["-p", zipPath, zipEntry], { encoding: "utf8", maxBuffer: 4 * 1024 * 1024 })
    const parsed = parseKubernetesRegoCheck(relPath, content)
    if (parsed) checksList.push(parsed)
  }

  checksList.sort((a, b) => a.id.localeCompare(b.id))
  const checks = {}
  for (const entry of checksList) {
    if (Object.hasOwn(checks, entry.id)) {
      throw new Error(`Duplicate normalized check ID in catalogue: ${entry.id}`)
    }
    checks[entry.id] = entry
  }

  return {
    source: {
      trivyOperatorVersion: pins.trivyOperatorVersion,
      trivyOperatorChartVersion: pins.trivyOperatorChartVersion,
      trivyChecksModule: "github.com/aquasecurity/trivy-checks",
      trivyChecksVersion: pins.trivyChecksVersion,
      policyMode: pins.policyMode,
      excludedPaths: ["checks/kubernetes/advanced/optional"],
    },
    checks,
  }
}

export function validateCatalogue(catalogue, pins, digestManifest = null) {
  const errors = []
  if (!catalogue || typeof catalogue !== "object") {
    return ["catalogue must be a JSON object"]
  }
  const src = catalogue.source
  if (!src || typeof src !== "object") {
    errors.push("catalogue.source is missing")
  } else {
    if (src.trivyOperatorVersion !== pins.trivyOperatorVersion) {
      errors.push(
        `source.trivyOperatorVersion=${src.trivyOperatorVersion} != pinned ${pins.trivyOperatorVersion} (scripts/airgap/images.txt)`,
      )
    }
    if (src.trivyOperatorChartVersion !== pins.trivyOperatorChartVersion) {
      errors.push(
        `source.trivyOperatorChartVersion=${src.trivyOperatorChartVersion} != pinned ${pins.trivyOperatorChartVersion} (trivy-operator.yaml)`,
      )
    }
    if (!pins.trivyChecksVersion) {
      errors.push(
        `no trivy-checks mapping for trivy-operator ${pins.trivyOperatorVersion} in PINNED_OPERATOR_TO_CHECKS — regenerate catalogue`,
      )
    } else if (src.trivyChecksVersion !== pins.trivyChecksVersion) {
      errors.push(
        `source.trivyChecksVersion=${src.trivyChecksVersion} != pinned ${pins.trivyChecksVersion}`,
      )
    }
    if (src.policyMode !== pins.policyMode) {
      errors.push(`source.policyMode=${src.policyMode} != repo policyMode=${pins.policyMode}`)
    }
  }

  const checks = catalogue.checks
  if (!checks || typeof checks !== "object" || Array.isArray(checks)) {
    errors.push("catalogue.checks must be a non-empty object")
    return errors
  }

  const keys = Object.keys(checks)
  if (keys.length < 100) {
    errors.push(`catalogue.checks unexpectedly small (${keys.length} entries; expected >= 100)`)
  }
  const sortedKeys = [...keys].sort((a, b) => a.localeCompare(b))
  if (keys.some((k, i) => k !== sortedKeys[i])) {
    errors.push("catalogue.checks keys must be sorted lexicographically")
  }

  const validFamilies = new Set(["configAudit", "rbac", "infra"])
  const validSeverities = new Set(["Critical", "High", "Medium", "Low", "Unknown"])
  for (const [key, entry] of Object.entries(checks)) {
    if (!entry || typeof entry !== "object") {
      errors.push(`${key}: invalid entry`)
      continue
    }
    if (entry.id !== key || normalizeCheckId(entry.id) !== key) {
      errors.push(`${key}: entry.id=${entry.id} must match normalized key`)
    }
    if (typeof entry.avdId !== "string" || entry.avdId.length === 0) {
      errors.push(`${key}: missing avdId`)
    }
    if (!validFamilies.has(entry.reportFamily)) {
      errors.push(`${key}: invalid reportFamily=${entry.reportFamily}`)
    }
    if (!Array.isArray(entry.kinds) || entry.kinds.length === 0) {
      errors.push(`${key}: missing kinds`)
    }
    if (!validSeverities.has(entry.severity)) {
      errors.push(`${key}: invalid severity=${entry.severity}`)
    }
    if (typeof entry.title !== "string" || entry.title.length === 0) {
      errors.push(`${key}: missing title`)
    }
    if (entry.kinds?.includes("Node")) {
      if (!Array.isArray(entry.nodeTypes) || entry.nodeTypes.length === 0) {
        errors.push(`${key}: Node check missing nodeTypes`)
      }
    }
    if (entry.aliases !== undefined) {
      if (!Array.isArray(entry.aliases) || entry.aliases.some((a) => typeof a !== "string" || a.trim().length === 0)) {
        errors.push(`${key}: aliases must be an array of non-empty strings`)
      }
    }
    if (typeof entry.legacyId === "string" && entry.legacyId.trim().length > 0) {
      if (!Array.isArray(entry.aliases) || !entry.aliases.includes(entry.legacyId.trim().toUpperCase())) {
        errors.push(`${key}: legacyId=${entry.legacyId} must be included in aliases[]`)
      }
    }
  }

  if (digestManifest !== null) {
    if (!digestManifest || typeof digestManifest !== "object") {
      errors.push("catalogue digest manifest must be a JSON object")
    } else {
      if (digestManifest.algorithm !== "sha256") {
        errors.push(`digest manifest algorithm=${digestManifest.algorithm} must be "sha256"`)
      }
      const actualDigest = computeCanonicalCatalogueDigest(catalogue)
      if (typeof digestManifest.canonicalDigest !== "string" || !/^[0-9a-f]{64}$/.test(digestManifest.canonicalDigest)) {
        errors.push("digest manifest canonicalDigest must be a 64-hex sha256 string")
      } else if (digestManifest.canonicalDigest !== actualDigest) {
        errors.push(
          `catalogue canonical SHA-256 mismatch (expected ${digestManifest.canonicalDigest}, got ${actualDigest}) — do not hand-edit trivy-check-catalogue.generated.json; regenerate from upstream source`,
        )
      }
      if (digestManifest.checkCount !== keys.length) {
        errors.push(`digest manifest checkCount=${digestManifest.checkCount} != catalogue checks count ${keys.length}`)
      }
      const dSrc = digestManifest.source
      if (!dSrc || typeof dSrc !== "object") {
        errors.push("digest manifest source is missing")
      } else {
        for (const field of ["trivyOperatorVersion", "trivyOperatorChartVersion", "trivyChecksModule", "trivyChecksVersion", "policyMode"]) {
          if (dSrc[field] !== src?.[field]) {
            errors.push(`digest manifest source.${field}=${dSrc[field]} != catalogue source.${field}=${src?.[field]}`)
          }
        }
        if (canonicalJson(dSrc.excludedPaths ?? []) !== canonicalJson(src?.excludedPaths ?? [])) {
          errors.push("digest manifest source.excludedPaths != catalogue source.excludedPaths")
        }
      }
    }
  }
  return errors
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const pins = readRepoPins()

  if (process.argv.includes("--check")) {
    let parsed
    try {
      parsed = JSON.parse(readFileSync(OUT_JSON, "utf8"))
    } catch (err) {
      console.error(`Failed to read ${OUT_JSON}: ${err instanceof Error ? err.message : err}`)
      process.exit(1)
    }
    let digestManifest
    try {
      digestManifest = JSON.parse(readFileSync(OUT_DIGEST_JSON, "utf8"))
    } catch (err) {
      console.error(`Failed to read ${OUT_DIGEST_JSON}: ${err instanceof Error ? err.message : err}`)
      process.exit(1)
    }
    const errors = validateCatalogue(parsed, pins, digestManifest)
    if (errors.length > 0) {
      console.error("portal/src/data/trivy-check-catalogue.generated.json failed integrity or pin verification:")
      for (const e of errors) console.error(`  - ${e}`)
      console.error("Regenerate from upstream source (network): node portal/scripts/gen-trivy-check-catalogue.mjs")
      process.exit(1)
    }
    console.log(
      `trivy-check-catalogue.generated.json is in sync (trivy-operator ${pins.trivyOperatorVersion}, chart ${pins.trivyOperatorChartVersion}, trivy-checks ${pins.trivyChecksVersion}, ${Object.keys(parsed.checks).length} checks, sha256 ${digestManifest.canonicalDigest.slice(0, 12)}…)`,
    )
    process.exit(0)
  }

  if (!pins.trivyChecksVersion) {
    console.error(
      `No trivy-checks version mapped for trivy-operator ${pins.trivyOperatorVersion}. Update PINNED_OPERATOR_TO_CHECKS first.`,
    )
    process.exit(1)
  }

  const fromZipIdx = process.argv.indexOf("--from-zip")
  let zipPath = fromZipIdx >= 0 ? process.argv[fromZipIdx + 1] : null
  let tempDir = null
  try {
    if (!zipPath) {
      tempDir = mkdtempSync(join(tmpdir(), "trivy-checks-"))
      zipPath = join(tempDir, `trivy-checks-${pins.trivyChecksVersion}.zip`)
      const url = `https://proxy.golang.org/github.com/aquasecurity/trivy-checks/@v/${pins.trivyChecksVersion}.zip`
      execFileSync("curl", ["-sfL", url, "-o", zipPath], { stdio: "inherit" })
    }
    const catalogue = generateFromZip(zipPath, pins)
    const digestManifest = buildDigestManifest(catalogue)
    const errors = validateCatalogue(catalogue, pins, digestManifest)
    if (errors.length > 0) {
      console.error("Generated catalogue failed validation:")
      for (const e of errors) console.error(`  - ${e}`)
      process.exit(1)
    }
    writeFileSync(OUT_JSON, JSON.stringify(catalogue, null, 2) + "\n")
    writeFileSync(OUT_DIGEST_JSON, JSON.stringify(digestManifest, null, 2) + "\n")
    console.log(
      `wrote ${OUT_JSON} and ${OUT_DIGEST_JSON} (${Object.keys(catalogue.checks).length} checks, sha256 ${digestManifest.canonicalDigest})`,
    )
  } finally {
    if (tempDir) rmSync(tempDir, { recursive: true, force: true })
  }
}
