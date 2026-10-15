#!/usr/bin/env python3
"""Static check: ambient enrollment list matches the namespaces ambient NetworkPolicy checks cover (#128).

check-ambient-netpol.py only validates HBONE/probe rules for its hardcoded AMBIENT set. If
09-istio-ambient.sh enrolls another namespace (or drops one), that set silently goes stale.
Fails closed when the array is absent, empty, duplicated, or not plain DNS labels.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
DEFAULT = ROOT / "scripts/cluster/09-istio-ambient.sh"
DNS_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?")


def covered_namespaces() -> set[str]:
    spec = importlib.util.spec_from_file_location(
        "check_ambient_netpol", pathlib.Path(__file__).with_name("check-ambient-netpol.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return set(module.AMBIENT)


def enrolled_namespaces(text: str) -> list[str]:
    arrays = re.findall(r"^AMBIENT_NAMESPACES=\(([^)]*)\)", text, re.M)
    if len(arrays) != 1:
        raise ValueError(f"expected exactly one AMBIENT_NAMESPACES array, found {len(arrays)}")
    names: list[str] = []
    for line in arrays[0].splitlines():
        line = line.split("#", 1)[0].strip()
        names.extend(line.split())
    return names


def validate(text: str, covered: set[str]) -> list[str]:
    try:
        names = enrolled_namespaces(text)
    except ValueError as exc:
        return [str(exc)]
    problems: list[str] = []
    if not names:
        problems.append("AMBIENT_NAMESPACES is empty")
    for name in names:
        if not DNS_LABEL.fullmatch(name):
            problems.append(f"AMBIENT_NAMESPACES entry {name!r} is not a plain namespace name")
    problems.extend(f"AMBIENT_NAMESPACES lists {n} more than once" for n in sorted({n for n in names if names.count(n) > 1}))
    for name in sorted(set(names) - covered):
        problems.append(f"{name} is ambient-enrolled but not covered by check-ambient-netpol.py AMBIENT")
    for name in sorted(covered - set(names)):
        problems.append(f"{name} is covered by check-ambient-netpol.py AMBIENT but not enrolled")
    return problems


def main() -> int:
    path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    try:
        text = path.read_text()
    except OSError as exc:
        print(f"cannot read {path}: {exc}", file=sys.stderr)
        return 1
    problems = validate(text, covered_namespaces())
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print("PASS: ambient enrollment list matches the NetworkPolicy check's namespace set")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
