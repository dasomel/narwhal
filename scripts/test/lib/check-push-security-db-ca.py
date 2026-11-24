#!/usr/bin/env python3
"""R372: push-security-db.sh validates --ca-cert bundle and docs name the canonical namespace (#728).

Invariants:
  1. push-security-db.sh validates --ca-cert early before any expensive operations.
  2. Missing, unreadable, empty, non-PEM, completely expired, or not-yet-valid certificates are
     rejected with a clear message and nonzero exit code.
  3. Valid multi-certificate bundles with expired or not-yet-valid siblings are accepted with a WARN
     line naming the count only.
  4. Certificate file contents are never dumped to stdout or stderr.
  5. Documentation across operations.md, dns-access.md, disaster-recovery.md, and airgap README.md
     references the canonical devtools namespace for narwhal-ca-cert and platform-system for
     cert-manager/narwhal-wildcard-tls (never the non-existent cert-manager namespace).
  6. Behavioral execution tests pass without requiring a registry or cluster, and remain
     hermetic regardless of local narwhal-airgap-bundle-* directories or AIRGAP_* env vars.
  7. Temporary files from CA validation and subsequent stages are cleaned up on all exit paths via
     a single EXIT trap without leaving files behind.

--mutation-verify proves each invariant fails when mutated.
"""
import datetime
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PUSH_SCRIPT = ROOT / "scripts/airgap/lib/push-security-db.sh"
DOC_OPS = ROOT / "docs/vagrant/operations.md"
DOC_DNS = ROOT / "docs/vagrant/dns-access.md"
DOC_DR = ROOT / "docs/vagrant/disaster-recovery.md"
DOC_AIRGAP = ROOT / "scripts/airgap/README.md"


def check_static(root: Path) -> list[str]:
    errs = []
    push_path = root / "scripts/airgap/lib/push-security-db.sh"
    push_text = push_path.read_text()

    if "validate_ca_cert" not in push_text:
        errs.append(f"{push_path}: missing validate_ca_cert helper")

    if 'openssl x509 -in "${b}" -noout' not in push_text:
        errs.append(f"{push_path}: missing openssl x509 PEM validation per block")

    if "-checkend 0" not in push_text:
        errs.append(f"{push_path}: missing openssl x509 expiration check (-checkend 0)")

    if "-startdate" not in push_text:
        errs.append(f"{push_path}: missing openssl x509 notBefore check (-startdate)")

    if '[[ ! -s "${cert}" ]]' not in push_text:
        errs.append(f"{push_path}: missing empty file check (-s)")

    if '[[ ! -f "${cert}" ]]' not in push_text:
        errs.append(f"{push_path}: missing file existence check (-f)")

    if '[[ ! -r "${cert}" ]]' not in push_text:
        errs.append(f"{push_path}: missing file readability check (-r)")

    # Do not dump file contents
    val_body = push_text[push_text.find("validate_ca_cert()"):push_text.find("if [[ -n \"${SECDB_CA_CERT}\" ]]; then")]
    if re.search(r'\b(cat|head|tail|less)\b.*["\$]\{?(cert|b)\}?', val_body):
        errs.append(f"{push_path}: validate_ca_cert must not dump certificate contents")

    # Documentation checks
    doc_checks = [
        ("docs/vagrant/operations.md", root / "docs/vagrant/operations.md"),
        ("docs/vagrant/dns-access.md", root / "docs/vagrant/dns-access.md"),
        ("docs/vagrant/disaster-recovery.md", root / "docs/vagrant/disaster-recovery.md"),
        ("scripts/airgap/README.md", root / "scripts/airgap/README.md"),
    ]

    for name, p in doc_checks:
        text = p.read_text()
        if re.search(r"narwhal-ca-cert\s+-n\s+cert-manager", text):
            errs.append(f"{name}: references non-existent namespace cert-manager for narwhal-ca-cert")
        if re.search(r"narwhal-wildcard-tls\s+-n\s+cert-manager", text):
            errs.append(f"{name}: references non-existent namespace cert-manager for narwhal-wildcard-tls")
        if name != "scripts/airgap/README.md" and "kubectl get secret narwhal-ca-cert -n devtools" not in text:
            errs.append(f"{name}: missing canonical kubectl get secret narwhal-ca-cert -n devtools")

    airgap_readme = (root / "scripts/airgap/README.md").read_text()
    if "kubectl get secret narwhal-ca-cert -n devtools" not in airgap_readme:
        errs.append("scripts/airgap/README.md: missing export command with devtools namespace")

    return errs


