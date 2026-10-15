#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 dasomel
"""Package installs in CI/build paths must not run a freshly published package's own scripts (#164).

The incident this models: a malicious release carried a build script that downloaded and ran a
payload during the build. Pinning an exact version (check-mutable-inputs.py) does not help once
that exact version is the malicious one, because the install step itself executes package code.

Rules, applied to every `.github/workflows/*.y(a)ml` `run:` string and every `scripts/**/*.sh` outside scripts/test/:
  npm / pnpm / yarn / bun  install|i|add|ci|update|up|upgrade|rebuild (and bare `yarn`) must pass
                                       --ignore-scripts (lifecycle scripts never run); a falsy value
                                       (`=false`, ` false`, `--no-ignore-scripts`) does not count
  pip                install           must pass --only-binary=:all: (no sdist build, so no setup.py
                                       / build-backend code) and must not pass --no-binary

Usage: check-install-scripts.py [--root DIR] [FILE...]
  FILE...   check only those files (.yml/.yaml: every `run:`; anything else: text). No baseline.
  --each-bad FILE | --each-good FILE   test mode: FILE holds cases split by a `----` line; every case
            must be flagged (bad) / none may be flagged (good).
  (none)    scan the real tree under --root (default: repo root) and apply KNOWN_ISSUES.

Fails closed: an unreadable, unparsable or absent file, and a default scan that finds no workflow.
Shell-aware: `&& || ; | ( ) $( ) ` + "`" + ` { }` and newlines split commands, quotes stay intact, heredoc bodies and
quoted/echo'd text are skipped (a heredoc fed to bash/sh is scanned), `bash -c "..."`/`eval "..."` literals
are scanned. The manager's global options are skipped (flag+value pairs, a small per-tool list); when an
unknown flag precedes the subcommand, any later install-like word in the same command is treated as the
subcommand (fail closed).
KNOWN_ISSUES is an explicit baseline keyed on (file, manager, rule, normalised command) with an exact
COUNT, so an added violation, a swapped one, or a fixed one (stale entry) all fail. YAML aliases are
resolved once per source node. It is printed on stdout as KNOWN-ISSUE lines (the regression harness
surfaces them as a WARN).
Not covered (stated, not hidden): apt/go/cargo/gem installs, Dockerfiles, an install hidden behind a
variable, `eval` of a non-literal or a command in another file, npm `.npmrc`/env (`npm_config_*`) based
ignore-scripts, composite actions (none exist). Deliberately not flagged because running the fetched
package's code is their purpose: npx, `pnpm dlx`, `yarn dlx`, bunx, `npm exec`/`npm x`, corepack wrappers.
pipx is not covered (no --only-binary equivalent short of `--pip-args`). Accepted conservative limit: a
quoted flag (`"--ignore-scripts"`) does not count as the flag, so it is reported and needs a baseline entry.
This is a source-text guard.
"""

from __future__ import annotations

import collections
import pathlib
import re
import sys

import yaml

INSTALLISH = {"install", "i", "add", "ci", "update", "up", "upgrade", "rebuild", "it", "install-test"}
NPM_LIKE = {"npm", "pnpm", "yarn", "bun"}
PIP = {"pip", "pip3"}
VALUE_FLAGS = {  # global options that take a separate value (verified against `npm ci --help` / pnpm / yarn docs)
    "npm": {"--prefix", "-C", "--workspace", "-w", "--userconfig", "--globalconfig", "--cache", "--registry", "--loglevel", "--scope"},
    "pnpm": {"--filter", "-F", "--dir", "-C", "--reporter", "--loglevel", "--store-dir", "--filter-prod", "--workspace-concurrency"},
    "yarn": {"--cwd", "--modules-folder", "--cache-folder", "--global-folder", "--registry", "--network-timeout", "--mutex"},
    "bun": {"--cwd", "--config", "-c", "--registry", "--cache-dir"},
    "pip": {"--python", "--log", "--cache-dir", "--proxy", "--retries", "--timeout", "--cert", "--exists-action"},
}
QUIET_HEADS = {"echo", "printf", "log", "info", "warn", "error", "die"}
SHELLS = {"bash", "sh", "zsh", "dash", "eval"}
FALSY = {"false", "0", "no", "off"}
Q = "\0"  # prefix marking a token that contained quotes (never a flag, a name or a subcommand)

