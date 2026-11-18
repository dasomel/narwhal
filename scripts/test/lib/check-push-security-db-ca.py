#!/usr/bin/env python3
"""R371: push-security-db.sh validates --ca-cert and docs name the canonical namespace (#728).

Invariants:
  1. push-security-db.sh validates --ca-cert early before any expensive operations.
  2. Missing, unreadable, empty, non-PEM, and expired certificates are rejected with a clear
     message and nonzero exit code.
  3. Certificate file contents are never dumped to stdout or stderr.
  4. Documentation across operations.md, dns-access.md, disaster-recovery.md, and airgap README.md
     references the canonical devtools namespace for narwhal-ca-cert and platform-system for
     cert-manager/narwhal-wildcard-tls (never the non-existent cert-manager namespace).
  5. Behavioral execution tests pass without requiring a registry or cluster.

--mutation-verify proves each invariant fails when mutated.
"""
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

    if 'openssl x509 -in "${cert}" -noout' not in push_text:
        errs.append(f"{push_path}: missing openssl x509 PEM validation")

    if "-checkend 0" not in push_text:
        errs.append(f"{push_path}: missing openssl x509 expiration check (-checkend 0)")

    if '[[ ! -s "${cert}" ]]' not in push_text:
        errs.append(f"{push_path}: missing empty file check (-s)")

    if '[[ ! -f "${cert}" ]]' not in push_text:
        errs.append(f"{push_path}: missing file existence check (-f)")

    if '[[ ! -r "${cert}" ]]' not in push_text:
        errs.append(f"{push_path}: missing file readability check (-r)")

    # Do not dump file contents
    val_body = push_text[push_text.find("validate_ca_cert()"):push_text.find("if [[ -n \"${SECDB_CA_CERT}\" ]]; then")]
    if re.search(r'\b(cat|head|tail|less)\b.*["\$]\{?cert\}?', val_body):
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

        expired_k = tdp / "expired.key"
        expired_f = tdp / "expired.crt"
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", str(expired_k), "-out", str(expired_f),
             "-not_before", "20190101000000Z", "-not_after", "20200101000000Z",
             "-subj", "/CN=expired-test"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

        missing_f = tdp / "nonexistent.crt"

        def exec_push(ca_arg: str):
            cmd = ["bash", str(push_script_path), "--ca-cert", ca_arg]
            res = subprocess.run(cmd, capture_output=True, text=True)
            return res.returncode, res.stdout, res.stderr

        # 1. Missing file
        rc, out, err = exec_push(str(missing_f))
        if rc == 0 or "not found" not in err:
            errs.append(f"missing file: expected nonzero exit with 'not found', got rc={rc}, err={err!r}")

        # 2. Empty file
        rc, out, err = exec_push(str(empty_f))
        if rc == 0 or "is empty" not in err:
            errs.append(f"empty file: expected nonzero exit with 'is empty', got rc={rc}, err={err!r}")

        # 3. Garbage file
        rc, out, err = exec_push(str(garbage_f))
        if rc == 0 or "is not a valid PEM certificate" not in err:
            errs.append(f"garbage file: expected nonzero exit with 'not a valid PEM certificate', got rc={rc}, err={err!r}")
        if garbage_content in out or garbage_content in err:
            errs.append("garbage file: certificate file contents leaked into output")

        # 4. Expired cert
        rc, out, err = exec_push(str(expired_f))
        if rc == 0 or "has expired" not in err:
            errs.append(f"expired cert: expected nonzero exit with 'has expired', got rc={rc}, err={err!r}")

        # 5. Valid cert
        rc, out, err = exec_push(str(valid_f))
        # Valid cert must pass the CA validation. It will fail on manifest not found because
        # AIRGAP_BUNDLE_DIR promoted manifest does not exist in temp dir, which proves it passed
        # CA verification and proceeded to bundle inspection without needing a registry.
        if "ERROR: --ca-cert" in err:
            errs.append(f"valid cert: rejected by --ca-cert check: {err!r}")
        if "manifest.json not found" not in err:
            errs.append(f"valid cert: expected progression to manifest check, got: {err!r}")

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
        ("allow empty cert", "scripts/airgap/lib/push-security-db.sh",
         '[[ ! -s "${cert}" ]]', 'false && [[ ! -s "${cert}" ]]'),
        ("skip pem validation", "scripts/airgap/lib/push-security-db.sh",
         'openssl x509 -in "${cert}" -noout', 'true'),
        ("leak cert content", "scripts/airgap/lib/push-security-db.sh",
         'echo "ERROR: --ca-cert ${cert} is not a valid PEM certificate" >&2',
         'cat "${cert}" >&2; echo "ERROR: --ca-cert ${cert} is not a valid PEM certificate" >&2'),
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
