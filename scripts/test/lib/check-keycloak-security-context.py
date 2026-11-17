#!/usr/bin/env python3
"""R359: Keycloak & Keycloak Operator securityContext hardening and bootstrap/GitOps parity."""

import copy
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
BOOTSTRAP_SH = ROOT / "scripts/cluster/11-keycloak.sh"
GITOPS_CR = ROOT / "gitops/charts/narwhal-platform/templates/keycloak-cr.yaml"

START_MARKER = "# D-KC-OPERATOR-LIVENESS:"
END_MARKER = 'echo "Waiting for Keycloak Operator deployment..."'

STUB_KUBECTL = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

state_path = Path(os.environ["R359_STATE"])
dep = json.loads(state_path.read_text())
args = sys.argv[1:]

def deep_merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            deep_merge(dst[k], v)
        else:
            dst[k] = v

if args[:3] == ["get", "deployment", "keycloak-operator"]:
    if "-o" in args:
        fmt = args[args.index("-o") + 1]
        if fmt.startswith("jsonpath="):
            for c in dep["spec"]["template"]["spec"]["containers"]:
                print(f"{c['name']}\t{c.get('image', '')}")
            sys.exit(0)
    print(json.dumps(dep))
    sys.exit(0)

if args[:3] == ["patch", "deployment", "keycloak-operator"]:
    patch_str = None
    for i, a in enumerate(args):
        if a == "-p" and i + 1 < len(args):
            patch_str = args[i + 1]
        elif a.startswith("-p="):
            patch_str = a.split("=", 1)[1]
    assert patch_str is not None, f"missing -p in {args}"
    patch_obj = json.loads(patch_str)
    containers = dep["spec"]["template"]["spec"]["containers"]
    for pc in patch_obj["spec"]["template"]["spec"]["containers"]:
        match = next((c for c in containers if c.get("name") == pc.get("name")), None)
        assert match is not None, f"missing container {pc.get('name')}"
        deep_merge(match, pc)
    state_path.write_text(json.dumps(dep))
    sys.exit(0)

