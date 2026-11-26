#!/usr/bin/env python3
"""R412: pin the approver policy, bundle inputs, and staged kubelet rollout."""

import argparse
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[3]
APPS = "gitops/charts/narwhal-apps"
APP = f"{APPS}/templates/kubelet-csr-approver.yaml"
INIT = "scripts/cluster/02-init-cluster.sh"
CNI = "scripts/cluster/03-cni-install.sh"
INSTALL = "scripts/cluster/install-kubelet-csr-approver.sh"
OPS = "scripts/ops/apply-kubelet-serving-tls.sh"
IMAGES = "scripts/airgap/images.txt"
DIGESTS = "scripts/airgap/lib/image-digests.tsv"
GENERATOR = "scripts/airgap/01-generate-image-list.sh"
IMAGE = "ghcr.io/postfinance/kubelet-csr-approver:v1.2.16"
DIGEST = "sha256:3a09df2f9e1e628863fccbe5479e63b11bbc1f411c82134ee975778e1a25b5e6"
REF = f"{IMAGE}@{DIGEST}"
PATHS = (APP, INIT, CNI, INSTALL, OPS, IMAGES, DIGESTS, GENERATOR)


def command(*args, data=None):
    return subprocess.run(args, input=data, text=True, check=True, capture_output=True).stdout


def app_values(chart, provider, topology, subnet=""):
    config = command(
        "helm", "template", "narwhal-apps", str(chart),
        "--show-only", "templates/kubelet-csr-approver.yaml",
        "--set-string", f"provider={provider},kubeletCsrTopology={topology},kubeletCsrNodeSubnet={subnet}",
    )
    return yaml.safe_load(config)["spec"]


def check_policy(chart):
    for provider, topology, subnet, expected, good, bad in (
        ("vagrant", "single", "", "192.168.56.0/24",
         ("narwhal-master-1", "narwhal-worker-3"), ("other-narwhal-master-1", "narwhal-worker-01", "narwhal-master-1.evil")),
        ("kakao", "ha", "", "172.16.0.0/24",
         ("host-172-16-0-10", "host-172-16-0-22"),
         ("node-host-172-16-0-10", "host-172-16-0-256", "host-172-16-0-22.evil", "host-172-16-1-10")),
        ("kakao", "ha", "172.16.3.0/24", "172.16.3.0/24",
         ("host-172-16-3-10",), ("host-172-16-3", "narwhal-master-1", "host-172-16-0-10")),
        ("kakao", "single", "172.16.2.0/23", "172.16.2.0/23",
         ("host-172-16-3-10",), ("host-172-16-2", "narwhal-master-1", "host-172-16-3-10.evil")),
    ):
        spec = app_values(chart, provider, topology, subnet)
        source = spec["source"]
        assert source["chart"] == "kubelet-csr-approver" and source["targetRevision"] == "1.2.16"
        assert spec["destination"]["namespace"] == "kube-system" and source["helm"]["skipTests"]
        values = source["helm"]["valuesObject"]
        regex = values["providerRegex"]
        assert regex and regex.startswith("^") and regex.endswith("$")
        assert all(re.search(regex, name) for name in good)
        assert not any(re.search(regex, name) for name in bad)
        assert values["providerIpPrefixes"] == [expected]
        assert values["bypassDnsResolution"] is True and not values.get("bypassHostnameCheck")
        assert values["allowedDnsNames"] == 1 and "maxExpirationSeconds" not in values
        assert values["replicas"] == (1 if topology == "single" else 2)
        assert values["image"]["repository"] + ":" + values["image"]["tag"] == REF
        security = values["securityContext"]
        assert security["runAsNonRoot"] and security["readOnlyRootFilesystem"]
        assert not security["allowPrivilegeEscalation"] and security["capabilities"]["drop"] == ["ALL"]
        assert security["seccompProfile"]["type"] == "RuntimeDefault"


def ordered(text, *parts):
    offsets = [text.index(part) for part in parts]
    assert offsets == sorted(offsets), parts


