// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2025 dasomel
//
// Markdown fragments emitted by generate-third-party-notices.mjs.
//
// WHY A SEPARATE MODULE: the generator runs `pnpm licenses` at import time, so its
// emitters cannot be unit-tested in place. The generated file is gated by narwhal's
// `Markdown changed lines` job, which lints every line a dependency bump touches
// (MD034 bare URL, MD040 fence without language), so the emitters must stay lint-clean.

const ABSOLUTE_URL = /^[a-z][a-z0-9+.-]*:\/\/[^\s<>]+$/i

/** Wrap an absolute URL as an autolink (MD034); anything else is passed through verbatim. */
export function autolink(value) {
  return ABSOLUTE_URL.test(value) ? `<${value}>` : value
}

/**
 * One <details> block holding a license/notice file. The text is untouched except
 * that nested ``` are rewritten to ''' so they cannot close the fence early; the
 * fence carries `text` (MD040).
 */
export function legalFileBlock(file, text) {
  return [
    `<details><summary>${file}</summary>`,
    "",
    "```text",
    text.replace(/```/g, "'''"),
    "```",
    "",
    "</details>",
    "",
  ]
}
