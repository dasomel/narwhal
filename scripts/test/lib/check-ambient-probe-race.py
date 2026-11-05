#!/usr/bin/env python3
"""R307: ambient kubelet-probe race after a node restart (#576).

Pins the three parts of the fix so none can drift silently:
  1. istio-cni retries the ambient pod lookup (cni.ambient.enableAmbientDetectionRetry) in both the
     GitOps Application and the 09-istio-ambient.sh bootstrap values;
  2. verify-cluster.sh counts ztunnel rejections only when src == node CiliumInternalIP and
     dst == the pod (behavioral, against a real-format ztunnel log fixture);
  3. the podhealth section scans the unfiltered not-ready list (the openbao exclusion would hide
     openbao-agent-injector).
--mutation-verify proves each assertion fails when its target is broken.
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
CNI_APP = ROOT / "gitops/charts/narwhal-apps/templates/istio-cni.yaml"
BOOTSTRAP = ROOT / "scripts/cluster/09-istio-ambient.sh"
VERIFY = ROOT / "scripts/test/verify-cluster.sh"
FIXTURE = ROOT / "scripts/test/fixtures/ambient-probe-denial-ztunnel.txt"
POD_IP, HOST_IP = "10.244.4.230", "10.244.4.38"


def gitops_flag(text):
    app = yaml.safe_load(text)
    values = app["spec"]["source"]["helm"]["valuesObject"]
    return values.get("cni", {}).get("ambient", {}).get("enableAmbientDetectionRetry")


def bootstrap_flag(text):
    match = re.search(r"cat > /tmp/istio-cni-values\.yaml << 'EOF'\n(.*?)\nEOF\n", text, re.S)
    if not match:
        return None
    return yaml.safe_load(match.group(1)).get("cni", {}).get("ambient", {}).get("enableAmbientDetectionRetry")


def function_text(text):
    match = re.search(r"^ambient_probe_denials\(\) \{\n.*?^\}\n", text, re.S | re.M)
    return match.group(0) if match else None


def denials(func, pod_ip, host_ip):
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "run.sh"
        script.write_text(f"set -uo pipefail\n{func}\nambient_probe_denials \"$1\" \"$2\" \"$3\"\n")
        out = subprocess.run(["bash", str(script), str(FIXTURE), pod_ip, host_ip],
                             capture_output=True, text=True, check=False)
    return out.stdout.strip()


def validate(cni_app, bootstrap, verify):
    problems = []
    if gitops_flag(cni_app) is not True:
        problems.append("istio-cni Application must set cni.ambient.enableAmbientDetectionRetry: true")
    if bootstrap_flag(bootstrap) is not True:
        problems.append("09-istio-ambient.sh istio-cni values must set cni.ambient.enableAmbientDetectionRetry: true")
    func = function_text(verify)
    if func is None:
        return problems + ["verify-cluster.sh lacks ambient_probe_denials()"]
    expect = {
        # 5 fixture lines: 2 node probes, 1 plaintext client (other src), 1 other pod, 1 meshed.
        "node probes rejected for the volume pod, plaintext client excluded": ((POD_IP, HOST_IP), "2"),
        "same host IP, other pod is not counted": (("10.244.4.1", HOST_IP), "0"),
        "meshed source with no rejection is not counted": ((POD_IP, "10.244.4.91"), "0"),
        "unknown host IP is not counted": ((POD_IP, "10.244.4.99"), "0"),
    }
    for name, (args, want) in expect.items():
        got = denials(func, *args)
        if got != want:
            problems.append(f"ambient_probe_denials: {name}: expected {want}, got {got!r}")
    section = verify.split('should_run "podhealth"', 1)[-1]
    if 'done <<< "${NOTREADY_PODS}"' not in section:
        problems.append("podhealth probe-denial scan must read NOTREADY_PODS (not the openbao-filtered list)")
    if "kubectl get ciliumnode" not in section or "CiliumInternalIP" not in section:
        problems.append("podhealth probe-denial scan must derive the host IP from the node's CiliumInternalIP")
    return problems


MUTATIONS = [
    ("gitops retry disabled", "cni", lambda t: t.replace("enableAmbientDetectionRetry: true", "enableAmbientDetectionRetry: false")),
    ("gitops retry key removed", "cni", lambda t: t.replace("enableAmbientDetectionRetry: true", "")),
    ("bootstrap retry disabled", "boot", lambda t: t.replace("enableAmbientDetectionRetry: true", "enableAmbientDetectionRetry: false")),
    ("bootstrap retry removed", "boot", lambda t: t.replace("enableAmbientDetectionRetry: true", "")),
    ("function ignores src", "ver", lambda t: t.replace('grep -F "src.addr=${3}:" "${1}"', 'cat "${1}"')),
    ("function ignores dst", "ver", lambda t: t.replace('grep -F "dst.addr=${2}:"', 'grep -F "dst.addr="')),
    ("function ignores policy", "ver", lambda t: t.replace('| grep -F "istio_converted_static_strict"', "")),
    ("scan reads filtered list", "ver", lambda t: t.replace('done <<< "${NOTREADY_PODS}"', 'done <<< "${NOTREADY_FILTERED}"')),
    ("host IP not from ciliumnode", "ver", lambda t: t.replace("kubectl get ciliumnode", "kubectl get node")),
]


def main(argv):
    cni, boot, ver = (p.read_text() for p in (CNI_APP, BOOTSTRAP, VERIFY))
    problems = validate(cni, boot, ver)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    if "--mutation-verify" not in argv:
        print("ambient probe race fix pinned (retry flag x2, probe-denial scan)")
        return 0
    escaped = []
    for name, target, mutate in MUTATIONS:
        texts = {"cni": cni, "boot": boot, "ver": ver}
        mutated = mutate(texts[target])
        if mutated == texts[target]:
            print(f"mutation did not change its target: {name}", file=sys.stderr)
            return 1
        texts[target] = mutated
        if not validate(texts["cni"], texts["boot"], texts["ver"]):
            escaped.append(name)
    if escaped:
        print("mutations not caught: " + ", ".join(escaped), file=sys.stderr)
        return 1
    print(f"{len(MUTATIONS)} mutations caught")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
