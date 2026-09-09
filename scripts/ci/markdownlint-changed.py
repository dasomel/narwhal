#!/usr/bin/env python3
"""Fail when markdownlint finds violations on newly added/modified active-doc lines.

The repository has pre-existing Markdown debt. Blocking the entire historical tree at
once would make CI permanently red and encourage disabling rules. Instead this gate
keeps legacy debt visible while preventing changed active documentation from adding or
modifying violating lines. `docs/archive/**` is historical evidence and is intentionally
outside the blocking scope.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
LINT_RE = re.compile(r"^(.*?):(\d+)(?::\d+)?\s+(MD\d+|[A-Za-z][A-Za-z0-9-]*)\b")


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=check, text=True, capture_output=True)


def active_markdown_files() -> list[str]:
    tracked = run("git", "ls-files", "*.md").stdout.splitlines()
    return [path for path in tracked if not path.startswith("docs/archive/")]


def changed_line_ranges(base: str) -> dict[str, list[range]]:
    diff = run(
        "git",
        "diff",
        "--unified=0",
        "--no-color",
        f"{base}...HEAD",
        "--",
        "*.md",
    ).stdout

    changed: dict[str, list[range]] = {}
    current: str | None = None
    for raw in diff.splitlines():
        if raw.startswith("+++ b/"):
            current = raw[6:]
            continue
        if not current or not raw.startswith("@@"):
            continue
        match = HUNK_RE.match(raw)
        if not match:
            continue
        start = int(match.group(1))
        count = int(match.group(2) or "1")
        if count > 0:
            changed.setdefault(current, []).append(range(start, start + count))
    return changed


def line_changed(ranges: list[range], line: int) -> bool:
    return any(line in item for item in ranges)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, help="base commit SHA used for the diff")
    args = parser.parse_args()

    changed = changed_line_ranges(args.base)
    active = active_markdown_files()
    if not active:
        print("No active Markdown files found.")
        return 0

    proc = run(
        "markdownlint",
        "--config",
        ".markdownlint.json",
        *active,
        check=False,
    )
    # markdownlint uses 1 for lint findings. Anything else is an execution failure and
    # must fail closed rather than being reclassified as legacy debt.
    if proc.returncode not in (0, 1):
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        print(f"markdownlint execution failed with exit code {proc.returncode}", file=sys.stderr)
        return proc.returncode

    violations: list[str] = []
    for raw in (proc.stdout + proc.stderr).splitlines():
        match = LINT_RE.match(raw)
        if not match:
            continue
        path = match.group(1).removeprefix("./")
        line = int(match.group(2))
        ranges = changed.get(path, [])
        if ranges and line_changed(ranges, line):
            violations.append(raw)

    changed_active = sorted(path for path in changed if path in active)
    print(f"Changed active Markdown files: {len(changed_active)}")
    for path in changed_active:
        print(f"  {path}")

    if violations:
        print("\nMarkdown violations on added/modified active-documentation lines:", file=sys.stderr)
        for violation in violations:
            print(violation, file=sys.stderr)
        return 1

    print("No markdownlint violations on added/modified active-documentation lines.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
