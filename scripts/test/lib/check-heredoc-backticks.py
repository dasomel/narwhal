#!/usr/bin/env python3
"""Find unescaped backticks inside unquoted `cat <<TAG` heredocs.

An unquoted heredoc is used when the body needs ${VAR} interpolation, which also means
the shell expands backticks in it — INCLUDING on lines that look like comments. `#` is a
comment to whatever consumes the document, not to the shell, so the shell still runs what
is between the backticks and substitutes the result into the output.

This shape appeared twice in two days in this repo. Once the substitution would have
landed inside an argocd-rbac-cm policy.csv, i.e. inside a Casbin policy. shellcheck reports
it as SC2006 "style", which is the wrong severity for that, so it does not stop anyone.

DELIBERATELY NARROW. Only heredocs opened by a line that begins with `cat <<` are
considered, because those are the ones this parser can track reliably; heredocs nested
inside a quoted string (ssh_bastion "... <<EOF ... EOF") are skipped rather than guessed
at, since guessing produced false positives on real, correct code. Escaped backticks are
ignored — deferring expansion to a remote shell is the point of writing them that way.

So: absence of a finding is not proof, presence of one is. That trade is worth it because
the one shape it does cover is the one that keeps happening.
"""
import re
import sys
from pathlib import Path

OPEN = re.compile(r"^\s*cat\s+<<-?\s*(?P<q>['\"]?)(?P<tag>[A-Za-z_][A-Za-z0-9_]*)(?P=q)")
UNESCAPED_BACKTICK = re.compile(r"(?<!\\)`")


def scan(path: Path) -> list:
    problems, tag, quoted = [], None, False
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if tag is None:
            m = OPEN.match(line)
            if m:
                tag, quoted = m.group("tag"), bool(m.group("q"))
            continue
        if line.strip() == tag:
            tag = None
            continue
        if not quoted and UNESCAPED_BACKTICK.search(line):
            problems.append(
                f"{path}:{n}: backticks inside an unquoted heredoc are EXECUTED by the shell\n"
                f"    {line.strip()[:100]}"
            )
    return problems


def main() -> int:
    problems = [p for path in sorted(Path("scripts").rglob("*.sh")) for p in scan(path)]
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