# D1: baseline of the real violations found when this guard was added. The three are CI installs
# of one exact version each (markdownlint-cli, jsonschema); the fix is `--ignore-scripts` /
# `--only-binary=:all:` in .github/workflows/**, which is a HIGH-risk path and needs its own change.
# Escape hatch: fix the workflow line, then delete the entry (a stale entry fails this check).
KNOWN_ISSUES = {
    (".github/workflows/lint.yml", "npm", "no-ignore-scripts", "npm install -g markdownlint-cli@0.49.1"):
        (1, "the global markdownlint-cli npm install runs package lifecycle scripts"),
    (".github/workflows/markdown-changed.yml", "npm", "no-ignore-scripts", "npm install -g markdownlint-cli@0.49.1"):
        (1, "the global markdownlint-cli npm install runs package lifecycle scripts"),
    (".github/workflows/research-evidence.yml", "pip", "no-only-binary", "pip install jsonschema==4.25.1"):
        (1, "pip install jsonschema may build an sdist and run its build backend"),
}


def run_strings(node, seen: set | None = None) -> list[str]:
    seen = set() if seen is None else seen
    if not isinstance(node, (dict, list)) or id(node) in seen:  # an alias is the same node: count once
        return []
    seen.add(id(node))
    if isinstance(node, dict):
        out = [v for k, v in node.items() if k == "run" and isinstance(v, str)]
        return out + [s for v in node.values() for s in run_strings(v, seen)]
    return [s for v in node for s in run_strings(v, seen)]


def strip_heredocs(text: str) -> str:
    out, end = [], None
    for line in text.replace("\\\n", " ").splitlines():
        if end is not None:
            if line.strip() == end:
                end = None
            continue
        out.append(line)
        m = re.search(r"<<-?\s*(['\"]?)(\w+)\1", line)
        if m and not re.search(r"\b(ba|z|da)?sh\b", line[:m.start()]):  # a heredoc fed to a shell is code
            end = m.group(2)
    return "\n".join(out)


def commands(text: str) -> list[list[str]]:
    """Split on shell separators outside quotes; tokens containing quotes carry the Q prefix."""
    text = strip_heredocs(text)
    cmds: list[list[str]] = [[]]
    cur, quoted, mode, depth, dq = "", False, "", 0, []
    i = 0

    def end_tok():
        nonlocal cur, quoted
        if cur or quoted:
            cmds[-1].append((Q if quoted else "") + cur)
        cur, quoted = "", False

    def split():
        end_tok()
        if cmds[-1]:
            cmds.append([])

    while i < len(text):
        c, two = text[i], text[i:i + 2]
        if mode == "'":
            if c == "'":
                mode = ""
            else:
                cur += c
        elif mode == '"' and two != "$(" and c != "`":
            if c == '"':
                mode = ""
            elif c == "\\" and i + 1 < len(text):
                i += 1
                cur += text[i]
            else:
                cur += c
        elif two == "$(":
            split()
            if mode == '"':
                dq.append(depth)
                mode = ""
            depth += 1
            i += 1
        elif c == "`":
            split()
        elif c in "'\"":
            mode, quoted = c, True
        elif c == "\\" and i + 1 < len(text):
            i += 1
            cur += text[i]
        elif c == "#" and not cur:
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        elif c in ";&|\n(":
            depth += c == "("
            split()
        elif c == ")":
            depth -= 1
            split()
            if dq and depth == dq[-1]:
                dq.pop()
                mode = '"'
        elif c in "{}" and not cur and (c == "}" or text[i + 1:i + 2] in ("", " ", "\n", "\t")):
            split()
        elif c in " \t":
            end_tok()
        else:
            cur += c
        i += 1
    end_tok()
    return [t for t in cmds if t]


def subcommand(name: str, rest: list[str]) -> str | None:
    """First non-flag token after the manager's global options (flag+value pairs skipped)."""
    j, unknown = 0, False
    while j < len(rest) and rest[j].startswith("-"):
        if "=" in rest[j]:
            j += 1
        elif rest[j] in VALUE_FLAGS[name]:
            j += 2
        else:
            unknown, j = True, j + 1
    sub = rest[j] if j < len(rest) else None
    if unknown and sub not in INSTALLISH:  # cannot be sure what the unknown flag consumed: fail closed
        sub = next((t for t in rest[j:] if t in INSTALLISH), sub)
    return sub


