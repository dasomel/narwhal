#!/usr/bin/env python3
"""R412: pin the approver policy, bundle inputs, and staged kubelet rollout."""

import argparse
from ipaddress import IPv4Address, IPv4Network, summarize_address_range
import json
import os
from pathlib import Path
import re
import runpy
import shlex
import shutil
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[3]
APPS = "gitops/charts/narwhal-apps"
APP = f"{APPS}/templates/kubelet-csr-approver.yaml"
VAGRANT = "Vagrantfile"
KAKAO = "scripts/cloud/provision-kakao.sh"
GITOPS = "scripts/cluster/14-gitops-bootstrap.sh"
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
PATHS = (APP, VAGRANT, KAKAO, GITOPS, INIT, CNI, INSTALL, OPS, IMAGES, DIGESTS, GENERATOR)


def command(*args, data=None):
    return subprocess.run(args, input=data, text=True, check=True, capture_output=True).stdout


def app_values(chart, provider, topology, nodes):
    config = command(
        "helm", "template", "narwhal-apps", str(chart),
        "--show-only", "templates/kubelet-csr-approver.yaml",
        "--set-string", f"provider={provider},kubeletCsrTopology={topology}",
        "--set-json", f"kubeletCsrNodes={json.dumps(nodes)}",
    )
    return yaml.safe_load(config)["spec"]


def vagrant_nodes(vagrant, topology):
    definitions = runpy.run_path(str(Path(__file__).with_name("check-single-node-topology.py")))["definitions"]
    machines = definitions(vagrant, {"NARWHAL_TOPOLOGY": topology})
    provision = {
        name: {Path(item["path"]).name: item.get("env", {}) for item in vm["provisions"] if "path" in item}
        for name, vm in machines.items()
    }
    master = provision["master-1"]
    nodes = json.loads(master["03-cni-install.sh"]["KUBELET_CSR_NODES"])
    assert nodes == json.loads(master["06-phase2-start.sh"]["KUBELET_CSR_NODES"])
    assert nodes == {f"narwhal-{name}": env["03-k8s-install.sh"]["KUBELET_NODE_IP"] for name, env in provision.items()}
    return nodes, master["02-init-cluster.sh"]["VIP_ADDRESS"]


KAKAO_STATE = {
    "bastion_public_ip": "203.0.113.4",
    "bastion_private_ip": "172.16.3.4",
    "master_lb_vip": "172.16.3.190",
    "worker_lb_vip": "172.16.3.191",
    "subnet_cidr": "172.16.2.0/23",
    "vpc_cidr": "172.16.0.0/16",
    "master_private_ips": '["172.16.3.10", "172.16.3.11"]',
    "worker_private_ips": '["172.16.3.21", "172.16.3.22"]',
    "ssh_key_path": "test-key",
}


def kakao_nodes(provision, state, error=None, wrong_hostname_ip=""):
    with tempfile.TemporaryDirectory(prefix="r412-kakao-") as tmp:
        root = Path(tmp)
        script = root / KAKAO
        script.parent.mkdir(parents=True)
        script.write_text(provision)
        shutil.copyfile(ROOT / "scripts/cloud/lib-ssh.sh", script.parent / "lib-ssh.sh")
        tofu = root / "tofu"
        tofu.write_text("#!/bin/bash\ncase \"$3\" in\n" + "".join(
            f"{key}) printf '%s\\n' {shlex.quote(value)} ;;\n" for key, value in state.items()
        ) + "*) exit 1 ;;\nesac\n")
        ssh = root / "ssh"
        ssh.write_text(r'''#!/bin/bash
printf "%s\\n" "$*" >> "$TF_DIR/ssh.log"
if [[ "${@: -1}" == hostname ]]; then
  target="${@: -2:1}"
  ip="${target#*@}"
  if [[ "$ip" == "$WRONG_HOSTNAME_IP" ]]; then
    echo unexpected-hostname
  else
    echo "host-${ip//./-}"
  fi
fi
''')
        for tool in (tofu, ssh):
            tool.chmod(0o700)
        (root / "test-key").touch()
        result = subprocess.run(
            ["bash", str(script), "init"], text=True, capture_output=True,
            env={**os.environ, "PATH": f"{tmp}:{os.environ['PATH']}", "TF_DIR": tmp,
                 "KAKAO_KNOWN_HOSTS": str(root / "known_hosts"), "FORCE": "1",
                 "WRONG_HOSTNAME_IP": wrong_hostname_ip},
        )
        log = root / "ssh.log"
        if error:
            assert result.returncode and error in result.stderr, result.stderr
            if wrong_hostname_ip:
                assert "03-cni-install.sh" not in log.read_text()
            else:
                assert not log.exists()
            return None
        assert result.returncode == 0, result.stderr
        return json.loads(re.search(r"KUBELET_CSR_NODES='([^']+)'", log.read_text())[1])


