#!/usr/bin/env python3
"""R282: every COST_* key portal/src/lib/cost.ts requires is set by 13-2 for both providers.

2026-10-26: portal 1.1.1 made getCostPricing fail closed (/api/cost -> 503) unless five
pricing-metadata keys exist, but 13-2-narwhal-portal-bindings.sh still set only the three
unit prices, so the Kakao cost page loaded forever. The required set is read from the
in-repo portal tree (never a tag), so a new required key in cost.ts fails this check
until 13-2 provides it.

Usage: check-portal-cost-env-seam.py [--mutation-verify] [<cost.ts> <13-2.sh>]
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COST_TS = ROOT / "portal/src/lib/cost.ts"
BINDINGS = ROOT / "scripts/cluster/13-2-narwhal-portal-bindings.sh"


def required_keys(cost_ts: str) -> set[str]:
    keys = set(re.findall(r'\["\w+",\s*"(COST_[A-Z_]+)"\]', cost_ts))
    keys |= set(re.findall(r'configuredString\("(COST_[A-Z_]+)"\)', cost_ts))
    return keys


def problems(cost_ts: str, bindings: str) -> list[str]:
    req = required_keys(cost_ts)
    if len(req) < 8:
        return [f"parsed only {len(req)} COST_* keys from cost.ts ({sorted(req)}); parser is stale"]
    out = []
    literals = set(re.findall(r'--from-literal=(COST_[A-Z_]+)=', bindings))
    for key in sorted(req - literals):
        out.append(f"{key}: required by cost.ts, not passed as --from-literal in 13-2")
    for key in sorted(req):
        # one default per provider branch (kakao.* and the Vagrant fallback)
        n = len(re.findall(r':\s*"\$\{' + key + r':=', bindings))
        if n < 2:
            out.append(f"{key}: {n} provider default(s) in 13-2, expected 2 (kakao.* and default)")
    return out


def main(argv: list[str]) -> int:
    mutation = "--mutation-verify" in argv
    args = [a for a in argv if a != "--mutation-verify"]
    cost_path, bind_path = (Path(args[0]), Path(args[1])) if len(args) == 2 else (COST_TS, BINDINGS)
    cost_ts, bindings = cost_path.read_text(), bind_path.read_text()

    found = problems(cost_ts, bindings)
    for p in found:
        print(f"FAIL {p}")
    if found:
        return 1
    print(f"OK {len(required_keys(cost_ts))} COST_* keys set by 13-2 for both providers")

    if mutation:
        mutants = {
            "drop the COST_PRICING_SCOPE literal": bindings.replace("--from-literal=COST_PRICING_SCOPE=", "--from-literal=X_SCOPE=", 1),
            "drop the Vagrant COST_CURRENCY default": bindings.replace(': "${COST_CURRENCY:=USD}"', "", 1),
            "cost.ts requires a new key": cost_ts.replace('configuredString("COST_CURRENCY")', 'configuredString("COST_CURRENCY") || configuredString("COST_REGION")', 1),
        }
        for name, (c, b) in {
            k: ((v, bindings) if k.startswith("cost.ts") else (cost_ts, v)) for k, v in mutants.items()
        }.items():
            if (c, b) == (cost_ts, bindings):
                print(f"FAIL mutation '{name}' did not apply (anchor drifted)")
                return 1
            if not problems(c, b):
                print(f"FAIL mutation '{name}' was not caught")
                return 1
            print(f"OK mutation caught: {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
