#!/usr/bin/env python3
"""Check release artifact coverage by the checked-in SBOM/license inputs (Narwhal#53)."""

import csv
import contextlib
import io
import pathlib
import re
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[3]
UNKNOWN_REVIEW = {
    # Bundle SBOM records these artifact identities, but has no license source for them yet.
    ("chart", "base@1.30.1"): ("Helm chart license is not mapped by the bundle SBOM", "supply-chain"),
    ("chart", "cert-manager@v1.20.2"): ("Helm chart license is not mapped by the bundle SBOM", "supply-chain"),
    ("chart", "chaos-mesh@2.8.3"): ("Helm chart license is not mapped by the bundle SBOM", "supply-chain"),
    ("chart", "cni@1.30.1"): ("Helm chart license is not mapped by the bundle SBOM", "supply-chain"),
    ("chart", "common@2.x.x"): ("Helm dependency has no standalone license mapping", "supply-chain"),
    ("chart", "istiod@1.30.1"): ("Helm chart license is not mapped by the bundle SBOM", "supply-chain"),
    ("chart", "k8s-monitoring@4.2.0"): ("Helm chart license is not mapped by the bundle SBOM", "supply-chain"),
    ("chart", "kyverno@3.8.1"): ("Helm chart license is not mapped by the bundle SBOM", "supply-chain"),
    ("chart", "loki@18.4.0"): ("Helm chart license is not mapped by the bundle SBOM", "supply-chain"),
    ("chart", "openbao@0.28.3"): ("Helm chart license is not mapped by the bundle SBOM", "supply-chain"),
    ("chart", "seaweedfs@4.34.0"): ("Helm chart license is not mapped by the bundle SBOM", "supply-chain"),
    ("chart", "tempo@2.2.3"): ("Helm chart license is not mapped by the bundle SBOM", "supply-chain"),
    ("chart", "trivy-operator@0.27.0"): ("Helm chart license is not mapped by the bundle SBOM", "supply-chain"),
    ("chart", "velero@12.0.3"): ("Helm chart license is not mapped by the bundle SBOM", "supply-chain"),
    ("chart", "ztunnel@1.30.1"): ("Helm chart license is not mapped by the bundle SBOM", "supply-chain"),
    ("chart", "apisix@2.13.0"): ("Helm chart license is not mapped by the bundle SBOM", "supply-chain"),
    ("chart", "falco@4.16.0"): ("Helm chart license is not mapped by the bundle SBOM", "supply-chain"),
    ("chart", "harbor@1.19.1"): ("Helm chart license is not mapped by the bundle SBOM", "supply-chain"),
    ("chart", "headlamp@0.42.0"): ("Helm chart license is not mapped by the bundle SBOM", "supply-chain"),
    ("chart", "kube-prometheus-stack@86.2.3"): ("Helm chart license is not mapped by the bundle SBOM", "supply-chain"),
    ("chart", "metallb@0.16.1"): ("Helm chart license is not mapped by the bundle SBOM", "supply-chain"),
    ("chart", "velero-ui@0.14.0"): ("Helm chart license is not mapped by the bundle SBOM", "supply-chain"),
    **{("binary", name): ("Pinned binary has checksum but no SBOM/license inventory row", "supply-chain") for name in (
        "argocd-install.yaml", "cilium", "gateway-api-gatewayclasses.yaml", "gateway-api-gateways.yaml",
        "gateway-api-grpcroutes.yaml", "gateway-api-httproutes.yaml", "gateway-api-referencegrants.yaml",
        "helm", "hubble", "keycloak-keycloakrealmimports.k8s.keycloak.org-v1.yml",
        "keycloak-keycloaks.k8s.keycloak.org-v1.yml", "keycloak-kubernetes.yml", "metrics-server.yaml",
        "nfs-quota-agent", "yq",
    )},
}


def image_key(ref):
    value = ref.split("@", 1)[0]
    tail = value.rsplit("/", 1)[-1]
    if ":" in tail:
        value = value.rsplit(":", 1)[0]
    return value


