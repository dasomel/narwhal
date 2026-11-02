#!/usr/bin/env python3
"""R299: the portal live-dev manifest is the chart Deployment plus image/resources/startupProbe only.

D-PORTAL-DEV-FROM-CHART, D-PORTAL-DEV-FIELD-MANAGER. Renders the chart as ArgoCD would, runs
portal/scripts/dev-cluster.sh --render-only and asserts (1) the generated Deployment equals
the chart Deployment after deleting the three allowed container fields, (2) the fields that
differ are exactly those three, (3) argocd-cm apps_Deployment is exactly
managedFieldsManagers: [narwhal-portal-dev] with no jq paths (a jq guard on a live-only value
is asymmetric target-vs-live), (4) skaffold dev applies as that field manager, (5)
dev-cluster.sh keeps the argocd-cm preflight and the restore trap. --mutation-verify proves
each assertion fails when broken.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
ALLOWED = {"image", "resources", "startupProbe"}
MANAGER = "narwhal-portal-dev"
SCRIPT = "portal/scripts/dev-cluster.sh"
ARGO = "gitops/charts/narwhal-platform/templates/argocd-config.yaml"


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def container(dep):
    return next(c for c in dep["spec"]["template"]["spec"]["containers"] if c["name"] == "narwhal-portal")


def check(root):
    """Return a list of failure strings for the tree at root."""
    fails = []
    values = yaml.safe_load(run(
        ["helm", "template", "narwhal-apps", f"{root}/gitops/charts/narwhal-apps",
         "--show-only", "templates/narwhal-platform.yaml"]).stdout)["spec"]["source"]["helm"]["valuesObject"]
    with tempfile.TemporaryDirectory() as tmp:
        vf = Path(tmp, "values.yaml")
        vf.write_text(yaml.safe_dump(values))
        chart_docs = run(["helm", "template", "narwhal-platform", f"{root}/gitops/charts/narwhal-platform",
                          "-f", str(vf), "--show-only", "templates/narwhal-portal-k8s.yaml"])
        if chart_docs.returncode:
            return [f"chart render failed: {chart_docs.stderr.strip()}"]
        chart = next(d for d in yaml.safe_load_all(chart_docs.stdout)
                     if d and d["kind"] == "Deployment" and d["metadata"]["name"] == "narwhal-portal")
        out = Path(tmp, "gen.yaml")
        gen = run(["bash", f"{root}/{SCRIPT}", "--render-only", str(out)], env={**os.environ, "NARWHAL_ROOT": str(root)})
        if gen.returncode:
            return [f"generator failed: {gen.stderr.strip()}"]
        docs = [d for d in yaml.safe_load_all(out.read_text()) if d]
        if len(docs) != 1:
            return [f"generator must emit exactly the one narwhal-portal Deployment, got {len(docs)} documents"]
        dev = docs[0]
        argo = run(["helm", "template", "narwhal-platform", f"{root}/gitops/charts/narwhal-platform",
                    "-f", str(vf), "--show-only", "templates/argocd-config.yaml"]).stdout
    if dev["kind"] != "Deployment" or dev["metadata"]["name"] != "narwhal-portal":
        fails.append("generated document is not Deployment narwhal-portal")
    cc, dc = container(chart), container(dev)
    changed = {k for k in set(cc) | set(dc) if cc.get(k) != dc.get(k)}
    if changed != ALLOWED:
        fails.append(f"container fields changed {sorted(changed)} != {sorted(ALLOWED)}")
    for d in (chart, dev):
        next(c for c in d["spec"]["template"]["spec"]["containers"] if c["name"] == "narwhal-portal").clear()
    if chart != dev:
        fails.append("Deployment differs from the chart outside the narwhal-portal container")
    cm = next(d for d in yaml.safe_load_all(argo) if d and d.get("kind") == "ConfigMap" and d["metadata"]["name"] == "argocd-cm")
    ign = yaml.safe_load(cm["data"]["resource.customizations.ignoreDifferences.apps_Deployment"])
    if ign != {"managedFieldsManagers": [MANAGER]}:
        fails.append(f"apps_Deployment ignoreDifferences must be exactly managedFieldsManagers: [{MANAGER}], got {ign}")
    skaffold = yaml.safe_load(Path(root, "portal/skaffold.yaml").read_text())
    dev = next(p for p in skaffold["profiles"] if p["name"] == "dev")
    flags = dev.get("deploy", {}).get("kubectl", {}).get("flags", {}).get("apply", [])
    if f"--field-manager={MANAGER}" not in flags or "--server-side" not in flags:
        fails.append(f"skaffold dev profile apply flags lack --server-side/--field-manager={MANAGER}: {flags}")
    sh = Path(root, SCRIPT).read_text()
    for need in ("trap on_exit EXIT", "--field-manager=argocd-controller", "patch configmap argocd-cm", "--restore"):
        if need not in sh:
            fails.append(f"{SCRIPT} lacks {need!r}")
    return fails


def mutation_verify():
    muts = [
        (SCRIPT, "    )\n' \"${tmp}/git-deployment.yaml\"", "    )\n  | del(.spec.template.spec.securityContext)\n' \"${tmp}/git-deployment.yaml\""),
        (SCRIPT, "| .resources.limits.cpu = \"1\"", "| .resources.limits.cpu = \"1\" | del(.securityContext)"),
        (SCRIPT, "| .resources.limits.cpu = \"1\"", "| .resources.limits.cpu = \"1\" | .imagePullPolicy = \"Always\""),
        (SCRIPT, "yq '\n  (.spec.template.spec.containers[]", "yq '\n  del(.spec.template.spec.volumes) | (.spec.template.spec.containers[]"),
        (ARGO, "    managedFieldsManagers:\n    - narwhal-portal-dev\n", "    managedFieldsManagers:\n    - narwhal-portal-dev\n    - kube-controller-manager\n"),
        (ARGO, "    managedFieldsManagers:\n    - narwhal-portal-dev\n", "    jqPathExpressions:\n    - .spec.template.spec.containers[] | select(.image | test(\":dev-\")) | .image\n"),
        (ARGO, "    - narwhal-portal-dev\n", "    - narwhal-portal-other\n"),
        ("portal/skaffold.yaml", "            - --field-manager=narwhal-portal-dev\n", ""),
        (SCRIPT, "trap on_exit EXIT", "trap - EXIT"),
        (SCRIPT, "--field-manager=argocd-controller", "--field-manager=kubectl"),
        (SCRIPT, "patch configmap argocd-cm", "get configmap argocd-cm"),
    ]
    surv = []
    for rel, old, new in muts:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "r")
            shutil.copytree(REPO / "gitops", root / "gitops")
            shutil.copytree(REPO / "portal/scripts", root / "portal/scripts")
            shutil.copy(REPO / "portal/skaffold.yaml", root / "portal/skaffold.yaml")
            f = root / rel
            text = f.read_text()
            if old not in text:
                surv.append(f"mutation target not found: {old!r}")
                continue
            f.write_text(text.replace(old, new, 1))
            if not check(root):
                surv.append(f"mutation survived: {old!r} -> {new!r}")
    return surv


def main():
    for tool in ("helm", "yq"):
        if not shutil.which(tool):
            print(f"SKIP: {tool} not on PATH")
            return 0
    fails = check(REPO)
    if "--mutation-verify" in sys.argv:
        fails += mutation_verify()
    for f in fails:
        print(f"FAIL: {f}")
    if not fails:
        print("PASS: portal dev manifest = chart Deployment + image/resources/startupProbe; argocd-cm ignores only field manager narwhal-portal-dev"
              + ("; 10 mutations caught" if "--mutation-verify" in sys.argv else ""))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
