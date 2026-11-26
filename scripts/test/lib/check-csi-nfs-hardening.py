#!/usr/bin/env python3
"""R417: Pin bootstrap CSI NFS sidecar hardening and exact exceptions."""
import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
CHART = ROOT / ".review/charts/csi-driver-nfs"
ADDONS = ROOT / "scripts/cluster/04-addons.sh"
PLUGIN = ROOT / "scripts/cluster/lib/helm-plugins/csi-nfs-postrender"
POSTRENDER = PLUGIN / "csi-nfs-postrender.sh"
PLUGIN_NAME = "csi-nfs-postrender"
SIDECARS = {
    ("Deployment", "csi-nfs-controller"): ["csi-provisioner", "csi-resizer", "csi-snapshotter", "liveness-probe"],
    ("DaemonSet", "csi-nfs-node"): ["liveness-probe", "node-driver-registrar"],
}
CPU_LIMITS = {
    "controller.resources.csiProvisioner": "100m",
    "controller.resources.csiResizer": "100m",
    "controller.resources.csiSnapshotter": "100m",
    "controller.resources.livenessProbe": "100m",
    "controller.resources.nfs": "100m",
    "node.resources.livenessProbe": "100m",
    "node.resources.nodeDriverRegistrar": "100m",
    "node.resources.nfs": "100m",
}


def index(source: str) -> dict:
    return {(obj.get("kind"), obj.get("metadata", {}).get("name")): obj for obj in yaml.safe_load_all(source) if isinstance(obj, dict)}


