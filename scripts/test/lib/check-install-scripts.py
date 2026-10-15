#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 dasomel
"""Package installs in CI/build paths must not run a freshly published package's own scripts (#164).

The incident this models: a malicious release carried a build script that downloaded and ran a
payload during the build. Pinning an exact version (check-mutable-inputs.py) does not help once
that exact version is the malicious one, because the install step itself executes package code.

Rules, applied to every `.github/workflows/*.y(a)ml` `run:` string and every `scripts/**/*.sh` outside scripts/test/:
  npm / pnpm / yarn  install|i|add|ci  must pass --ignore-scripts (lifecycle scripts never run)
  pip                install           must pass --only-binary=:all: (no sdist build, so no setup.py
                                       / build-backend code) and must not pass --no-binary

Usage: check-install-scripts.py [--root DIR] [FILE...]
  FILE...   check only those files (.yml/.yaml: every `run:`; anything else: text). No baseline.
  (none)    scan the real tree under --root (default: repo root) and apply KNOWN_ISSUES.

Fails closed: an unreadable, unparsable or absent file, and a default scan that finds no workflow.
KNOWN_ISSUES is an explicit baseline keyed on (file, manager, rule) with an exact COUNT, so an
added violation or a fixed one (stale entry) both fail. It is printed on stdout as KNOWN-ISSUE
lines (the regression harness surfaces them as a WARN).
Not covered (stated, not hidden): apt/go/cargo/gem installs, Dockerfiles, an install hidden behind
a variable or `eval`, and npm `.npmrc`/env based ignore-scripts. This is a source-text guard.
"""

from __future__ import annotations

import collections
import pathlib
import re
import sys

import yaml

NPM_LIKE = {"npm": {"install", "i", "add", "ci"}, "pnpm": {"install", "i", "add"}, "yarn": {"install", "add"}}
PIP = {"pip", "pip3"}
QUIET_HEADS = {"echo", "printf", "log", "info", "warn", "error", "die"}

# D1: baseline of the real violations found when this guard was added. The three are CI installs
# of one exact version each (markdownlint-cli, jsonschema); the fix is `--ignore-scripts` /
# `--only-binary=:all:` in .github/workflows/**, which is a HIGH-risk path and needs its own change.
# Escape hatch: fix the workflow line, then delete the entry (a stale entry fails this check).
KNOWN_ISSUES = {
    (".github/workflows/lint.yml", "npm", "no-ignore-scripts"):
        (1, "the global markdownlint-cli npm install runs package lifecycle scripts"),
    (".github/workflows/markdown-changed.yml", "npm", "no-ignore-scripts"):
        (1, "the global markdownlint-cli npm install runs package lifecycle scripts"),
    (".github/workflows/research-evidence.yml", "pip", "no-only-binary"):
        (1, "pip install jsonschema may build an sdist and run its build backend"),
}


def run_strings(node) -> list[str]:
    if isinstance(node, dict):
        out = [v for k, v in node.items() if k == "run" and isinstance(v, str)]
        return out + [s for v in node.values() for s in run_strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in run_strings(v)]
    return []


def commands(text: str) -> list[list[str]]:
    lines = [l for l in text.replace("\\\n", " ").splitlines() if not l.lstrip().startswith("#")]
    segs = re.split(r"&&|\|\||;|\||\n", "\n".join(re.sub(r"\s#.*$", "", l) for l in lines))
    return [s.split() for s in segs if s.split()]


def findings(text: str) -> list[tuple[str, str]]:
    found = []
    for toks in commands(text):
        if toks[0] in QUIET_HEADS:
            continue
        for i, tok in enumerate(toks):
            name = tok.rsplit("/", 1)[-1]
            sub = next((t for t in toks[i + 1:] if not t.startswith("-")), None)
            rest = toks[i + 1:]
            if name in NPM_LIKE and sub in NPM_LIKE[name]:
                if "--ignore-scripts=false" in rest or not ({"--ignore-scripts", "--ignore-scripts=true"} & set(rest)):
                    found.append((name, "no-ignore-scripts"))
            elif name in PIP and sub == "install":
                if any(t == "--no-binary" or t.startswith("--no-binary=") for t in rest):
                    found.append(("pip", "no-binary"))
                elif not any(t == "--only-binary=:all:" or (t == "--only-binary" and j + 1 < len(rest) and rest[j + 1] == ":all:")
                             for j, t in enumerate(rest)):
                    found.append(("pip", "no-only-binary"))
    return found


def scan(rel: str, path: pathlib.Path) -> list[tuple[str, str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"{rel}: unreadable: {exc}") from exc
    if path.suffix in (".yml", ".yaml"):
        try:
            docs = list(yaml.safe_load_all(text))
        except yaml.YAMLError as exc:
            raise ValueError(f"{rel}: unparsable YAML: {exc}") from exc
        return [f for d in docs for run in run_strings(d) for f in findings(run)]
    return findings(text)


def main() -> int:
    argv = sys.argv[1:]
    root = pathlib.Path(__file__).resolve().parents[3]
    if argv[:1] == ["--root"]:
        if len(argv) < 2:
            print("--root needs a directory", file=sys.stderr)
            return 2
        root, argv = pathlib.Path(argv[1]), argv[2:]
    if argv:
        targets = [(a, pathlib.Path(a)) for a in argv]
    else:
        wf = sorted(p for p in (root / ".github/workflows").glob("*") if p.suffix in (".yml", ".yaml"))
        sh = sorted(p for p in (root / "scripts").rglob("*") if p.suffix in (".sh", ".txt")
                    and p.relative_to(root).parts[:2] != ("scripts", "test"))  # harness text quotes the patterns
        targets = [(str(p.relative_to(root)), p) for p in wf + sh]
        if not wf:
            print(f"no workflows found under {root}/.github/workflows (absent evidence is not compliant)", file=sys.stderr)
            return 1
    problems: list[str] = []
    seen: collections.Counter = collections.Counter()
    for rel, path in targets:
        if not path.is_file():
            problems.append(f"{rel}: absent or not a file")
            continue
        try:
            for manager, rule in scan(rel, path):
                if argv:
                    problems.append(f"{rel}: {manager} install runs package scripts ({rule})")
                else:
                    seen[(rel, manager, rule)] += 1
        except ValueError as exc:
            problems.append(str(exc))
    baseline: list[str] = []
    if not argv:
        for key, (want, reason) in sorted(KNOWN_ISSUES.items()):
            got = seen[key]
            if got != want:
                problems.append(f"KNOWN_ISSUES drift: {key[0]} {key[1]} {key[2]} expected {want}, found {got} (remove or update the entry)")
            baseline.extend(f"KNOWN-ISSUE {key[0]} {key[1]} {key[2]}: {reason}" for _ in range(got))
        problems.extend(f"unexpected script-running install {k[0]} {k[1]} ({k[2]})" for k in sorted(set(seen) - set(KNOWN_ISSUES)))
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print("\n".join(baseline))
    print(f"PASS: no unreviewed script-running installs; {len(baseline)} known-issue baseline entr{'y' if len(baseline) == 1 else 'ies'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
