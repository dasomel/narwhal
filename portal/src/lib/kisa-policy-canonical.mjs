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

export function computeKyvernoSpecDigest(spec) {
  const canonical = canonicalizeKyvernoPolicySpec(spec)
  return createHash("sha256").update(canonicalJson(canonical), "utf8").digest("hex")
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