def helm_env(temp: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HELM_DATA_HOME"] = str(temp / "data")
    # Helm 4 installs local development plugins under data/plugins; setting the
    # same resolved path explicitly keeps install and render isolated together.
    env["HELM_PLUGINS"] = str(temp / "data" / "plugins")
    return env


def install_plugin(plugin: Path, env: dict[str, str]) -> None:
    subprocess.run(["helm", "plugin", "install", str(plugin)], check=True, capture_output=True, text=True, env=env)


def render(chart: Path) -> tuple[dict, dict]:
    values = yaml.safe_load((chart / "values.yaml").read_text())
    text = ADDONS.read_text()
    for path, value in CPU_LIMITS.items():
        assert f"--set {path}.limits.cpu={value}" in text, f"04-addons.sh missing {path} CPU limit"
        node = values
        for key in path.split("."):
            node = node[key]
        node.setdefault("limits", {})["cpu"] = value
    with tempfile.TemporaryDirectory(prefix="r417-helm-") as temp_name:
        temp = Path(temp_name)
        env = helm_env(temp)
        install_plugin(PLUGIN, env)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml") as stream:
            yaml.safe_dump(values, stream)
            stream.flush()
            rendered = subprocess.run(
                ["helm", "template", "csi-driver-nfs", str(chart), "--namespace", "kube-system", "-f", stream.name],
                check=True, capture_output=True, text=True, env=env,
            ).stdout
            patched = subprocess.run(
                ["helm", "template", "csi-driver-nfs", str(chart), "--namespace", "kube-system", "-f", stream.name,
                 "--post-renderer", PLUGIN_NAME],
                check=True, capture_output=True, text=True, env=env,
            ).stdout
    return index(rendered), index(patched)


def verify(chart: Path) -> None:
    addons = ADDONS.read_text()
    assert_addon_wiring(addons)
    plugin = yaml.safe_load((PLUGIN / "plugin.yaml").read_text())
    assert plugin.get("type") == "postrenderer/v1" and plugin.get("name") == PLUGIN_NAME, "invalid Helm 4 post-renderer plugin metadata"
    baseline, objects = render(chart)
    assert_hardening(objects, baseline)


def assert_addon_wiring(source: str) -> None:
    assert 'helm plugin install "${csi_nfs_plugin_dir}"' in source, "04-addons.sh missing Helm plugin install block"
    assert "--post-renderer csi-nfs-postrender" in source, "04-addons.sh must pass the installed plugin name"
    assert "--post-renderer /home/vagrant/" not in source, "04-addons.sh must not pass a renderer path to Helm 4"
    assert 'helm plugin uninstall "${csi_nfs_plugin_name}"' in source, "04-addons.sh missing wrong-origin plugin replacement"


def assert_hardening(objects: dict, baseline: dict) -> None:
    for key, names in SIDECARS.items():
        obj = objects[key]
        containers = {item["name"]: item for item in obj["spec"]["template"]["spec"]["containers"]}
        original = {item["name"]: item for item in baseline[key]["spec"]["template"]["spec"]["containers"]}
        for name in names:
            context = containers[name].get("securityContext", {})
            expected = dict(original[name].get("securityContext", {}))
            expected["allowPrivilegeEscalation"] = False
            if name in {"csi-resizer", "csi-snapshotter", "node-driver-registrar"}:
                expected["readOnlyRootFilesystem"] = True
            assert context == expected, f"{key}/{name}: unexpected sidecar securityContext {context}"
        for name, container in containers.items():
            if name not in names:
                assert container == original[name], f"{key}/{name} changed during post-render"
        for name, container in containers.items():
            assert container["resources"]["limits"].get("cpu") == "100m", f"{key}/{name}: CPU limit != 100m"

    # Missing-container behavior is exercised directly with a valid minimal workload.
    minimal = "apiVersion: apps/v1\nkind: DaemonSet\nmetadata:\n  name: csi-nfs-node\nspec:\n  template:\n    spec:\n      containers:\n      - name: nfs\n      - name: liveness-probe\n"
    result = subprocess.run([str(POSTRENDER)], input=minimal, text=True, capture_output=True)
    assert result.returncode != 0 and "node-driver-registrar" in result.stderr, "post-renderer did not fail closed"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mutation-verify", action="store_true")
    args = parser.parse_args()
    if not CHART.is_dir():
        print("SKIPPED: offline csi-driver-nfs chart copy not present")
        return 0
    if not shutil.which("helm"):
        print("SKIPPED: Helm unavailable; direct post-renderer fail-closed check only")
        result = subprocess.run([str(POSTRENDER)], input="apiVersion: v1\nkind: List\nitems: []\n", text=True, capture_output=True)
        if result.returncode == 0:
            print("FAIL: direct post-renderer accepted empty input")
            return 1
        return 0
    try:
        verify(CHART)
        if args.mutation_verify:
            baseline, hardened = render(CHART)
            mutations = [
                ("sidecar escalation", lambda o: o[("Deployment", "csi-nfs-controller")]["spec"]["template"]["spec"]["containers"][0]["securityContext"].update(allowPrivilegeEscalation=True)),
                ("sidecar rootfs", lambda o: o[("DaemonSet", "csi-nfs-node")]["spec"]["template"]["spec"]["containers"][1]["securityContext"].update(readOnlyRootFilesystem=False)),
                ("CPU limit", lambda o: o[("Deployment", "csi-nfs-controller")]["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"].update(cpu="10m")),
                ("nfs mutation", lambda o: o[("DaemonSet", "csi-nfs-node")]["spec"]["template"]["spec"]["containers"][-1]["securityContext"].update(privileged=False)),
            ]
            for label, mutate in mutations:
                import copy
                changed = copy.deepcopy(hardened)
                mutate(changed)
                try:
                    assert_hardening(changed, baseline)
                except (AssertionError, KeyError):
                    continue
                raise AssertionError(f"R417 mutation not rejected: {label}")
            with tempfile.TemporaryDirectory(prefix="r417-plugin-mutation-") as temp_name:
                temp = Path(temp_name)
                bad_plugin = temp / "bad-plugin"
                shutil.copytree(PLUGIN, bad_plugin)
                (bad_plugin / "plugin.yaml").write_text((bad_plugin / "plugin.yaml").read_text().replace("postrenderer/v1", "downloader/v1"))
                env = helm_env(temp / "helm")
                failed = False
                try:
                    install_plugin(bad_plugin, env)
                    subprocess.run(["helm", "template", "csi-driver-nfs", str(CHART), "--post-renderer", PLUGIN_NAME],
                                   check=True, capture_output=True, text=True, env=env)
                except subprocess.CalledProcessError:
                    failed = True
                assert failed, "R417 accepted invalid plugin.yaml type"
            mutated_addons = ADDONS.read_text().replace("--post-renderer csi-nfs-postrender", "--post-renderer /home/vagrant/scripts/cluster/lib/csi-nfs-postrender.sh")
            try:
                assert_addon_wiring(mutated_addons)
            except AssertionError:
                pass
            else:
                raise AssertionError("R417 did not reject renderer path mutation")
            missing_install = ADDONS.read_text().replace('  helm plugin install "${csi_nfs_plugin_dir}"\n', "")
            try:
                assert_addon_wiring(missing_install)
            except AssertionError:
                pass
            else:
                raise AssertionError("R417 did not reject missing plugin install block")
        print("PASS: R417 Helm 4 plugin render, exact contexts, CPU limits, and fail-closed post-renderer")
        return 0
    except (AssertionError, KeyError, subprocess.CalledProcessError, yaml.YAMLError) as exc:
        print(f"FAIL: R417 {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
