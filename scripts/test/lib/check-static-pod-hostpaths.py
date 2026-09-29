"""Guard repository hostPath use and the static-pod manifest hardening contract."""

import argparse
import re
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]

# D1: Record the one checked-in hostPath by source identity and security properties.
# Reason: rendered/generated static Pods are separately covered by the doc inventory;
# this YAML is an ordinary namespaced workload. Escape hatch: update this record and
# its fixture when an approved workload changes.
HOSTPATH_ALLOWLIST = (
    {
        "path": "/srv/airgap-registry",
        "file": "scripts/airgap/04-bootstrap-registry-k8s.yaml",
        "namespace": "platform-system",
        "readOnly": False,
        "reason": "Airgap registry reads its local OCI bundle and serves it to containerd",
    },
)


def workload_pod_spec(document):
    kind = document.get("kind")
    if kind == "Pod":
        return document.get("metadata", {}), document.get("spec", {})
    template = document.get("spec", {}).get("template", {})
    return template.get("metadata", {}), template.get("spec", {})


def hostpath_mounts(path):
    mounts = []
    text = path.read_text()
    try:
        documents = yaml.safe_load_all(text)
        for document in documents:
            if not isinstance(document, dict):
                continue
            metadata, pod_spec = workload_pod_spec(document)
            namespace = metadata.get("namespace") or document.get("metadata", {}).get("namespace")
            volume_names = {volume.get("name"): volume.get("hostPath", {}).get("path")
                            for volume in pod_spec.get("volumes", [])
                            if isinstance(volume, dict) and isinstance(volume.get("hostPath"), dict)}
            mount_flags = {}
            for container in pod_spec.get("containers", []) + pod_spec.get("initContainers", []):
                for mount in container.get("volumeMounts", []):
                    if mount.get("name") in volume_names:
                        mount_flags.setdefault(mount["name"], []).append(mount.get("readOnly", False))
            for name, host_path in volume_names.items():
                for read_only in mount_flags.get(name, [False]):
                    mounts.append({"path": host_path, "namespace": namespace, "readOnly": read_only})
    except yaml.YAMLError:
        if "{{" not in text:
            raise
        return mounts
    return mounts


def check_gitops(paths, identity_root=ROOT):
    manifests = sorted({path for directory in paths
                        for pattern in ("*.yaml", "*.yml")
                        for path in directory.rglob(pattern)})
    found = []
    violations = []
    matched = set()
    for manifest in manifests:
        relative = manifest.relative_to(identity_root).as_posix()
        for mount in hostpath_mounts(manifest):
            found.append((relative, mount))
            candidates = [index for index, entry in enumerate(HOSTPATH_ALLOWLIST)
                          if entry["path"] == mount["path"]]
            if not candidates:
                violations.append(f"{relative}: unapproved hostPath {mount['path']!r}")
                continue
            valid = [index for index in candidates
                     if HOSTPATH_ALLOWLIST[index]["file"] == relative
                     and HOSTPATH_ALLOWLIST[index]["namespace"] == mount["namespace"]]
            if not valid:
                violations.append(
                    f"{relative}: {mount['path']!r} appears in unlisted workload/namespace "
                    f"{mount['namespace']!r}"
                )
                continue
            index = valid[0]
            if HOSTPATH_ALLOWLIST[index]["readOnly"] != mount["readOnly"]:
                violations.append(
                    f"{relative}: {mount['path']!r} readOnly={mount['readOnly']!r}, "
                    f"expected {HOSTPATH_ALLOWLIST[index]['readOnly']!r}"
                )
            else:
                matched.add(index)
    print(f"Evaluated: files scanned={len(manifests)}, hostPath entries found={len(found)}, "
          f"allowlist entries matched={len(matched)}/{len(HOSTPATH_ALLOWLIST)}")
    if not HOSTPATH_ALLOWLIST:
        violations.append("hostPath allowlist is empty")
    if not found:
        violations.append("no hostPath entries found in repository scan")
    for index, entry in enumerate(HOSTPATH_ALLOWLIST):
        if index not in matched:
            violations.append(f"stale or unmatched allowlist entry: {entry['file']} {entry['path']}")
    if violations:
        raise AssertionError("\n".join(violations))


def check_bootstrap(root):
    harden = (root / "scripts/cluster/harden-node-files.sh").read_text()
    expected = ('[ -d /etc/kubernetes/manifests ] && chmod 600 '
                '/etc/kubernetes/manifests/*.yaml 2>/dev/null && changed="$changed manifests"')
    if expected not in harden:
        raise AssertionError("harden-node-files.sh no longer chmods manifest YAML files to 600")
    manifest_lines = [line.strip() for line in harden.splitlines()
                      if "/etc/kubernetes/manifests" in line and not line.lstrip().startswith("#")]
    if manifest_lines != [expected]:
        raise AssertionError("manifest directory/ownership hardening appeared; update documented contract")
    for name in ("02-init-cluster.sh", "02-join-control-plane.sh"):
        source = (root / "scripts/cluster" / name).read_text()
        if "harden-node-files.sh" in source:
            raise AssertionError(f"{name} unexpectedly invokes harden-node-files.sh")
        if re.search(r"\b(chmod|chown)\b[^\n]*/etc/kubernetes/manifests", source):
            raise AssertionError(f"{name} changes manifest permissions or ownership")


