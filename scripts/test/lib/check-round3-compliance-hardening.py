#!/usr/bin/env python3
"""R370: Workload compliance hardening round 3 (Velero & Keycloak operator KSV014).

Pins:
1. storage/velero (velero.yaml & 08-4-storage.sh): server containerSecurityContext
   readOnlyRootFilesystem=true with velero-home emptyDir mounted at /home/cnb, no
   second /tmp mount (chart mounts its own tmpdir at /tmp); nodeAgent hardened in round 4.
   Offline helm template renders both GitOps values and 08-4 heredoc when a chart
   bundle is present.
2. iam/keycloak-operator (11-keycloak.sh): strategic patch sets
   readOnlyRootFilesystem=true, tmp emptyDir volume and /tmp volumeMount; idempotent
   on re-run and when volumes already exist; Keycloak StatefulSet CR stays without
   readOnlyRootFilesystem in both 11-keycloak.sh and keycloak-cr.yaml.
"""
import argparse
import copy
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
FILES = {
    "velero_yaml": "gitops/charts/narwhal-apps/templates/velero.yaml",
    "storage_sh": "scripts/cluster/08-4-storage.sh",
    "keycloak_sh": "scripts/cluster/11-keycloak.sh",
    "keycloak_cr": "gitops/charts/narwhal-platform/templates/keycloak-cr.yaml",
}
ERRORS = (AssertionError, KeyError, TypeError, ValueError, yaml.YAMLError)

KC_START = "# D-KC-OPERATOR-LIVENESS:"
KC_END = 'echo "Waiting for Keycloak Operator deployment..."'

STUB_KUBECTL = r'''#!/usr/bin/env python3
import copy
import json
import os
import sys
from pathlib import Path

state_path = Path(os.environ["R370_STATE"])
dep = json.loads(state_path.read_text())
args = sys.argv[1:]

def deep_merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            deep_merge(dst[k], v)
        else:
            dst[k] = v

def merge_list(dst_list, src_list, merge_key):
    for item in src_list:
        key_val = item.get(merge_key)
        match = next((x for x in dst_list if x.get(merge_key) == key_val), None)
        if match is not None:
            deep_merge(match, item)
        else:
            dst_list.append(copy.deepcopy(item))

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
    pod_spec = dep["spec"]["template"]["spec"]
    patch_pod_spec = patch_obj["spec"]["template"]["spec"]
    if "volumes" in patch_pod_spec:
        pod_spec.setdefault("volumes", [])
        merge_list(pod_spec["volumes"], patch_pod_spec["volumes"], "name")
    for pc in patch_pod_spec["containers"]:
        match = next((c for c in pod_spec["containers"] if c.get("name") == pc.get("name")), None)
        assert match is not None, f"missing container {pc.get('name')}"
        pc_copy = copy.deepcopy(pc)
        if "volumeMounts" in pc_copy:
            match.setdefault("volumeMounts", [])
            merge_list(match["volumeMounts"], pc_copy.pop("volumeMounts"), "mountPath")
        deep_merge(match, pc_copy)
    state_path.write_text(json.dumps(dep))
    sys.exit(0)

raise AssertionError(f"unexpected kubectl args: {args}")
'''


def _app_values(text: str) -> dict:
    cleaned = re.sub(r"\{\{.*?\}\}", "placeholder", text)
    return yaml.safe_load(cleaned)["spec"]["source"]["helm"]["valuesObject"]


def _extract_velero_heredoc(text: str) -> dict:
    marker = "cat > /tmp/velero-values.yaml << EOF\n"
    assert text.count(marker) == 1, "08-4-storage.sh: expected one velero values heredoc"
    return yaml.safe_load(text.split(marker, 1)[1].split("\nEOF\n", 1)[0])


def _extract_keycloak_cr_heredoc(text: str) -> dict:
    m = re.search(r"kubectl apply -f - <<EOF\n(.*?)\nEOF", text, re.S)
    assert m, "11-keycloak.sh: Keycloak CR heredoc not found"
    return yaml.safe_load(m.group(1).replace("${DOMAIN}", "local.narwhal.internal"))


def _load_gitops_keycloak_cr(text: str) -> dict:
    cleaned = re.sub(r"\{\{.*?\}\}", "placeholder", text)
    return yaml.safe_load(cleaned)