def policy_fixtures(files):
    single, vip = vagrant_nodes(files[VAGRANT], "single")
    ha, ha_vip = vagrant_nodes(files[VAGRANT], "ha")
    kube_vip = re.search(r'VIP_ADDRESS:-([\d.]+)', (ROOT / "scripts/cluster/00-kube-vip.sh").read_text())[1]
    metallb = (ROOT / "gitops/resources/metallb-config.yaml").read_text()
    blocked = [IPv4Network(ip) for ip in {vip, ha_vip, kube_vip}]
    for pool in yaml.safe_load_all(metallb):
        if pool and pool["kind"] == "IPAddressPool":
            for entry in pool["spec"]["addresses"]:
                if "-" in entry:
                    blocked.extend(summarize_address_range(*map(IPv4Address, entry.split("-"))))
                else:
                    blocked.append(IPv4Network(entry))
    assert len(single) == 1 and len(ha) == 6 and single.items() <= ha.items()
    kakao = kakao_nodes(files[KAKAO], KAKAO_STATE)
    ips = json.loads(KAKAO_STATE["master_private_ips"]) + json.loads(KAKAO_STATE["worker_private_ips"])
    assert kakao == {f"host-{ip.replace('.', '-')}": ip for ip in ips}
    return (
        ("vagrant", "single", single, blocked),
        ("vagrant", "ha", ha, blocked),
        ("kakao", "ha", kakao, [IPv4Network(KAKAO_STATE[key]) for key in ("master_lb_vip", "worker_lb_vip")]),
    )


def check_prefixes(prefixes, nodes, blocked):
    for prefix in prefixes:
        cidr = IPv4Network(prefix)
        assert cidr.prefixlen == 32, f"non-host CSR prefix: {prefix}"
        assert not any(cidr.overlaps(reserved) for reserved in blocked), f"reserved CSR IP: {prefix}"
    assert sorted(prefixes) == sorted(f"{ip}/32" for ip in nodes.values())


def check_rbac(values):
    assert values["rbac"]["manage"] is False
    objects = {obj["kind"]: obj for obj in values["extraObjects"]}
    assert sorted(objects) == ["ClusterRole", "ClusterRoleBinding", "Role", "RoleBinding"]
    rules = objects["ClusterRole"]["rules"]
    assert not any("leases" in rule["resources"] for rule in rules), "cluster-wide lease access"
    assert rules == [
        {"apiGroups": ["certificates.k8s.io"], "resources": ["certificatesigningrequests"], "verbs": ["get", "list", "watch"]},
        {"apiGroups": ["certificates.k8s.io"], "resources": ["certificatesigningrequests/approval"], "verbs": ["update"]},
        {"apiGroups": ["certificates.k8s.io"], "resources": ["signers"], "resourceNames": ["kubernetes.io/kubelet-serving"], "verbs": ["approve"]},
    ]
    assert objects["Role"]["rules"] == [
        {"apiGroups": ["coordination.k8s.io"], "resources": ["leases"], "verbs": ["create", "get", "update"]},
        {"apiGroups": [""], "resources": ["events"], "verbs": ["create"]},
    ]
    for kind in ("ClusterRole", "Role"):
        role, binding = objects[kind], objects[f"{kind}Binding"]
        assert binding["roleRef"] == {
            "apiGroup": "rbac.authorization.k8s.io", "kind": kind, "name": role["metadata"]["name"],
        }
        assert binding["subjects"] == [{
            "kind": "ServiceAccount", "name": values["serviceAccount"]["name"], "namespace": "kube-system",
        }]
    assert all(objects[kind]["metadata"]["namespace"] == "kube-system" for kind in ("Role", "RoleBinding"))


