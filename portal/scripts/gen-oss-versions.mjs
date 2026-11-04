#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
//
// Generates src/data/oss-versions.generated.json from the repo-root VERSIONS.md (the declared
// platform versions that the "OSS 업데이트" settings tab shows). The portal image is built from
// portal/ alone, so VERSIONS.md is not readable at runtime; the JSON is committed instead and this
// script is the sync check:  node portal/scripts/gen-oss-versions.mjs --check
// (run by .github/workflows/version-check.yml, which already triggers on VERSIONS.md changes).
//
// Parsing mirrors scripts/ci/check-version-consistency.sh: the FIRST version token of the
// "Version" cell is the declared version. A `chart X.Y.Z` token, when present, is the Helm chart
// version (what an Argo CD Application's targetRevision carries).

import { readFileSync, writeFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const here = dirname(fileURLToPath(import.meta.url))
export const VERSIONS_MD = resolve(here, "../../VERSIONS.md")
export const OUT_JSON = resolve(here, "../src/data/oss-versions.generated.json")

const VERSION_RE = /v?(\d+\.\d+(?:\.\d+)?)/
const CHART_RE = /chart\s+v?(\d+\.\d+(?:\.\d+)?)|v?(\d+\.\d+(?:\.\d+)?)\s*\(chart\)/

export function parseVersionsMd(text) {
  const rows = []
  let section = ""
  for (const line of text.split("\n")) {
    const heading = /^## (.+)$/.exec(line)
    if (heading) {
      section = heading[1].trim()
      // Frozen / compatibility / policy sections repeat component names with prose, not pins.
      if (/^(Frozen|Version Update Policy|Compatibility Matrix)/.test(section)) break
      continue
    }
    if (!line.startsWith("|")) continue
    const cells = line.split("|").map((c) => c.trim())
    const [, component, versionCell] = cells
    if (!component || !versionCell || component === "Component" || /^-+$/.test(component)) continue
    const v = VERSION_RE.exec(versionCell)
    if (!v) continue
    const chart = CHART_RE.exec(versionCell)
    rows.push({ component, section, version: v[1], chartVersion: chart ? (chart[1] ?? chart[2]) : null })
  }
  return { source: "VERSIONS.md", rows }
}

export function renderJson(text) {
  return JSON.stringify(parseVersionsMd(text), null, 2) + "\n"
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const next = renderJson(readFileSync(VERSIONS_MD, "utf8"))
  if (process.argv.includes("--check")) {
    let current = ""
    try {
      current = readFileSync(OUT_JSON, "utf8")
    } catch {
      // missing file == out of sync
    }
    if (current !== next) {
      console.error("portal/src/data/oss-versions.generated.json is out of sync with VERSIONS.md.")
      console.error("Run: node portal/scripts/gen-oss-versions.mjs   (from the repo root) and commit the result.")
      process.exit(1)
    }
    console.log("oss-versions.generated.json is in sync with VERSIONS.md")
  } else {
    writeFileSync(OUT_JSON, next)
    console.log(`wrote ${OUT_JSON}`)
  }
}
