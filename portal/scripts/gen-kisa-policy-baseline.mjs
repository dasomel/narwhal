#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
//
// Generates portal/src/data/kisa-policy-baseline.json from the repo's own
// GitOps Kyverno ClusterPolicy manifest (gitops/resources/kyverno-policies.yaml)
// and reviewed NetworkPolicy / CiliumNetworkPolicy / CiliumClusterwideNetworkPolicy
// manifests under gitops/.
//
// Usage:
//   node portal/scripts/gen-kisa-policy-baseline.mjs
//   node portal/scripts/gen-kisa-policy-baseline.mjs --check

import { execFileSync } from "node:child_process"
import { readdirSync, readFileSync, writeFileSync } from "node:fs"
import { dirname, relative, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import {
  KYVERNO_ADMISSION_DEFAULTS,
  NETWORK_POLICY_DEFAULTS,
  canonicalJson,
  computeKyvernoPolicyDigest,
  computeNetworkPolicyDigest,
  specHasOverrides,
} from "../src/lib/kisa-policy-canonical.mjs"

const here = dirname(fileURLToPath(import.meta.url))
export const REPO_ROOT = resolve(here, "../..")
export const MANIFEST_PATH = resolve(REPO_ROOT, "gitops/resources/kyverno-policies.yaml")
export const RESOURCES_DIR = resolve(REPO_ROOT, "gitops/resources")
export const PLATFORM_TEMPLATES_DIR = resolve(REPO_ROOT, "gitops/charts/narwhal-platform/templates")
export const OUT_JSON = resolve(here, "../src/data/kisa-policy-baseline.json")

export const IMG02_POLICY_ROLES = {
  "disallow-latest-tag": "latest-tag",
  "verify-image-signatures": "verify-images",
}

export const NET01_DEFAULT_DENY_POLICIES = {
  database: "NetworkPolicy/database/database-default-deny-ingress",
  devtools: null,
  iam: "NetworkPolicy/iam/iam-default-deny-ingress",
  monitoring: "NetworkPolicy/monitoring/monitoring-default-deny-ingress",
  storage: "NetworkPolicy/storage/storage-default-deny-ingress",
}

export const ETCD02_POLICY_KEY = "NetworkPolicy/platform-system/apisix-etcd-ingress-policy"
export const ETCD02_WORKLOAD = {
  kind: "Deployment",
  namespace: "platform-system",
  name: "apisix-etcd",
}

const NAMESPACED_NETWORK_POLICY_KINDS = new Set(["NetworkPolicy", "CiliumNetworkPolicy"])
const CLUSTERWIDE_NETWORK_POLICY_KINDS = new Set(["CiliumClusterwideNetworkPolicy"])
const ALL_NETWORK_POLICY_KINDS = new Set([
  ...NAMESPACED_NETWORK_POLICY_KINDS,
  ...CLUSTERWIDE_NETWORK_POLICY_KINDS,
])

function isGlobalImageScopePattern(pattern) {
  return pattern === "*" || pattern === "*:*" || pattern === "*/*" || pattern === "*/*:*"
}

function isEmptyPodSelector(podSelector) {
  if (!podSelector || typeof podSelector !== "object" || Array.isArray(podSelector)) return false
  const allowedKeys = new Set(["matchLabels", "matchExpressions"])
  for (const key of Object.keys(podSelector)) {
    if (!allowedKeys.has(key)) return false
  }
  const matchLabels = podSelector.matchLabels ?? {}
  const matchExpressions = podSelector.matchExpressions ?? []
  if (typeof matchLabels !== "object" || Array.isArray(matchLabels)) return false
  if (!Array.isArray(matchExpressions)) return false
  return Object.keys(matchLabels).length === 0 && matchExpressions.length === 0
}

export function loadRepoNetworkPolicySources(repoRoot = REPO_ROOT) {
  const sources = []
  const resourcesDir = resolve(repoRoot, "gitops/resources")
  for (const name of readdirSync(resourcesDir).filter((f) => f.endsWith(".yaml")).sort()) {
    const absPath = resolve(resourcesDir, name)
    const relPath = relative(repoRoot, absPath).replace(/\\/g, "/")
    sources.push({ path: relPath, text: readFileSync(absPath, "utf8"), templated: false })
  }
  const portalK8s = resolve(repoRoot, "gitops/charts/narwhal-platform/templates/narwhal-portal-k8s.yaml")
  sources.push({
    path: "gitops/charts/narwhal-platform/templates/narwhal-portal-k8s.yaml",
    text: readFileSync(portalK8s, "utf8"),
    templated: true,
  })
  return sources
}

const PY_PARSE_YAML_BATCH = `
import json, re, sys, yaml
payload = json.load(sys.stdin)
out = []
for item in payload:
    if item.get("templated"):
        docs = []
        for chunk in re.split(r"^---\\s*$", item["text"], flags=re.M):
            if not chunk.strip() or "{{" in chunk:
                continue
            docs.extend(d for d in yaml.safe_load_all(chunk) if isinstance(d, dict))
        out.append(docs)
    else:
        out.append([d for d in yaml.safe_load_all(item["text"]) if isinstance(d, dict)])
print(json.dumps(out, default=str))
`.trim()

function parseYamlSourcesBatch(items) {
  if (items.length === 0) return []
  const out = execFileSync("python3", ["-c", PY_PARSE_YAML_BATCH], {
    input: JSON.stringify(items),
    encoding: "utf8",
    maxBuffer: 16 * 1024 * 1024,
  })
  return JSON.parse(out)
}

function resolveNetworkPolicyDocs(sources) {
  const pendingIdx = []
  const pendingPayload = []
  for (let i = 0; i < sources.length; i++) {
    if (!Array.isArray(sources[i].docs)) {
      pendingIdx.push(i)
      pendingPayload.push({ text: sources[i].text ?? "", templated: Boolean(sources[i].templated) })
    }
  }
  const parsedBatch = parseYamlSourcesBatch(pendingPayload)
  const parsedByIdx = new Map()
  for (let j = 0; j < pendingIdx.length; j++) {
    parsedByIdx.set(pendingIdx[j], parsedBatch[j])
  }
  return sources.map((src, i) => {
    const rawDocs = Array.isArray(src.docs) ? src.docs : (parsedByIdx.get(i) ?? [])
    return {
      path: src.path,
      docs: rawDocs.filter((d) => d && typeof d === "object" && ALL_NETWORK_POLICY_KINDS.has(d.kind)),
    }
  })
}

export function buildNetworkPolicyBaselineFromSources(sources) {
  const entries = new Map()
  const docsByKey = new Map()
  const clusterwideEntries = new Map()
  const contributingFiles = new Set()

  for (const src of resolveNetworkPolicyDocs(sources)) {
    const docs = src.docs
    for (const doc of docs) {
      const kind = doc.kind
      const name = doc?.metadata?.name
      if (typeof name !== "string" || !name) {
        throw new Error(`${src.path}: ${kind} missing metadata.name`)
      }

      if (CLUSTERWIDE_NETWORK_POLICY_KINDS.has(kind)) {
        if (doc?.metadata?.namespace) {
          throw new Error(`${src.path}: ${kind}/${name} is cluster-scoped and must not set metadata.namespace`)
        }
        const key = `${kind}/${name}`
        if (clusterwideEntries.has(key)) {
          throw new Error(`Duplicate clusterwide policy ${key} across ${clusterwideEntries.get(key).sourceManifest} and ${src.path}`)
        }
        contributingFiles.add(src.path)
        clusterwideEntries.set(key, {
          kind,
          namespace: null,
          name,
          sourceManifest: src.path,
          specSha256: computeNetworkPolicyDigest(kind, doc),
        })
        continue
      }

      const namespace = doc?.metadata?.namespace
      if (typeof namespace !== "string" || !namespace) {
        throw new Error(`${src.path}: ${kind}/${name} missing metadata.namespace`)
      }
      const key = `${kind}/${namespace}/${name}`
      if (entries.has(key)) {
        throw new Error(`Duplicate network policy ${key} across ${entries.get(key).sourceManifest} and ${src.path}`)
      }
      contributingFiles.add(src.path)
      docsByKey.set(key, doc)
      entries.set(key, {
        kind,
        namespace,
        name,
        sourceManifest: src.path,
        specSha256: computeNetworkPolicyDigest(kind, doc),
      })
    }
  }

  for (const [ns, key] of Object.entries(NET01_DEFAULT_DENY_POLICIES)) {
    if (key === null) continue
    if (!entries.has(key)) {
      throw new Error(`Missing required NET-01 default-deny baseline policy ${key} for namespace ${ns}`)
    }
    const doc = docsByKey.get(key)
    if (doc?.kind !== "NetworkPolicy" || !isEmptyPodSelector(doc?.spec?.podSelector)) {
      throw new Error(`NET-01 default-deny baseline policy ${key} must be a NetworkPolicy with an empty podSelector ({})`)
    }
    if (!Array.isArray(doc?.spec?.policyTypes) || !doc.spec.policyTypes.includes("Ingress")) {
      throw new Error(`NET-01 default-deny baseline policy ${key} must declare policyTypes: [Ingress]`)
    }
  }
  if (!entries.has(ETCD02_POLICY_KEY)) {
    throw new Error(`Missing required ETCD-02 baseline policy ${ETCD02_POLICY_KEY}`)
  }

  const networkPolicies = {}
  for (const [key, val] of [...entries.entries()].sort(([a], [b]) => a.localeCompare(b))) {
    networkPolicies[key] = val
  }

  const clusterwidePolicies = {}
  for (const [key, val] of [...clusterwideEntries.entries()].sort(([a], [b]) => a.localeCompare(b))) {
    clusterwidePolicies[key] = val
  }

  return {
    networkPolicySourceManifests: [...contributingFiles].sort(),
    net01DefaultDenyByNamespace: { ...NET01_DEFAULT_DENY_POLICIES },
    etcd02PolicyKey: ETCD02_POLICY_KEY,
    etcd02Workload: { ...ETCD02_WORKLOAD },
    networkPolicies,
    clusterwidePolicies,
  }
}

export function buildPolicyBaselineFromParsed(kyvernoDocs, networkSources) {
  const docs = kyvernoDocs.filter((d) => d && typeof d === "object" && d.kind === "ClusterPolicy")

  const byName = new Map()
  for (const doc of docs) {
    const name = doc?.metadata?.name
    if (typeof name === "string" && name.length > 0) {
      if (byName.has(name)) {
        throw new Error(`Duplicate ClusterPolicy ${name} in ${MANIFEST_PATH}`)
      }
      byName.set(name, doc)
    }
  }

  for (const requiredName of Object.keys(IMG02_POLICY_ROLES)) {
    if (!byName.has(requiredName)) {
      throw new Error(`Missing required ClusterPolicy ${requiredName} in ${MANIFEST_PATH}`)
    }
  }

  const policies = {}
  for (const [name, doc] of [...byName.entries()].sort(([a], [b]) => a.localeCompare(b))) {
    const role = IMG02_POLICY_ROLES[name] ?? null
    const spec = doc.spec ?? {}
    const action = spec.validationFailureAction ?? "Audit"
    const hasOverrides = specHasOverrides(spec)
    const specSha256 = computeKyvernoPolicyDigest(doc)
    const hasValidateOrVerifyRules = (spec.rules ?? []).some(
      (r) => Boolean(r?.validate) || (Array.isArray(r?.verifyImages) && r.verifyImages.length > 0),
    )
    const admissionRelevant = hasValidateOrVerifyRules
    const resolvedRole = role ?? (admissionRelevant ? "admission" : "mutate")
    const knownLimitations = []
    if (action !== "Enforce") {
      knownLimitations.push(`validationFailureAction=${action}`)
    }
    if (hasOverrides) {
      knownLimitations.push("policy_or_rule_overrides_present")
    }

    if (resolvedRole === "verify-images") {
      const verifyEntries = (spec.rules ?? []).flatMap((r) => (Array.isArray(r?.verifyImages) ? r.verifyImages : []))
      const imageReferences = [...new Set(verifyEntries.flatMap((vi) => (Array.isArray(vi?.imageReferences) ? vi.imageReferences : [])))]
      const required = verifyEntries.some((vi) => vi?.required === false) ? false : verifyEntries.every((vi) => vi?.required === true)
      if (required === false) {
        knownLimitations.push("verifyImages.required=false")
      }
      if (!imageReferences.some(isGlobalImageScopePattern)) {
        knownLimitations.push("narrow_imageReferences_scope")
      }
      policies[name] = {
        name,
        role: resolvedRole,
        admissionRelevant,
        specSha256,
        validationFailureAction: action,
        hasOverrides,
        imageReferences,
        required,
        enforceEligible:
          action === "Enforce" &&
          !hasOverrides &&
          required !== false &&
          imageReferences.some(isGlobalImageScopePattern),
        knownLimitations,
      }
    } else {
      policies[name] = {
        name,
        role: resolvedRole,
        admissionRelevant,
        specSha256,
        validationFailureAction: action,
        hasOverrides,
        enforceEligible: admissionRelevant && action === "Enforce" && !hasOverrides,
        knownLimitations,
      }
    }
  }

  const netBaseline = buildNetworkPolicyBaselineFromSources(networkSources)

  return {
    schemaVersion: 2,
    sourceManifest: "gitops/resources/kyverno-policies.yaml",
    normalizedOutServerDefaults: [...KYVERNO_ADMISSION_DEFAULTS],
    policies,
    normalizedOutNetworkPolicyDefaults: [...NETWORK_POLICY_DEFAULTS],
    ...netBaseline,
  }
}

export function buildPolicyBaselineFromManifest(manifestText, networkSources = loadRepoNetworkPolicySources()) {
  const batchInputs = [{ text: manifestText, templated: false }]
  const pendingNetIdx = []
  for (let i = 0; i < networkSources.length; i++) {
    if (!Array.isArray(networkSources[i].docs)) {
      pendingNetIdx.push(i)
      batchInputs.push({ text: networkSources[i].text ?? "", templated: Boolean(networkSources[i].templated) })
    }
  }
  const parsedBatch = parseYamlSourcesBatch(batchInputs)
  const kyvernoDocs = parsedBatch[0] ?? []
  const netByIdx = new Map()
  for (let j = 0; j < pendingNetIdx.length; j++) {
    netByIdx.set(pendingNetIdx[j], parsedBatch[j + 1] ?? [])
  }
  const resolvedNetSources = networkSources.map((src, i) =>
    Array.isArray(src.docs) ? src : { ...src, docs: netByIdx.get(i) ?? [] },
  )
  return buildPolicyBaselineFromParsed(kyvernoDocs, resolvedNetSources)
}

export function validateBaseline(actual, expected) {
  const errors = []
  if (!actual || typeof actual !== "object") {
    return ["baseline must be a JSON object"]
  }
  if (canonicalJson(actual) !== canonicalJson(expected)) {
    errors.push("kisa-policy-baseline.json is out of sync with gitops/resources/*.yaml")
  }
  for (const [name, role] of Object.entries(IMG02_POLICY_ROLES)) {
    const entry = actual.policies?.[name]
    if (!entry) {
      errors.push(`missing baseline entry for ${name}`)
      continue
    }
    if (entry.role !== role) {
      errors.push(`${name}: expected role=${role}, got ${entry.role}`)
    }
    if (typeof entry.specSha256 !== "string" || !/^[0-9a-f]{64}$/.test(entry.specSha256)) {
      errors.push(`${name}: invalid specSha256`)
    }
    if (role === "verify-images") {
      const hasGlobal = Array.isArray(entry.imageReferences) && entry.imageReferences.some(isGlobalImageScopePattern)
      const shouldBeEligible =
        entry.validationFailureAction === "Enforce" &&
        entry.hasOverrides === false &&
        entry.required !== false &&
        hasGlobal
      if (entry.enforceEligible !== shouldBeEligible) {
        errors.push(`${name}: enforceEligible=${entry.enforceEligible} inconsistent with policy properties (${shouldBeEligible})`)
      }
    }
  }

  const netPols = actual.networkPolicies ?? {}
  for (const [ns, key] of Object.entries(NET01_DEFAULT_DENY_POLICIES)) {
    if (actual.net01DefaultDenyByNamespace?.[ns] !== key) {
      errors.push(`net01DefaultDenyByNamespace[${ns}] mismatch`)
    }
    if (key !== null) {
      const npEntry = netPols[key]
      if (!npEntry) {
        errors.push(`missing network policy baseline entry ${key}`)
      } else if (typeof npEntry.specSha256 !== "string" || !/^[0-9a-f]{64}$/.test(npEntry.specSha256)) {
        errors.push(`${key}: invalid specSha256`)
      }
    }
  }
  if (actual.etcd02PolicyKey !== ETCD02_POLICY_KEY || !netPols[ETCD02_POLICY_KEY]) {
    errors.push(`missing or mismatched etcd02PolicyKey ${ETCD02_POLICY_KEY}`)
  }
  if (
    actual.etcd02Workload?.kind !== ETCD02_WORKLOAD.kind ||
    actual.etcd02Workload?.namespace !== ETCD02_WORKLOAD.namespace ||
    actual.etcd02Workload?.name !== ETCD02_WORKLOAD.name
  ) {
    errors.push("missing or mismatched etcd02Workload")
  }
  const ccnpPols = actual.clusterwidePolicies ?? {}
  if (Object.keys(ccnpPols).length === 0) {
    errors.push("missing baseline clusterwidePolicies")
  }
  for (const [key, entry] of Object.entries(ccnpPols)) {
    if (typeof entry?.specSha256 !== "string" || !/^[0-9a-f]{64}$/.test(entry.specSha256)) {
      errors.push(`${key}: invalid clusterwidePolicy specSha256`)
    }
  }

  return errors
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const fromJsonIdx = process.argv.indexOf("--from-json")
  let expected
  if (fromJsonIdx >= 0) {
    const jsonArg = process.argv[fromJsonIdx + 1]
    if (!jsonArg) {
      console.error("--from-json requires a file path or '-' for stdin")
      process.exit(1)
    }
    const raw = jsonArg === "-" ? readFileSync(0, "utf8") : readFileSync(jsonArg, "utf8")
    const parsedInput = JSON.parse(raw)
    expected = buildPolicyBaselineFromParsed(parsedInput.kyvernoDocs ?? [], parsedInput.networkSources ?? [])
  } else {
    const manifestText = readFileSync(MANIFEST_PATH, "utf8")
    expected = buildPolicyBaselineFromManifest(manifestText)
  }

  if (process.argv.includes("--check")) {
    let actual
    try {
      actual = JSON.parse(readFileSync(OUT_JSON, "utf8"))
    } catch (err) {
      console.error(`Failed to read ${OUT_JSON}: ${err instanceof Error ? err.message : err}`)
      process.exit(1)
    }
    const errors = validateBaseline(actual, expected)
    if (errors.length > 0) {
      console.error("portal/src/data/kisa-policy-baseline.json failed sync or integrity check:")
      for (const e of errors) console.error(`  - ${e}`)
      console.error("Regenerate via: node portal/scripts/gen-kisa-policy-baseline.mjs")
      process.exit(1)
    }
    console.log(
      `kisa-policy-baseline.json is in sync (${Object.keys(actual.policies).length} Kyverno, ${Object.keys(actual.networkPolicies).length} Network/Cilium policies, ${Object.keys(actual.clusterwidePolicies ?? {}).length} clusterwide policies)`,
    )
    process.exit(0)
  }

  writeFileSync(OUT_JSON, JSON.stringify(expected, null, 2) + "\n")
  console.log(`wrote ${OUT_JSON}`)
}