raise AssertionError(f"unexpected kubectl args: {args}")
'''


def _assert_container_sc(sc: dict, where: str, errs: list[str]) -> None:
    if not isinstance(sc, dict):
        errs.append(f"{where}: securityContext missing")
        return
    if sc.get("allowPrivilegeEscalation") is not False:
        errs.append(f"{where}: securityContext.allowPrivilegeEscalation must be false")
    if sc.get("runAsNonRoot") is not True:
        errs.append(f"{where}: securityContext.runAsNonRoot must be true")
    if sc.get("runAsUser") != 1000:
        errs.append(f"{where}: securityContext.runAsUser must be 1000")
    if "ALL" not in ((sc.get("capabilities") or {}).get("drop") or []):
        errs.append(f"{where}: securityContext.capabilities.drop must include ALL")
    if (sc.get("seccompProfile") or {}).get("type") != "RuntimeDefault":
        errs.append(f"{where}: securityContext.seccompProfile.type must be RuntimeDefault")
    if sc.get("readOnlyRootFilesystem") is True:
        errs.append(f"{where}: readOnlyRootFilesystem must stay unset until writable mounts are live-proven")


def _extract_operator_block(sh_text: str) -> str:
    s = sh_text.index(START_MARKER)
    e = sh_text.index(END_MARKER, s)
    return sh_text[s:e]


def _extract_bootstrap_cr(sh_text: str) -> dict:
    m = re.search(r"kubectl apply -f - <<EOF\n(.*?)\nEOF", sh_text, re.S)
    assert m, "11-keycloak.sh: Keycloak CR heredoc not found"
    rendered = m.group(1).replace("${DOMAIN}", "local.narwhal.internal")
    return yaml.safe_load(rendered)


def _load_gitops_cr(cr_text: str) -> dict:
    rendered = cr_text.replace("{{ .Values.baseDomain }}", "local.narwhal.internal")
    rendered = re.sub(r"\{\{[^}]+\}\}", "checksum-stub", rendered)
    return yaml.safe_load(rendered)


def check(sh_text: str, cr_text: str, run_stub: bool = True) -> list[str]:
    errs: list[str] = []

    # 1. Operator strategic-merge patch
    try:
        block = _extract_operator_block(sh_text)
    except ValueError as exc:
        return [f"11-keycloak.sh: operator patch block missing ({exc})"]

    m_patch = re.search(r"<<PATCHEOF\n(.*?)\nPATCHEOF", block, re.S)
    if not m_patch:
        errs.append("11-keycloak.sh: PATCHEOF missing in operator patch block")
    else:
        patch_json = m_patch.group(1).replace("${OPERATOR_CONTAINER}", "keycloak-operator")
        patch_obj = json.loads(patch_json)
        op_c = patch_obj["spec"]["template"]["spec"]["containers"][0]
        _assert_container_sc(op_c.get("securityContext"), "11-keycloak.sh:keycloak-operator", errs)
        if (op_c.get("livenessProbe") or {}).get("httpGet", {}).get("path") != "/q/health/ready":
            errs.append("11-keycloak.sh: keycloak-operator livenessProbe path must remain /q/health/ready")

    if run_stub and not errs:
        dep = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "keycloak-operator", "namespace": "iam"},
            "spec": {"template": {"spec": {"containers": [
                {"name": "istio-proxy", "image": "istio/proxyv2:1.30.1"},
                {"name": "keycloak-operator", "image": "quay.io/keycloak/keycloak-operator:26.5.7",
                 "livenessProbe": {"httpGet": {"path": "/q/health/live", "port": 8080, "scheme": "HTTP"}}},
            ]}}},
        }
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            kb = td_path / "kubectl"
            kb.write_text(STUB_KUBECTL)
            kb.chmod(0o755)
            sf = td_path / "state.json"
            sf.write_text(json.dumps(dep))
            env = dict(os.environ, PATH=f"{td_path}:{os.environ['PATH']}", R359_STATE=str(sf))
            proc = subprocess.run(
                ["bash", "-c", "set -euo pipefail\n" + block + "\n" + block],
                env=env, capture_output=True, text=True, timeout=10,
            )
            if proc.returncode != 0:
                errs.append(f"11-keycloak.sh operator patch stub run failed: {proc.stderr}")
            else:
                res = json.loads(sf.read_text())
                if res["spec"]["template"]["spec"]["containers"][0] != {"name": "istio-proxy", "image": "istio/proxyv2:1.30.1"}:
                    errs.append("11-keycloak.sh operator patch touched sidecar container")
                _assert_container_sc(res["spec"]["template"]["spec"]["containers"][1].get("securityContext"), "stub-result:keycloak-operator", errs)

    # 2. Bootstrap & GitOps Keycloak CR parity
    boot_cr = _extract_bootstrap_cr(sh_text)
    gitops_cr = _load_gitops_cr(cr_text)
    boot_sc = boot_cr["spec"]["unsupported"]["podTemplate"]["spec"]["containers"][0].get("securityContext")
    gitops_sc = gitops_cr["spec"]["unsupported"]["podTemplate"]["spec"]["containers"][0].get("securityContext")
    _assert_container_sc(boot_sc, "11-keycloak.sh:keycloak-cr", errs)
    _assert_container_sc(gitops_sc, "keycloak-cr.yaml", errs)
    if boot_sc != gitops_sc:
        errs.append(f"Keycloak CR securityContext parity mismatch: bootstrap={boot_sc!r} vs gitops={gitops_sc!r}")

    return errs


def main() -> int:
    sh_text = BOOTSTRAP_SH.read_text()
    cr_text = GITOPS_CR.read_text()
    errs = check(sh_text, cr_text, run_stub=True)
    if errs:
        for e in errs:
            print(f"FAIL: {e}", file=sys.stderr)
        return 1

    if "--mutation-verify" in sys.argv:
        sh_mutations = [
            ('"allowPrivilegeEscalation": false', '"allowPrivilegeEscalation": true'),
            ('"runAsNonRoot": true', '"runAsNonRoot": false'),
            ('"drop": ["ALL"]', '"drop": []'),
            ("            securityContext:\n              allowPrivilegeEscalation: false", "            securityContext:\n              allowPrivilegeEscalation: true"),
            ("              runAsUser: 1000", "              runAsUser: 0"),
        ]
        cr_mutations = [
            ("            securityContext:\n              allowPrivilegeEscalation: false", "            securityContext:\n              allowPrivilegeEscalation: true"),
            ("              runAsNonRoot: true", "              runAsNonRoot: false"),
            ("              capabilities:\n                drop:\n                  - ALL", "              capabilities:\n                drop: []"),
        ]
        caught = 0
        for old, new in sh_mutations:
            assert old in sh_text, f"missing in 11-keycloak.sh: {old!r}"
            if not check(sh_text.replace(old, new, 1), cr_text, run_stub=False):
                print(f"mutation survived (11-keycloak.sh): {old!r}", file=sys.stderr)
                return 1
            caught += 1
        for old, new in cr_mutations:
            assert old in cr_text, f"missing in keycloak-cr.yaml: {old!r}"
            if not check(sh_text, cr_text.replace(old, new, 1), run_stub=False):
                print(f"mutation survived (keycloak-cr.yaml): {old!r}", file=sys.stderr)
                return 1
            caught += 1
        print(f"ok: Keycloak operator/CR securityContext & parity verified (incl. idempotent stub kubectl); {caught} mutations caught")
    else:
        print("ok: Keycloak operator/CR securityContext & parity verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
