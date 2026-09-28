#!/bin/bash
set -euo pipefail
POD_NETWORK_CIDR="${POD_NETWORK_CIDR:-10.244.0.0/16}"
python3 - "${POD_NETWORK_CIDR}" <<'PY'
import ipaddress, json, re, subprocess, sys
configured = ipaddress.ip_network(sys.argv[1], strict=False)
cluster = json.loads(subprocess.check_output(["kubectl", "-n", "kube-system", "get", "configmap", "kubeadm-config", "-o", "json"]))
match = re.search(r"^\s*podSubnet:\s*[\"\']?([^\"\'\s]+)", cluster["data"]["ClusterConfiguration"], re.MULTILINE)
if not match: raise SystemExit("kubeadm ClusterConfiguration has no networking.podSubnet")
pod_subnet = match.group(1)
if ipaddress.ip_network(pod_subnet, strict=False) != configured:
    raise SystemExit(f"POD_NETWORK_CIDR {configured} differs from kubeadm podSubnet {pod_subnet}")
nodes = json.loads(subprocess.check_output(["kubectl", "get", "ciliumnodes", "-o", "json"]))["items"]
if not nodes: raise SystemExit("no CiliumNode objects found")
for node in nodes:
    pod_cidrs = node.get("spec", {}).get("ipam", {}).get("podCIDRs", [])
    if not pod_cidrs: raise SystemExit(f"{node['metadata']['name']} has no spec.ipam.podCIDRs")
    for cidr in pod_cidrs:
        if not ipaddress.ip_network(cidr, strict=False).subnet_of(configured):
            raise SystemExit(f"{node['metadata']['name']} podCIDR {cidr} is outside {configured}")
print(f"POD_NETWORK_CIDR={configured}; kubeadm podSubnet matches; {len(nodes)} CiliumNode(s) are contained")
PY