def mutation_check(root):
    fixtures = Path(__file__).resolve().parents[1] / "fixtures/static-pod-hostpaths"
    check_gitops([root / "scripts/airgap"])
    original_allowlist = HOSTPATH_ALLOWLIST
    try:
        globals()["HOSTPATH_ALLOWLIST"] = ({
            **HOSTPATH_ALLOWLIST[0], "file": "good/allowed.yaml",
        },)
        check_gitops([fixtures / "good"], identity_root=fixtures)
    finally:
        globals()["HOSTPATH_ALLOWLIST"] = original_allowlist

    bad = fixtures / "bad"
    try:
        check_gitops([bad], identity_root=fixtures)
    except AssertionError:
        print("PASS: unapproved path mutation was rejected")
    else:
        raise AssertionError("unapproved fixture hostPath was accepted")

    with tempfile.TemporaryDirectory() as tmp:
        empty = Path(tmp) / "empty"
        empty.mkdir()
        try:
            check_gitops([empty])
        except AssertionError:
            print("PASS: zero-entry scan mutation was rejected")
        else:
            raise AssertionError("zero-entry scan mutation was accepted")

    stale = HOSTPATH_ALLOWLIST + ({
        **HOSTPATH_ALLOWLIST[0], "file": "scripts/airgap/removed-workload.yaml",
    },)
    original_allowlist = HOSTPATH_ALLOWLIST
    try:
        globals()["HOSTPATH_ALLOWLIST"] = stale
        try:
            check_gitops([root / "scripts/airgap"])
        except AssertionError as error:
            if "stale or unmatched allowlist entry" not in str(error):
                raise
            print("PASS: stale allowlist entry mutation was rejected")
        else:
            raise AssertionError("stale allowlist entry mutation was accepted")
    finally:
        globals()["HOSTPATH_ALLOWLIST"] = original_allowlist

    with tempfile.TemporaryDirectory() as tmp:
        mutated_root = Path(tmp)
        mutated = mutated_root / "scripts/airgap/04-bootstrap-registry-k8s.yaml"
        mutated.parent.mkdir(parents=True)
        source = (root / "scripts/airgap/04-bootstrap-registry-k8s.yaml").read_text()
        mutated.write_text(source.replace("mountPath: /var/lib/registry",
                                          "mountPath: /var/lib/registry\n              readOnly: true"))
        try:
            check_gitops([mutated.parent], identity_root=mutated_root)
        except AssertionError as error:
            if "readOnly=True" not in str(error):
                raise
            print("PASS: readOnly mutation was rejected")
        else:
            raise AssertionError("readOnly mutation was accepted")

    with tempfile.TemporaryDirectory() as tmp:
        copied_root = Path(tmp)
        copied = copied_root / "unlisted.yaml"
        copied.write_text((root / "scripts/airgap/04-bootstrap-registry-k8s.yaml").read_text())
        try:
            check_gitops([copied.parent], identity_root=copied_root)
        except AssertionError as error:
            if "unlisted workload/namespace" not in str(error):
                raise
            print("PASS: unlisted workload/file mutation was rejected")
        else:
            raise AssertionError("unlisted workload/file mutation was accepted")

    with tempfile.TemporaryDirectory() as tmp:
        mutated = Path(tmp)
        (mutated / "scripts/cluster").mkdir(parents=True)
        (mutated / "scripts/cluster/harden-node-files.sh").write_text(
            (root / "scripts/cluster/harden-node-files.sh").read_text().replace(
                "chmod 600 /etc/kubernetes/manifests/*.yaml",
                "chmod 700 /etc/kubernetes/manifests && chmod 600 /etc/kubernetes/manifests/*.yaml",
                1,
            )
        )
        for name in ("02-init-cluster.sh", "02-join-control-plane.sh"):
            (mutated / "scripts/cluster" / name).write_text(
                (root / "scripts/cluster" / name).read_text()
            )
        try:
            check_bootstrap(mutated)
        except AssertionError:
            print("PASS: manifest-directory chmod mutation was rejected")
        else:
            raise AssertionError("manifest-directory chmod mutation was accepted")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mutation-verify", action="store_true")
    args = parser.parse_args()
    check_gitops([ROOT / "gitops", ROOT / "scripts/airgap"])
    check_bootstrap(ROOT)
    if args.mutation_verify:
        mutation_check(ROOT)
    print("PASS: repository hostPaths are allowlisted and manifest hardening matches scripts")


if __name__ == "__main__":
    main()