def _verify_velero(velero_yaml: str, storage_sh: str) -> None:
    gitops_vals = _app_values(velero_yaml)
    boot_vals = _extract_velero_heredoc(storage_sh)
    for label, vals in (("gitops", gitops_vals), ("08-4", boot_vals)):
        csc = vals.get("containerSecurityContext") or {}
        assert csc.get("readOnlyRootFilesystem") is True, f"{label} velero readOnlyRootFilesystem must be true"
        assert csc.get("allowPrivilegeEscalation") is False, f"{label} velero allowPrivilegeEscalation must be false"
        assert csc.get("runAsNonRoot") is True, f"{label} velero runAsNonRoot must be true"
        assert csc.get("runAsUser") == 1002 and csc.get("runAsGroup") == 1000, f"{label} velero uid/gid 1002:1000"
        assert (csc.get("capabilities") or {}).get("drop") == ["ALL"], f"{label} velero drop ALL"

        vols = {v["name"]: v for v in (vals.get("extraVolumes") or []) if isinstance(v, dict)}
        mounts = vals.get("extraVolumeMounts") or []
        mount_paths = [m.get("mountPath") for m in mounts if isinstance(m, dict)]
        assert "/tmp" not in mount_paths, f"{label} velero must not mount a second /tmp (chart mounts tmpdir)"
        home_mounts = [m for m in mounts if isinstance(m, dict) and m.get("mountPath") == "/home/cnb"]
        assert len(home_mounts) == 1, f"{label} velero must mount /home/cnb exactly once"
        vname = home_mounts[0].get("name")
        assert vols.get(vname, {}).get("emptyDir") == {}, f"{label} velero /home/cnb volume {vname!r} must be emptyDir"

        na = vals.get("nodeAgent") or {}
        assert (na.get("podSecurityContext") or {}).get("runAsNonRoot") is False, f"{label} nodeAgent podSecurityContext.runAsNonRoot"


def _upstream_keycloak_deployment(root: Path) -> dict:
    manifests = sorted(glob.glob(str(root / "narwhal-airgap-bundle-*/manifests/keycloak-kubernetes.yml")))
    if not manifests:
        manifests = sorted(glob.glob(str(root.parents[2] / "narwhal-airgap-bundle-*/manifests/keycloak-kubernetes.yml")))
    if manifests:
        for doc in yaml.safe_load_all(Path(manifests[0]).read_text()):
            if isinstance(doc, dict) and doc.get("kind") == "Deployment":
                return doc
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "keycloak-operator", "namespace": "iam"},
        "spec": {"template": {"spec": {"containers": [
            {"name": "keycloak-operator", "image": "quay.io/keycloak/keycloak-operator:26.5.7",
             "livenessProbe": {"httpGet": {"path": "/q/health/live", "port": 8080, "scheme": "HTTP"}}},
        ]}}},
    }


def _assert_operator_hardened(pod_spec: dict, container: dict, where: str) -> None:
    sc = container.get("securityContext") or {}
    assert sc.get("readOnlyRootFilesystem") is True, f"{where}: readOnlyRootFilesystem must be true"
    assert sc.get("allowPrivilegeEscalation") is False, f"{where}: allowPrivilegeEscalation must be false"
    assert sc.get("runAsNonRoot") is True and sc.get("runAsUser") == 1000, f"{where}: non-root uid 1000"
    assert "ALL" in ((sc.get("capabilities") or {}).get("drop") or []), f"{where}: drop ALL"
    assert (sc.get("seccompProfile") or {}).get("type") == "RuntimeDefault", f"{where}: RuntimeDefault"

    vols = [v for v in (pod_spec.get("volumes") or []) if isinstance(v, dict)]
    tmp_vols = [v for v in vols if v.get("name") == "tmp"]
    assert len(tmp_vols) == 1 and tmp_vols[0].get("emptyDir") == {}, f"{where}: exactly one tmp emptyDir volume"

    mounts = [m for m in (container.get("volumeMounts") or []) if isinstance(m, dict)]
    tmp_mounts = [m for m in mounts if m.get("mountPath") == "/tmp"]
    assert len(tmp_mounts) == 1 and tmp_mounts[0].get("name") == "tmp", f"{where}: exactly one /tmp mount"


