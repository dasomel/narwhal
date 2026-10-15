#!/usr/bin/env python3
"""A job-level `defaults.run.working-directory` needs a checkout that always ran first.

Failure this prevents (portal-test.yml `regression-gate`, PR #347): the job defaulted its cwd to
`portal` but skipped actions/checkout when portal/ was untouched, so the first `run:` step failed
before the shell started and `Portal gate` went red on every PR that did not touch portal/.

Rule, per job with `defaults.run.working-directory` (default scan: .github/workflows/portal-*.yml):
  - an unconditional `actions/checkout` step must exist, and
  - no step with `run:` may precede it (`uses:` steps such as harden-runner may).
Fix by dropping the job default and setting `working-directory:` on the post-checkout steps.

Usage: check-workflow-workdir.py [FILE...]
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml


def problems(path: Path) -> list[str]:
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return [f"{path}: unreadable workflow: {exc}"]
    out: list[str] = []
    for name, job in ((doc or {}).get("jobs") or {}).items():
        workdir = (((job or {}).get("defaults") or {}).get("run") or {}).get("working-directory")
        if not workdir:
            continue
        steps = job.get("steps") or []
        checkout = next(
            (i for i, s in enumerate(steps) if str(s.get("uses", "")).startswith("actions/checkout@")), None
        )
        if checkout is None:
            out.append(f"{path}: job '{name}' sets working-directory '{workdir}' but has no actions/checkout step")
            continue
        if "if" in steps[checkout]:
            out.append(
                f"{path}: job '{name}' sets working-directory '{workdir}' but its checkout is conditional "
                f"(if: {steps[checkout]['if']})"
            )
        for s in steps[:checkout]:
            if "run" in s:
                out.append(
                    f"{path}: job '{name}' runs step '{s.get('name', '?')}' before checkout "
                    f"with working-directory '{workdir}'"
                )
    return out


def main(argv: list[str]) -> int:
    files = [Path(a) for a in argv] or sorted(Path(".github/workflows").glob("portal-*.yml"))
    if not files:
        print("ERROR: no portal-* workflows found", file=sys.stderr)
        return 2
    bad = [p for f in files for p in problems(f)]
    for line in bad:
        print(line, file=sys.stderr)
    if bad:
        return 1
    print(f"PASS: {len(files)} workflow(s); every job default working-directory has an unconditional checkout first")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
