"""Guard node file hardening integration across all provisioning scripts (R368).

Verifies on active (non-comment) lines that harden-node-files.sh is invoked
non-fatally and idempotently in:
- scripts/cluster/02-init-cluster.sh
- scripts/cluster/02-join-control-plane.sh
- scripts/cluster/02-join-worker.sh
- scripts/cluster/03-cni-install.sh

Also verifies that joining scripts wait boundedly for a CNI config file in
/etc/cni/net.d before hardening (and still harden on already-joined re-runs),
runs mutation checks, and executes behavioural tests of the join wait/harden
snippet against a stub harden script and temporary CNI directory.
"""

import argparse
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

HOOK_SCRIPTS = (
    "scripts/cluster/02-init-cluster.sh",
    "scripts/cluster/02-join-control-plane.sh",
    "scripts/cluster/02-join-worker.sh",
    "scripts/cluster/03-cni-install.sh",
)

JOIN_SCRIPTS = (
    "scripts/cluster/02-join-control-plane.sh",
    "scripts/cluster/02-join-worker.sh",
)

REQUIRED_HARDEN_PATTERNS = (
    "/etc/cni/net.d",
    "/var/lib/kubelet/config.yaml",
    "/usr/lib/systemd/system/kubelet.service",
    "/etc/kubernetes/pki/ca.crt",
    "/etc/kubernetes/manifests",
    "/var/lib/etcd",
)

STAGED_PATH_RE = re.compile(
    r'^\s*(?:local\s+)?HARDEN_SCRIPT="\$\{STAGE_DIR:-/home/vagrant\}/scripts/cluster/harden-node-files\.sh"\s*$',
    re.M,
)
FALLBACK_GUARD_RE = re.compile(
    r'^\s*if\s+\[\s+!\s+-f\s+"\$\{HARDEN_SCRIPT\}"\s*\];\s*then\s*$',
    re.M,
)
FALLBACK_PATH_RE = re.compile(
    r'^\s*(?:local\s+)?HARDEN_SCRIPT="\$\(cd "\$\(dirname "\$\{BASH_SOURCE\[0\]\}"\)" && pwd\)/harden-node-files\.sh"\s*$',
    re.M,
)
EXEC_LINE_RE = re.compile(r'^\s*bash\s+"\$\{HARDEN_SCRIPT\}"', re.M)