def _verify_keycloak(root: Path, keycloak_sh: str, keycloak_cr: str, run_stub: bool) -> None:
    s = keycloak_sh.index(KC_START)
    e = keycloak_sh.index(KC_END, s)
    block = keycloak_sh[s:e]
    m_patch = re.search(r"<<PATCHEOF\n(.*?)\nPATCHEOF", block, re.S)
    assert m_patch, "11-keycloak.sh: PATCHEOF missing"
    patch_obj = json.loads(m_patch.group(1).replace("${OPERATOR_CONTAINER}", "keycloak-operator"))
    patch_spec = patch_obj["spec"]["template"]["spec"]
    _assert_operator_hardened(patch_spec, patch_spec["containers"][0], "11-keycloak.sh patch")

    # Keycloak StatefulSet CR stays without readOnlyRootFilesystem (re-augments at start).
    for label, cr in (("11-keycloak.sh CR", _extract_keycloak_cr_heredoc(keycloak_sh)),
                      ("keycloak-cr.yaml", _load_gitops_keycloak_cr(keycloak_cr))):
        kc_sc = cr["spec"]["unsupported"]["podTemplate"]["spec"]["containers"][0].get("securityContext") or {}
        assert "readOnlyRootFilesystem" not in kc_sc, f"{label}: Keycloak StatefulSet must not set readOnlyRootFilesystem"

    if not run_stub:
        return

    upstream_dep = _upstream_keycloak_deployment(root)
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        kb = td_path / "kubectl"
        kb.write_text(STUB_KUBECTL)
        kb.chmod(0o755)
        sf = td_path / "state.json"
        env = dict(os.environ, PATH=f"{td_path}:{os.environ['PATH']}", R370_STATE=str(sf))

        # 1. Upstream Deployment patched twice -> idempotent.
        sf.write_text(json.dumps(copy.deepcopy(upstream_dep)))
        proc = subprocess.run(
            ["bash", "-c", "set -euo pipefail\n" + block + "\n" + block],
            env=env, capture_output=True, text=True, timeout=10,
        )
        assert proc.returncode == 0, f"stub kubectl failed: {proc.stderr}"
        res_spec = json.loads(sf.read_text())["spec"]["template"]["spec"]
        op_c = next(c for c in res_spec["containers"] if c["name"] == "keycloak-operator")
        _assert_operator_hardened(res_spec, op_c, "stub upstream x2")

        # 2. Deployment with existing tmp + extra volume/mount -> still applies, no duplicates.
        existing = copy.deepcopy(upstream_dep)
        existing["spec"]["template"]["spec"]["volumes"] = [
            {"name": "tmp", "emptyDir": {}},
            {"name": "extra", "emptyDir": {}},
        ]
        existing["spec"]["template"]["spec"]["containers"][0]["volumeMounts"] = [
            {"name": "tmp", "mountPath": "/tmp"},
            {"name": "extra", "mountPath": "/extra"},
        ]
        sf.write_text(json.dumps(existing))
        proc2 = subprocess.run(
            ["bash", "-c", "set -euo pipefail\n" + block],
            env=env, capture_output=True, text=True, timeout=10,
        )
        assert proc2.returncode == 0, f"stub kubectl failed with existing volume: {proc2.stderr}"
        res2_spec = json.loads(sf.read_text())["spec"]["template"]["spec"]
        _assert_operator_hardened(res2_spec, res2_spec["containers"][0], "stub existing-vol")
        assert [v["name"] for v in res2_spec["volumes"]] == ["tmp", "extra"]
        assert [m["mountPath"] for m in res2_spec["containers"][0]["volumeMounts"]] == ["/tmp", "/extra"]


def verify(root: Path, run_stub: bool = True) -> None:
    texts = {k: (root / rel).read_text() for k, rel in FILES.items()}
    _verify_velero(texts["velero_yaml"], texts["storage_sh"])
    _verify_keycloak(root, texts["keycloak_sh"], texts["keycloak_cr"], run_stub=run_stub)


