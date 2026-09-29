#!/usr/bin/env python3
"""Check declared namespace PSA labels against the workload security profile."""

import argparse
import re
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
PROFILE = "docs/common/workload-security-profile.md"
SECURITY_SCRIPT = "scripts/cluster/08-3-security.sh"

# D1: profile goals remain warnings while the table is PROPOSED; changing this to a
# failure requires making the profile normative. Cost: no admission guarantee yet.
# Escape hatch: update the profile status and this check together when enforcement lands.
# D2: existing undeclared namespaces are explicit ratchet debt, never silent exemptions.
# Cost: each known gap needs an owner-facing reason and cleanup when declarations land.
# Escape hatch: remove the entry once the namespace has a complete, consistent declaration.
KNOWN_GAPS = {
    "database": "restricted profile is proposed; enforce is intentionally unset and audit/warn remain baseline",
    "dev": "restricted profile is proposed; enforce is intentionally unset and audit/warn remain baseline",
    "iam": "restricted profile is proposed; enforce is intentionally unset and audit/warn remain baseline",
    "devtools": "platform profile is proposed; enforce is intentionally unset",
    "monitoring": "platform profile is proposed; enforce is intentionally unset",
    "cilium-secrets": "node-agent profile is proposed; enforce is intentionally unset and audit/warn remain baseline",
    "kube-system": "node-agent profile is proposed; enforce is intentionally unset",
    "nfs-quota-agent": "host-storage-agent profile is proposed; enforce is intentionally unset",
}


def namespaces(root):
    found = set()
    problems = []
    files = sorted((root / "gitops").rglob("*.yaml"))
    for path in files:
        source = path.read_text()
        mentions_namespace = re.search(r"(?m)^\s*kind:\s*Namespace\b", source) is not None
        try:
            documents = list(yaml.safe_load_all(source))
            for doc in documents:
                if isinstance(doc, dict) and doc.get("kind") == "Namespace":
                    name = (doc.get("metadata") or {}).get("name")
                    if name:
                        found.add(name)
        except yaml.YAMLError as exc:
            if mentions_namespace:
                # Helm templates are not valid YAML until rendered; only those get
                # text-level inventory fallback. Ordinary malformed Namespace YAML fails.
                name_match = re.search(r"(?m)^\s*name:\s*([a-z0-9][a-z0-9.-]*)\s*(?:#.*)?$", source)
                if "{{" in source and name_match:
                    found.add(name_match.group(1))
                else:
                    problems.append(f"{path.relative_to(root)}: Namespace YAML failed to parse ({exc})")
            continue
        if mentions_namespace and not any(
            isinstance(doc, dict) and doc.get("kind") == "Namespace"
            for doc in documents
        ):
            # Text-level fallback handles Go-template directives even when YAML parsing
            # succeeds but the templated kind/name is not represented as a normal mapping.
            name_match = re.search(r"(?m)^\s*name:\s*([a-z0-9][a-z0-9.-]*)\s*(?:#.*)?$", source)
            if name_match:
                found.add(name_match.group(1))
            else:
                problems.append(f"{path.relative_to(root)}: Namespace marker has no statically inventoryable metadata.name")
    for path in (root / "scripts").rglob("*.sh"):
        source = path.read_text()
        for match in re.finditer(r"kubectl\s+create\s+namespace\s+([\w.-]+)", source):
            found.add(match.group(1))
        for match in re.finditer(r"ensure_namespace\s+([\w.-]+)", source):
            found.add(match.group(1))
    # Include namespaces configured by the PSA label loops, including system namespaces
    # created by Kubernetes or an operator rather than by a repo manifest.
    source = (root / SECURITY_SCRIPT).read_text()
    for match in re.finditer(r"for ns in ([^;]+); do", source):
        found.update(match.group(1).split())
    return found, len(files), problems


def declared_labels(root):
    source = (root / SECURITY_SCRIPT).read_text()
    labels = {}
    for loop in re.finditer(r"for ns in ([^;]+); do(.*?)done", source, re.S):
        names = loop.group(1).split()
        values = dict(re.findall(
            r"pod-security\.kubernetes\.io/(enforce|audit|warn)=([a-z]+)", loop.group(2)
        ))
        for name in names:
            labels[name] = values
    return labels


def profile_targets(doc):
    targets = {}
    for line in doc.splitlines():
        if not line.startswith("| `"):
            continue
        columns = [column.strip() for column in line.strip("|").split("|")]
        match = re.fullmatch(r"`([^`]+)`", columns[0])
        if not match or len(columns) < 4:
            continue
        names = set(re.findall(r"`([a-z][a-z0-9-]*)`", columns[1]))
        level = re.search(r"(?:enforce=|`)(restricted|baseline|privileged)", columns[2])
        if level:
            targets[match.group(1)] = (names, level.group(1))
    return targets


