// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2025 dasomel
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { test } from "node:test"

import { autolink, legalFileBlock } from "./notices-format.mjs"

test("autolink wraps absolute URLs and leaves everything else alone", () => {
  assert.equal(autolink("https://authjs.dev"), "<https://authjs.dev>")
  assert.equal(autolink("https://github.com/a/b#readme"), "<https://github.com/a/b#readme>")
  assert.equal(autolink("github.com/a/b"), "github.com/a/b")
  assert.equal(autolink("https://a.dev/with space"), "https://a.dev/with space")
  assert.equal(autolink("git+https://github.com/a/b.git"), "<git+https://github.com/a/b.git>")
  // untrusted metadata: non-web schemes are never turned into autolinks
  assert.equal(autolink("javascript://a%0Aalert(1)"), "javascript://a%0Aalert(1)")
  assert.equal(autolink("data://text/html,x"), "data://text/html,x")
})

test("legalFileBlock opens with a tagged fence and keeps nested fences from closing it", () => {
  const block = legalFileBlock("LICENSE", "a\n```js\nb")
  assert.equal(block[2], "```text")
  assert.equal(block[3], "a\n'''js\nb")
  assert.equal(block[4], "```")
})

test("legalFileBlock expands hard tabs so the generated file stays MD010-clean", () => {
  assert.equal(legalFileBlock("LICENSE", "a\n\thttp://x")[3], "a\n    http://x")
})

test("committed THIRD-PARTY-NOTICES.md has no bare Homepage URLs and no untagged fences", () => {
  const lines = readFileSync(new URL("../THIRD-PARTY-NOTICES.md", import.meta.url), "utf8").split("\n")
  let inFence = false
  for (const [i, line] of lines.entries()) {
    if (!inFence && /^- Homepage: https?:\/\//.test(line)) assert.fail(`bare URL at line ${i + 1}`)
    if (line.startsWith("```")) {
      if (!inFence) assert.match(line, /^```\w+$/, `untagged fence at line ${i + 1}`)
      inFence = !inFence
    }
  }
})
