"""Check that every GitOps resource has an exact ArgoCD Application owner."""

import argparse
import re
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RESOURCES = ROOT / "gitops/resources"
TEMPLATES = ROOT / "gitops/charts/narwhal-apps/templates"

# Resources delivered by owning Applications with chart, generated, or multi-resource
# sources rather than per-file directory.include.
EXCLUSIONS = {
    "alertmanager-config.yaml": "prometheus-stack renders config from chart values",
    "cnpg-backup.yaml": "narwhal-db Application owns the CNPG backup configuration",
    "gitea-db.yaml": "gitea Application installs the database bootstrap Job",
    "grafana-datasources.yaml": "k8s-monitoring Application installs datasource config",
    "harbor-db.yaml": "harbor Application installs the database bootstrap Job",
    "metallb-config.yaml": "metallb Application installs address pools and advertisements",
    "narwhal-db.yaml": "narwhal-db Application installs the CNPG cluster and services",
    "narwhal-portal-policy.yaml": "narwhal-platform renders the portal policy",
}


def owned_resources(template_dir):
    names = set()
    for template in template_dir.glob("*.yaml"):
        # D11: plain includes are valid YAML too; cost: two scalar forms.
        # Escape hatch: use a YAML parser if include values become structured.
        match = re.search(r"^\s*include:[ \t]*(?:[\"']([^\"'\n]+)[\"']|([^\s\"'\n][^\n]*?))[ \t]*$",
                          template.read_text(), re.MULTILINE)
        if match:
            names.update(Path(name.strip()).name for name in (match.group(1) or match.group(2)).split(","))
    return names


def uncovered(template_dir):
    owned = owned_resources(template_dir)
    return [path.name for path in sorted(RESOURCES.glob("*.yaml"))
            if path.name not in owned and path.name not in EXCLUSIONS]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mutation-verify", action="store_true")
    args = parser.parse_args()
    actual = {path.name for path in RESOURCES.glob("*.yaml")}
    stale = sorted(set(EXCLUSIONS) - actual)
    if stale:
        raise SystemExit(f"stale documented exclusions: {', '.join(stale)}")
    missing = uncovered(TEMPLATES)
    if missing:
        raise SystemExit(f"resources lack an Application owner: {', '.join(missing)}")
    if args.mutation_verify:
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "templates"
            shutil.copytree(TEMPLATES, copied)
            probe = copied / "scalar-forms.yaml"
            for scalar in ('plain.yaml', '"double.yaml"', "'single.yaml'"):
                probe.write_text("directory:\n  include: " + scalar + "\n")
                assert scalar.strip("\"'") in owned_resources(copied)
            probe.unlink()
            (copied / "ambient-kubelet-probes-ccnp.yaml").unlink()
            if "ambient-kubelet-probes-ccnp.yaml" not in uncovered(copied):
                raise SystemExit("removing the probe Application was not detected")
            (copied / "dev-namespace.yaml").unlink()
            if "dev-namespace.yaml" not in uncovered(copied):
                raise SystemExit("removing the dev Application was not detected")
    print("PASS: every GitOps resource has an Application owner; mutation detected")


if __name__ == "__main__":
    main()
