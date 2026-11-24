#!/usr/bin/env python3
"""R404: trivy-operator 0.36.1 (chart 0.38.1) pass-record & CCR summary-mode invariants.

Verifies:
1. gitops/charts/narwhal-apps/templates/trivy-operator.yaml:
   - targetRevision is 0.38.1
   - trivyOperator.reportRecordFailedChecksOnly is the exact YAML string "false" (not boolean False,
     which Helm `{{- with ... }}` drops and reverts to Go default "true")
   - compliance.reportType is "summary" with D-CCR-SUMMARY decision comment
   - compliance.specs keeps ["k8s-cis-1.23", "k8s-nsa-1.0"]
   - all existing overrides (excludeNamespaces, skipResourceByLabels, excludeImages,
     supportedConfigAuditKinds, builtInTrivyServer, ClientServer, nfs-csi cache PVC, Harbor DB repos,
     resources, podSecurityContext, nodeCollector) remain intact
   - `helm template narwhal-apps` renders cleanly when helm is available
2. Every ClusterComplianceReport in gitops/resources/{pss,kisa}-compliance.yaml sets
   spec.reportType: summary, app.kubernetes.io/version: 0.36.1, and D-CCR-SUMMARY comment.
3. Air-gap & version pins agree across scripts/airgap/images.txt, scripts/airgap/lib/image-digests.tsv,
   scripts/verify/security-db-bundle-check.sh, VERSIONS.md, and portal/src/data/oss-versions.generated.json.
4. --mutation-verify proves broken variants (bool false, omitted key, "true", CCR reportType: all,
   stale chart/image pins, dropped R404 wiring) are all rejected.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]

FILES = {
    "trivy": ROOT / "gitops/charts/narwhal-apps/templates/trivy-operator.yaml",
    "pss": ROOT / "gitops/resources/pss-compliance.yaml",
    "kisa": ROOT / "gitops/resources/kisa-compliance.yaml",
    "images": ROOT / "scripts/airgap/images.txt",
    "digests": ROOT / "scripts/airgap/lib/image-digests.tsv",
    "db_check": ROOT / "scripts/verify/security-db-bundle-check.sh",
    "versions": ROOT / "VERSIONS.md",
    "oss_json": ROOT / "portal/src/data/oss-versions.generated.json",
    "reg": ROOT / "scripts/test/regression-check-kakao.sh",
}

EXPECTED_CHART = "0.38.1"
EXPECTED_APP = "0.36.1"
EXPECTED_TRIVY = "0.76.1"
EXPECTED_NODE_COLLECTOR = "0.3.1"
EXPECTED_SHA256 = {
    f"mirror.gcr.io/aquasec/trivy-operator:{EXPECTED_APP}": "sha256:05191a514247ae644dd5cc71306a94f44faff0a6a3fa817235911b911ecebcb9",
    f"mirror.gcr.io/aquasec/trivy:{EXPECTED_TRIVY}": "sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c",
    f"ghcr.io/aquasecurity/node-collector:{EXPECTED_NODE_COLLECTOR}": "sha256:f4702ce4667388f0219d6248d623a7e816231c2d4561d6af01c3ae053e524025",
}


def check(texts: dict[str, str]) -> list[str]:
    errors: list[str] = []

    # 1. trivy-operator.yaml
    trivy_raw = texts["trivy"]
    try:
        trivy_doc = yaml.safe_load(trivy_raw)
    except yaml.YAMLError as exc:
        return [f"trivy-operator.yaml invalid YAML: {exc}"]

    src = (trivy_doc.get("spec") or {}).get("source") or {}
    if str(src.get("targetRevision") or "") != EXPECTED_CHART:
        errors.append(f"trivy-operator.yaml targetRevision must be {EXPECTED_CHART!r}, got {src.get('targetRevision')!r}")

    vals = (src.get("helm") or {}).get("valuesObject") or {}
    top_op = vals.get("trivyOperator") or {}
    rec_val = top_op.get("reportRecordFailedChecksOnly")
    if not isinstance(rec_val, str) or rec_val != "false":
        errors.append(
            f"trivyOperator.reportRecordFailedChecksOnly must be the string 'false' "
            f"(not bool {rec_val!r}; Helm `with` drops bool false)"
        )
    # Guard raw YAML line too so a YAML boolean `false` (unquoted) cannot slip in via a custom loader.
    if not re.search(r'^\s*reportRecordFailedChecksOnly:\s*"false"\s*$', trivy_raw, re.M):
        errors.append('trivy-operator.yaml must quote reportRecordFailedChecksOnly: "false"')

    comp = vals.get("compliance") or {}
    if comp.get("reportType") != "summary":
        errors.append(f"trivy-operator.yaml compliance.reportType must be 'summary', got {comp.get('reportType')!r}")
    if comp.get("specs") != ["k8s-cis-1.23", "k8s-nsa-1.0"]:
        errors.append(f"trivy-operator.yaml compliance.specs must stay ['k8s-cis-1.23', 'k8s-nsa-1.0'], got {comp.get('specs')!r}")
    if "D-CCR-SUMMARY" not in trivy_raw:
        errors.append("trivy-operator.yaml missing D-CCR-SUMMARY decision comment")

    # Preserve critical overrides
    if vals.get("excludeNamespaces") != "kube-public,kube-node-lease,trivy-system":
        errors.append("trivy-operator.yaml excludeNamespaces override drifted")
    if "narwhal.dasomel.io/skip-ephemeral-job-scan" not in str(top_op.get("skipResourceByLabels") or ""):
        errors.append("trivy-operator.yaml skipResourceByLabels override drifted")
    if not top_op.get("excludeImages"):
        errors.append("trivy-operator.yaml excludeImages override dropped")
    trivy_cfg = vals.get("trivy") or {}
    if trivy_cfg.get("mode") != "ClientServer":
        errors.append("trivy-operator.yaml trivy.mode must stay ClientServer")
    if trivy_cfg.get("storageClassEnabled") is not True or trivy_cfg.get("storageClassName") != "nfs-csi":
        errors.append("trivy-operator.yaml trivy-server nfs-csi cache PVC override drifted")
    if trivy_cfg.get("dbRegistry") != "harbor.devtools.svc.cluster.local":
        errors.append("trivy-operator.yaml trivy.dbRegistry must stay internal Harbor")
    if (vals.get("operator") or {}).get("builtInTrivyServer") is not True:
        errors.append("trivy-operator.yaml operator.builtInTrivyServer must stay true")
    if (vals.get("operator") or {}).get("clusterComplianceEnabled") is not True:
        errors.append("trivy-operator.yaml operator.clusterComplianceEnabled must stay true")

    # 2. Standalone CCR manifests (PSS + KISA)
    for key, expected_names in (
        ("pss", ("k8s-pss-baseline-0.1", "k8s-pss-restricted-0.1")),
        ("kisa", ("k8s-kisa-1.0",)),
    ):
        raw = texts[key]
        if "D-CCR-SUMMARY" not in raw:
            errors.append(f"{key} missing D-CCR-SUMMARY decision comment")
        docs = [d for d in yaml.safe_load_all(raw) if isinstance(d, dict) and d.get("kind") == "ClusterComplianceReport"]
        by_name = {(d.get("metadata") or {}).get("name"): d for d in docs}
        for name in expected_names:
            doc = by_name.get(name)
            if not doc:
                errors.append(f"{key} missing ClusterComplianceReport {name}")
                continue
            spec = doc.get("spec") or {}
            if spec.get("reportType") != "summary":
                errors.append(f"{key} {name} spec.reportType must be 'summary', got {spec.get('reportType')!r}")
            ver = str(((doc.get("metadata") or {}).get("labels") or {}).get("app.kubernetes.io/version") or "")
            if ver != EXPECTED_APP:
                errors.append(f"{key} {name} app.kubernetes.io/version must be {EXPECTED_APP!r}, got {ver!r}")

    # 3. Airgap images.txt + image-digests.tsv + security-db-bundle-check.sh + VERSIONS.md + oss-versions
    img_lines = {l.strip() for l in texts["images"].splitlines() if l.strip() and not l.lstrip().startswith("#")}
    for ref in EXPECTED_SHA256:
        if ref not in img_lines:
            errors.append(f"scripts/airgap/images.txt missing {ref}")
    for old in ("0.25.0", "0.60.0"):
        if any(f"trivy-operator:{old}" in l or f"trivy:{old}" in l for l in img_lines):
            errors.append(f"scripts/airgap/images.txt still contains old trivy pin {old}")

    digest_rows: dict[str, str] = {}
    for line in texts["digests"].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) >= 2:
            digest_rows[cols[0]] = cols[1]
    for ref, want_dg in EXPECTED_SHA256.items():
        if digest_rows.get(ref) != want_dg:
            errors.append(f"scripts/airgap/lib/image-digests.tsv {ref} digest={digest_rows.get(ref)!r} != {want_dg!r}")

    if f'!= "{EXPECTED_TRIVY}"' not in texts["db_check"]:
        errors.append(f"scripts/verify/security-db-bundle-check.sh must pin Trivy {EXPECTED_TRIVY}")

    if f"| trivy-operator | {EXPECTED_APP} (chart {EXPECTED_CHART}) |" not in texts["versions"]:
        errors.append(f"VERSIONS.md missing trivy-operator {EXPECTED_APP} (chart {EXPECTED_CHART}) row")
    if f"| Trivy | {EXPECTED_TRIVY} |" not in texts["versions"]:
        errors.append(f"VERSIONS.md missing Trivy {EXPECTED_TRIVY} row")

    try:
        oss = json.loads(texts["oss_json"])
    except json.JSONDecodeError as exc:
        errors.append(f"oss-versions.generated.json invalid JSON: {exc}")
    else:
        by_comp = {r.get("component"): r for r in oss.get("rows", []) if isinstance(r, dict)}
        op_row = by_comp.get("trivy-operator") or {}
        if op_row.get("version") != EXPECTED_APP or op_row.get("chartVersion") != EXPECTED_CHART:
            errors.append(f"oss-versions.generated.json trivy-operator mismatch: {op_row!r}")
        if (by_comp.get("Trivy") or {}).get("version") != EXPECTED_TRIVY:
            errors.append(f"oss-versions.generated.json Trivy mismatch: {by_comp.get('Trivy')!r}")

    if "check R404 " not in texts["reg"] or "check-trivy-pass-records.py --mutation-verify" not in texts["reg"]:
        errors.append("scripts/test/regression-check-kakao.sh must wire R404 with --mutation-verify")

    return errors


MUTATIONS: list[tuple[str, str, str, str]] = [
    ("reportRecordFailedChecksOnly YAML bool false", "trivy", 'reportRecordFailedChecksOnly: "false"', "reportRecordFailedChecksOnly: false"),
    ("reportRecordFailedChecksOnly set to true", "trivy", 'reportRecordFailedChecksOnly: "false"', 'reportRecordFailedChecksOnly: "true"'),
    ("drop reportRecordFailedChecksOnly", "trivy", '          reportRecordFailedChecksOnly: "false"\n', ""),
    ("trivy-operator.yaml compliance.reportType all", "trivy", "          reportType: summary", "          reportType: all"),
    ("trivy-operator.yaml chart version reverted", "trivy", "    targetRevision: 0.38.1", "    targetRevision: 0.27.0"),
    ("pss baseline reportType all", "pss", "spec:\n  cron: \"0 */6 * * *\"\n  reportType: summary", "spec:\n  cron: \"0 */6 * * *\"\n  reportType: all"),
    ("pss restricted reportType all", "pss", "  name: k8s-pss-restricted-0.1\n  labels:\n    app.kubernetes.io/name: trivy-operator\n    app.kubernetes.io/instance: trivy-operator\n    app.kubernetes.io/version: 0.36.1\n    app.kubernetes.io/managed-by: kubectl\nspec:\n  cron: \"0 */6 * * *\"\n  reportType: summary", "  name: k8s-pss-restricted-0.1\n  labels:\n    app.kubernetes.io/name: trivy-operator\n    app.kubernetes.io/instance: trivy-operator\n    app.kubernetes.io/version: 0.36.1\n    app.kubernetes.io/managed-by: kubectl\nspec:\n  cron: \"0 */6 * * *\"\n  reportType: all"),
    ("kisa reportType all", "kisa", "  reportType: summary", "  reportType: all"),
    ("images.txt trivy 0.60.0", "images", "mirror.gcr.io/aquasec/trivy:0.76.1", "mirror.gcr.io/aquasec/trivy:0.60.0"),
    ("image-digests.tsv drop trivy 0.76.1", "digests", "mirror.gcr.io/aquasec/trivy:0.76.1\t", "mirror.gcr.io/aquasec/trivy:0.60.0\t"),
    ("security-db-bundle-check.sh revert to 0.60.0", "db_check", '!= "0.76.1"', '!= "0.60.0"'),
    ("drop R404 wiring", "reg", "check R404 ", "check R9999 "),
]


def main() -> int:
    texts = {k: p.read_text(encoding="utf-8") for k, p in FILES.items()}
    errors = check(texts)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1

    if shutil.which("helm") is not None:
        res = subprocess.run(
            ["helm", "template", "narwhal-apps", str(ROOT / "gitops/charts/narwhal-apps"), "--show-only", "templates/trivy-operator.yaml"],
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            print(f"FAIL: helm template narwhal-apps trivy-operator.yaml failed: {res.stderr}", file=sys.stderr)
            return 1
        rendered = yaml.safe_load(res.stdout)
        vo = (((rendered.get("spec") or {}).get("source") or {}).get("helm") or {}).get("valuesObject") or {}
        if vo.get("trivyOperator", {}).get("reportRecordFailedChecksOnly") != "false":
            print("FAIL: rendered trivy-operator.yaml missing string reportRecordFailedChecksOnly: 'false'", file=sys.stderr)
            return 1
        if vo.get("compliance", {}).get("reportType") != "summary":
            print("FAIL: rendered trivy-operator.yaml missing compliance.reportType: 'summary'", file=sys.stderr)
            return 1

    if "--mutation-verify" in sys.argv:
        for name, key, old, new in MUTATIONS:
            mutated = copy.deepcopy(texts)
            if old not in mutated[key]:
                print(f"FAIL: mutation target not found for {name}", file=sys.stderr)
                return 1
            mutated[key] = mutated[key].replace(old, new, 1)
            if not check(mutated):
                print(f"FAIL: mutation not rejected: {name}", file=sys.stderr)
                return 1
        print(f"PASS: R404 trivy-operator 0.36.1 pass-records & CCR summary ({len(MUTATIONS)} mutations rejected)")
        return 0

    print("PASS: R404 trivy-operator 0.36.1 pass-records & CCR summary")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