def run_behavioral(push_script_path: Path) -> list[str]:
    errs = []
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        empty_f = tdp / "empty.crt"
        empty_f.touch()

        garbage_content = "NOT-A-CERTIFICATE-DUMMY-DATA-SECRET12345"
        garbage_f = tdp / "garbage.crt"
        garbage_f.write_text(garbage_content + "\n")

        valid_k = tdp / "valid.key"
        valid_f = tdp / "valid.crt"
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", str(valid_k), "-out", str(valid_f), "-days", "10",
             "-subj", "/CN=valid-test"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

        now = datetime.datetime.now(datetime.timezone.utc)
        fmt = "%Y%m%d%H%M%SZ"
        exp_nb = (now - datetime.timedelta(days=365)).strftime(fmt)
        exp_na = (now - datetime.timedelta(days=30)).strftime(fmt)
        fut_nb = (now + datetime.timedelta(days=30)).strftime(fmt)
        fut_na = (now + datetime.timedelta(days=365)).strftime(fmt)

        expired_k = tdp / "expired.key"
        expired_f = tdp / "expired.crt"
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", str(expired_k), "-out", str(expired_f),
             "-not_before", exp_nb, "-not_after", exp_na,
             "-subj", "/CN=expired-test"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

        future_k = tdp / "future.key"
        future_f = tdp / "future.crt"
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", str(future_k), "-out", str(future_f),
             "-not_before", fut_nb, "-not_after", fut_na,
             "-subj", "/CN=future-test"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

        missing_f = tdp / "nonexistent.crt"

        bundle_exp_valid_f = tdp / "bundle-exp-valid.crt"
        bundle_exp_valid_f.write_text(
            expired_f.read_text() + "\n# Intermediates\n" + valid_f.read_text()
        )

        bundle_valid_exp_f = tdp / "bundle-valid-exp.crt"
        bundle_valid_exp_f.write_text(
            valid_f.read_text() + "\n# Sibling expired\n" + expired_f.read_text()
        )

        bundle_valid_garbage_f = tdp / "bundle-valid-garbage.crt"
        bundle_valid_garbage_f.write_text(
            valid_f.read_text() + "\n-----BEGIN CERTIFICATE-----\nTHIS_IS_CORRUPTED_GARBAGE\n-----END CERTIFICATE-----\n"
        )

        crlf_valid_f = tdp / "crlf-valid.crt"
        crlf_valid_f.write_bytes(
            valid_f.read_text().replace("\n", "\r\n").encode("utf-8")
        )

        missing_bundle = tdp / "missing-bundle"
        hermetic_cwd = tdp / "empty-cwd"
        hermetic_cwd.mkdir()

        # D-R372-HERMETIC-BUNDLE: push-security-db.sh sources 00-config.sh, which resolves
        # AIRGAP_BUNDLE_DIR to $(pwd)/narwhal-airgap-bundle-${AIRGAP_ARCH##*/} when unset.
        # Pin AIRGAP_BUNDLE_DIR to a nonexistent temp path, strip host AIRGAP_*/Harbor env,
        # and run from an empty cwd so local developer bundles cannot hijack behavioral tests.
        # Cost: tests do not probe default pwd bundle discovery; escape hatch: none needed.
        def hermetic_env(extra: dict[str, str] | None = None) -> dict[str, str]:
            env = {
                k: v for k, v in os.environ.items()
                if not k.startswith("AIRGAP_") and k not in ("HARBOR_ADMIN_PASSWORD", "DOMAIN")
            }
            env["AIRGAP_BUNDLE_DIR"] = str(missing_bundle)
            if extra:
                env.update(extra)
            return env

        def exec_push(ca_arg: str, cwd: Path = hermetic_cwd):
            cmd = ["bash", str(push_script_path), "--ca-cert", ca_arg]
            res = subprocess.run(cmd, env=hermetic_env(), cwd=str(cwd), capture_output=True, text=True)
            return res.returncode, res.stdout, res.stderr

        # 1. Missing file -> reject
        rc, out, err = exec_push(str(missing_f))
        if rc == 0 or "not found" not in err:
            errs.append(f"missing file: expected nonzero exit with 'not found', got rc={rc}, err={err!r}")

        # 2. Empty file -> reject
        rc, out, err = exec_push(str(empty_f))
        if rc == 0 or "is empty" not in err:
            errs.append(f"empty file: expected nonzero exit with 'is empty', got rc={rc}, err={err!r}")

        # 3. Garbage file -> reject
        rc, out, err = exec_push(str(garbage_f))
        if rc == 0 or "is not a valid PEM certificate" not in err:
            errs.append(f"garbage file: expected nonzero exit with 'not a valid PEM certificate', got rc={rc}, err={err!r}")
        if garbage_content in out or garbage_content in err:
            errs.append("garbage file: certificate file contents leaked into output")

        # 4. Single expired cert -> reject
        rc, out, err = exec_push(str(expired_f))
        if rc == 0 or ("expired" not in err and "no valid certificate" not in err):
            errs.append(f"single expired cert: expected rejection, got rc={rc}, err={err!r}")

        # 5. Single not-yet-valid cert -> reject
        rc, out, err = exec_push(str(future_f))
        if rc == 0 or ("not yet valid" not in err and "no valid certificate" not in err):
            errs.append(f"single not-yet-valid cert: expected rejection, got rc={rc}, err={err!r}")

        # 6. Single valid cert -> accept
        rc, out, err = exec_push(str(valid_f))
        if "ERROR: --ca-cert" in err:
            errs.append(f"single valid cert: rejected by --ca-cert check: {err!r}")
        if "manifest.json not found" not in err:
            errs.append(f"single valid cert: expected progression to manifest check, got: {err!r}")
        if "WARN:" in err:
            errs.append(f"single valid cert: unexpected WARN line: {err!r}")

        # 7. Bundle expired+valid -> accept with WARN
        rc, out, err = exec_push(str(bundle_exp_valid_f))
        if "ERROR: --ca-cert" in err:
            errs.append(f"bundle expired+valid: rejected by --ca-cert check: {err!r}")
        if "manifest.json not found" not in err:
            errs.append(f"bundle expired+valid: expected progression to manifest check, got: {err!r}")
        if "WARN:" not in err or (" 1 " not in err and "1 expired" not in err):
            errs.append(f"bundle expired+valid: expected WARN line naming count 1, got: {err!r}")

        # 8. Bundle valid+expired -> accept with WARN
        rc, out, err = exec_push(str(bundle_valid_exp_f))
        if "ERROR: --ca-cert" in err:
            errs.append(f"bundle valid+expired: rejected by --ca-cert check: {err!r}")
        if "manifest.json not found" not in err:
            errs.append(f"bundle valid+expired: expected progression to manifest check, got: {err!r}")
        if "WARN:" not in err or (" 1 " not in err and "1 expired" not in err):
            errs.append(f"bundle valid+expired: expected WARN line naming count 1, got: {err!r}")

        # 9. Bundle valid+garbage block -> reject
        rc, out, err = exec_push(str(bundle_valid_garbage_f))
        if rc == 0 or "is not a valid PEM certificate" not in err:
            errs.append(f"bundle valid+garbage block: expected nonzero exit with 'not a valid PEM certificate', got rc={rc}, err={err!r}")

        # 10. CRLF valid -> accept
        rc, out, err = exec_push(str(crlf_valid_f))
        if "ERROR: --ca-cert" in err:
            errs.append(f"CRLF valid: rejected by --ca-cert check: {err!r}")
        if "manifest.json not found" not in err:
            errs.append(f"CRLF valid: expected progression to manifest check, got: {err!r}")
        if "WARN:" in err:
            errs.append(f"CRLF valid: unexpected WARN line: {err!r}")

        # 11. Cleanup on exit: valid CA + missing bundle/manifest -> fails after validation, temp dir must be empty
        clean_test_tmp = tdp / "clean-test-tmp"
        clean_test_tmp.mkdir()
        env_clean = hermetic_env({"TMPDIR": str(clean_test_tmp)})
        cmd_clean = ["bash", str(push_script_path), "--ca-cert", str(valid_f), "--bundle", str(missing_bundle)]
        res_clean = subprocess.run(cmd_clean, env=env_clean, cwd=str(hermetic_cwd), capture_output=True, text=True)
        if res_clean.returncode == 0:
            errs.append("cleanup test: expected nonzero exit due to missing bundle, got rc=0")
        if "manifest.json not found" not in res_clean.stderr:
            errs.append(f"cleanup test: expected manifest failure message, got: {res_clean.stderr!r}")
        leftover = list(clean_test_tmp.iterdir())
        if leftover:
            errs.append(f"cleanup test: temporary files remained after script exit in TMPDIR: {[p.name for p in leftover]}")

        # 12. Decoy bundle isolation guard: local narwhal-airgap-bundle-* in cwd must not leak
        decoy_cwd = tdp / "decoy-cwd"
        for arch in ("arm64", "amd64"):
            promoted = decoy_cwd / f"narwhal-airgap-bundle-{arch}" / "security-db" / "promoted"
            promoted.mkdir(parents=True)
            (promoted / "manifest.json").write_text("{}")
        rc, out, err = exec_push(str(valid_f), cwd=decoy_cwd)
        if "ERROR: --ca-cert" in err:
            errs.append(f"decoy bundle cwd: rejected by --ca-cert check: {err!r}")
        if "manifest.json not found" not in err or str(missing_bundle) not in err:
            errs.append(f"decoy bundle cwd: host/cwd bundle leaked into push-security-db.sh, got: {err!r}")

    return errs


