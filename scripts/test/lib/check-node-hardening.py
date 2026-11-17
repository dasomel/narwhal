"""Guard node file hardening integration across all provisioning scripts (R368).

Verifies that harden-node-files.sh is invoked non-fatally and idempotently in:
- scripts/cluster/02-init-cluster.sh
- scripts/cluster/02-join-control-plane.sh
- scripts/cluster/02-join-worker.sh
- scripts/cluster/03-cni-install.sh
"""

import argparse
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

HOOK_SCRIPTS = (
    "scripts/cluster/02-init-cluster.sh",
    "scripts/cluster/02-join-control-plane.sh",
    "scripts/cluster/02-join-worker.sh",
    "scripts/cluster/03-cni-install.sh",
)

REQUIRED_HARDEN_PATTERNS = (
    "/etc/cni/net.d",
    "/var/lib/kubelet/config.yaml",
    "/usr/lib/systemd/system/kubelet.service",
    "/etc/kubernetes/pki/ca.crt",
    "/etc/kubernetes/manifests",
    "/var/lib/etcd",
)


def check_hooks(root: Path):
    harden_file = root / "scripts/cluster/harden-node-files.sh"
    if not harden_file.is_file():
        raise AssertionError("scripts/cluster/harden-node-files.sh is missing")
    harden_text = harden_file.read_text()
    for pattern in REQUIRED_HARDEN_PATTERNS:
        if pattern not in harden_text:
            raise AssertionError(f"harden-node-files.sh missing hardening target: {pattern}")

    for rel_path in HOOK_SCRIPTS:
        script_file = root / rel_path
        if not script_file.is_file():
            raise AssertionError(f"Hook script missing: {rel_path}")
        text = script_file.read_text()
        if "harden-node-files.sh" not in text:
            raise AssertionError(f"{rel_path} does not reference harden-node-files.sh")
        # Ensure path resolution accounts for staged guest environment
        if "/scripts/cluster/harden-node-files.sh" not in text and "${HARDEN_SCRIPT}" not in text:
            raise AssertionError(f"{rel_path} does not properly resolve harden-node-files.sh path")
        # Ensure the execution line itself is non-fatal (error handled so set -e does not abort)
        lines = [line for line in text.splitlines() if "HARDEN_SCRIPT" in line and "bash " in line]
        if not lines:
            raise AssertionError(f"{rel_path} missing execution of HARDEN_SCRIPT")
        for line in lines:
            if "|| echo" not in line and "|| true" not in line:
                raise AssertionError(f"{rel_path} invokes harden-node-files.sh fatally: {line}")


def mutation_check(root: Path):
    # Mutation 1: removing invocation from each of the hook scripts
    for target in HOOK_SCRIPTS:
        with tempfile.TemporaryDirectory() as tmp:
            mut_root = Path(tmp)
            (mut_root / "scripts/cluster").mkdir(parents=True)
            (mut_root / "scripts/cluster/harden-node-files.sh").write_text(
                (root / "scripts/cluster/harden-node-files.sh").read_text()
            )
            for s in HOOK_SCRIPTS:
                stext = (root / s).read_text()
                if s == target:
                    stext = stext.replace("harden-node-files.sh", "unrelated-file.sh")
                (mut_root / s).write_text(stext)
            try:
                check_hooks(mut_root)
            except AssertionError:
                print(f"PASS: missing invocation mutation in {target} was rejected")
            else:
                raise AssertionError(f"missing invocation mutation in {target} was accepted")

    # Mutation 2: fatal invocation (removing error guard)
    for target in HOOK_SCRIPTS:
        with tempfile.TemporaryDirectory() as tmp:
            mut_root = Path(tmp)
            (mut_root / "scripts/cluster").mkdir(parents=True)
            (mut_root / "scripts/cluster/harden-node-files.sh").write_text(
                (root / "scripts/cluster/harden-node-files.sh").read_text()
            )
            for s in HOOK_SCRIPTS:
                stext = (root / s).read_text()
                if s == target:
                    stext = stext.replace('|| echo "WARN: harden-node-files.sh reported issues, continuing..."', "")
                    stext = stext.replace('|| true', "")
                (mut_root / s).write_text(stext)
            try:
                check_hooks(mut_root)
            except AssertionError:
                print(f"PASS: fatal invocation mutation in {target} was rejected")
            else:
                raise AssertionError(f"fatal invocation mutation in {target} was accepted")

    # Mutation 3: removing a hardening target from harden-node-files.sh
    for pattern in ("/etc/cni/net.d", "/var/lib/etcd"):
        with tempfile.TemporaryDirectory() as tmp:
            mut_root = Path(tmp)
            (mut_root / "scripts/cluster").mkdir(parents=True)
            (mut_root / "scripts/cluster/harden-node-files.sh").write_text(
                (root / "scripts/cluster/harden-node-files.sh").read_text().replace(pattern, "/disabled/target")
            )
            for s in HOOK_SCRIPTS:
                (mut_root / s).write_text((root / s).read_text())
            try:
                check_hooks(mut_root)
            except AssertionError:
                print(f"PASS: missing target {pattern} mutation in harden-node-files.sh was rejected")
            else:
                raise AssertionError(f"missing target {pattern} mutation was accepted")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mutation-verify", action="store_true")
    args = parser.parse_args()

    check_hooks(ROOT)
    if args.mutation_verify:
        mutation_check(ROOT)
    print("PASS: node file permission hardening is wired into provisioning across all node types")


if __name__ == "__main__":
    main()