def check_policy(chart, fixtures):
    for provider, topology, nodes, blocked in fixtures:
        spec = app_values(chart, provider, topology, nodes)
        source = spec["source"]
        assert source["chart"] == "kubelet-csr-approver" and source["targetRevision"] == "1.2.16"
        assert spec["destination"]["namespace"] == "kube-system" and source["helm"]["skipTests"]
        values = source["helm"]["valuesObject"]
        assert values["providerRegex"] == "^(" + "|".join(sorted(nodes)) + ")$"
        check_prefixes(values["providerIpPrefixes"], nodes, blocked)
        check_rbac(values)
        assert values["bypassDnsResolution"] is True and not values.get("bypassHostnameCheck")
        assert values["allowedDnsNames"] == 1 and "maxExpirationSeconds" not in values
        assert values["replicas"] == (1 if topology == "single" else 2)
        assert values["image"]["repository"] + ":" + values["image"]["tag"] == REF
        security = values["securityContext"]
        assert security["runAsNonRoot"] and security["readOnlyRootFilesystem"]
        assert not security["allowPrivilegeEscalation"] and security["capabilities"]["drop"] == ["ALL"]
        assert security["seccompProfile"]["type"] == "RuntimeDefault"


def check_fail_closed(chart, provision):
    for nodes in ({}, {"host-8-8-8-8": "8.8.8.8"}, {"host-172-16-3-10": "172.16.3.0/24"}):
        try:
            app_values(chart, "kakao", "ha", nodes)
        except subprocess.CalledProcessError:
            continue
        raise AssertionError(f"Helm accepted invalid Kakao inventory: {nodes}")
    for key, value, error in (
        ("subnet_cidr", "0.0.0.0/0", "private RFC 1918 VPC"),
        ("subnet_cidr", "8.8.8.0/24", "private RFC 1918 VPC"),
        ("subnet_cidr", "172.17.0.0/24", "private RFC 1918 VPC"),
        ("vpc_cidr", "0.0.0.0/0", "private RFC 1918 VPC"),
        ("master_private_ips", "[]", "missing node IPs in master_private_ips"),
        ("worker_private_ips", "[]", "missing node IPs in worker_private_ips"),
        ("master_private_ips", '["172.16.4.10"]', "node IP 172.16.4.10 is outside the node subnet"),
        ("worker_private_ips", '["172.16.3.10"]', "node IP 172.16.3.10 is outside the node subnet, reserved or duplicated"),
        ("worker_private_ips", '["172.16.3.4"]', "node IP 172.16.3.4 is outside the node subnet, reserved or duplicated"),
        ("master_lb_vip", "172.16.3.10", "reserved or duplicated"),
        ("worker_lb_vip", "172.16.3.21", "reserved or duplicated"),
    ):
        kakao_nodes(provision, {**KAKAO_STATE, key: value}, error)
    for ip in ("172.16.3.10", "172.16.3.22"):
        kakao_nodes(provision, KAKAO_STATE, f"node {ip} reports hostname unexpected-hostname", ip)


def check_gitops_inventory(bootstrap, nodes):
    prefix = bootstrap.split('\nGITEA_URL=', 1)[0].replace(
        'source /home/vagrant/scripts/common/lib.sh', ':', 1,
    )
    harness = '''kubectl() {
  case "$*" in
    "get nodes") return 0 ;;
    "-n devtools get application idp-apps --ignore-not-found -o json")
      echo "read idp-apps" >&2
      printf '%s' "$LIVE_APPLICATION" ;;
    *) return 1 ;;
  esac
}
''' + prefix + '\nprintf "\\n%s\\n%s\\n" "$KUBELET_CSR_NODES" "$KUBELET_CSR_TOPOLOGY"\n'
    env = os.environ.copy()
    for name in ("KUBELET_CSR_NODES", "NARWHAL_TOPOLOGY"):
        env.pop(name, None)

    def app(values):
        return json.dumps({"spec": {"source": {"helm": {"valuesObject": values}}}})

    application = app({"kubeletCsrNodes": nodes, "kubeletCsrTopology": "single"})
    explicit_nodes = {"narwhal-master-1": "192.168.56.11"}
    # (live, supplied nodes, supplied topology, expected nodes, expected topology)
    for live, supplied, topology, expected, expected_topology in (
        (application, None, None, nodes, "single"),
        (application, explicit_nodes, "ha", explicit_nodes, "ha"),
        (application, explicit_nodes, None, explicit_nodes, "single"),
        (app({"kubeletCsrNodes": nodes}), None, None, nodes, "ha"),
        (app({"kubeletCsrNodes": nodes, "kubeletCsrTopology": "triple"}), None, None, None, "kubeletCsrTopology"),
        ("", None, None, None, "KUBELET_CSR_NODES"), ("{}", None, None, None, "KUBELET_CSR_NODES"),
    ):
        case_env = {**env, "LIVE_APPLICATION": live}
        if supplied is not None:
            case_env["KUBELET_CSR_NODES"] = json.dumps(supplied)
        if topology is not None:
            case_env["NARWHAL_TOPOLOGY"] = topology
        result = subprocess.run(
            ["bash", "-c", harness], text=True, capture_output=True,
            env=case_env,
        )
        assert ("read idp-apps" in result.stderr) == (supplied is None or topology is None), result.stderr
        if expected is not None:
            assert result.returncode == 0, result.stderr
            out = result.stdout.splitlines()
            assert json.loads(out[-2]) == expected and out[-1] == expected_topology, result.stdout
        else:
            assert result.returncode != 0 and expected_topology in result.stderr, result.stderr