def load_images(root=ROOT):
    return {image_key(line.strip()) for line in (root / "scripts/airgap/images.txt").read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")}


def load_image_inventory(root=ROOT):
    return {row[0].strip() for row in csv.reader(
        (line for line in (root / "scripts/airgap/lib/component-licenses.tsv").read_text().splitlines()
         if line.strip() and not line.lstrip().startswith("#")), delimiter="\t") if row}


def load_charts(root=ROOT):
    charts = set()
    # Read as text: GitOps templates contain Helm expressions and are not standalone YAML.
    for path in (root / "gitops").rglob("*.yaml"):
        text = path.read_text()
        for name, version in re.findall(
            r"^\s*chart:\s*([^\s]+).*?^\s*targetRevision:\s*[\"']?([^\s\"']+)", text, re.M | re.S
        ):
            if name.startswith("<") or version in {"HEAD", "<CHART_VERSION>"}:
                continue
            charts.add(f"{name}@{version}")
        if path.name == "Chart.yaml":
            for block in re.findall(r"(?ms)^dependencies:\s*\n(.*?)(?=^\S|\Z)", text):
                for name, version in re.findall(r"(?ms)^\s*-\s*name:\s*([^\s]+).*?^\s+version:\s*([^\s]+)", block):
                    charts.add(f"{name}@{version}")
    return charts


def load_binaries(root=ROOT):
    rows = []
    for line in (root / "scripts/airgap/lib/binary-checksums.tsv").read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            rows.append(line.split("\t", 1)[0])
    return set(rows)


def evaluate(images, image_inventory, charts, binaries, unknown):
    failures = []
    classes = {
        "image": (images, image_inventory),
        "chart": (charts, set()),
        "binary": (binaries, set()),
    }
    for kind, (artifacts, inventory) in classes.items():
        if not artifacts:
            failures.append(f"{kind}: zero artifacts evaluated")
        for artifact in sorted(artifacts):
            if artifact in inventory:
                continue
            review = unknown.get((kind, artifact))
            if not review or not all(value.strip() for value in review):
                failures.append(f"{kind}: {artifact} is absent from inventory and UNKNOWN_REVIEW")
        accepted_unknown = sum(1 for artifact in artifacts if artifact not in inventory and
                               (kind, artifact) in unknown and
                               all(value.strip() for value in unknown[(kind, artifact)]))
        covered = len(artifacts & inventory) + accepted_unknown
        print(f"{kind}: evaluated={len(artifacts)} covered={covered} inventory={len(artifacts & inventory)} unknown={accepted_unknown}")
    return failures


def copy_loader_inputs(source, target):
    for relative in (
        "scripts/airgap/images.txt",
        "scripts/airgap/lib/component-licenses.tsv",
        "scripts/airgap/lib/binary-checksums.tsv",
    ):
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, destination)
    for path in (source / "gitops").rglob("*.yaml"):
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


def main(root=ROOT, unknown=None, argv=None):
    unknown = UNKNOWN_REVIEW if unknown is None else unknown
    argv = sys.argv[1:] if argv is None else argv
    images = load_images(root)
    image_inventory = load_image_inventory(root)
    charts = load_charts(root)
    binaries = load_binaries(root)
    failures = evaluate(images, image_inventory, charts, binaries, unknown)
    if "--mutation-verify" in argv:
        cases = [
            evaluate(images, image_inventory - {next(iter(images))}, charts, binaries, unknown),
            evaluate(images, image_inventory, charts | {"mutation-unlisted@1.0.0"}, binaries, unknown),
            evaluate(images, image_inventory, charts, binaries,
                     {**unknown, ("binary", next(iter(binaries))): ("", "supply-chain")}),
        ]
        if not all(cases):
            print("FAIL: mutation was not rejected", file=sys.stderr)
            return 1
        with tempfile.TemporaryDirectory(prefix="sbom-inventory-mutation-") as temp:
            test_root = pathlib.Path(temp)
            copy_loader_inputs(root, test_root)
            inventory_path = test_root / "scripts/airgap/lib/component-licenses.tsv"
            inventory_lines = inventory_path.read_text().splitlines()
            image_inventory_key = next(iter(images & image_inventory))
            inventory_path.write_text("\n".join(
                line for line in inventory_lines
                if not (line.strip() and not line.lstrip().startswith("#") and
                        next(iter(csv.reader([line], delimiter="\t")), [""])[0].strip() == image_inventory_key)
            ) + "\n")
            chart_mutation_root = test_root / "chart-mutation"
            copy_loader_inputs(root, chart_mutation_root)
            unlisted_chart = chart_mutation_root / "gitops/__mutation__/application.yaml"
            unlisted_chart.parent.mkdir(parents=True, exist_ok=True)
            unlisted_chart.write_text("spec:\n  source:\n    chart: mutation-unlisted\n    targetRevision: 1.0.0\n")
            unknown_mutation_root = test_root / "unknown-mutation"
            copy_loader_inputs(root, unknown_mutation_root)
            binary_key = next(iter(binaries))
            patched_unknown = {**unknown, ("binary", binary_key): ("", "supply-chain")}
            file_cases = (
                (test_root, unknown),
                (chart_mutation_root, unknown),
                (unknown_mutation_root, patched_unknown),
            )
            for mutated_root, table in file_cases:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    if main(mutated_root, table, []) == 0:
                        print("FAIL: loader-path mutation was not rejected", file=sys.stderr)
                        return 1
        print("PASS: in-memory and loader-path mutations rejected")
    if failures:
        print("FAIL: " + "; ".join(failures), file=sys.stderr)
        return 1
    print("PASS: release artifact inventory coverage")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