def ignores_scripts(rest: list[str]) -> bool:
    on = False
    for j, t in enumerate(rest):
        nxt = rest[j + 1].lstrip(Q).lower() if j + 1 < len(rest) and not rest[j + 1].startswith("-") else None
        if t == "--no-ignore-scripts":
            on = False
        elif t == "--ignore-scripts":
            on = nxt not in FALSY
        elif t.startswith("--ignore-scripts="):
            on = t.split("=", 1)[1].lower() not in FALSY
    return on


def findings(text: str) -> list[tuple[str, str, str]]:
    found = []
    for toks in commands(text):
        if toks[0].lstrip(Q) in QUIET_HEADS:
            continue
        for i, tok in enumerate(toks):
            name = tok.lstrip(Q).rsplit("/", 1)[-1]
            rest = toks[i + 1:]
            cmd = " ".join(t.lstrip(Q) for t in toks[i:])
            if name in SHELLS:
                found.extend(f for t in rest if t.startswith(Q) and " " in t for f in findings(t[1:]))
            elif name in NPM_LIKE:
                sub = subcommand(name, rest)
                if sub in INSTALLISH or (name == "yarn" and sub is None and not {"--version", "-v", "--help", "-h"} & set(rest)):
                    if not ignores_scripts(rest):
                        found.append((name, "no-ignore-scripts", cmd))
            elif name in PIP and subcommand("pip", rest) == "install":
                if any(t == "--no-binary" or t.startswith("--no-binary=") for t in rest):
                    found.append(("pip", "no-binary", cmd))
                elif not any(t == "--only-binary=:all:" or (t == "--only-binary" and j + 1 < len(rest) and rest[j + 1] == ":all:")
                             for j, t in enumerate(rest)):
                    found.append(("pip", "no-only-binary", cmd))
    return found


def scan(rel: str, path: pathlib.Path) -> list[tuple[str, str, str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"{rel}: unreadable: {exc}") from exc
    if path.suffix in (".yml", ".yaml"):
        try:
            docs = list(yaml.safe_load_all(text))
        except yaml.YAMLError as exc:
            raise ValueError(f"{rel}: unparsable YAML: {exc}") from exc
        seen: set = set()
        return [f for d in docs for run in run_strings(d, seen) for f in findings(run)]
    return findings(text)


def main() -> int:
    argv = sys.argv[1:]
    root = pathlib.Path(__file__).resolve().parents[3]
    if argv[:1] == ["--root"]:
        if len(argv) < 2:
            print("--root needs a directory", file=sys.stderr)
            return 2
        root, argv = pathlib.Path(argv[1]), argv[2:]
    if argv[:1] in (["--each-bad"], ["--each-good"]):
        if len(argv) != 2:
            print(f"{argv[0]} needs one file", file=sys.stderr)
            return 2
        want, wrong = argv[0] == "--each-bad", []
        for case in pathlib.Path(argv[1]).read_text(encoding="utf-8").split("\n----\n"):
            if bool(findings(case)) != want:
                wrong.append(case)
        print("\n".join(f"{'NOT flagged' if want else 'flagged'}: {c!r}" for c in wrong), file=sys.stderr)
        return 1 if wrong else 0
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
            for manager, rule, cmd in scan(rel, path):
                if argv:
                    problems.append(f"{rel}: {manager} install runs package scripts ({rule}): {cmd}")
                else:
                    seen[(rel, manager, rule, cmd)] += 1
        except ValueError as exc:
            problems.append(str(exc))
    baseline: list[str] = []
    if not argv:
        for key, (want, reason) in sorted(KNOWN_ISSUES.items()):
            got = seen[key]
            if got != want:
                problems.append(f"KNOWN_ISSUES drift: {key[0]} {key[1]} {key[2]} `{key[3]}` expected {want}, found {got} (remove or update the entry)")
            baseline.extend(f"KNOWN-ISSUE {key[0]} {key[1]} {key[2]}: {reason}" for _ in range(got))
        problems.extend(f"unexpected script-running install {k[0]} {k[1]} ({k[2]}): {k[3]}" for k in sorted(set(seen) - set(KNOWN_ISSUES)))
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print("\n".join(baseline))
    print(f"PASS: no unreviewed script-running installs; {len(baseline)} known-issue baseline entr{'y' if len(baseline) == 1 else 'ies'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