def mutation_verify(root: Path) -> int:
    files = [
        "scripts/airgap/00-config.sh",
        "scripts/airgap/lib/push-security-db.sh",
        "docs/vagrant/operations.md",
        "docs/vagrant/dns-access.md",
        "docs/vagrant/disaster-recovery.md",
        "scripts/airgap/README.md",
    ]
    cases = [
        ("allow expired cert", "scripts/airgap/lib/push-security-db.sh",
         '-checkend 0', '-checkend 864000000000'),
        ("allow not-yet-valid cert", "scripts/airgap/lib/push-security-db.sh",
         'sys.exit(0 if t <= datetime.datetime.now(datetime.timezone.utc) else 1)',
         'sys.exit(0)'),
        ("allow empty cert", "scripts/airgap/lib/push-security-db.sh",
         '[[ ! -s "${cert}" ]]', 'false && [[ ! -s "${cert}" ]]'),
        ("skip pem validation", "scripts/airgap/lib/push-security-db.sh",
         'openssl x509 -in "${b}" -noout', 'true'),
        ("revert to first-cert-only validation", "scripts/airgap/lib/push-security-db.sh",
         'for b in "${cert_blocks[@]}"; do', 'for b in "${cert_blocks[0]}"; do'),
        ("leak cert content", "scripts/airgap/lib/push-security-db.sh",
         'echo "ERROR: --ca-cert ${cert} is not a valid PEM certificate" >&2',
         'cat "${cert}" >&2; echo "ERROR: --ca-cert ${cert} is not a valid PEM certificate" >&2'),
        ("reintroduce early trap - EXIT", "scripts/airgap/lib/push-security-db.sh",
         'validate_ca_cert "${SECDB_CA_CERT}"',
         'validate_ca_cert "${SECDB_CA_CERT}"\n  trap - EXIT'),
        ("reintroduce cert-manager in operations.md", "docs/vagrant/operations.md",
         'kubectl get secret narwhal-ca-cert -n devtools',
         'kubectl get secret narwhal-ca-cert -n cert-manager'),
        ("reintroduce cert-manager in dns-access.md", "docs/vagrant/dns-access.md",
         'kubectl get secret narwhal-ca-cert -n devtools',
         'kubectl get secret narwhal-ca-cert -n cert-manager'),
        ("reintroduce cert-manager in disaster-recovery.md", "docs/vagrant/disaster-recovery.md",
         'kubectl get secret narwhal-ca-cert -n devtools',
         'kubectl get secret narwhal-ca-cert -n cert-manager'),
        ("drop devtools in airgap README.md", "scripts/airgap/README.md",
         'kubectl get secret narwhal-ca-cert -n devtools',
         'kubectl get secret narwhal-ca-cert -n cert-manager'),
    ]

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        for rel in files:
            (base / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(root / rel, base / rel)

        clean_errs = check_static(base) + run_behavioral(base / "scripts/airgap/lib/push-security-db.sh")
        if clean_errs:
            print("clean control failed:", clean_errs, file=sys.stderr)
            return 1

        # Prove behavioral isolation even when caller exports a decoy AIRGAP_BUNDLE_DIR / AIRGAP_* env
        decoy_env_bundle = base / "decoy-env-bundle"
        (decoy_env_bundle / "security-db" / "promoted").mkdir(parents=True)
        (decoy_env_bundle / "security-db" / "promoted" / "manifest.json").write_text("{}")
        decoy_vars = {
            "AIRGAP_BUNDLE_DIR": str(decoy_env_bundle),
            "AIRGAP_ARCH": "linux/amd64",
            "AIRGAP_SECURITY_DB_CA_CERT": str(base / "nonexistent-decoy.crt"),
            "HARBOR_ADMIN_PASSWORD": "decoy-password",
        }
        saved_env = {k: os.environ.get(k) for k in decoy_vars}
        try:
            os.environ.update(decoy_vars)
            decoy_errs = run_behavioral(base / "scripts/airgap/lib/push-security-db.sh")
        finally:
            for k, old_v in saved_env.items():
                if old_v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = old_v
        if decoy_errs:
            print("decoy-env isolation failed:", decoy_errs, file=sys.stderr)
            return 1

        for name, rel, old, new in cases:
            case = Path(tmp) / "case"
            shutil.rmtree(case, ignore_errors=True)
            for r in files:
                (case / r).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(root / r, case / r)

            target_file = case / rel
            t = target_file.read_text()
            assert old in t, f"mutation anchor missing in {rel}: {old!r}"
            target_file.write_text(t.replace(old, new, 1))

            mut_errs = check_static(case) + run_behavioral(case / "scripts/airgap/lib/push-security-db.sh")
            if not mut_errs:
                print(f"mutation not caught: {name}", file=sys.stderr)
                return 1

    print(f"OK: clean control passes, {len(cases)} mutations caught")
    return 0


def main() -> int:
    root = ROOT
    if "--mutation-verify" in sys.argv:
        return mutation_verify(root)

    errs = check_static(root) + run_behavioral(root / "scripts/airgap/lib/push-security-db.sh")
    for e in errs:
        print("ERROR:", e, file=sys.stderr)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