def check_wiring(files):
    assert REF in files[IMAGES].splitlines()
    assert [row.split("\t")[1] for row in files[DIGESTS].splitlines() if row.startswith(f"{IMAGE}\t")] == [DIGEST]
    assert REF in files[GENERATOR].splitlines()
    init, cni, install, ops = (files[p] for p in (INIT, CNI, INSTALL, OPS))
    assert re.search(r"^serverTLSBootstrap: true$", init, re.M)
    ordered(init, "serverTLSBootstrap: true", 'yq \'del(select(.kind == "KubeletConfiguration").serverTLSBootstrap)\'',
            "kubeadm init --config=/tmp/kubeadm-initial-config.yaml", "JOIN_SUFFIX=.pending")
    assert not re.search(r"\bkubeadm init --config=/tmp/kubeadm-config.yaml", init)
    ordered(cni, 'kubectl wait --for=condition=Ready node', "install-kubelet-csr-approver.sh",
            "serverTLSBootstrap == true", "apply-kubelet-serving-tls.sh",
            'mv "${serving_state}/bootstrap-pending" "${serving_state}/join-pending"', 'mv "${join_file}.pending"')
    ordered(install, "--show-only templates/kubelet-csr-approver.yaml", "valuesObject",
            "$(chart kubelet-csr-approver)", 'helm template kubelet-csr-approver "$package"',
            "kubectl apply", "rollout status deploy/kubelet-csr-approver")
    assert "--dry-run) dry_run=true" in ops
    assert "--rollback) target=false" in ops
    ordered(ops, "rollout status deploy/kubelet-csr-approver", '[[ "$dry_run" == false ]] || exit 0',
            'on_node "$index" set "$before" "$target"', 'wait_node "$index" "$target" "$old_fp"',
            "patch cm kubelet-config")
    assert 'kubernetes.io/kubelet-serving' in ops and '"Approved"' in ops
    assert '[[ "$peer_fp" == "$csr_fp" ]]' in ops
    assert "openssl verify -trusted" in ops and "-verify_ip" in ops


def check_initial_render(init):
    expression = re.search(r"(?m)^  yq '([^']+)' .*\\$", init).group(1)
    config = (
        "kind: ClusterConfiguration\nclusterName: kubernetes\n"
        "---\nkind: KubeletConfiguration\nserverTLSBootstrap: true\nmaxPods: 200\n"
    )
    initial = list(yaml.safe_load_all(command("yq", expression, data=config)))
    assert initial == [
        {"kind": "ClusterConfiguration", "clusterName": "kubernetes"},
        {"kind": "KubeletConfiguration", "maxPods": 200},
    ]


def verify_mutations(files):
    cases = (
        (APP, "targetRevision: 1.2.16", "targetRevision: 1.2.15"),
        (APP, "{{ $nodeRegex | quote }}", '""'),
        (APP, "^narwhal-", "narwhal-"),
        (APP, 'if and $network (hasSuffix "/24" $prefix)', 'if false'),
        (APP, "- {{ $prefix | quote }}", "- 0.0.0.0/0"),
        (APP, 'providerIpPrefixes:\n          - {{ $prefix | quote }}', "providerIpPrefixes: []"),
        (IMAGES, REF, IMAGE),
        (DIGESTS, DIGEST, "sha256:" + "0" * 64),
        (GENERATOR, REF, IMAGE),
        (INIT, "init --config=/tmp/kubeadm-initial-config.yaml", "init --config=/tmp/kubeadm-config.yaml"),
        (CNI, "cluster/install-kubelet-csr-approver.sh", "ops/apply-kubelet-serving-tls.sh"),
        (OPS, "--dry-run) dry_run=true", "--dry-run) dry_run=false"),
        (OPS, 'wait_node "$index" "$target" "$old_fp"', "true"),
    )
    with tempfile.TemporaryDirectory(prefix="r412-") as tmp:
        chart = Path(tmp) / "chart"
        shutil.copytree(ROOT / APPS, chart)
        for path, old, new in cases:
            assert old in files[path], (path, old)
            mutated = {**files, path: files[path].replace(old, new, 1)}
            try:
                if path == APP:
                    (chart / "templates/kubelet-csr-approver.yaml").write_text(mutated[path])
                    check_policy(chart)
                else:
                    check_wiring(mutated)
            except (AssertionError, ValueError, subprocess.CalledProcessError):
                continue
            raise AssertionError(f"mutation survived in {path}: {old}")
    print(f"PASS: {len(cases)} mutations rejected")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mutation-verify", action="store_true")
    args = parser.parse_args()
    files = {path: (ROOT / path).read_text() for path in PATHS}
    check_policy(ROOT / APPS)
    check_wiring(files)
    check_initial_render(files[INIT])
    if args.mutation_verify:
        verify_mutations(files)
    print("PASS: R412 approver policy, air-gap pins, and staged serving TLS")


if __name__ == "__main__":
    main()