def current_claims(doc):
    claims = {}
    for line in doc.splitlines():
        match = re.search(
            r"((?:`[a-z][a-z0-9-]*`,?\s*)+)(?:에|에는) `audit=(\w+)` 및 `warn=(\w+)`[을를] 부여한다",
            line,
        )
        if match:
            names = re.findall(r"`([a-z][a-z0-9-]*)`", match.group(1))
            for name in names:
                claims[name] = {"audit": match.group(2), "warn": match.group(3)}
    return claims


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--mutation-verify", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    doc = (root / PROFILE).read_text()
    table_start = doc.find("| Profile ID")
    proposed = "**PROPOSED**" in doc[:table_start] if table_start >= 0 else False

    inventory, files_scanned, inventory_problems = namespaces(root)
    labels = declared_labels(root)
    problems = list(inventory_problems)
    print(f"Inventory: {files_scanned} YAML files scanned; {len(inventory)} namespaces found")
    if not inventory:
        problems.append("namespace inventory is empty")
    warnings = []
    for name in sorted(inventory):
        declared = labels.get(name)
        if not declared:
            if name in KNOWN_GAPS:
                warnings.append(f"{name}: known PSA gap: {KNOWN_GAPS[name]}")
            else:
                problems.append(f"{name}: no PSA labels declared and not listed in KNOWN_GAPS")
            continue
        for level, value in declared.items():
            if level not in {"enforce", "audit", "warn"} or value not in {"privileged", "baseline", "restricted"}:
                problems.append(f"{name}: invalid PSA {level}={value}")
        missing_current = [level for level in ("audit", "warn") if level not in declared]
        if missing_current:
            message = f"{name}: missing current PSA declaration(s): {', '.join(missing_current)}"
            (warnings if name in KNOWN_GAPS else problems).append(message)
        if "audit" in declared and "warn" in declared and declared["audit"] != declared["warn"]:
            message = f"{name}: contradictory audit/warn PSA declarations"
            (warnings if name in KNOWN_GAPS else problems).append(message)

    targets = profile_targets(doc)
    if not targets:
        print("profile class table could not be parsed", file=sys.stderr)
        return 1
    for profile, (members, target) in targets.items():
        for name in sorted(members & inventory):
            declared = labels.get(name, {})
            for level in ("enforce", "audit", "warn"):
                if declared.get(level) != target:
                    message = (
                        f"{name}: proposed {profile} target {level}={target}; "
                        f"current declaration is {declared.get(level, 'missing')}"
                    )
                    (warnings if proposed else problems).append(message)

    # Proposed profile differences remain warnings. The ratchet tracks only known
    # inventory/declaration debt and fails when an allowlisted target fully converges.
    for name, reason in KNOWN_GAPS.items():
        if name not in inventory:
            continue
        profile_target = next((target for _, (members, target) in targets.items() if name in members), None)
        declared = labels.get(name, {})
        if profile_target and all(declared.get(level) == profile_target for level in ("enforce", "audit", "warn")):
            problems.append(f"{name}: KNOWN_GAPS entry is stale; remove it after profile convergence")
        elif not declared:
            warnings.append(f"{name}: known PSA gap: {reason}")

    for name, expected in current_claims(doc).items():
        if name not in inventory:
            continue
        for level, value in expected.items():
            if labels.get(name, {}).get(level) != value:
                problems.append(f"{name}: document current-state claim requires {level}={value}")

    for warning in warnings:
        print(f"WARN: {warning}")
    for problem in problems:
        print(f"FAIL: {problem}", file=sys.stderr)
    if problems:
        return 1
    if args.mutation_verify:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp)
            (fixture / PROFILE).parent.mkdir(parents=True)
            shutil.copyfile(root / PROFILE, fixture / PROFILE)
            (fixture / SECURITY_SCRIPT).parent.mkdir(parents=True)
            mutated = (root / SECURITY_SCRIPT).read_text().replace("audit=baseline", "audit=privileged", 1)
            (fixture / SECURITY_SCRIPT).write_text(mutated)
            (fixture / "gitops/resources").mkdir(parents=True)
            (fixture / "gitops/resources/namespace.yaml").write_text(
                "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: iam\n"
            )
            if run(fixture) == 0:
                print("mutation check failed to detect changed current PSA label", file=sys.stderr)
                return 1
        print("PASS: mutation detected")
    print(f"PASS: {len(inventory)} namespaces checked; {len(warnings)} proposed/current gaps warned")
    return 0


def run(root):
    original = sys.argv[:]
    try:
        sys.argv = [original[0], "--root", str(root)]
        return main()
    finally:
        sys.argv = original


if __name__ == "__main__":
    sys.exit(main())
