#!/usr/bin/env python3
"""Assert fetch-security-db.sh fails closed when a downloaded security-db artifact's
digest does not match what the registry reported before the copy (narwhal#48).

Unlike binary-checksums.tsv (a pinned golden digest checked on every future fetch),
security-db artifacts are legitimately re-published under the same tag, so the only
integrity check available is "does what landed on disk match what the registry said
it was sending, right now" — this asserts that comparison exists AND that a mismatch
is treated as a failure (increments the fail counter and `continue`s past recording
the artifact), not logged and ignored. The negative case in regression-check-kakao.sh
runs this against a mutated temp copy with the comparison's failure branch removed,
never the real file.
"""
import re
import sys

DEFAULT_PATH = "scripts/airgap/lib/fetch-security-db.sh"

# The guard: an `if [[ "post" != "pre" ]]` (or equivalent) block whose body both
# prints a FAIL and increments fail — i.e. a real fail-closed branch, not just a
# variable comparison with no consequence.
GUARD_RE = re.compile(
    r'if\s*\[\[\s*"\$\{post_digest\}"\s*!=\s*"\$\{pre_digest\}"\s*\]\];\s*then'
    r'(?:(?!\bfi\b).)*?\bfail=\$\(\(fail \+ 1\)\)',
    re.DOTALL,
)


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    with open(path) as f:
        text = f.read()

    if not GUARD_RE.search(text):
        print(
            f"VIOLATION: {path} has no fail-closed branch comparing post_digest against "
            "pre_digest (digest-mismatch guard missing or defanged)",
            file=sys.stderr,
        )
        return 1

    print(f"OK: fetch-security-db.sh fails closed on a digest mismatch ({path})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
