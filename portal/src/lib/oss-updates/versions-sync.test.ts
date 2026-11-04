import { readFileSync } from "node:fs"
import { describe, expect, it } from "vitest"
import declared from "@/data/oss-versions.generated.json"
import { OSS_COMPONENTS } from "./catalog"
import { parseVersionsMd, renderJson, VERSIONS_MD } from "../../../scripts/gen-oss-versions.mjs"

describe("VERSIONS.md -> generated JSON", () => {
  it("parses first version token and chart version like check-version-consistency.sh", () => {
    const md = [
      "## Networking", "| Component | Version | Description |", "|---|---|---|",
      "| APISIX | 3.15.0 (app) / 2.13.0 (chart) | gateway |",
      "| Harbor | v2.15.1 (chart 1.19.1) — pinned | registry |",
      "| NFS Server | apt package | no version |",
      "## Frozen this cycle", "| APISIX | 9.9.9 | must be ignored |",
    ].join("\n")
    expect(parseVersionsMd(md).rows).toEqual([
      { component: "APISIX", section: "Networking", version: "3.15.0", chartVersion: "2.13.0" },
      { component: "Harbor", section: "Networking", version: "2.15.1", chartVersion: "1.19.1" },
    ])
  })

  it("committed JSON is in sync with ../VERSIONS.md (run: node scripts/gen-oss-versions.mjs)", () => {
    expect(readFileSync(VERSIONS_MD, "utf8").length).toBeGreaterThan(0)
    expect(JSON.stringify(declared, null, 2) + "\n").toBe(renderJson(readFileSync(VERSIONS_MD, "utf8")))
  })

  it("every catalog component has a declared VERSIONS.md row and no duplicates", () => {
    const names = new Set((declared.rows as { component: string }[]).map((r) => r.component))
    const missing = OSS_COMPONENTS.filter((c) => !names.has(c.name)).map((c) => c.name)
    expect(missing).toEqual([])
    expect(new Set(OSS_COMPONENTS.map((c) => c.name)).size).toBe(OSS_COMPONENTS.length)
  })
})
