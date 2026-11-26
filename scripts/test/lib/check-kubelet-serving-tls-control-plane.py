#!/usr/bin/env python3
"""R416: exercise stage 3 sequencing and render/restore with offline node fixtures."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[3]
INIT = "scripts/cluster/02-init-cluster.sh"
CNI = "scripts/cluster/03-cni-install.sh"
OPS = "scripts/ops/apply-kubelet-serving-tls.sh"
CONTROL = "scripts/ops/lib/kubelet-serving-tls-control-plane.sh"
NODE = "scripts/ops/lib/kubelet-serving-tls-control-plane-node.sh"
FILES = (INIT, CNI, OPS, CONTROL, NODE)
CA = "/etc/kubernetes/pki/ca.crt"
FLAG = f"--kubelet-certificate-authority={CA}"
GATE = "RotateKubeletServerCertificate=true"
MASTERS = ("master-1", "master-2")
HOSTS = (*MASTERS, "worker-1")
IPS = {host: f"192.168.56.{i}" for i, host in enumerate(HOSTS, 10)}

KUBECTL = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import sys

root = Path(os.environ["FIXTURE"])
argv = sys.argv[1:]
cm = root / "config.json"
if "nodes" in argv:
    print((root / "nodes.json").read_text())
elif "kubeadm-config" in argv:
    if "patch" in argv:
        patch = json.loads(Path(argv[argv.index("--patch-file") + 1]).read_text())
        doc = json.loads(cm.read_text())
        assert patch[0] == {"op": "test", "path": "/metadata/resourceVersion",
                            "value": doc["metadata"]["resourceVersion"]}
        assert patch[1]["path"] == "/data/ClusterConfiguration"
        doc["data"]["ClusterConfiguration"] = patch[1]["value"]
        doc["metadata"]["resourceVersion"] += "1"
        cm.write_text(json.dumps(doc))
        with (root / "events").open("a") as events:
            print("patch", file=events)
    print(cm.read_text())
elif "/readyz" in argv:
    host = os.environ["SIM_HOST"]
    assert f"--server=https://{os.environ['SIM_IP']}:6443" in argv
    print("ok")
elif "pod" in argv:
    host = os.environ["SIM_HOST"]
    component = argv[argv.index("pod") + 1].removesuffix("-" + host)
    manifest = root / host / "manifests" / (component + ".yaml")
    cmd = json.loads(subprocess.check_output(
        ["yq", "-o=json", ".spec.containers[0].command", str(manifest)]))
    if os.environ.get("STALE_API") == host:
        cmd = [arg for arg in cmd if not arg.startswith("--kubelet-certificate-authority=")]
    with (root / "events").open("a") as events:
        print("ready", host, component, *cmd[1:], file=events)
    print(json.dumps({"status": {"conditions": [{"type": "Ready", "status": "True"}]},
                      "spec": {"containers": [{"name": component, "command": cmd}]}}))
else:
    sys.exit("unexpected kubectl: " + " ".join(argv))
"""

SSH = r"""#!/usr/bin/env bash
set -eu
while [[ "$1" != *@* ]]; do shift; done
export SIM_HOST="${1#*@}"
export SIM_IP="$(cat "$FIXTURE/$SIM_HOST/ip")"
shift 3 # host, sudo, -n
exec "$@"
"""

OPENSSL = r"""#!/usr/bin/env bash
set -eu
[[ "$1" == s_client && "$2" == -connect && "$3" == "$9":10250 &&
   "$4 $5 $6 $7 $8" == "-purpose sslserver -verifyCAfile /etc/kubernetes/pki/ca.crt -verify_ip" &&
   "${10} ${11}" == "-verify_return_error -brief" ]] || exit 2
printf 'verify %s %s\n' "$SIM_HOST" "$3" >> "$FIXTURE/events"
[[ "${FAIL_PEER:-}" != "$SIM_HOST/$3" ]]
"""


def wiring(files):
    raw = files[INIT].split("cat <<EOF > /tmp/kubeadm-config.yaml\n", 1)[1].split("\nEOF\n", 1)[0]
    cfg = raw.replace("${CERT_SANS}", '\n    - localhost').split("\n---\n", 1)[0]
    assert cfg.count("\n  extraArgs:") == 3
    assert "kubelet-certificate-authority" not in "\n".join(
        line for line in cfg.splitlines() if not line.lstrip().startswith("#"))
    doc = json.loads(subprocess.check_output(["yq", "-o=json", "."], input=cfg, text=True))
    assert {"name": "feature-gates", "value": GATE} in doc["controllerManager"]["extraArgs"]
    cni = files[CNI]
    stage2 = 'apply-kubelet-serving-tls.sh" --kubeconfig "${KUBECONFIG}" --local "$(hostname)"'
    assert cni.index("install-kubelet-csr-approver.sh") < cni.index(stage2) < cni.index(
        stage2 + " --control-plane --timeout 600") < cni.index('mv "${serving_state}/bootstrap-pending"')


