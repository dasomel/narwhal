#!/usr/bin/env python3
"""Static guard for the trivy-db provisioning gap (D-SECDB-EMPTY-WARN, D-SECDB-PUSH-ENDPOINT).

A fresh cluster's Harbor `trivy-db` project is created EMPTY by 08-5-registry.sh and nothing in
Phase 2 loads it, so trivy-operator scans fail `trivy-db/aquasecurity/trivy-db ... NOT_FOUND` and
the Portal Security/Compliance pages are silently empty. Invariants kept here:

  1. push-security-db.sh authenticates (authfile) and never disables TLS verification, and its
     default endpoint is Harbor's external URL (harbor.<domain>), not the in-cluster Service:
     the registry token realm is https, so an HTTP endpoint dies on the first token fetch.
  2. 08-5-registry.sh checks the project's repo_count after creating it and WARNs with the
     push-security-db.sh remediation.
  3. The portal's display-only TRIVY_DB_REGISTRY (13-2) equals trivy-operator.yaml's
     dbRegistry/dbRepository, derived from the file, never a second hardcoded copy.

`--mutation-verify` proves each invariant fails when broken (on temp copies).
"""
import re
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

PUSH = "scripts/airgap/lib/push-security-db.sh"
REG = "scripts/cluster/08-5-registry.sh"
BIND = "scripts/cluster/13-2-narwhal-portal-bindings.sh"
OP = "gitops/charts/narwhal-apps/templates/trivy-operator.yaml"


def check(root: Path) -> list[str]:
    errs = []
    push = (root / PUSH).read_text()
    if "--authfile" not in push or "skopeo login" not in push:
        errs.append(f"{PUSH}: no authenticated push (skopeo login + --authfile); a public project only allows anonymous pull")
    if "--dest-tls-verify=false" in push:
        errs.append(f"{PUSH}: disables TLS verification; use --ca-cert (certs.d layout) instead")
    m = re.search(r'^SECDB_REGISTRY="\$\{AIRGAP_SECURITY_DB_REGISTRY:-(.*)\}"$', push, re.M)
    if not m or "svc.cluster.local" in m.group(1) or "${DOMAIN}" not in m.group(1):
        errs.append(f"{PUSH}: default push registry must be harbor.${{DOMAIN}} (https token realm), got {m.group(1) if m else None}")

    reg = (root / REG).read_text()
    if "repo_count" not in reg or "push-security-db.sh" not in reg or not re.search(r'echo "WARN:[^"]*trivy-db', reg):
        errs.append(f"{REG}: must WARN (repo_count check + push-security-db.sh remediation) when the trivy-db project is empty")

    vals = yaml.safe_load((root / OP).read_text())["spec"]["source"]["helm"]["valuesObject"]["trivy"]
    want = f"{vals['dbRegistry']}/{vals['dbRepository']}"
    bm = re.search(r'TRIVY_DB_REGISTRY="([^"]*)"', (root / BIND).read_text())
    if not bm or bm.group(1) != want:
        errs.append(f"{BIND}: TRIVY_DB_REGISTRY={bm.group(1) if bm else None!r} must equal {want!r} (trivy-operator.yaml)")
    return errs


def mutate(root: Path, rel: str, old: str, new: str):
    p = root / rel
    t = p.read_text()
    assert old in t, f"mutation anchor missing in {rel}: {old!r}"
    p.write_text(t.replace(old, new))


def mutation_verify(root: Path) -> int:
    cases = [
        ("anonymous push", PUSH, '--authfile "${AUTHFILE}"', ""),
        ("tls disabled", PUSH, "skopeo copy ", "skopeo copy --dest-tls-verify=false "),
        ("in-cluster default endpoint", PUSH, "harbor.${DOMAIN}}", "harbor.devtools.svc.cluster.local}"),
        ("no empty-project warning", REG, "repo_count", "repos"),
        ("library/ display path", BIND, "harbor.devtools.svc.cluster.local/trivy-db/aquasecurity/trivy-db", "harbor.${DOMAIN}/library/trivy-db"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        for rel in (PUSH, REG, BIND, OP):
            (base / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(root / rel, base / rel)
        if check(base):
            print("clean control failed", check(base), file=sys.stderr)
            return 1
        for name, rel, old, new in cases:
            case = Path(tmp) / "case"
            shutil.rmtree(case, ignore_errors=True)
            for r in (PUSH, REG, BIND, OP):
                (case / r).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(root / r, case / r)
            mutate(case, rel, old, new)
            if not check(case):
                print(f"mutation not caught: {name}", file=sys.stderr)
                return 1
    print(f"OK: clean control passes, {len(cases)} mutations caught")
    return 0


def main() -> int:
    root = Path.cwd()
    if "--mutation-verify" in sys.argv:
        return mutation_verify(root)
    errs = check(root)
    for e in errs:
        print("ERROR:", e, file=sys.stderr)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
