#!/usr/bin/env python3
"""Assert Trivy is not configured to pull its vulnerability/checks data live from the
public internet at scan time (narwhal#48).

trivy-operator.yaml has THREE independent online-pull paths, all invisible to the
existing airgap tooling because none of them go through containerd (they are the
Trivy binary's own OCI registry client, called from inside the scan Job pod):

  - trivy.dbRegistry / trivy.dbRepository        (vulnerability DB)
  - trivy.javaDbRegistry / trivy.javaDbRepository (Java vulnerability DB)
  - policiesBundle.registry / .repository         (config-audit/compliance rego bundle
                                                    — pulled because compliance.reportType:
                                                    all + configAuditScannerEnabled: true
                                                    are both on; this third path was not
                                                    identified by the original narwhal#48
                                                    triage or the narwhal#50 test-strategy
                                                    pass, only found while fixing this)

All three must point at the cluster's own internal registry
(harbor.devtools.svc.cluster.local, per scripts/airgap/lib/push-security-db.sh) rather
than any public registry (ghcr.io, mirror.gcr.io, docker.io, quay.io) — the whole point
of this issue. The negative case in regression-check-kakao.sh runs this against a
mutated temp copy with one of the three reverted to a public registry, never the real
file, and proves the check catches ANY of the three regressing individually.
"""
import sys

import yaml

DEFAULT_PATH = "gitops/charts/narwhal-apps/templates/trivy-operator.yaml"

PUBLIC_REGISTRY_HINTS = ("ghcr.io", "mirror.gcr.io", "docker.io", "quay.io", "gcr.io")

FIELDS = [
    ("trivy", "dbRegistry"),
    ("trivy", "javaDbRegistry"),
    ("policiesBundle", "registry"),
]


def load_values(path: str):
    with open(path) as f:
        doc = yaml.safe_load(f)
    try:
        return doc["spec"]["source"]["helm"]["valuesObject"]
    except (KeyError, TypeError):
        return None


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    values = load_values(path)
    if values is None:
        print(f"ERROR: {path} has no spec.source.helm.valuesObject — not the Application manifest?", file=sys.stderr)
        return 1

    violations = []
    for section, field in FIELDS:
        registry = (values.get(section) or {}).get(field)
        label = f"{section}.{field}"
        if not registry:
            violations.append(f"{label} is not set")
            continue
        if any(hint in registry for hint in PUBLIC_REGISTRY_HINTS):
            violations.append(f"{label}={registry!r} still names a public registry")
        if "harbor" not in registry:
            violations.append(f"{label}={registry!r} does not point at the internal Harbor mirror")

    if violations:
        for v in violations:
            print(f"VIOLATION: {v}", file=sys.stderr)
        return 1

    print(f"OK: all {len(FIELDS)} Trivy DB/checks registry fields point at the internal mirror ({path})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
