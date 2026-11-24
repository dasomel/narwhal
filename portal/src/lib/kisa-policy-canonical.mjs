import { createHash } from "node:crypto"

export const KYVERNO_ADMISSION_DEFAULTS = [
  "spec.admission (when true)",
  "spec.emitWarning (when false)",
  "spec.rules[].skipBackgroundRequests (when true)",
  "spec.rules[].validate.allowExistingViolations (when true)",
  "spec.rules[].verifyImages[].useCache (when true)",
  "spec.rules[].verifyImages[].attestors[].entries[].signatureAlgorithm (when sha256)",
  "spec.rules[].verifyImages[].attestors[].entries[].keys.signatureAlgorithm (when sha256)",
  "spec.rules[].verifyImages[].attestations[].attestors[].entries[].signatureAlgorithm (when sha256)",
  "spec.rules[].verifyImages[].attestations[].attestors[].entries[].keys.signatureAlgorithm (when sha256)",
]

export const NETWORK_POLICY_DEFAULTS = [
  "NetworkPolicy spec.ingress (when empty array [])",
  "NetworkPolicy spec.egress (when empty array [])",
  "CiliumNetworkPolicy/CiliumClusterwideNetworkPolicy specs (when empty array [])",
]

/**
 * Explicit allow-list of non-semantic top-level Kubernetes object fields omitted from
 * canonical policy digests. Every other top-level field (known policy-bearing fields
 * and any unknown future field) is hashed so nothing outside metadata/status/type
 * metadata can be silently ignored.
 */
export const NON_SEMANTIC_TOP_LEVEL_FIELDS = [
  "apiVersion",
  "kind",
  "metadata",
  "status",
]

const NON_SEMANTIC_TOP_LEVEL_SET = new Set(NON_SEMANTIC_TOP_LEVEL_FIELDS)

export const POLICY_TOP_LEVEL_ALLOWLIST_BY_KIND = {
  NetworkPolicy: {
    nonSemantic: NON_SEMANTIC_TOP_LEVEL_FIELDS,
    knownPolicyFields: ["spec"],
  },
  CiliumNetworkPolicy: {
    nonSemantic: NON_SEMANTIC_TOP_LEVEL_FIELDS,
    knownPolicyFields: ["spec", "specs"],
  },
  CiliumClusterwideNetworkPolicy: {
    nonSemantic: NON_SEMANTIC_TOP_LEVEL_FIELDS,
    knownPolicyFields: ["spec", "specs"],
  },
  ClusterPolicy: {
    nonSemantic: NON_SEMANTIC_TOP_LEVEL_FIELDS,
    knownPolicyFields: ["spec"],
  },
  Policy: {
    nonSemantic: NON_SEMANTIC_TOP_LEVEL_FIELDS,
    knownPolicyFields: ["spec"],
  },
}

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

