#!/usr/bin/env python3
"""Narwhal#113: the Kakao Cloud security group's kubelet (10250) rule must be
scoped to the node subnet, not the whole VPC -- the VPC's other subnet holds
only the bastion, which never calls kubelet's API. Parses the rule block
structurally (by its port_range_min/max pair) rather than grepping the whole
file, so a different rule's remote_ip_prefix on a nearby line can't produce a
false pass."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SG_FILE = ROOT / "csp/kakao-cloud/terraform/modules/security/main.tf"


def main():
    text = SG_FILE.read_text()
    blocks = re.findall(r"\{[^{}]*\}", text, re.S)
    kubelet_blocks = [
        b for b in blocks
        if re.search(r"port_range_min\s*=\s*10250", b) and re.search(r"port_range_max\s*=\s*10250", b)
    ]
    if len(kubelet_blocks) != 1:
        raise SystemExit(f"expected exactly one kubelet (10250) rule block in {SG_FILE}, found {len(kubelet_blocks)}")
    match = re.search(r"remote_ip_prefix\s*=\s*(\S+)", kubelet_blocks[0])
    if not match:
        raise SystemExit(f"kubelet (10250) rule block has no remote_ip_prefix in {SG_FILE}")
    value = match.group(1)
    if value != "var.subnet_cidr":
        raise SystemExit(
            f"kubelet (10250) remote_ip_prefix is {value!r}, expected 'var.subnet_cidr' "
            "(narwhal#113: scoped to the node subnet, not var.vpc_cidr)"
        )
    print("PASS kubelet (10250) security group rule is scoped to var.subnet_cidr")


if __name__ == "__main__":
    main()
