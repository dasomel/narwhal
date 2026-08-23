#!/usr/bin/env python3
"""Enforce the allowed/forbidden license policy over component-licenses.tsv (narwhal#53).

The tsv already carries a resolved SPDX id for every image in the bundle — what was
missing was a CI gate that actually blocks a merge when a new row's license is
forbidden, blank, or unrecognized. Until now that was enforced by a human reading
the file's own comments (e.g. the RSALv2/SSPLv1 redis note: "regressed — fix the
swap, do not add the row") rather than by anything that runs on every PR.

Policy source: the licenses already resolved in this file's own header comments
(the AGPL-3.0/GPL-2.0/MPL-2.0 rows and their documented redistribution obligations)
are ALLOWED — this project already accepted those obligations. RSALv2 and SSPLv1
are explicitly FORBIDDEN: they are source-available, not OSI open source, and the
file's own history records removing an image that carried one (redis -> valkey).
Anything else — blank, NOASSERTION, or a license this policy has no opinion on yet
— fails closed as "needs review", per the file's own principle that a wrong or
absent answer here is worse than admitting "not yet resolved".
"""
import sys

DEFAULT_PATH = "scripts/airgap/lib/component-licenses.tsv"

ALLOWED = {
    "Apache-2.0",
    "MIT",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "MPL-2.0",
    "GPL-2.0-only",
    "GPL-2.0-or-later",
    "AGPL-3.0",
    "curl",
}

# Source-available licenses this project has explicitly rejected — see the redis ->
# valkey swap this file's header documents. Listed by name (not just "not in ALLOWED")
# so a hit here gets a specific, actionable message instead of a generic "unknown".
FORBIDDEN = {
    "RSALv2",
    "SSPLv1",
    "SSPL-1.0",
    "BUSL-1.1",
    "Elastic-2.0",
}


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    problems: list[str] = []
    rows_checked = 0

    with open(path) as f:
        for lineno, raw_line in enumerate(f, 1):
            line = raw_line.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            cols = line.split("\t")
            if len(cols) < 3:
                problems.append(f"{path}:{lineno}: expected at least 3 tab-separated columns, got {len(cols)}")
                continue
            image, _upstream, license = cols[0], cols[1], cols[2].strip()
            rows_checked += 1

            if not license:
                problems.append(f"{path}:{lineno}: {image} — license is blank")
            elif license in FORBIDDEN:
                problems.append(
                    f"{path}:{lineno}: {image} — license '{license}' is on the forbidden list "
                    "(source-available, not OSI open source — see this file's header)"
                )
            elif license not in ALLOWED:
                problems.append(
                    f"{path}:{lineno}: {image} — license '{license}' is not in the allowed policy "
                    "list; add it to ALLOWED in check-license-policy.py after review, or fix the row"
                )

    if rows_checked == 0:
        problems.append(f"{path}: no component rows found — check the path or the file format")

    for p in problems:
        print(p, file=sys.stderr)
    if not problems:
        print(f"{path}: {rows_checked} component license(s) OK", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
