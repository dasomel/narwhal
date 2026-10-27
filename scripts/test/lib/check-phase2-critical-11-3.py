#!/usr/bin/env python3
"""R289: Phase 2 treats 11-3-keycloak-clients.sh as critical and runs it before 12-gitea.sh.

Since #478, 11-3 fails closed when it cannot read a Keycloak client secret, but as a
non-critical step that failure only warned, so the clients after it stayed unconfigured and
later steps (12-gitea's OAuth source, ArgoCD, Grafana, Harbor, OpenBao) consumed empty or
stale Secrets (#491). Both the CRITICAL_SCRIPTS entry and the real run-loop order are
checked; --mutation-verify proves each assertion fails on a mutated copy.

Usage: check-phase2-critical-11-3.py [--mutation-verify] [<06-phase2-start.sh>]
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PHASE2 = ROOT / "scripts/cluster/06-phase2-start.sh"
TARGET = "11-3-keycloak-clients.sh"


def problems(s: str) -> list[str]:
    m = re.search(r"CRITICAL_SCRIPTS=\((.*?)\n\)", s, re.S)
    if not m:
        return ["CRITICAL_SCRIPTS array missing"]
    out = []
    if TARGET not in re.findall(r'^\s*"([^"]+\.sh)"', m.group(1), re.M):
        out.append(f"{TARGET} is not in CRITICAL_SCRIPTS")
    loop = re.search(r'^for script in ((?:"[^"]+\.sh"\s*)+); do', s, re.M)
    if not loop:
        return out + ["Phase 2 run loop missing"]
    order = re.findall(r'"([^"]+\.sh)"', loop.group(1))
    if TARGET not in order or "12-gitea.sh" not in order:
        out.append(f"the run loop lacks {TARGET} or 12-gitea.sh")
    elif order.index(TARGET) > order.index("12-gitea.sh"):
        out.append(f"the run loop runs {TARGET} after 12-gitea.sh")
    return out


def main(argv: list[str]) -> int:
    mutation = "--mutation-verify" in argv
    args = [a for a in argv if a != "--mutation-verify"]
    s = Path(args[0] if args else PHASE2).read_text()

    found = problems(s)
    for p in found:
        print(f"FAIL {p}")
    if found:
        return 1
    print(f"OK {TARGET} is in CRITICAL_SCRIPTS and the run loop runs it before 12-gitea.sh")

    if mutation:
        mutants = {
            "drop the CRITICAL_SCRIPTS entry": s.replace(f'  "{TARGET}"\n', "", 1),
            "run 11-3 after 12-gitea": s.replace(
                f'"{TARGET}" "11-4-keycloak-apiserver.sh" "12-gitea.sh"',
                f'"11-4-keycloak-apiserver.sh" "12-gitea.sh" "{TARGET}"',
                1,
            ),
        }
        for name, mutated in mutants.items():
            if mutated == s:
                print(f"FAIL mutation '{name}' did not apply (anchor drifted)")
                return 1
            if not problems(mutated):
                print(f"FAIL mutation '{name}' was not caught")
                return 1
            print(f"OK mutation caught: {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
