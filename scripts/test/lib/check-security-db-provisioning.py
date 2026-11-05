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
  4. D-SECDB-SOURCES-SEAM: security-db.txt, the portal's source list (trivy-db-sources.ts) and the
     Harbor paths trivy-operator.yaml pulls (dbRepository, javaDbRepository, policiesBundle) name the
     same set of repositories and tags, so a new source cannot be mirrored but invisible in the
     Portal card, or shown there but never fetched.

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
LIST = "scripts/airgap/security-db.txt"
SOURCES = "portal/src/lib/trivy-db-sources.ts"
FILES = (PUSH, REG, BIND, OP, LIST, SOURCES)


def check(root: Path) -> list[str]:
    errs = []
    push = (root / PUSH).read_text()
    # Every skopeo copy/login/inspect must carry --authfile (continuations joined, comments stripped).
    code = "\n".join(l for l in re.sub(r"\\\n", " ", push).splitlines() if not l.lstrip().startswith("#"))
    code = re.sub(r"[ \t]+#[^\n]*", "", code)
    calls = re.findall(r"skopeo (?:copy|login|inspect)\b[^\n]*", code)
    if not any(c.startswith("skopeo login") for c in calls):
        errs.append(f"{PUSH}: no skopeo login; a public project only allows anonymous pull")
    for c in calls:
        if "--authfile" not in c:
            errs.append(f"{PUSH}: skopeo call without --authfile (anonymous Harbor access): {c[:60]}")
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

    # name -> "org/repo:tag" from the three places that must agree.
    listed = {}
    for line in (root / LIST).read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            name, ref = line.split("\t")
            listed[name] = ref.split("/", 1)[1]
    ts = (root / SOURCES).read_text()
    portal = {
        m[0]: f"{m[1]}:{m[2]}"
        for m in re.findall(r'name: "([^"]+)", repository: "([^"]+)", tag: "([^"]+)"', ts)
    }
    if listed != portal:
        errs.append(f"{SOURCES}: sources {portal} != {LIST} {listed}")
    top = yaml.safe_load((root / OP).read_text())["spec"]["source"]["helm"]["valuesObject"]
    pb = top["policiesBundle"]
    # Tags are Trivy's business for the two DBs (it appends :2 / :1); only the checks bundle is tagged here.
    pulled = {vals["dbRepository"], vals["javaDbRepository"], pb["repository"]}
    mirrored = {f"trivy-db/{ref.rsplit(':', 1)[0]}" for ref in listed.values()}
    if pulled != mirrored:
        errs.append(f"{OP}: pulled repositories {sorted(pulled)} != mirrored in {LIST} {sorted(mirrored)}")
    if listed.get("trivy-checks") != f"{pb['repository'].removeprefix('trivy-db/')}:{pb['tag']}":
        errs.append(f"{OP}: policiesBundle tag {pb['tag']!r} != {LIST} trivy-checks {listed.get('trivy-checks')!r}")
    return errs


def mutate(root: Path, rel: str, old: str, new: str):
    p = root / rel
    t = p.read_text()
    assert old in t, f"mutation anchor missing in {rel}: {old!r}"
    p.write_text(t.replace(old, new))


def mutation_verify(root: Path) -> int:
    cases = [
        ("anonymous push", PUSH, '--authfile "${AUTHFILE}"', ""),
        ("copy without authfile", PUSH, 'skopeo copy --authfile "${AUTHFILE}" ', "skopeo copy "),
        ("login without authfile", PUSH, '     --authfile "${AUTHFILE}" -u admin', "     -u admin"),
        ("tls disabled", PUSH, "skopeo copy ", "skopeo copy --dest-tls-verify=false "),
        ("in-cluster default endpoint", PUSH, "harbor.${DOMAIN}}", "harbor.devtools.svc.cluster.local}"),
        ("no empty-project warning", REG, "repo_count", "repos"),
        ("source missing from the portal list", SOURCES, 'name: "trivy-java-db", repository: "aquasecurity/trivy-java-db", tag: "1"', 'name: "trivy-java-db-x", repository: "aquasecurity/trivy-java-db", tag: "1"'),
        ("portal tag drift", SOURCES, 'repository: "aquasecurity/trivy-db", tag: "2"', 'repository: "aquasecurity/trivy-db", tag: "3"'),
        ("operator pulls an unmirrored repository", OP, "trivy-db/aquasecurity/trivy-java-db", "trivy-db/aquasecurity/trivy-java-db2"),
        ("operator checks tag drift", OP, 'tag: "1"', 'tag: "2"'),
        ("library/ display path", BIND, "harbor.devtools.svc.cluster.local/trivy-db/aquasecurity/trivy-db", "harbor.${DOMAIN}/library/trivy-db"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        for rel in FILES:
            (base / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(root / rel, base / rel)
        if check(base):
            print("clean control failed", check(base), file=sys.stderr)
            return 1
        for name, rel, old, new in cases:
            case = Path(tmp) / "case"
            shutil.rmtree(case, ignore_errors=True)
            for r in FILES:
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
