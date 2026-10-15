#!/usr/bin/env python3
"""Every kakaocloud_public_ip resource must carry lifecycle { prevent_destroy = true } (D-PUBIP)."""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
TF_DIR = ROOT / "csp/kakao-cloud/terraform"
RESOURCE = re.compile(r'^resource "kakaocloud_public_ip" "([^"]+)" \{\n(.*?)^\}\n', re.S | re.M)
GUARD = re.compile(r'^\s*lifecycle \{[^}]*^\s*prevent_destroy\s*=\s*true\s*$[^}]*\}', re.S | re.M)


def unguarded(files):
    found, missing = 0, []
    for name, text in files.items():
        for m in RESOURCE.finditer(text):
            found += 1
            if not GUARD.search(m.group(2)):
                missing.append(f"{name}: kakaocloud_public_ip.{m.group(1)}")
    return found, missing


def load(root):
    return {str(p.relative_to(root)): p.read_text() for p in sorted(root.rglob("*.tf"))}


def main():
    found, missing = unguarded(load(TF_DIR))
    if found < 5:
        print(f"expected at least 5 kakaocloud_public_ip resources, found {found}")
        return 1
    if missing:
        print("missing prevent_destroy = true:\n  " + "\n  ".join(missing))
        return 1
    if "--mutation-verify" in sys.argv:
        files = load(TF_DIR)
        victim = next(k for k, v in files.items() if RESOURCE.search(v))
        files[victim] = files[victim].replace("prevent_destroy = true", "prevent_destroy = false")
        if not unguarded(files)[1]:
            print("mutation (prevent_destroy removed) was not detected")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
