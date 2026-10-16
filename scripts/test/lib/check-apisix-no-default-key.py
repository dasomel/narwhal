#!/usr/bin/env python3
"""R256: no tracked text file contains the public upstream APISIX admin/viewer key (run from repo root).

The keys are recognised by sha256 (APISIX_DEFAULT_KEY_SHA256S in scripts/common/lib.sh), so this
check itself carries no key literal. Vendored chart tarballs are binary and not tracked."""
import hashlib, re, subprocess, sys

lib = open("scripts/common/lib.sh").read()
m = re.search(r'APISIX_DEFAULT_KEY_SHA256S="([0-9a-f ]+)"', lib)
assert m, "APISIX_DEFAULT_KEY_SHA256S missing from lib.sh"
bad = set(m.group(1).split())
assert len(bad) == 2
tok = re.compile(rb"(?<![0-9A-Za-z])[0-9a-f]{32}(?![0-9A-Za-z])")
hits = []
for f in subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0"):
    if not f:
        continue
    try:
        data = open(f, "rb").read()
    except OSError:
        continue
    if b"\0" in data:
        continue
    for t in set(tok.findall(data)):
        if hashlib.sha256(t).hexdigest() in bad:
            hits.append(f)
if hits:
    print("public APISIX default key found in:", *sorted(set(hits)), file=sys.stderr)
    sys.exit(1)
