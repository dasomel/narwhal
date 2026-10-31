#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2025 dasomel
# =============================================================================
# harness-rules.sh — make the countable AGENTS.md "Verify" rules actually block.
#
# These rules were prose only, and the codebase already violates them (i18n debt
# is even acknowledged in-code, see the TODO at the top of service-map-view.tsx).
# Cleaning all of it up is a separate project, so this is a RATCHET: the current
# counts are recorded as baselines and CI fails when a count goes UP. Going down
# is fine — lower the baseline in the same commit that pays the debt off.
#
# Usage: scripts/harness-rules.sh      (exit 1 = a new violation was added)
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

# --- baselines (2026-07-25) --------------------------------------------------
BASE_INLINE_STYLE=47
# 2026-10-31: 48 -> 66. CI counted 0 (see D-KO-COUNT), so 18 hardcoded strings got in
# unchecked; 66 is the real count on main 64ed65da (macOS BSD grep and the perl count agree).
# #512: the 66 were almost all Korean comments (the old filter only skipped lines that start
# with a comment marker); the 10 real UI strings moved to i18n.ts, so the true count is 0.
BASE_HARDCODED_KO=0

fail=0

check() {
  local name=$1 cur=$2 base=$3
  if [ "$cur" -gt "$base" ]; then
    printf 'FAIL  %-34s %s (baseline %s) — new violations added\n' "$name" "$cur" "$base"
    fail=1
  elif [ "$cur" -lt "$base" ]; then
    printf 'OK    %-34s %s (baseline %s) — improved; lower the baseline here\n' "$name" "$cur" "$base"
  else
    printf 'OK    %-34s %s\n' "$name" "$cur"
  fi
}

# Tailwind-first: inline styles are allowed only for runtime-computed values
# (a colour from data, a width from a percentage). Static ones belong in classes.
inline_style=$({ grep -ro 'style={{' src --include='*.tsx' || true; } | wc -l | tr -d ' ')

# i18n: UI text goes through src/lib/i18n.ts. Korean in comments/JSDoc is fine,
# so comment-leading lines are excluded.
# D-KO-COUNT: match Hangul syllables by code point (U+AC00-U+D7A3) in perl, not with a
# grep bracket range. GNU grep under C.UTF-8 (the GitHub runner) rejects '[가-힣]' with
# "Invalid collation character", and the old `2>/dev/null ... || true` turned that error
# into a count of 0, so CI never enforced this rule; under LANG=C it matched bytes (339).
# BSD grep on macOS counted correctly, so the number only differed in CI. A perl error
# now fails the script (pipefail) instead of reading as "no violations".
# D-KO-COMMENT: strip comment text (block comments across lines, `{/* */}` JSX comments, and
# trailing ` // ...`) before testing for Hangul, so a Korean comment is not counted as UI text.
# A marker only opens a comment at line start or after whitespace, `{`, `(`, `,` or `;`, so
# `https://`, `a//b` and globs such as `src/**/*.tsx` inside strings do not hide what follows.
# Line-prefix filtering missed `{/*` and continuation lines, which made comments ~80% of the
# count. Test files (*.test.tsx) assert Korean rendering on purpose and are not scanned.
hardcoded_ko=$(find src -name '*.tsx' -not -name '*.test.tsx' -type f -print0 | sort -z \
  | xargs -0 perl -CSD -ne '
      my $l = $_; my $o = "";
      while (length $l) {
        if ($in) { if ($l =~ s{^.*?\*/}{}s) { $in = 0 } else { $l = "" } next }
        if ($l =~ s#^(.*?(?:^|[\s{(,;]))(/\*|//)##s) {
          $o .= $1;
          if ($2 eq "//") { $l = "" } else { $in = 1 }
        } else { $o .= $l; $l = "" }
      }
      print "$ARGV:$.:$_" if $o =~ /[\x{AC00}-\x{D7A3}]/;
      if (eof) { close ARGV; $in = 0 }' \
  | { grep -v 'i18n' || true; } | wc -l | tr -d ' ')

check "inline styles (style={{)" "$inline_style" "$BASE_INLINE_STYLE"
check "hardcoded Korean (non-comment)" "$hardcoded_ko" "$BASE_HARDCODED_KO"

# Not counted here: "don't hand-edit src/components/ui/". Adding a component via
# `npx shadcn@latest add` legitimately writes to that directory, so a diff-based
# check cannot tell a regeneration from a hand-edit. It stays a review-time rule.

exit "$fail"