def ordered(text, *parts):
    offsets = [text.index(part) for part in parts]
    assert offsets == sorted(offsets), parts


def check_wiring(files):
    assert REF in files[IMAGES].splitlines()
    assert [row.split("\t")[1] for row in files[DIGESTS].splitlines() if row.startswith(f"{IMAGE}\t")] == [DIGEST]
    assert REF in files[GENERATOR].splitlines()
    init, cni, install, ops = (files[p] for p in (INIT, CNI, INSTALL, OPS))
    vagrant = files[VAGRANT]
    assert 'master_ip = MASTER_NODES.fetch(master_name)' in vagrant
    assert 'worker_ip = WORKER_NODES.fetch(worker_name)' in vagrant
    assert 'master.vm.network "private_network", ip: master_ip' in vagrant
    assert 'worker.vm.network "private_network", ip: worker_ip' in vagrant
    assert "kubeletCsrNodes: ${KUBELET_CSR_NODES}" in files[GITOPS]
    assert '--set-json "kubeletCsrNodes=${KUBELET_CSR_NODES:?' in install
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
    assert '--ssh-opt) ssh_opts+=(-o "${2:?' in ops
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


def verify_mutations(files, fixtures):
    render_prefixes = "providerIpPrefixes: {{ $prefixes | toJson }}"
    _, _, _, vagrant_blocked = fixtures[0]
    vip = vagrant_nodes(files[VAGRANT], "single")[1]
    lb_ip = next(str(pool.network_address) for pool in vagrant_blocked if pool.prefixlen < 32)
    kakao_vip = KAKAO_STATE["master_lb_vip"]
    cases = (
        (APP, "targetRevision: 1.2.16", "targetRevision: 1.2.15"),
        (APP, 'printf "^(%s)$"', 'printf "(%s)"'),
        (APP, 'join "|" $names', 'join "|.*|" $names'),
        (APP, render_prefixes, "providerIpPrefixes: [0.0.0.0/0]"),
        (APP, render_prefixes, "providerIpPrefixes: [192.168.56.0/24]"),
        (APP, render_prefixes, "providerIpPrefixes: []"),
        (APP, render_prefixes, f"providerIpPrefixes: [\"{vip}/32\"]"),
        (APP, render_prefixes, f"providerIpPrefixes: [\"{lb_ip}/32\"]"),
        (APP, render_prefixes, 'providerIpPrefixes: {{ if $kakao }}'
         f'["{kakao_vip}/32"]' '{{ else }}{{ $prefixes | toJson }}{{ end }}'),
        (APP, "manage: false", "manage: true"),
        (APP, "resources: [certificatesigningrequests]", "resources: [certificatesigningrequests, leases]"),
        (KAKAO, "sys.argv[3:6]", "sys.argv[5:6]"),
        (KAKAO, '"${actual_hostname}" != "${expected_hostname}"', '"${actual_hostname}" != "${actual_hostname}"'),
        (GITOPS, 'if [[ -z "${KUBELET_CSR_NODES+x}" ]]; then', 'if false; then'),
        (GITOPS, 'if [[ -z "${KUBELET_CSR_TOPOLOGY}" && -n "${LIVE_IDP_APPS}" ]]; then', 'if false; then'),
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
                    check_policy(chart, fixtures)
                elif path == KAKAO:
                    check_fail_closed(chart, mutated[path])
                elif path == GITOPS:
                    check_gitops_inventory(mutated[path], fixtures[0][2])
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
    fixtures = policy_fixtures(files)
    check_policy(ROOT / APPS, fixtures)
    check_fail_closed(ROOT / APPS, files[KAKAO])
    check_gitops_inventory(files[GITOPS], fixtures[0][2])
    check_wiring(files)
    check_initial_render(files[INIT])
    if args.mutation_verify:
        verify_mutations(files, fixtures)
    print("PASS: R412 approver policy, air-gap pins, and staged serving TLS")


if __name__ == "__main__":
    main()
