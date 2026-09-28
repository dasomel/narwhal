#!/usr/bin/env python3
"""Every scripts/cluster/*.sh that calls a scripts/common/lib.sh function must source lib.sh.

bash -n and shellcheck do not flag a call to an undefined function; it only fails at
runtime with rc=127 (narwhal#243: 13-2 called generate_password without sourcing lib.sh).
"""
import pathlib
import re
import sys

root = pathlib.Path(__file__).resolve().parents[3]
lib = (root / "scripts/common/lib.sh").read_text()
funcs = set(re.findall(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*\(\)\s*\{", lib, re.M))


def problems(paths):
    out = []
    for path in paths:
        text = path.read_text()
        code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
        local = set(re.findall(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\(\)\s*\{", code, re.M))
        called = {f for f in funcs - local if re.search(rf"(^|[\s;$(|&]){f}\b(?!\s*\(\))", code, re.M)}
        if called and not re.search(r"^\s*(source|\.)\s+\S*common/lib\.sh", code, re.M):
            out.append(f"{path.name}: calls {sorted(called)} without sourcing lib.sh")
    return out


if "--mutation-verify" in sys.argv:
    # A real lib-using script with its source line removed must be reported.
    import tempfile
    src = root / "scripts/cluster/07-cnpg.sh"
    with tempfile.TemporaryDirectory() as d:
        mutated = pathlib.Path(d) / src.name
        mutated.write_text(re.sub(r"^\s*source\s+\S*common/lib\.sh\s*$", "", src.read_text(), flags=re.M))
        assert problems([mutated]), "unsourced lib call was not reported"
        assert not problems([src]), "unmodified 07-cnpg.sh was reported"
    print("mutation: removing the lib.sh source line is reported")
    sys.exit(0)

errs = problems(sorted((root / "scripts/cluster").glob("*.sh")))
for e in errs:
    print(e, file=sys.stderr)
sys.exit(1 if errs else 0)
