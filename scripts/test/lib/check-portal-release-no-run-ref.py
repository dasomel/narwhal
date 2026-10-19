#!/usr/bin/env python3
"""R276 (#395): portal-release.yml must not check out a workflow_run ref, and must prove the tag first.

OpenSSF Scorecard's Dangerous-Workflow check flags `actions/checkout` with a `ref:` taken from
`github.event.workflow_run.*`. The tagged commit is added as a worktree instead, and only
after scripts/ci/check-portal-release-ref.py has proven the tag points at head_sha.
"""

from __future__ import annotations

from pathlib import Path
import sys

import yaml

WF = Path(".github/workflows/portal-release.yml")


def main() -> int:
    steps = yaml.safe_load(WF.read_text(encoding="utf-8"))["jobs"]["release"]["steps"]
    errors: list[str] = []
    for s in steps:
        if str(s.get("uses", "")).startswith("actions/checkout@") and "workflow_run" in str(s.get("with", {}).get("ref", "")):
            errors.append(f"checkout step takes ref from workflow_run: {s['with']['ref']}")
    runs = [s.get("run", "") for s in steps]
    proof = [i for i, r in enumerate(runs) if "check-portal-release-ref.py" in r]
    wt = [i for i, r in enumerate(runs) if "worktree add" in r]
    if not proof:
        errors.append("no ref proof step (check-portal-release-ref.py)")
    if not wt:
        errors.append("no worktree add step for tagged/")
    if proof and wt and proof[0] >= wt[0]:
        errors.append("the ref proof step does not precede the worktree add")
    for e in errors:
        print(f"FAIL: {e}", file=sys.stderr)
    if not errors:
        print("OK: no workflow_run checkout ref; ref proof precedes the tagged worktree")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
