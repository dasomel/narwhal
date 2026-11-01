#!/usr/bin/env python3
"""R299: the portal live-dev manifest is the chart Deployment plus image/resources/startupProbe only.

D-PORTAL-DEV-FROM-CHART. Renders the chart as ArgoCD would, runs
portal/scripts/dev-cluster.sh --render-only and asserts (1) the generated Deployment equals
the chart Deployment after deleting the three allowed container fields, (2) the set of
fields that differ is exactly those three, (3) argocd-config.yaml ignoreDifferences covers
exactly those fields behind the :dev- guard. --mutation-verify proves the check fails when
the generator drops/changes another field or the ignore list widens/narrows.
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
    paths = yaml.safe_load(cm["data"]["resource.customizations.ignoreDifferences.apps_Deployment"])["jqPathExpressions"]
    guard = r'^\.spec\.template\.spec\.containers\[\] \| select\(\.name == "narwhal-portal"\) \| select\(\.image \| test\(":dev-"\)\) \| \.(\w+)$'
    got = set()
    for p in paths:
        m = re.match(guard, p)
        if not m:
            fails.append(f"ignoreDifferences path not behind the :dev- guard: {p}")
        else:
            got.add(m.group(1))
    if got != ALLOWED:
        fails.append(f"ignoreDifferences fields {sorted(got)} != {sorted(ALLOWED)}")
    return fails


def mutation_verify():
    muts = [
        (SCRIPT, "    )\n' \"${tmp}/rendered.yaml\"", "    )\n  | del(.spec.template.spec.securityContext)\n' \"${tmp}/rendered.yaml\""),
        (SCRIPT, "| .resources.limits.cpu = \"1\"", "| .resources.limits.cpu = \"1\" | del(.securityContext)"),
        (SCRIPT, "| .resources.limits.cpu = \"1\"", "| .resources.limits.cpu = \"1\" | .imagePullPolicy = \"Always\""),
        (SCRIPT, "select(.kind == \"Deployment\" and .metadata.name == \"narwhal-portal\")\n  |",
         "select(.kind == \"Deployment\" and .metadata.name == \"narwhal-portal\")\n  | del(.spec.template.spec.volumes)\n  |"),
        (ARGO, "| .startupProbe\n", "| .livenessProbe\n"),
        (ARGO, "    - .spec.template.spec.containers[] | select(.name == \"narwhal-portal\") | select(.image | test(\":dev-\")) | .startupProbe\n", ""),
        (ARGO, "select(.image | test(\":dev-\")) | .resources", "| .resources"),
    ]
    surv = []
    for rel, old, new in muts:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "r")
            shutil.copytree(REPO / "gitops", root / "gitops")
            shutil.copytree(REPO / "portal/scripts", root / "portal/scripts")
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
        print("PASS: portal dev manifest = chart Deployment + image/resources/startupProbe; ignoreDifferences covers exactly those"
              + ("; 7 mutations caught" if "--mutation-verify" in sys.argv else ""))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
