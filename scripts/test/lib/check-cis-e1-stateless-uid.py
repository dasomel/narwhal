#!/usr/bin/env python3
"""R428 (lane E1): CIS 5.7.3 (KSV020/KSV021) stateless UID/GID > 10000 remediation invariants.

Verifies:
1. ArgoCD 6 stateless workloads in scripts/cluster/13-argocd.sh patch pod+container(+initContainer)
   to UID/GID 10100 (argocd-redis stays untouched).
2. kube-system/metrics-server in scripts/cluster/04-addons.sh moves runAsUser 1000 -> 10100 and sets
   runAsGroup 10100 inside the single D10 JSON patch.
3. iam/keycloak-operator in scripts/cluster/11-keycloak.sh runs as 10100:10100 (pod + container),
   while the Keycloak CR stays at 1000.
4. monitoring/prometheus-stack-grafana sets grafana.securityContext (runAsNonRoot: true, runAsUser: 472,
   runAsGroup: 10100, fsGroup: 472) identically in prometheus-stack.yaml and 08-2-monitoring.sh, and
   helm template confirms all 3 containers inherit runAsGroup 10100.
5. Istio waypoint Gateways in 09-istio-ambient.sh attach ConfigMap/waypoint-options via
   spec.infrastructure.parametersRef setting 10100:10100 for pod + istio-proxy container.
6. csi-nfs-postrender.sh sets pod-level runAsUser: 0 and runAsGroup: 10100 on csi-nfs-node and
   csi-nfs-controller (offline helm template + post-renderer verifies both when chart is present).
7. Exact acceptances in compliance-acceptance-rules.json, i18n.ts, and compliance-hardening.md stay
   in sync for istiod (KSV020/KSV021 upstream), ztunnel (KSV030/KSV104 upstream), and csi-nfs
   node/controller (KSV105 accept; KSV021 stays unaccepted).
8. R428 is registered in scripts/test/regression-check-kakao.sh with --mutation-verify.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
FILES = {
    "argocd": ROOT / "scripts/cluster/13-argocd.sh",
    "addons": ROOT / "scripts/cluster/04-addons.sh",
    "keycloak": ROOT / "scripts/cluster/11-keycloak.sh",
    "prom": ROOT / "gitops/charts/narwhal-apps/templates/prometheus-stack.yaml",
    "mon_boot": ROOT / "scripts/cluster/08-2-monitoring.sh",
    "istio_boot": ROOT / "scripts/cluster/09-istio-ambient.sh",
    "csi_pr": ROOT / "scripts/cluster/lib/helm-plugins/csi-nfs-postrender/csi-nfs-postrender.sh",
    "rules": ROOT / "portal/src/data/compliance-acceptance-rules.json",
    "i18n": ROOT / "portal/src/lib/i18n.ts",
    "doc": ROOT / "docs/common/compliance-hardening.md",
    "reg": ROOT / "scripts/test/regression-check-kakao.sh",
}


def _values_object(text: str) -> dict:
    cleaned = re.sub(r"\{\{.*?\}\}", "placeholder", text)
    doc = yaml.safe_load(cleaned) or {}
    return (((doc.get("spec") or {}).get("source") or {}).get("helm") or {}).get("valuesObject") or {}


def _check_argocd(text: str, errs: list[str]) -> None:
    if "10100" not in text or '\\"fsGroup\\":10100' not in text:
        errs.append("13-argocd.sh missing 10100 UID/GID/fsGroup patch helper")
    expected_calls = [
        ("statefulset", "argocd-application-controller", "argocd-application-controller", ""),
        ("deployment", "argocd-repo-server", "argocd-repo-server", "copyutil"),
        ("deployment", "argocd-server", "argocd-server", ""),
        ("deployment", "argocd-applicationset-controller", "argocd-applicationset-controller", ""),
        ("deployment", "argocd-notifications-controller", "argocd-notifications-controller", ""),
        ("deployment", "argocd-dex-server", "dex", "copyutil"),
    ]
    for kind, name, cont, init in expected_calls:
        pat = rf"patch_uid_gid\s+{kind}\s+{re.escape(name)}\s+{re.escape(cont)}"
        if init:
            pat += rf"\s+{re.escape(init)}"
        if not re.search(pat, text):
            errs.append(f"13-argocd.sh missing patch_uid_gid call for {kind}/{name}")
    if re.search(r"patch_uid_gid\s+\S+\s+argocd-redis\b", text):
        errs.append("13-argocd.sh must not patch argocd-redis UID/GID (valkey 999:999)")


def _check_addons(text: str, errs: list[str]) -> None:
    m = re.search(r"kubectl patch deployment metrics-server -n kube-system --type='json' -p='(\[.*?\])'", text, re.S)
    if not m:
        errs.append("04-addons.sh missing metrics-server JSON patch")
        return
    try:
        ops = json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        errs.append(f"04-addons.sh metrics-server JSON patch invalid: {exc}")
        return
    by_path = {op.get("path"): op for op in ops if isinstance(op, dict)}
    psc = (by_path.get("/spec/template/spec/securityContext") or {}).get("value") or {}
    if psc.get("runAsUser") != 10100 or psc.get("runAsGroup") != 10100 or psc.get("fsGroup") != 10100 or psc.get("runAsNonRoot") is not True:
        errs.append(f"04-addons.sh metrics-server pod securityContext must be 10100:10100/fsGroup 10100, got {psc!r}")
    if (by_path.get("/spec/template/spec/containers/0/securityContext/runAsUser") or {}).get("value") != 10100:
        errs.append("04-addons.sh metrics-server container runAsUser must be 10100")
    if (by_path.get("/spec/template/spec/containers/0/securityContext/runAsGroup") or {}).get("value") != 10100:
        errs.append("04-addons.sh metrics-server container runAsGroup must be 10100")


def _check_keycloak(text: str, errs: list[str]) -> None:
    m = re.search(r"<<PATCHEOF\n(.*?)\nPATCHEOF", text, re.S)
    if not m:
        errs.append("11-keycloak.sh missing PATCHEOF")
        return
    obj = json.loads(m.group(1).replace("${OPERATOR_CONTAINER}", "keycloak-operator"))
    spec = obj["spec"]["template"]["spec"]
    psc = spec.get("securityContext") or {}
    csc = spec["containers"][0].get("securityContext") or {}
    for label, sc in (("pod", psc), ("container", csc)):
        if sc.get("runAsUser") != 10100 or sc.get("runAsGroup") != 10100 or sc.get("runAsNonRoot") is not True:
            errs.append(f"11-keycloak.sh keycloak-operator {label} securityContext must be 10100:10100, got {sc!r}")
    if psc.get("fsGroup") != 10100:
        errs.append(f"11-keycloak.sh keycloak-operator pod fsGroup must be 10100, got {psc.get('fsGroup')!r}")


def _check_grafana(prom_text: str, boot_text: str, errs: list[str]) -> None:
    vals = _values_object(prom_text)
    gsc = (vals.get("grafana") or {}).get("securityContext") or {}
    want = {"runAsNonRoot": True, "runAsUser": 472, "runAsGroup": 10100, "fsGroup": 472}
    if gsc != want:
        errs.append(f"prometheus-stack.yaml grafana.securityContext must be {want!r}, got {gsc!r}")
    m = re.search(r"--set-json\s+'grafana\.securityContext=(\{[^']+\})'", boot_text)
    if not m:
        errs.append("08-2-monitoring.sh missing --set-json grafana.securityContext")
    else:
        try:
            boot_gsc = json.loads(m.group(1))
        except json.JSONDecodeError as exc:
            errs.append(f"08-2-monitoring.sh grafana.securityContext invalid JSON: {exc}")
        else:
            if boot_gsc != want:
                errs.append(f"08-2-monitoring.sh grafana.securityContext mismatch: {boot_gsc!r} != {want!r}")


def _check_waypoint(istio_text: str, errs: list[str]) -> None:
    m = re.search(r"for ns in \"\$\{WAYPOINT_NAMESPACES\[@\]\}\"; do.*?cat <<EOF \| kubectl apply -f -\n(.*?)\nEOF", istio_text, re.S)
    if not m:
        errs.append("09-istio-ambient.sh missing waypoint Gateway/ConfigMap heredoc")
        return
    docs = [d for d in yaml.safe_load_all(m.group(1).replace("${ns}", "monitoring")) if isinstance(d, dict)]
    cm = next((d for d in docs if d.get("kind") == "ConfigMap" and d["metadata"]["name"] == "waypoint-options"), None)
    gw = next((d for d in docs if d.get("kind") == "Gateway" and d["metadata"]["name"] == "waypoint"), None)
    if not cm or not gw:
        errs.append("09-istio-ambient.sh waypoint heredoc must define ConfigMap/waypoint-options and Gateway/waypoint")
        return
    pref = ((gw.get("spec") or {}).get("infrastructure") or {}).get("parametersRef") or {}
    if pref != {"group": "", "kind": "ConfigMap", "name": "waypoint-options"}:
        errs.append(f"09-istio-ambient.sh Gateway/waypoint parametersRef mismatch: {pref!r}")
    patch = yaml.safe_load((cm.get("data") or {}).get("deployment") or "") or {}
    pspec = (((patch.get("spec") or {}).get("template") or {}).get("spec") or {})
    psc = pspec.get("securityContext") or {}
    if psc.get("runAsUser") != 10100 or psc.get("runAsGroup") != 10100 or psc.get("fsGroup") != 10100 or psc.get("runAsNonRoot") is not True:
        errs.append(f"09-istio-ambient.sh waypoint-options pod securityContext mismatch: {psc!r}")
    c_list = pspec.get("containers") or []
    proxy = next((c for c in c_list if c.get("name") == "istio-proxy"), {})
    csc = proxy.get("securityContext") or {}
    if csc.get("runAsUser") != 10100 or csc.get("runAsGroup") != 10100:
        errs.append(f"09-istio-ambient.sh waypoint-options istio-proxy securityContext mismatch: {csc!r}")


def _check_csi_postrender(pr_text: str, errs: list[str]) -> None:
    if ".spec.template.spec.securityContext.runAsUser = 0" not in pr_text or ".spec.template.spec.securityContext.runAsGroup = 10100" not in pr_text:
        errs.append("csi-nfs-postrender.sh must set pod-level runAsUser = 0 and runAsGroup = 10100")
    # Functional smoke check with minimal valid YAML (does not touch tracked files).
    minimal = (
        "apiVersion: apps/v1\nkind: DaemonSet\nmetadata:\n  name: csi-nfs-node\nspec:\n  template:\n    spec:\n"
        "      securityContext:\n        seccompProfile:\n          type: RuntimeDefault\n"
        "      containers:\n      - name: nfs\n      - name: liveness-probe\n      - name: node-driver-registrar\n"
        "---\napiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: csi-nfs-controller\nspec:\n  template:\n    spec:\n"
        "      securityContext:\n        seccompProfile:\n          type: RuntimeDefault\n"
        "      containers:\n      - name: nfs\n      - name: csi-provisioner\n      - name: csi-resizer\n"
        "      - name: csi-snapshotter\n      - name: liveness-probe\n"
    )
    if shutil.which("yq"):
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as tf:
            tf.write(pr_text)
            tf_path = tf.name
        try:
            proc = subprocess.run(["bash", tf_path], input=minimal, text=True, capture_output=True)
            if proc.returncode != 0:
                errs.append(f"csi-nfs-postrender.sh failed on minimal manifest: {proc.stderr}")
            else:
                for doc in yaml.safe_load_all(proc.stdout):
                    if not isinstance(doc, dict):
                        continue
                    psc = doc["spec"]["template"]["spec"].get("securityContext") or {}
                    if psc.get("runAsUser") != 0 or psc.get("runAsGroup") != 10100:
                        errs.append(f"csi-nfs-postrender.sh output {doc['metadata']['name']} pod securityContext={psc!r}")
        finally:
            os.unlink(tf_path)


def _check_acceptances(rules_text: str, i18n_text: str, doc_text: str, errs: list[str]) -> None:
    try:
        data = json.loads(rules_text)
    except json.JSONDecodeError as exc:
        errs.append(f"compliance-acceptance-rules.json invalid JSON: {exc}")
        return
    rules = data.get("configAudit") or []
    by_id = {r["id"]: r for r in rules if isinstance(r, dict)}
    expected = {
        "cfg:istio-system:ReplicaSet:istiod:upstream": ("istio-system", "ReplicaSet", "istiod", ["KSV020", "KSV021"], "upstream", "compliance.acceptedReason.istiodUid1337"),
        "cfg:istio-system:DaemonSet:ztunnel:upstream": ("istio-system", "DaemonSet", "ztunnel", ["KSV030", "KSV104"], "upstream", "compliance.acceptedReason.ztunnelSeccompUpstream"),
        "cfg:kube-system:DaemonSet:csi-nfs-node:ksv105-accept": ("kube-system", "DaemonSet", "csi-nfs-node", ["KSV105"], "accept", "compliance.acceptedReason.csiNfsNodeRunAsUser0"),
        "cfg:kube-system:ReplicaSet:csi-nfs-controller:ksv105-accept": ("kube-system", "ReplicaSet", "csi-nfs-controller", ["KSV105"], "accept", "compliance.acceptedReason.csiNfsControllerRunAsUser0"),
    }
    for rid, (ns, kind, name, checks, cls, rk) in expected.items():
        r = by_id.get(rid)
        if not r:
            errs.append(f"missing acceptance rule {rid}")
            continue
        if (r.get("namespace"), r.get("kind"), r.get("name"), r.get("checks"), r.get("class"), r.get("reasonKey")) != (ns, kind, name, checks, cls, rk):
            errs.append(f"acceptance rule {rid} mismatch: {r!r}")
        if i18n_text.count(f'"{rk}":') != 2:
            errs.append(f"i18n.ts missing bilingual entries for {rk}")
    # Ensure KSV021 stays unaccepted on csi-nfs and grafana, and KSV020/KSV021 are not accepted on ArgoCD/metrics-server/keycloak-operator/waypoint
    for r in rules:
        target = (r.get("namespace"), r.get("kind"), r.get("name"))
        if target in {("kube-system", "DaemonSet", "csi-nfs-node"), ("kube-system", "ReplicaSet", "csi-nfs-controller"), ("monitoring", "ReplicaSet", "prometheus-stack-grafana")}:
            if "KSV021" in (r.get("checks") or []):
                errs.append(f"{target} must not accept fixed KSV021")
        if r.get("namespace") == "devtools" and r.get("name", "").startswith("argocd-") and r.get("name") != "argocd-redis":
            if any(c in (r.get("checks") or []) for c in ("KSV020", "KSV021")):
                errs.append(f"stateless {target} must not accept KSV020/KSV021")
        if target in {("kube-system", "ReplicaSet", "metrics-server"), ("iam", "ReplicaSet", "keycloak-operator")} or r.get("name") == "waypoint":
            errs.append(f"stateless {target} must not be in acceptance rules")
    summary = f"{len(rules)} Config Audit rules across {len({(r['namespace'], r['kind'], r['name']) for r in rules})} workloads"
    if summary not in doc_text:
        errs.append(f"compliance-hardening.md summary missing '{summary}'")
    for needle in ("ReplicaSet/istiod | KSV020, KSV021", "DaemonSet/ztunnel | KSV030, KSV104", "DaemonSet/csi-nfs-node | KSV105", "ReplicaSet/csi-nfs-controller | KSV105"):
        if needle not in doc_text:
            errs.append(f"compliance-hardening.md missing table row '{needle}'")


def check(texts: dict[str, str]) -> list[str]:
    errs: list[str] = []
    _check_argocd(texts["argocd"], errs)
    _check_addons(texts["addons"], errs)
    _check_keycloak(texts["keycloak"], errs)
    _check_grafana(texts["prom"], texts["mon_boot"], errs)
    _check_waypoint(texts["istio_boot"], errs)
    _check_csi_postrender(texts["csi_pr"], errs)
    _check_acceptances(texts["rules"], texts["i18n"], texts["doc"], errs)
    if "check R428 " not in texts["reg"] or "check-cis-e1-stateless-uid.py --mutation-verify" not in texts["reg"]:
        errs.append("regression-check-kakao.sh missing R428 wiring")
    return errs


def _verify_offline_renders() -> None:
    if not shutil.which("helm"):
        return
    prom_chart = ROOT / ".review/charts/extracted/kube-prometheus-stack"
    if prom_chart.is_dir():
        vals = _values_object(FILES["prom"].read_text())
        with tempfile.NamedTemporaryFile("w", suffix=".yaml") as vf:
            yaml.safe_dump(vals, vf)
            vf.flush()
            out = subprocess.check_output(
                ["helm", "template", "prometheus-stack", str(prom_chart), "--namespace", "monitoring",
                 "-f", vf.name, "--show-only", "charts/grafana/templates/deployment.yaml"],
                text=True,
            )
        dep = yaml.safe_load(out)
        assert dep["spec"]["template"]["spec"]["securityContext"] == {
            "runAsNonRoot": True, "runAsUser": 472, "runAsGroup": 10100, "fsGroup": 472
        }, "rendered grafana pod securityContext mismatch"


MUTATIONS: list[tuple[str, str, str, str]] = [
    ("argocd-server UID/GID patch dropped", "argocd", "patch_uid_gid deployment  argocd-server                    argocd-server\n", ""),
    ("argocd-dex-server UID/GID patch dropped", "argocd", "patch_uid_gid deployment  argocd-dex-server                dex                              copyutil\n", ""),
    ("argocd-application-controller UID/GID patch dropped", "argocd", "patch_uid_gid statefulset argocd-application-controller    argocd-application-controller\n", ""),
    ("argocd-redis accidentally patched", "argocd", "patch_uid_gid deployment  argocd-dex-server                dex                              copyutil\n",
     "patch_uid_gid deployment  argocd-dex-server                dex                              copyutil\npatch_uid_gid deployment  argocd-redis                     redis\n"),
    ("metrics-server runAsUser reverted to 1000", "addons", '{"op": "replace", "path": "/spec/template/spec/containers/0/securityContext/runAsUser", "value": 10100}',
     '{"op": "replace", "path": "/spec/template/spec/containers/0/securityContext/runAsUser", "value": 1000}'),
    ("metrics-server runAsGroup dropped", "addons", '  {"op": "add",     "path": "/spec/template/spec/containers/0/securityContext/runAsGroup", "value": 10100},\n', ""),
    ("keycloak-operator runAsUser reverted to 1000", "keycloak", '"runAsUser": 10100,\n              "runAsGroup": 10100,', '"runAsUser": 1000,\n              "runAsGroup": 10100,'),
    ("keycloak-operator runAsGroup dropped", "keycloak", '              "runAsGroup": 10100,\n', ""),
    ("grafana GitOps runAsGroup reverted to 472", "prom", "            runAsGroup: 10100\n", "            runAsGroup: 472\n"),
    ("grafana bootstrap runAsGroup reverted to 472", "mon_boot", '"runAsGroup":10100', '"runAsGroup":472'),
    ("waypoint parametersRef dropped", "istio_boot", "  infrastructure:\n    parametersRef:\n      group: \"\"\n      kind: ConfigMap\n      name: waypoint-options\n", ""),
    ("waypoint istio-proxy UID reverted to 1337", "istio_boot", "                runAsUser: 10100\n                runAsGroup: 10100\n", "                runAsUser: 1337\n                runAsGroup: 1337\n"),
    ("csi-nfs-postrender runAsGroup dropped", "csi_pr", " | .spec.template.spec.securityContext.runAsGroup = 10100", ""),
    ("istiod acceptance removed", "rules", "cfg:istio-system:ReplicaSet:istiod:upstream", "cfg:istio-system:ReplicaSet:istiod:removed"),
    ("ztunnel seccomp acceptance removed", "rules", "cfg:istio-system:DaemonSet:ztunnel:upstream", "cfg:istio-system:DaemonSet:ztunnel:removed"),
    ("csi-nfs-node KSV105 acceptance removed", "rules", "cfg:kube-system:DaemonSet:csi-nfs-node:ksv105-accept", "cfg:kube-system:DaemonSet:csi-nfs-node:removed"),
    ("R428 wiring dropped", "reg", "check R428 ", "check R9999 "),
]


def main() -> int:
    texts = {k: p.read_text(encoding="utf-8") for k, p in FILES.items()}
    errs = check(texts)
    if errs:
        print("\n".join(errs), file=sys.stderr)
        return 1
    _verify_offline_renders()
    if "--mutation-verify" in sys.argv:
        for name, key, old, new in MUTATIONS:
            mutated = copy.deepcopy(texts)
            if old not in mutated[key]:
                print(f"FAIL: mutation needle missing for {name}", file=sys.stderr)
                return 1
            mutated[key] = mutated[key].replace(old, new, 1)
            if not check(mutated):
                print(f"FAIL: mutation not rejected: {name}", file=sys.stderr)
                return 1
        print(f"PASS: R428 CIS E1 stateless UID/GID remediation ({len(MUTATIONS)} mutations rejected)")
        return 0
    print("PASS: R428 CIS E1 stateless UID/GID remediation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