def verify_helm_renders(root: Path) -> bool:
    bundles = sorted(glob.glob(str(root / "narwhal-airgap-bundle-*/charts/velero-12.0.3.tgz")))
    if not bundles:
        bundles = sorted(glob.glob(str(root.parents[2] / "narwhal-airgap-bundle-*/charts/velero-12.0.3.tgz")))
    if not bundles:
        return False
    chart_tgz = bundles[0]

    gitops_vals = _app_values((root / FILES["velero_yaml"]).read_text())
    boot_vals = _extract_velero_heredoc((root / FILES["storage_sh"]).read_text())
    for label, vals in (("gitops", gitops_vals), ("08-4", boot_vals)):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
            yaml.dump(vals, f)
            f.flush()
            out = subprocess.run(
                [
                    "helm", "template", "velero", chart_tgz,
                    "-n", "storage", "-f", f.name,
                    "--show-only", "templates/deployment.yaml",
                    "--show-only", "templates/node-agent-daemonset.yaml",
                ],
                capture_output=True, text=True, check=True,
            ).stdout
        docs = [d for d in yaml.safe_load_all(out) if isinstance(d, dict)]
        dep = next(d for d in docs if d["kind"] == "Deployment")
        ds = next(d for d in docs if d["kind"] == "DaemonSet")

        dspec = dep["spec"]["template"]["spec"]
        vc = dspec["containers"][0]
        assert vc["securityContext"]["readOnlyRootFilesystem"] is True, f"{label} render ro-rootfs"
        vol_names = {v["name"]: v for v in dspec["volumes"]}
        assert vol_names.get("velero-home", {}).get("emptyDir") == {}, f"{label} render velero-home"
        assert vol_names.get("tmpdir", {}).get("emptyDir") == {}, f"{label} render chart tmpdir"
        paths = [m["mountPath"] for m in vc["volumeMounts"]]
        assert paths.count("/home/cnb") == 1 and paths.count("/tmp") == 1, f"{label} render mounts: {paths}"

        nspec = ds["spec"]["template"]["spec"]
        assert nspec["securityContext"]["runAsNonRoot"] is False, f"{label} node-agent runAsNonRoot"
        nc = nspec["containers"][0]
        assert not any(m["mountPath"] == "/home/cnb" for m in nc.get("volumeMounts") or []), f"{label} node-agent mounts"
    return True


def mutation_verify(root: Path) -> int:
    sources = {k: (root / rel).read_text() for k, rel in FILES.items()}
    mutations = [
        ("velero_yaml", "readOnlyRootFilesystem: true", "readOnlyRootFilesystem: false"),
        ("velero_yaml", "mountPath: /home/cnb", "mountPath: /tmp"),
        ("velero_yaml", "name: velero-home\n            emptyDir: {}", "name: velero-home\n            hostPath:\n              path: /tmp"),
        ("storage_sh", "  runAsGroup: 1000\n  readOnlyRootFilesystem: true", "  runAsGroup: 1000\n  readOnlyRootFilesystem: false"),
        ("storage_sh", "mountPath: /home/cnb", "mountPath: /tmp"),
        ("storage_sh", "extraVolumes:\n  - name: velero-home\n    emptyDir: {}", "extraVolumes:\n  - name: other\n    emptyDir: {}"),
        ("keycloak_sh", '"readOnlyRootFilesystem": true', '"readOnlyRootFilesystem": false'),
        ("keycloak_sh", '"mountPath": "/tmp"', '"mountPath": "/var/tmp"'),
        ("keycloak_sh", '"name": "tmp"', '"name": "other"'),
        ("keycloak_sh", "              runAsUser: 1000\n              capabilities:", "              runAsUser: 1000\n              readOnlyRootFilesystem: true\n              capabilities:"),
        ("keycloak_cr", "              runAsUser: 1000\n              capabilities:", "              runAsUser: 1000\n              readOnlyRootFilesystem: true\n              capabilities:"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        fixture = Path(tmp)
        for rel in FILES.values():
            (fixture / rel).parent.mkdir(parents=True, exist_ok=True)
        for key, old, new in mutations:
            for k, rel in FILES.items():
                (fixture / rel).write_text(sources[k])
            assert old in sources[key], f"mutation anchor missing in {key}: {old!r}"
            (fixture / FILES[key]).write_text(sources[key].replace(old, new, 1))
            try:
                verify(fixture, run_stub=False)
            except ERRORS:
                continue
            raise AssertionError(f"mutation survived: {key}: {old!r}")
    return len(mutations)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--mutation-verify", action="store_true")
    args = parser.parse_args()

    verify(args.root, run_stub=True)
    rendered = verify_helm_renders(args.root)
    renders = "offline helm renders" if rendered else "offline helm renders SKIPPED (no chart bundle)"
    if args.mutation_verify:
        caught = mutation_verify(args.root)
        print(f"PASS: R370 round-3 compliance hardening + {renders} ({caught} mutations rejected)")
    else:
        print(f"PASS: R370 round-3 compliance hardening + {renders}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