def put(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def fixture(root, files):
    node_source = files[NODE].replace(
        "manifests=/etc/kubernetes/manifests", 'manifests="$FIXTURE/${SIM_HOST:-}/manifests"').replace(
        "backup=/var/lib/narwhal/kubelet-serving-tls/kube-apiserver.pre-ca.yaml",
        'backup="$FIXTURE/${SIM_HOST:-}/state/kube-apiserver.pre-ca.yaml"').replace(
        "exec 9>/run/lock/narwhal-kubelet-serving-tls-control-plane.lock",
        'exec 9>"$FIXTURE/$SIM_HOST/lock"')
    for name, text in ((OPS, files[OPS]), (CONTROL, files[CONTROL]), (NODE, node_source)):
        put(root / name, text)
    put(root / "scripts/ops/lib/kubelet-serving-tls-node.sh",
        '#!/bin/bash\n[[ "$1" == read && "$2" == "$SIM_HOST" ]]\n')
    for name, text in (("kubectl", KUBECTL), ("ssh", SSH), ("openssl", OPENSSL),
                       ("flock", '#!/bin/bash\necho "$*" >> "$FIXTURE/flock"\n'), ("timeout", '#!/bin/bash\nshift\nexec "$@"\n')):
        put(root / "bin" / name, text).chmod(0o755)
    nodes = []
    for host, ip in IPS.items():
        put(root / host / "ip", ip)
        labels = {"node-role.kubernetes.io/control-plane": ""} if host in MASTERS else {}
        nodes.append({"metadata": {"name": host, "labels": labels},
                      "status": {"addresses": [{"type": "InternalIP", "address": ip}]}})
    put(root / "nodes.json", json.dumps({"items": nodes}))
    for host in MASTERS:
        for component, option in (
            ("kube-apiserver", "--kubelet-preferred-address-types=InternalIP,ExternalIP,Hostname"),
            ("kube-controller-manager", "--feature-gates=OtherGate=false"),
        ):
            put(root / host / "manifests" / f"{component}.yaml", f"""# previous {host}
apiVersion: v1
kind: Pod
spec:
  containers:
  - name: {component}
    command:
    - {component}
    - {option}
    - --profiling=false
""")
    cfg = {
        "apiVersion": "kubeadm.k8s.io/v1beta4", "kind": "ClusterConfiguration",
        "apiServer": {"extraArgs": [{"name": "profiling", "value": "false"}]},
        "controllerManager": {"extraArgs": [{"name": "feature-gates", "value": "OtherGate=false"}]},
    }
    put(root / "config.json", json.dumps({"metadata": {"resourceVersion": "1"},
                                        "data": {"ClusterConfiguration": json.dumps(cfg) + "\n"}}))
    put(root / "admin.conf", "")
    put(root / "events", "")


def flow(files, scenario):
    with tempfile.TemporaryDirectory(prefix="r413-") as tmp:
        root = Path(tmp)
        fixture(root, files)
        env = {**os.environ, "FIXTURE": tmp, "PATH": f"{root / 'bin'}:{os.environ['PATH']}"}
        api = [root / host / "manifests/kube-apiserver.yaml" for host in MASTERS]
        kcm = [root / host / "manifests/kube-controller-manager.yaml" for host in MASTERS]
        cm = root / "config.json"
        events = root / "events"
        before = [p.read_bytes() for p in (*api, *kcm, cm)]

        def run(*options, failure="", bad_peer="", stale_api=""):
            events.write_text("")
            base = ["bash", str(root / OPS), "--kubeconfig", str(root / "admin.conf"), "--timeout", "6"]
            for host in HOSTS:
                base += ["--node", f"{host}=tester@{host}"]
            try:
                p = subprocess.run(base + list(options), env={**env, "FAIL_PEER": bad_peer, "STALE_API": stale_api},
                                   stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
            except subprocess.TimeoutExpired:
                # Diagnose an intermittent stall seen 2026-11-26 that did not reproduce: show what is blocked.
                print(subprocess.run(["ps", "-o", "pid,ppid,etime,command", "-ax"], capture_output=True,
                                     text=True).stdout, file=sys.stderr)
                raise
            if failure:
                assert p.returncode and failure in p.stderr, (p.stdout, p.stderr)
            else:
                assert p.returncode == 0, (p.stdout, p.stderr)
            return events.read_text().splitlines()

        probes = [f"verify {host} {ip}:10250" for host in MASTERS for ip in IPS.values()]
        if scenario in ("all", "dry"):
            assert run("--control-plane", "--dry-run") == probes
            assert before == [p.read_bytes() for p in (*api, *kcm, cm)]
        if scenario in ("all", "reject"):
            assert run("--control-plane", failure="failed kubelet TLS validation",
                       bad_peer=probes[-1].removeprefix("verify ").replace(" ", "/")) == probes
            assert before == [p.read_bytes() for p in (*api, *kcm, cm)]
        if scenario not in ("all", "apply", "rollback"):
            return
        if scenario == "all":
            logged = run("--control-plane", failure="restoring previous kube-apiserver manifest", stale_api=MASTERS[0])
            assert "patch" not in logged and all(f"ready {MASTERS[1]} " not in line for line in logged)
            assert [p.read_bytes() for p in (*api, cm)] == [*before[:len(api)], before[-1]]

        logged = run("--control-plane")
        assert logged[:6] == probes and logged[-1] == "patch", logged
        assert (root / "flock").read_text().split("\n").count("-w 6 9") >= 2, (root / "flock").read_text()
        assert [line.split()[1:3] for line in logged[6:-1]] == [
            [host, component] for host in MASTERS for component in ("kube-controller-manager", "kube-apiserver")]
        assert all(p.read_text().count(FLAG) == 1 and "--profiling=false" in p.read_text() for p in api)
        assert all(f"--feature-gates=OtherGate=false,{GATE}" in p.read_text() for p in kcm)

        def config():
            rendered = json.loads(cm.read_text())["data"]["ClusterConfiguration"]
            return json.loads(subprocess.check_output(["yq", "-o=json", "."], input=rendered, text=True))

        cfg = config()
        assert cfg["apiServer"]["extraArgs"] == [
            {"name": "profiling", "value": "false"}, {"name": "kubelet-certificate-authority", "value": CA}]
        assert cfg["controllerManager"]["extraArgs"] == [{"name": "feature-gates", "value": f"OtherGate=false,{GATE}"}]
        if scenario == "apply":
            return
        assert "patch" not in run("--control-plane")
        run("--rollback", failure="run --control-plane --rollback before kubelet rollback")
        applied_cm = cm.read_bytes()
        cm.write_bytes(before[-1])
        run("--rollback", failure="master-1: run --control-plane --rollback before kubelet rollback")
        cm.write_bytes(applied_cm)
        subprocess.run(["yq", "-i", '.spec.containers[0].command += ["--oidc-client-id=kubernetes"]',
                        str(api[1])], check=True)
        logged = run("--control-plane", "--rollback")
        assert logged[-1] == "patch" and len(logged) == 3, logged
        assert api[0].read_bytes() == before[0]
        assert FLAG not in api[1].read_text() and "--oidc-client-id=kubernetes" in api[1].read_text(), api[1].read_text()
        assert config()["apiServer"]["extraArgs"] == [{"name": "profiling", "value": "false"}]
        assert config()["controllerManager"]["extraArgs"] == cfg["controllerManager"]["extraArgs"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mutation-verify", action="store_true")
    args = parser.parse_args()
    files = {name: (ROOT / name).read_text() for name in FILES}
    wiring(files)
    flow(files, "all")
    if args.mutation_verify:
        cases = (
            (INIT, 'value: "RotateKubeletServerCertificate=true"', 'value: "RotateKubeletServerCertificate=false"', wiring),
            (INIT, '    - name: profiling', '    - name: kubelet-certificate-authority', wiring),
            (CNI, ' --control-plane --timeout 600\n', '\n', wiring),
            (CNI, ' --control-plane --timeout 600\n', ' --control-plane\n', wiring),
            (CONTROL, '"${control_ips[@]}"', '"${control_ips[0]}"', lambda fs: flow(fs, "reject")),
            (NODE, "-verify_ip", "-verify_hostname", lambda fs: flow(fs, "dry")),
            (CONTROL, '[[ "$dry_run" == false ]] || return 0', "true", lambda fs: flow(fs, "dry")),
            (CONTROL, 'kube -n kube-system patch cm kubeadm-config', 'cat "$work/kubeadm.json" #', lambda fs: flow(fs, "apply")),
            (NODE, 'cat "$backup"', 'cat "$file"', lambda fs: flow(fs, "rollback")),
            (NODE, 'flock -w "$limit" 9', "flock 9", lambda fs: flow(fs, "apply")),
        )
        for name, old, new, check in cases:
            assert old in files[name], (name, old)
            try:
                check({**files, name: files[name].replace(old, new, 1)})
            except (AssertionError, ValueError):
                continue
            raise AssertionError(f"mutation survived: {name}: {old}")
        print(f"PASS: R416 rejected {len(cases)} mutations")
    print("PASS: R416 staged control-plane TLS, dry-run, persistence and rollback (offline fixtures)")


if __name__ == "__main__":
    main()
