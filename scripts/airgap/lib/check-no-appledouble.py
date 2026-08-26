#!/usr/bin/env python3
"""Fail if a tar archive carries a macOS AppleDouble sidecar (._<name>).

macOS's system tar (bsdtar) writes a binary ._<name> sidecar for every packaged file
that carries an extended attribute, and folds it back into the real file's metadata
in its OWN listing — so `tar tzf` on the machine that built the archive looks clean.
GNU tar on Linux does not do that folding; it extracts the sidecar as a literal file,
and if that file lands somewhere Helm parses as YAML (e.g. a CRD directory), the
binary content fails with "control characters are not allowed". Verification must
therefore read the archive with Python's tarfile module (no macOS-specific folding),
never with the producer's own bsdtar listing.

Usage: check-no-appledouble.py <path-to.tgz> [<path-to.tgz> ...]
"""

import sys
import tarfile
from pathlib import Path


def appledouble_members(tgz_path):
    with tarfile.open(tgz_path) as tf:
        return [name for name in tf.getnames() if Path(name).name.startswith("._")]


def main():
    if len(sys.argv) < 2:
        print(f"usage: {Path(sys.argv[0]).name} <path-to.tgz> [<path-to.tgz> ...]", file=sys.stderr)
        return 2

    errors = []
    for raw_path in sys.argv[1:]:
        path = Path(raw_path)
        if not path.is_file():
            errors.append(f"{path}: not found")
            continue
        bad = appledouble_members(path)
        if bad:
            errors.append(f"{path}: {len(bad)} AppleDouble entr{'y' if len(bad) == 1 else 'ies'}: {', '.join(bad)}")

    if errors:
        print("AppleDouble check failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(f"AppleDouble check passed: {len(sys.argv) - 1} archive(s) clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
