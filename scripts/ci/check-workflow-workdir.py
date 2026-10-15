#!/usr/bin/env python3
"""A default `working-directory` needs a checkout that always ran first.

Failure this prevents (portal-test.yml `regression-gate`, PR #347): the job defaulted its cwd to
`portal` but skipped actions/checkout when portal/ was untouched, so the first `run:` step failed
before the shell started and `Portal gate` went red on every PR that did not touch portal/.

Rule, per job whose effective `defaults.run.working-directory` is a subdirectory (the job's own
default, else the WORKFLOW-level one; `.` is the always-present workspace root and is ignored):
  - an UNCONDITIONAL `actions/checkout` must exist (a conditional one earlier does not count, a
    later unconditional one does) and it must check out into the workspace root, or into exactly
    the working directory (`with.path` unset, `.`, or equal to it);
  - before that checkout there may be no `run:` step that inherits the default (a step with its own
    `working-directory` is not affected) and no local action (`uses: ./...`), which needs the files.
`uses:` steps such as harden-runner may precede it.
Fix by dropping the default and setting `working-directory:` on the post-checkout steps.

Usage: check-workflow-workdir.py [FILE...]   (default scan: .github/workflows/portal-*.yml)
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml


def default_workdir(node: object) -> str | None:
    wd = ((((node or {}).get("defaults") or {}).get("run") or {}).get("working-directory"))
    return str(wd).strip() if wd else None


def norm(path: str) -> str:
    return path.strip().removeprefix("./").rstrip("/")


def is_checkout(step: dict) -> bool:
    return str(step.get("uses", "")).startswith("actions/checkout@")


def satisfies(step: dict, workdir: str) -> bool:
    """Unconditional and checked out into the root or exactly the working directory."""
    if "if" in step:
        return False
    sub = (step.get("with") or {}).get("path")
    return sub is None or norm(str(sub)) in ("", ".", norm(workdir))


def problems(path: Path) -> list[str]:
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return [f"{path}: unreadable workflow: {exc}"]
    doc = doc or {}
    wf_default = default_workdir(doc)
    out: list[str] = []
    for name, job in (doc.get("jobs") or {}).items():
        job = job or {}
        workdir = default_workdir(job) or wf_default
        if not workdir or norm(workdir) in ("", "."):
            continue
        steps = job.get("steps") or []
        checkouts = [i for i, s in enumerate(steps) if is_checkout(s)]
        good = next((i for i in checkouts if satisfies(steps[i], workdir)), None)
        if good is None:
            if not checkouts:
                why = "has no actions/checkout step"
            else:
                why = "has no unconditional checkout into the workspace root or that directory " + "; ".join(
                    f"checkout #{n + 1}: "
                    + (f"conditional (if: {steps[i]['if']})" if "if" in steps[i] else f"with.path '{steps[i]['with']['path']}'")
                    for n, i in enumerate(checkouts)
                )
            out.append(f"{path}: job '{name}' sets working-directory '{workdir}' but {why}")
            continue
        for s in steps[:good]:
            if "run" in s and "working-directory" not in s:
                out.append(
                    f"{path}: job '{name}' runs step '{s.get('name', '?')}' before checkout "
                    f"with working-directory '{workdir}'"
                )
            if str(s.get("uses", "")).startswith("./"):
                out.append(
                    f"{path}: job '{name}' uses local action '{s['uses']}' before checkout "
                    f"(working-directory '{workdir}')"
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