/** Deterministic JSON serialization with lexicographically sorted keys and undefined omitted. */
export function canonicalJson(value) {
  if (value === null || typeof value !== "object") {
    return JSON.stringify(value)
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => (item === undefined ? "null" : canonicalJson(item))).join(",")}]`
  }
  const keys = Object.keys(value).sort()
  const parts = []
  for (const k of keys) {
    if (value[k] === undefined) continue
    parts.push(`${JSON.stringify(k)}:${canonicalJson(value[k])}`)
  }
  return `{${parts.join(",")}}`
}

function normalizeAttestorEntries(entries) {
  if (!Array.isArray(entries)) return entries
  return entries.map((entry) => {
    if (!isRecord(entry)) return entry
    const out = { ...entry }
    if (out.signatureAlgorithm === "sha256") delete out.signatureAlgorithm
    if (isRecord(out.keys)) {
      const keysOut = { ...out.keys }
      if (keysOut.signatureAlgorithm === "sha256") delete keysOut.signatureAlgorithm
      out.keys = keysOut
    }
    return out
  })
}

function normalizeAttestorList(attestors) {
  if (!Array.isArray(attestors)) return attestors
  return attestors.map((att) => {
    if (!isRecord(att)) return att
    const out = { ...att }
    if (Array.isArray(out.entries)) {
      out.entries = normalizeAttestorEntries(out.entries)
    }
    return out
  })
}

function normalizeVerifyImage(vi) {
  if (!isRecord(vi)) return vi
  const out = { ...vi }
  if (out.useCache === true) delete out.useCache
  if (Array.isArray(out.attestors)) {
    out.attestors = normalizeAttestorList(out.attestors)
  }
  if (Array.isArray(out.attestations)) {
    out.attestations = out.attestations.map((att) => {
      if (!isRecord(att)) return att
      const attOut = { ...att }
      if (Array.isArray(attOut.attestors)) {
        attOut.attestors = normalizeAttestorList(attOut.attestors)
      }
      return attOut
    })
  }
  return out
}

function normalizeRule(rule) {
  if (!isRecord(rule)) return rule
  const out = { ...rule }
  if (out.skipBackgroundRequests === true) delete out.skipBackgroundRequests
  if (isRecord(out.validate)) {
    const valOut = { ...out.validate }
    if (valOut.allowExistingViolations === true) delete valOut.allowExistingViolations
    out.validate = valOut
  }
  if (Array.isArray(out.verifyImages)) {
    out.verifyImages = out.verifyImages.map(normalizeVerifyImage)
  }
  return out
}

/**
 * Strips ONLY documented Kyverno 1.18 admission defaults when equal to their benign
 * defaults, preserving every security-relevant field (match/exclude/preconditions/
 * validate/verifyImages/failureAction/overrides/background/admission=false).
 */
export function canonicalizeKyvernoPolicySpec(spec) {
  if (!isRecord(spec)) return {}
  const out = { ...spec }
  if (out.admission === true) delete out.admission
  if (out.emitWarning === false) delete out.emitWarning
  if (Array.isArray(out.rules)) {
    out.rules = out.rules.map(normalizeRule)
  }
  return JSON.parse(canonicalJson(out))
}

/**
 * Strips ONLY Kubernetes networking.k8s.io/v1 NetworkPolicySpec Go `omitempty`
 * empty slices (`spec.ingress: []`, `spec.egress: []`), which the API server omits
 * on readback while preserving identical NetworkPolicy semantics. Every non-empty
 * rule, selector, port, policyTypes, and CiliumNetworkPolicy field is preserved.
 */
export function canonicalizeNetworkPolicySpec(kind, spec) {
  if (!isRecord(spec)) return {}
  const out = { ...spec }
  if (kind === "NetworkPolicy") {
    if (Array.isArray(out.ingress) && out.ingress.length === 0) delete out.ingress
    if (Array.isArray(out.egress) && out.egress.length === 0) delete out.egress
  }
  return JSON.parse(canonicalJson(out))
}

/**
 * Canonicalizes ALL policy-bearing top-level fields of a Kubernetes policy object,
 * hashing every top-level key outside `NON_SEMANTIC_TOP_LEVEL_SET`
 * (`apiVersion`, `kind`, `metadata`, `status`).
 *
 * - NetworkPolicy: hashes `{ spec, ...anyUnknownTopLevelFields }` (canonicalizing `spec`).
 * - CiliumNetworkPolicy / CiliumClusterwideNetworkPolicy: hashes `{ spec?, specs?, ...anyUnknownTopLevelFields }`
 *   (stripping only Go `omitempty` empty `specs: []` or `undefined`/`null` `spec`/`specs`).
 * - ClusterPolicy / Policy: hashes `{ spec, ...anyUnknownTopLevelFields }` (canonicalizing Kyverno `spec`).
 */
export function canonicalizePolicyObject(kind, policyObj) {
  if (!isRecord(policyObj)) return {}
  const out = {}
  for (const key of Object.keys(policyObj)) {
    if (NON_SEMANTIC_TOP_LEVEL_SET.has(key)) continue
    const val = policyObj[key]
    if (val === undefined) continue
    out[key] = val
  }

  if (kind === "NetworkPolicy") {
    out.spec = canonicalizeNetworkPolicySpec("NetworkPolicy", out.spec)
  } else if (kind === "CiliumNetworkPolicy" || kind === "CiliumClusterwideNetworkPolicy") {
    if (out.spec === null || out.spec === undefined) {
      delete out.spec
    } else {
      out.spec = canonicalizeNetworkPolicySpec(kind, out.spec)
    }
    if (out.specs === null || out.specs === undefined || (Array.isArray(out.specs) && out.specs.length === 0)) {
      delete out.specs
    }
  } else if (kind === "ClusterPolicy" || kind === "Policy") {
    out.spec = canonicalizeKyvernoPolicySpec(out.spec)
  }

  return JSON.parse(canonicalJson(out))
}

export function computePolicyDigest(kind, policyObj) {
  const canonical = canonicalizePolicyObject(kind, policyObj)
  return createHash("sha256").update(canonicalJson(canonical), "utf8").digest("hex")
}

export function computeNetworkPolicyDigest(kind, policyObj) {
  return computePolicyDigest(kind, policyObj)
}

export function computeKyvernoPolicyDigest(policyObj) {
  const kind = isRecord(policyObj) && typeof policyObj.kind === "string" ? policyObj.kind : "ClusterPolicy"
  return computePolicyDigest(kind, policyObj)
}

export function computeKyvernoSpecDigest(spec) {
  return computePolicyDigest("ClusterPolicy", { spec: isRecord(spec) ? spec : {} })
}

export function computeNetworkPolicySpecDigest(kind, spec) {
  return computePolicyDigest(kind, { spec: isRecord(spec) ? spec : {} })
}

export function specHasOverrides(spec) {
  if (!isRecord(spec)) return false
  if (Array.isArray(spec.validationFailureActionOverrides) && spec.validationFailureActionOverrides.length > 0) {
    return true
  }
  if (Array.isArray(spec.failureActionOverrides) && spec.failureActionOverrides.length > 0) {
    return true
  }
  for (const rule of Array.isArray(spec.rules) ? spec.rules : []) {
    if (!isRecord(rule)) continue
    if (
      rule.failureAction !== undefined ||
      rule.validationFailureAction !== undefined ||
      (Array.isArray(rule.validationActions) && rule.validationActions.length > 0)
    ) {
      return true
    }
    if (isRecord(rule.validate)) {
      const v = rule.validate
      if (
        v.failureAction !== undefined ||
        v.validationFailureAction !== undefined ||
        (Array.isArray(v.validationActions) && v.validationActions.length > 0) ||
        (Array.isArray(v.failureActionOverrides) && v.failureActionOverrides.length > 0)
      ) {
        return true
      }
    }
    for (const vi of Array.isArray(rule.verifyImages) ? rule.verifyImages : []) {
      if (!isRecord(vi)) continue
      if (
        vi.failureAction !== undefined ||
        vi.validationFailureAction !== undefined ||
        (Array.isArray(vi.validationActions) && vi.validationActions.length > 0) ||
        (Array.isArray(vi.failureActionOverrides) && vi.failureActionOverrides.length > 0)
      ) {
        return true
      }
    }
  }
  return false
}
