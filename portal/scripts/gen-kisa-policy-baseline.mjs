#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
//
// Generates portal/src/data/kisa-policy-baseline.json from the repo's own
// GitOps Kyverno ClusterPolicy manifest (gitops/resources/kyverno-policies.yaml).
//
// Usage:
//   node portal/scripts/gen-kisa-policy-baseline.mjs
//   node portal/scripts/gen-kisa-policy-baseline.mjs --check

import { readFileSync, writeFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import yaml from "js-yaml"
import {
  KYVERNO_ADMISSION_DEFAULTS,
  canonicalJson,
  computeKyvernoSpecDigest,
  specHasOverrides,
} from "../src/lib/kisa-policy-canonical.mjs"

const here = dirname(fileURLToPath(import.meta.url))
export const REPO_ROOT = resolve(here, "../..")
export const MANIFEST_PATH = resolve(REPO_ROOT, "gitops/resources/kyverno-policies.yaml")
export const OUT_JSON = resolve(here, "../src/data/kisa-policy-baseline.json")

export const IMG02_POLICY_ROLES = {
  "disallow-latest-tag": "latest-tag",
  "verify-image-signatures": "verify-images",
}

function isGlobalImageScopePattern(pattern) {
  return pattern === "*" || pattern === "*:*" || pattern === "*/*" || pattern === "*/*:*"
}

export function buildPolicyBaselineFromManifest(manifestText) {
  const docs = yaml
    .loadAll(manifestText)
    .filter((d) => d && typeof d === "object" && d.kind === "ClusterPolicy")

  const byName = new Map()
  for (const doc of docs) {
    const name = doc?.metadata?.name
    if (typeof name === "string" && name in IMG02_POLICY_ROLES) {
      if (byName.has(name)) {
        throw new Error(`Duplicate ClusterPolicy ${name} in ${MANIFEST_PATH}`)
      }
      byName.set(name, doc)
    }
  }

  const policies = {}
  for (const [name, role] of Object.entries(IMG02_POLICY_ROLES)) {
    const doc = byName.get(name)
    if (!doc) {
      throw new Error(`Missing required ClusterPolicy ${name} in ${MANIFEST_PATH}`)
    }
    const spec = doc.spec ?? {}
    const action = spec.validationFailureAction ?? "Audit"
    const hasOverrides = specHasOverrides(spec)
    const specSha256 = computeKyvernoSpecDigest(spec)
    const knownLimitations = []
    if (action !== "Enforce") {
      knownLimitations.push(`validationFailureAction=${action}`)
    }
    if (hasOverrides) {
      knownLimitations.push("policy_or_rule_overrides_present")
    }

    if (role === "verify-images") {
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
        role,
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
        role,
        specSha256,
        validationFailureAction: action,
        hasOverrides,
        enforceEligible: action === "Enforce" && !hasOverrides,
        knownLimitations,
      }
    }
  }

  return {
    schemaVersion: 1,
    sourceManifest: "gitops/resources/kyverno-policies.yaml",
    normalizedOutServerDefaults: [...KYVERNO_ADMISSION_DEFAULTS],
    policies,
  }
}

export function validateBaseline(actual, expected) {
  const errors = []
  if (!actual || typeof actual !== "object") {
    return ["baseline must be a JSON object"]
  }
  if (canonicalJson(actual) !== canonicalJson(expected)) {
    errors.push("kisa-policy-baseline.json is out of sync with gitops/resources/kyverno-policies.yaml")
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
  return errors
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const manifestText = readFileSync(MANIFEST_PATH, "utf8")
  const expected = buildPolicyBaselineFromManifest(manifestText)

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
      `kisa-policy-baseline.json is in sync (${Object.keys(actual.policies).length} policies: ${Object.entries(actual.policies).map(([k, v]) => `${k}=${v.specSha256.slice(0, 12)}`).join(", ")})`,
    )
    process.exit(0)
  }

  writeFileSync(OUT_JSON, JSON.stringify(expected, null, 2) + "\n")
  console.log(`wrote ${OUT_JSON}`)
}
