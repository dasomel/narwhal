"""Keep current etcd-operation claims in sync with repository configuration."""

import argparse
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DOC = "docs/common/etcd-operations.md"

# Rule tuples: source path, required source text, doc anchor, claim text on that line.
RULES = {
    "master-default": ("scripts/cluster/02-init-cluster.sh", 'MASTER_COUNT="${MASTER_COUNT:-3}"', "| 토폴로지 |", "MASTER_COUNT` 기본값은 3"),
    "vagrant-count": ("Vagrantfile", "MASTER_COUNT = 3", "| 토폴로지 |", "MASTER_COUNT` 기본값은 3"),
    "control-plane-join": ("scripts/cluster/02-join-control-plane.sh", "kubeadm join --control-plane", "| 토폴로지 |", "`kubeadm join --control-plane`"),
    "tls-verification": ("scripts/test/verify-cluster.sh", "--endpoints=https://127.0.0.1:2379", "| TLS/접근 |", "`https://127.0.0.1:2379`"),
    "tls-cert-path": ("scripts/verify/etcd-encryption-check.sh", "--cacert=/etc/kubernetes/pki/etcd/ca.crt", "| TLS/접근 |", "`/etc/kubernetes/pki/etcd/ca.crt`"),
    "tls-server-cert": ("scripts/verify/etcd-encryption-check.sh", "--cert=/etc/kubernetes/pki/etcd/server.crt", "| TLS/접근 |", "`server.crt`"),
    "tls-server-key": ("scripts/verify/etcd-encryption-check.sh", "--key=/etc/kubernetes/pki/etcd/server.key", "| TLS/접근 |", "`server.key`"),
    "secret-encryption-init": ("scripts/cluster/02-init-cluster.sh", "EncryptionConfiguration", "| 암호화와 키 |", "API 서버 Secret 암호화를 설정"),
    "secret-encryption-join": ("scripts/cluster/02-join-control-plane.sh", "install -o root -g root -m 600 /tmp/encryption-config.yaml /etc/kubernetes/enc/encryption-config.yaml", "| 암호화와 키 |", "같은 encryption config를 새 master에 복사"),
    "etcd-monitor-disabled": ("gitops/charts/narwhal-apps/templates/prometheus-stack.yaml", "kubeEtcd:\n          enabled: false", "| 모니터링 |", "`kubeEtcd.enabled: false`"),
}
BACKUP_ANCHOR = "| 정비/백업 |"
BACKUP_CLAIM = "control-plane etcd의 자동 compaction/defrag/member replace 또는 snapshot 주기 설정이 없다"
QUORUM_ANCHOR = "| 토폴로지 |"
QUORUM_CLAIM = "quorum=2/3"
BACKUP_PATTERNS = (
    re.compile(r"\betcd(?:ctl|utl)\b.*\bsnapshot\s+(?:save|restore)\b", re.IGNORECASE),
    re.compile(r"\bcp\b.*?/var/lib/etcd", re.IGNORECASE),
    re.compile(r"\btar\b.*?/var/lib/etcd", re.IGNORECASE),
)


def contains_backup_operation(script):
    return any(pattern.search(line) for pattern in BACKUP_PATTERNS for line in script.splitlines())


