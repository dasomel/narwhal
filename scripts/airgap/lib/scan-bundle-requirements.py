#!/usr/bin/env python3
"""Print install-time bundle requirements from scripts outside scripts/airgap."""
import re
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
# Call sites must pass literal names; variable arguments in loops and calls embedded in
# heredocs or ssh strings are not detected by these line based patterns.
found = {"bin": set(), "manifests": set(), "charts": set()}
patterns = {
    "bin": re.compile(r"\binstall_bin\s+([A-Za-z0-9_.+-]+)"),
    "manifests": re.compile(r"\$\(\s*manifest\s+([A-Za-z0-9_.+-]+)\s*\)"),
    "charts": re.compile(r"\$\(\s*chart\s+([A-Za-z0-9_.+-]+)\s*\)"),
}
for path in sorted((root / "scripts").rglob("*.sh")):
    if "airgap" in path.relative_to(root / "scripts").parts:
        continue
    text = path.read_text()
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        line = line.split("#", 1)[0]
        for kind, pattern in patterns.items():
            found[kind].update(pattern.findall(line))
    if re.search(r"\b(?:install_bin|chart|manifest)\s+\$", text):
        print(f"[WARN] {path}: dynamic bundle requirement argument may not be detected", file=sys.stderr)

for kind in ("bin", "manifests", "charts"):
    for name in sorted(found[kind]):
        print(f"{kind}\t{name}")
