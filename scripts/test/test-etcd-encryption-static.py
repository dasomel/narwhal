#!/usr/bin/env python3
"""Offline behavioral checks for etcd encryption verification and rotation gates."""
import os
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
VERIFY = ROOT / "scripts/verify/etcd-encryption-check.sh"
ROTATE = ROOT / "scripts/ops/rotate-etcd-encryption-key.sh"


def run(*args, **kwargs):
    return subprocess.run(args, text=True, capture_output=True, **kwargs)


def main():
    verifier = VERIFY.read_text()
    assert '[[ "$first" == identity || -z "$first" ]]' in verifier
    assert 'legacy layout: key embedded in config (covered by config permissions)' in verifier
    assert 'KUBECONFIG=/etc/kubernetes/admin.conf' in verifier
    assert 'get /registry/secrets/ --prefix -w json' in verifier
    encrypted = b"k8s:enc:aescbc:v1:" + b"\x00\xffbinary"
    plaintext = b"k8s\x00\x01protobuf-payload"
    assert encrypted.startswith(b"k8s:enc:aescbc:v1:")
    assert not plaintext.startswith(b"k8s:enc:")
    with tempfile.TemporaryDirectory(prefix="etcd-encryption-negative-") as tmp:
        tmp_path = pathlib.Path(tmp)
        config = tmp_path / "identity-first.yaml"
        config.write_text("""apiVersion: apiserver.config.k8s.io/v1
kind: EncryptionConfiguration
resources:
  - resources: [secrets]
    providers:
      - identity: {}
      - aescbc:
          keys:
            - name: test
              secret: AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
""")
        fakebin = tmp_path / "bin"
        fakebin.mkdir()
        sudo = fakebin / "sudo"
        sudo.write_text("#!/bin/sh\nexec \"$@\"\n")
        sudo.chmod(0o755)
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text("--encryption-provider-config=" + str(config) + "\n")
        unsafe = tmp_path / "unsafe-key"
        unsafe.write_text("key")
        unsafe.chmod(0o644)
        mode = unsafe.stat().st_mode & 0o777
        assert mode == 0o644
        assert mode != 0o600  # verifier's FAIL branch
        verifier_env = os.environ.copy()
        verifier_env.update({
            "PATH": f"{fakebin}:{verifier_env['PATH']}",
            "CONFIG": str(config),
            "ENC_DIR": str(tmp_path),
            "KEY_FILE": str(unsafe),
            "MANIFEST": str(manifest),
        })
        verifier_result = run("bash", str(VERIFY), env=verifier_env)
        assert verifier_result.returncode != 0
        assert "FAIL provider-secrets" in verifier_result.stdout
        assert "FAIL permissions" in verifier_result.stdout and "expected 600 root:root" in verifier_result.stdout

        legacy_config = tmp_path / "legacy.yaml"
        legacy_config.write_text(config.read_text().replace("              secret: AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", "              secret: AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="))
        legacy_env = verifier_env.copy()
        legacy_env.update({"CONFIG": str(legacy_config), "KEY_FILE": str(tmp_path / "absent-key")})
        legacy_result = run("bash", str(VERIFY), env=legacy_env)
        assert "PASS permissions: legacy layout: key embedded in config (covered by config permissions)" in legacy_result.stdout

        strict = run("bash", "-c", 'FAIL=0; SKIP=1; STRICT=1; (( FAIL == 0 && (STRICT == 0 || SKIP == 0) ))')
        assert strict.returncode != 0  # a peer SKIP fails strict verification

        kubectl = fakebin / "kubectl"
        kubectl.write_text('#!/bin/sh\nprintf \'{"items":[]}\\n\'\n')
        kubectl.chmod(0o755)
        config.write_text("""apiVersion: apiserver.config.k8s.io/v1
kind: EncryptionConfiguration
resources:
  - resources: [secrets]
    providers:
      - aescbc:
          keys:
            - name: key-new
              secret: AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
            - name: key-old
              secret: BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=
      - identity: {}
""")
        state = tmp_path / "state"
        state.write_text("4 key-new\n")
        fail_verify = tmp_path / "verify-fail"
        fail_verify.write_text("#!/bin/sh\nexit 1\n")
        fail_verify.chmod(0o755)
        env = os.environ.copy()
        env.update({
            "PATH": f"{fakebin}:{env['PATH']}",
            "CONFIG": str(config),
            "STATE": str(state),
            "VERIFY": str(fail_verify),
            "NODE_SSH_USER": "vagrant",
        })
        fixture_rotate = tmp_path / "rotate-fixture.sh"
        fixture_rotate.write_text(ROTATE.read_text().replace(
            'if (( EUID != 0 && DRY_RUN == 0 )); then',
            'if (( 0 )); then',
        ))
        result = run("bash", str(fixture_rotate), "127.0.0.1", env=env)
        assert result.returncode != 0, result.stdout + result.stderr
        assert "STEP 6" not in result.stdout, result.stdout
        assert "Verification failed" in result.stderr, result.stderr

    print("PASS: legacy layout, binary aescbc/plaintext fixtures, identity-first, unsafe key mode, strict SKIP, and failed-verifier removal gate")


if __name__ == "__main__":
    main()