def evaluate(root):
    failures = []
    assertions = 0
    scanned_files = 0
    doc_path = root / DOC
    doc = doc_path.read_text() if doc_path.is_file() else None
    for rule, (relative, expected, anchor, claim) in RULES.items():
        assertions += 2
        if doc is None:
            failures.append(f"{rule}: unresolved guarded document {DOC}")
        elif not any(anchor in line and claim in line for line in doc.splitlines()):
            failures.append(f"{rule}: cited document line missing anchor/claim ({anchor}: {claim})")
        source = root / relative
        scanned_files += 1
        if not source.is_file():
            failures.append(f"{rule}: unresolved source {relative}")
        elif expected not in source.read_text():
            failures.append(f"{rule}: expected configuration missing in {relative}")

    assertions += 1
    if doc is None or not any(BACKUP_ANCHOR in line and BACKUP_CLAIM in line for line in doc.splitlines()):
        failures.append(f"backup-scan: cited document claim missing ({BACKUP_ANCHOR}: {BACKUP_CLAIM})")
    scan_dirs = (root / "scripts/cluster", root / "scripts/ops")
    if not all(path.is_dir() for path in scan_dirs):
        failures.append("backup-scan: unresolved scripts/cluster or scripts/ops")
    else:
        scripts = [path for directory in scan_dirs for path in directory.rglob("*.sh")]
        scanned_files += len(scripts)
        if not scripts:
            failures.append("backup-scan: no shell scripts evaluated")
        elif any(contains_backup_operation(path.read_text()) for path in scripts):
            failures.append("backup-scan: etcd snapshot or /var/lib/etcd copy/archive command found")

    assertions += 1
    if doc is None or not any(QUORUM_ANCHOR in line and "quorum 2/3" in line for line in doc.splitlines()):
        failures.append(f"quorum-doc: cited claim missing ({QUORUM_ANCHOR}: {QUORUM_CLAIM})")
    architecture = root / "docs/common/architecture.md"
    scanned_files += 1
    if not architecture.is_file():
        failures.append("quorum-doc: unresolved docs/common/architecture.md")
    elif "etcd 3-node: quorum=2/3" not in architecture.read_text():
        failures.append("quorum-doc: three-member quorum statement missing in architecture.md")
    return assertions, scanned_files, failures


def mutation_verify(root):
    # Stage every file read by evaluate, including recursive backup scan inputs.
    with tempfile.TemporaryDirectory() as directory:
        mutant = Path(directory)
        candidates = {DOC, "docs/common/architecture.md"}
        candidates.update(relative for relative, *_ in RULES.values())
        for base in (root / "scripts/cluster", root / "scripts/ops"):
            if base.is_dir():
                candidates.update(str(path.relative_to(root)) for path in base.rglob("*.sh"))
        for relative in candidates:
            source = root / relative
            if source.is_file():
                target = mutant / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())

        mutations = []
        for rule, (relative, expected, *_rest) in RULES.items():
            mutations.append((rule, relative, expected, "R230_MUTATED"))
        mutations.extend([
            ("backup-scan", "scripts/ops/r230-mutant.sh", "", "etcdctl snapshot save"),
            ("backup-scan", "scripts/ops/r230-mutant.sh", "", "etcdutl snapshot save"),
            ("backup-scan", "scripts/ops/r230-mutant.sh", "", "ETCDCTL_API=3 etcdctl snapshot restore"),
            ("quorum-doc", "docs/common/architecture.md", "etcd 3-node: quorum=2/3", "R230_MUTATED"),
        ])
        for rule, relative, expected, replacement in mutations:
            target = mutant / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            original = target.read_text() if target.is_file() else ""
            if expected and expected not in original:
                raise AssertionError(f"mutation setup missing expected text for {rule}")
            target.write_text(original.replace(expected, replacement) if expected else replacement)
            _, _, failures = evaluate(mutant)
            if not any(failure.startswith(f"{rule}:") for failure in failures):
                raise AssertionError(f"mutation was not detected for {rule}")
            if relative == "scripts/ops/r230-mutant.sh":
                target.unlink()
            else:
                target.write_text(original)

        (mutant / DOC).unlink()
        _, _, failures = evaluate(mutant)
        if not any("guarded document" in failure for failure in failures):
            raise AssertionError("deleting the guarded document was not detected")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mutation-verify", action="store_true")
    args = parser.parse_args()
    if args.mutation_verify:
        mutation_verify(ROOT)
        print(f"Mutation verification: {len(RULES) + 5} mutations rejected (source rules, backup scan cases, quorum doc, guarded doc deletion)")
    assertions, scanned_files, failures = evaluate(ROOT)
    print(f"Evaluated assertions: {assertions}")
    print(f"Scanned files: {scanned_files}")
    if failures:
        raise SystemExit("\n".join(failures))
    print("PASS: etcd current-state claims match repository sources")


if __name__ == "__main__":
    main()