def active_lines(text: str) -> list[str]:
    return [
        line for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def extract_harden_joined_node(text: str) -> str:
    start_marker = "harden_joined_node() {"
    idx = text.find(start_marker)
    if idx == -1:
        raise AssertionError("harden_joined_node() definition not found")
    end_idx = text.find("\n}\n", idx)
    if end_idx == -1:
        raise AssertionError("harden_joined_node() closing brace not found")
    return text[idx : end_idx + 2]


def check_hooks(root: Path):
    harden_file = root / "scripts/cluster/harden-node-files.sh"
    if not harden_file.is_file():
        raise AssertionError("scripts/cluster/harden-node-files.sh is missing")
    harden_active = "\n".join(active_lines(harden_file.read_text()))
    for pattern in REQUIRED_HARDEN_PATTERNS:
        if pattern not in harden_active:
            raise AssertionError(
                f"harden-node-files.sh missing active hardening target: {pattern}"
            )

    for rel_path in HOOK_SCRIPTS:
        script_file = root / rel_path
        if not script_file.is_file():
            raise AssertionError(f"Hook script missing: {rel_path}")
        text = script_file.read_text()
        active = active_lines(text)
        active_text = "\n".join(active)

        if not STAGED_PATH_RE.search(active_text):
            raise AssertionError(
                f"{rel_path} missing active staged candidate path "
                '(${STAGE_DIR:-/home/vagrant}/scripts/cluster/harden-node-files.sh)'
            )
        if not FALLBACK_GUARD_RE.search(active_text) or not FALLBACK_PATH_RE.search(active_text):
            raise AssertionError(
                f"{rel_path} missing active script-directory fallback path "
                '($(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/harden-node-files.sh)'
            )

        exec_lines = [line for line in active if EXEC_LINE_RE.search(line)]
        if not exec_lines:
            raise AssertionError(f"{rel_path} missing active execution of HARDEN_SCRIPT")
        for line in exec_lines:
            if "|| echo" not in line and "|| true" not in line:
                raise AssertionError(f"{rel_path} invokes harden-node-files.sh fatally: {line}")

        if rel_path in JOIN_SCRIPTS:
            fn_text = "\n".join(active_lines(extract_harden_joined_node(text)))
            for required in (
                'CNI_CONF_DIR:-/etc/cni/net.d',
                'CNI_WAIT_TIMEOUT:-180',
                'CNI_WAIT_INTERVAL:-5',
                'while :;',
                'find "${cni_dir}"',
                '-type f',
                'sleep ',
                'WARN:',
                'CIS 1.1.9',
            ):
                if required not in fn_text:
                    raise AssertionError(
                        f"{rel_path} harden_joined_node missing CNI wait element: {required}"
                    )
            wait_pos = fn_text.find("while :;")
            exec_match = EXEC_LINE_RE.search(fn_text)
            if not exec_match or wait_pos >= exec_match.start():
                raise AssertionError(
                    f"{rel_path} must wait for CNI config before running harden-node-files.sh"
                )

            guard_match = re.search(
                r'if \[\[ -f /etc/kubernetes/kubelet\.conf \]\]; then(.*?)\nfi',
                active_text,
                re.S,
            )
            if not guard_match:
                raise AssertionError(f"{rel_path} missing kubelet.conf idempotency guard")
            guard_body = guard_match.group(1)
            if not re.search(r'^\s*harden_joined_node\s*$', guard_body, re.M):
                raise AssertionError(
                    f"{rel_path} already-joined branch must call harden_joined_node before exit 0"
                )
            harden_pos = guard_body.find("harden_joined_node")
            exit_pos = guard_body.find("exit 0")
            if exit_pos == -1 or harden_pos > exit_pos:
                raise AssertionError(
                    f"{rel_path} already-joined branch calls harden_joined_node after exit 0"
                )

            post_guard = active_text[guard_match.end():]
            if not re.search(r'^\s*harden_joined_node\s*$', post_guard, re.M):
                raise AssertionError(
                    f"{rel_path} post-join path must call harden_joined_node"
                )


def _setup_mutated_root(root: Path, mut_root: Path, mutator_fn):
    (mut_root / "scripts/cluster").mkdir(parents=True)
    harden_text = (root / "scripts/cluster/harden-node-files.sh").read_text()
    (mut_root / "scripts/cluster/harden-node-files.sh").write_text(
        mutator_fn("scripts/cluster/harden-node-files.sh", harden_text)
    )
    for s in HOOK_SCRIPTS:
        stext = (root / s).read_text()
        (mut_root / s).write_text(mutator_fn(s, stext))


def _assert_rejected(root: Path, label: str, mutator_fn):
    with tempfile.TemporaryDirectory() as tmp:
        mut_root = Path(tmp)
        _setup_mutated_root(root, mut_root, mutator_fn)
        try:
            check_hooks(mut_root)
        except AssertionError:
            print(f"PASS: {label} was rejected")
        else:
            raise AssertionError(f"{label} was accepted")


def mutation_check(root: Path):
    # 1. Missing invocation from each hook script
    for target in HOOK_SCRIPTS:
        _assert_rejected(
            root,
            f"missing invocation mutation in {target}",
            lambda s, t, tgt=target: t.replace("harden-node-files.sh", "unrelated-file.sh") if s == tgt else t,
        )

    # 2. Commented-out invocation in each hook script
    for target in HOOK_SCRIPTS:
        def comment_mut(s, t, tgt=target):
            if s != tgt:
                return t
            out = []
            for line in t.splitlines():
                if any(tok in line for tok in ("HARDEN_SCRIPT", "harden-node-files.sh")) or re.match(r'^\s*harden_joined_node\s*$', line):
                    out.append("# " + line)
                else:
                    out.append(line)
            return "\n".join(out) + "\n"

        _assert_rejected(root, f"commented-out invocation mutation in {target}", comment_mut)

    # 3. Wrong path mutations (staged path, fallback path, and both)
    for target in HOOK_SCRIPTS:
        _assert_rejected(
            root,
            f"wrong staged path mutation in {target}",
            lambda s, t, tgt=target: (
                t.replace(
                    "${STAGE_DIR:-/home/vagrant}/scripts/cluster/harden-node-files.sh",
                    "/missing/harden-node-files.sh",
                )
                if s == tgt else t
            ),
        )
        _assert_rejected(
            root,
            f"wrong script-dir fallback path mutation in {target}",
            lambda s, t, tgt=target: (
                t.replace(
                    '$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/harden-node-files.sh',
                    "/missing/harden-node-files.sh",
                )
                if s == tgt else t
            ),
        )
        _assert_rejected(
            root,
            f"both paths wrong mutation in {target}",
            lambda s, t, tgt=target: (
                t.replace(
                    "${STAGE_DIR:-/home/vagrant}/scripts/cluster/harden-node-files.sh",
                    "/missing/harden-node-files.sh",
                ).replace(
                    '$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/harden-node-files.sh',
                    "/missing/harden-node-files.sh",
                )
                if s == tgt else t
            ),
        )

    # 4. Fatal invocation (removing error guard)
    for target in HOOK_SCRIPTS:
        _assert_rejected(
            root,
            f"fatal invocation mutation in {target}",
            lambda s, t, tgt=target: (
                t.replace('|| echo "WARN: harden-node-files.sh reported issues, continuing..."', "")
                .replace("|| true", "")
                if s == tgt else t
            ),
        )

    # 5. Wait removed on join scripts
    for target in JOIN_SCRIPTS:
        def remove_wait(s, t, tgt=target):
            if s != tgt:
                return t
            return re.sub(
                r'  local cni_dir=.*?  if \[ "\$\{cni_ready\}" != true \]; then.*?  fi\n',
                "",
                t,
                flags=re.S,
            )

        _assert_rejected(root, f"CNI wait removed mutation in {target}", remove_wait)

    # 6. Already-joined guard skips hardening on join scripts
    for target in JOIN_SCRIPTS:
        def bypass_already_joined(s, t, tgt=target):
            if s != tgt:
                return t
            return re.sub(
                r'(if \[\[ -f /etc/kubernetes/kubelet\.conf \]\]; then\n.*?\n)\s*harden_joined_node\n',
                r'\1',
                t,
                count=1,
                flags=re.S,
            )

        _assert_rejected(root, f"already-joined bypass mutation in {target}", bypass_already_joined)

    # 7. Removing hardening target from harden-node-files.sh
    for pattern in ("/etc/cni/net.d", "/var/lib/etcd"):
        _assert_rejected(
            root,
            f"missing target {pattern} mutation in harden-node-files.sh",
            lambda s, t, pat=pattern: t.replace(pat, "/disabled/target") if s == "scripts/cluster/harden-node-files.sh" else t,
        )


def behavioural_test(root: Path):
    stub_body = (
        "#!/bin/bash\n"
        "set -eu\n"
        'files="$(ls -A "${CNI_CONF_DIR}" 2>/dev/null | tr \'\\n\' \',\')"\n'
        'printf "RAN %s\\n" "${files}" >> "${STUB_LOG}"\n'
        'exit "${STUB_RC:-0}"\n'
    )

    for rel_path in JOIN_SCRIPTS:
        fn_src = extract_harden_joined_node((root / rel_path).read_text())
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cluster_dir = tmp_path / "scripts/cluster"
            cluster_dir.mkdir(parents=True)
            stub_script = cluster_dir / "harden-node-files.sh"
            stub_script.write_text(stub_body)
            stub_script.chmod(0o755)

            runner = cluster_dir / "runner.sh"
            runner.write_text(f"#!/bin/bash\nset -euo pipefail\n{fn_src}\nharden_joined_node\n")
            runner.chmod(0o755)

            base_env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "STAGE_DIR": str(tmp_path),
            }

            # Case A: CNI config file appears after wait begins -> stub runs after file appears
            cni_a = tmp_path / "cni-a"
            cni_a.mkdir()
            log_a = tmp_path / "log-a"
            env_a = {
                **base_env,
                "CNI_CONF_DIR": str(cni_a),
                "CNI_WAIT_TIMEOUT": "5",
                "CNI_WAIT_INTERVAL": "1",
                "STUB_LOG": str(log_a),
            }
            proc = subprocess.Popen(
                ["bash", str(runner)],
                env=env_a,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            time.sleep(0.3)
            if proc.poll() is not None:
                out, err = proc.communicate()
                raise AssertionError(
                    f"{rel_path} exited before CNI file appeared (rc={proc.returncode}): {out} {err}"
                )
            if log_a.exists():
                proc.kill()
                raise AssertionError(f"{rel_path} ran stub before CNI file appeared")
            (cni_a / "05-cilium.conflist").write_text("{}\n")
            out_a, err_a = proc.communicate(timeout=10)
            if proc.returncode != 0:
                raise AssertionError(f"{rel_path} wait-then-harden failed: {out_a} {err_a}")
            if "WARN: no CNI config file appeared" in out_a:
                raise AssertionError(f"{rel_path} logged timeout WARN even though file appeared")
            if not log_a.is_file() or log_a.read_text().strip() != "RAN 05-cilium.conflist,":
                raise AssertionError(
                    f"{rel_path} stub output unexpected: {log_a.read_text() if log_a.exists() else '<missing>'}"
                )

            # Case B: Timeout + failing stub -> logs WARN naming consequence and still runs non-fatally
            cni_b = tmp_path / "cni-b"
            cni_b.mkdir()
            log_b = tmp_path / "log-b"
            res_b = subprocess.run(
                ["bash", str(runner)],
                env={
                    **base_env,
                    "CNI_CONF_DIR": str(cni_b),
                    "CNI_WAIT_TIMEOUT": "1",
                    "CNI_WAIT_INTERVAL": "1",
                    "STUB_LOG": str(log_b),
                    "STUB_RC": "42",
                },
                capture_output=True,
                text=True,
                timeout=10,
            )
            if res_b.returncode != 0:
                raise AssertionError(
                    f"{rel_path} timeout/failing-stub exited non-zero ({res_b.returncode}): {res_b.stdout} {res_b.stderr}"
                )
            if "WARN: no CNI config file appeared" not in res_b.stdout or "CIS 1.1.9" not in res_b.stdout:
                raise AssertionError(f"{rel_path} missing timeout WARN naming consequence: {res_b.stdout}")
            if "WARN: harden-node-files.sh reported issues" not in res_b.stdout:
                raise AssertionError(f"{rel_path} missing non-fatal harden WARN: {res_b.stdout}")
            if not log_b.is_file() or log_b.read_text().strip() != "RAN":
                raise AssertionError(f"{rel_path} stub did not run on timeout")

            # Case C: BASH_SOURCE[0] directory fallback when STAGE_DIR lacks script
            fallback_dir = tmp_path / "fallback"
            fallback_dir.mkdir()
            fb_stub = fallback_dir / "harden-node-files.sh"
            fb_stub.write_text(stub_body)
            fb_stub.chmod(0o755)
            fb_runner = fallback_dir / "runner.sh"
            fb_runner.write_text(runner.read_text())
            fb_runner.chmod(0o755)
            log_c = tmp_path / "log-c"
            res_c = subprocess.run(
                ["bash", str(fb_runner)],
                env={
                    "PATH": base_env["PATH"],
                    "STAGE_DIR": str(tmp_path / "missing-stage"),
                    "CNI_CONF_DIR": str(cni_a),
                    "CNI_WAIT_TIMEOUT": "1",
                    "CNI_WAIT_INTERVAL": "1",
                    "STUB_LOG": str(log_c),
                },
                capture_output=True,
                text=True,
                timeout=10,
            )
            if res_c.returncode != 0:
                raise AssertionError(f"{rel_path} BASH_SOURCE fallback failed: {res_c.stdout} {res_c.stderr}")
            if not log_c.is_file() or log_c.read_text().strip() != "RAN 05-cilium.conflist,":
                raise AssertionError(f"{rel_path} BASH_SOURCE fallback did not run local stub")

        print(f"PASS: behavioural wait/timeout/fallback test passed for {rel_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mutation-verify", action="store_true")
    args = parser.parse_args()

    check_hooks(ROOT)
    behavioural_test(ROOT)
    if args.mutation_verify:
        mutation_check(ROOT)
    print("PASS: node file permission hardening is wired into provisioning across all node types")


if __name__ == "__main__":
    main()
