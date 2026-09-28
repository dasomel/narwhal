#!/usr/bin/env python3
"""Check Velero chart switches, generated RBAC safety, and legacy upgrade handling."""

import argparse
from pathlib import Path
import re
import subprocess
import tempfile

import yaml


FORBIDDEN_VERBS = {"impersonate", "escalate", "bind"}
READ_VERBS = {"get", "list", "watch"}
WRITE_VERBS = {"create", "update", "patch", "delete"}
OUTPUT = Path("gitops/resources/velero-server-rbac.yaml")
SNAPSHOT = Path("scripts/ops/velero-rbac-api-resources.snapshot.txt")
GENERATOR = Path("scripts/ops/gen-velero-rbac.py")


def check_values(path: Path, shell_values: bool) -> list[str]:
    text = path.read_text()
    if shell_values:
        marker = "cat > /tmp/velero-values.yaml << EOF\n"
        if marker not in text:
            return [f"{path}: Velero values heredoc missing"]
        values = text.split(marker, 1)[1].split("\nEOF", 1)[0]
        document = yaml.safe_load(values)
    else:
        document = yaml.safe_load(text)["spec"]["source"]["helm"]["valuesObject"]
    rbac = document.get("rbac", {}) if isinstance(document, dict) else {}
    return [f"{path}: {key} must be explicitly false" for key in
            ("create", "clusterAdministrator") if rbac.get(key) is not False]


def role_docs(path: Path) -> tuple[dict, dict]:
    docs = list(yaml.safe_load_all(path.read_text()))
    role = next(doc for doc in docs if doc.get("kind") == "ClusterRole")
    binding = next(doc for doc in docs if doc.get("kind") == "ClusterRoleBinding")
    return role, binding


def check_role(path: Path) -> list[str]:
    try:
        role, binding = role_docs(path)
    except (StopIteration, yaml.YAMLError) as exc:
        return [f"{path}: expected one ClusterRole and ClusterRoleBinding: {exc}"]
    problems = []
    if binding.get("metadata", {}).get("name") == "velero-server":
        problems.append(f"{path}: binding collides with chart legacy velero-server")
    if binding.get("roleRef", {}).get("name") != role.get("metadata", {}).get("name"):
        problems.append(f"{path}: binding roleRef does not target generated ClusterRole")
    resource_verbs = {}
    for rule in role.get("rules", []):
        groups, resources, verbs = rule.get("apiGroups", []), rule.get("resources", []), rule.get("verbs", [])
        if set(FORBIDDEN_VERBS) & set(verbs):
            problems.append(f"{path}: escalation verb present: {sorted(set(FORBIDDEN_VERBS) & set(verbs))}")
        if any("/" in resource for resource in resources):
            problems.append(f"{path}: subresource permission present: {resources}")
        if (any("*" in value for value in groups) or any("*" in verb for verb in verbs)) and groups != ["velero.io"]:
            problems.append(f"{path}: wildcard outside velero.io resources is forbidden")
        if "*" in resources and groups != ["velero.io"]:
            problems.append(f"{path}: wildcard resource outside velero.io")
        if groups == ["velero.io"] and not (resources == ["*"] and verbs == ["*"]):
            problems.append(f"{path}: velero.io must have full access")
        for resource in resources:
            resource_verbs.setdefault((groups[0] if len(groups) == 1 else tuple(groups), resource), set()).update(verbs)
    for group, name in (("rbac.authorization.k8s.io", "clusterroles"),
                        ("rbac.authorization.k8s.io", "clusterrolebindings"),
                        ("", "nodes")):
        verbs = resource_verbs.get((group, name), set())
        if "create" in verbs:
            problems.append(f"{path}: create on {group}/{name} is forbidden")
    for name in ("clusterroles", "clusterrolebindings"):
        if WRITE_VERBS & resource_verbs.get(("rbac.authorization.k8s.io", name), set()):
            problems.append(f"{path}: RBAC resource {name} must remain read-only")
    for name in ("roles", "rolebindings"):
        if not WRITE_VERBS <= resource_verbs.get(("rbac.authorization.k8s.io", name), set()):
            problems.append(f"{path}: namespaced RBAC resource {name} must be writable for restore")
    if not READ_VERBS <= resource_verbs.get(("", "secrets"), set()):
        problems.append(f"{path}: secrets must remain readable")
    if "list" not in resource_verbs.get(("rbac.authorization.k8s.io", "clusterrolebindings"), set()):
        problems.append(f"{path}: clusterrolebindings must be listable for backups")
    crd_write_verbs = set().union(*(set(rule.get("verbs", [])) & WRITE_VERBS for rule in role.get("rules", [])
                                     if rule.get("apiGroups") == ["apiextensions.k8s.io"] and
                                     "customresourcedefinitions" in rule.get("resources", [])))
    if crd_write_verbs - {"create"}:
        problems.append(f"{path}: CRDs must be create-only")
    return problems


def readable_resources(role_path: Path) -> set[str]:
    role, _ = role_docs(role_path)
    return {f"{resource}.{group}" if group else resource
            for rule in role.get("rules", [])
            if READ_VERBS <= set(rule.get("verbs", []))
            for group in rule.get("apiGroups", [])
            for resource in rule.get("resources", [])}


def schedules(document: dict) -> list[tuple[str, set[str]]]:
    configured = document.get("schedules", {})
    return [(name, set(value.get("template", {}).get("includedResources", [])))
            for name, value in configured.items()]


def check_schedule_resources(role_path: Path, gitops_path: Path, script_path: Path) -> list[str]:
    problems = []
    readable = readable_resources(role_path)
    gitops_doc = yaml.safe_load(gitops_path.read_text())["spec"]["source"]["helm"]["valuesObject"]
    marker = "cat > /tmp/velero-values.yaml << EOF\n"
    script_text = script_path.read_text()
    if marker not in script_text:
        return [f"{script_path}: Velero values heredoc missing"]
    script_doc = yaml.safe_load(script_text.split(marker, 1)[1].split("\nEOF", 1)[0])
    for path, document in ((gitops_path, gitops_doc), (script_path, script_doc)):
        found = schedules(document)
        if not found:
            problems.append(f"{path}: no Velero schedules found")
        for name, included in found:
            if not included:
                problems.append(f"{path}: schedule {name} lacks includedResources")
            if included != readable:
                problems.append(f"{path}: schedule {name} includedResources differs from RBAC READ set "
                                f"(expected {len(readable)}, got {len(included)})")
    return problems


def check_upgrade(script_path: Path, chart_path: Path) -> list[str]:
    script, chart = script_path.read_text(), chart_path.read_text()
    problems = []
    if "LEGACY_VELERO_ROLE_REF" not in script or "== cluster-admin" not in script or \
            "kubectl delete clusterrolebinding velero-server" not in script:
        problems.append(f"{script_path}: legacy binding deletion must be guarded by cluster-admin roleRef")
    if "rbac.create=false makes the chart render no server ClusterRoleBinding" not in chart or \
            "Argo prune removes the legacy cluster-admin binding on upgrade" not in chart:
        problems.append(f"{chart_path}: chart prune upgrade behavior is undocumented")
    return problems


def mutation_verify(role_path: Path, script_path: Path, chart_path: Path,
                    gitops_path: Path, schedules_script: Path) -> list[str]:
    problems = []
    role, binding = role_docs(role_path)
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        mutations = [
            ("subresource", {"apiGroups": [""], "resources": ["pods/exec"], "verbs": ["get"]}),
            ("outside wildcard", {"apiGroups": [""], "resources": ["*"], "verbs": ["get"]}),
            ("escalation", {"apiGroups": [""], "resources": ["pods"], "verbs": ["escalate"]}),
            ("cluster create", {"apiGroups": ["rbac.authorization.k8s.io"], "resources": ["clusterroles"], "verbs": ["create"]}),
            ("node create", {"apiGroups": [""], "resources": ["nodes"], "verbs": ["create"]}),
            ("cluster rolebinding update", {"apiGroups": ["rbac.authorization.k8s.io"], "resources": ["clusterrolebindings"], "verbs": ["update"]}),
        ]
        for name, mutation in mutations:
            role["rules"].append(mutation)
            target = temp / "mutated.yaml"
            target.write_text(yaml.safe_dump_all([role, binding], sort_keys=False))
            if not check_role(target):
                problems.append(f"safety mutation escaped: {name}")
            role["rules"].pop()
        for name in ("roles", "rolebindings"):
            role["rules"] = [rule for rule in role["rules"] if not
                             ("rbac.authorization.k8s.io" in rule.get("apiGroups", []) and
                              name in rule.get("resources", []) and WRITE_VERBS <= set(rule.get("verbs", [])))]
            target = temp / f"mutated-{name}.yaml"
            target.write_text(yaml.safe_dump_all([role, binding], sort_keys=False))
            if not check_role(target):
                problems.append(f"namespaced {name} write-removal mutation escaped")
            generated_role, _ = role_docs(role_path)
            role["rules"] = generated_role["rules"]
        original = binding["metadata"]["name"]
        binding["metadata"]["name"] = "velero-server"
        target = temp / "mutated-binding.yaml"
        target.write_text(yaml.safe_dump_all([role, binding], sort_keys=False))
        if not check_role(target):
            problems.append("binding-name mutation escaped")
        binding["metadata"]["name"] = original
        original = script_path.read_text()
        mutated = temp / "mutated-script.sh"
        mutated.write_text(original.replace('== cluster-admin', '== some-role'))
        if not check_upgrade(mutated, chart_path):
            problems.append("legacy roleRef guard mutation escaped")
        original_chart = chart_path.read_text()
        mutated_chart = temp / "mutated-chart.yaml"
        mutated_chart.write_text(original_chart.replace("Argo prune removes the legacy cluster-admin binding on upgrade.", "prune disabled."))
        if not check_upgrade(script_path, mutated_chart):
            problems.append("chart prune documentation mutation escaped")
        generated = temp / "generated.yaml"
        subprocess.run(["python3", str(GENERATOR), str(SNAPSHOT), "-o", str(generated)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if generated.read_bytes() != role_path.read_bytes():
            problems.append("snapshot regeneration differs from committed RBAC")
        generated.write_text(generated.read_text() + "# drift\n")
        result = subprocess.run(["python3", str(GENERATOR), str(SNAPSHOT), "-o", str(generated), "--check"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode == 0:
            problems.append("snapshot drift mutation escaped")
        for original_path in (gitops_path, schedules_script):
            text = original_path.read_text()
            match = re.search(r"^(\s*)includedResources: &velero_included\n((?:\s+- .*\n)+)", text, re.M)
            if not match:
                problems.append(f"{original_path}: includedResources anchor mutation fixture missing")
                continue
            items = match.group(2)
            first_item = re.search(r"^\s+- .*\n", items, re.M)
            dropped = items[:first_item.start()] + items[first_item.end():]
            mutated_text = text[:match.start(2)] + dropped + text[match.end(2):]
            mutated_path = temp / (original_path.name + ".dropped")
            mutated_path.write_text(mutated_text)
            paths = (mutated_path, schedules_script) if original_path == gitops_path else (gitops_path, mutated_path)
            if not check_schedule_resources(role_path, *paths):
                problems.append(f"anchor entry removal mutation escaped: {original_path}")
            # Remove one alias-backed schedule field while keeping the shared anchor valid.
            alias = re.search(r"^(\s*)includedResources: \*velero_included\n", text, re.M)
            if not alias:
                problems.append(f"{original_path}: alias schedule mutation fixture missing")
                continue
            missing_text = text[:alias.start()] + text[alias.end():]
            missing_path = temp / (original_path.name + ".missing")
            missing_path.write_text(missing_text)
            paths = (missing_path, schedules_script) if original_path == gitops_path else (gitops_path, missing_path)
            if not check_schedule_resources(role_path, *paths):
                problems.append(f"missing includedResources mutation escaped: {original_path}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gitops-values", type=Path)
    parser.add_argument("--script-values", type=Path)
    parser.add_argument("--role", type=Path)
    parser.add_argument("--script", type=Path)
    parser.add_argument("--chart", type=Path)
    parser.add_argument("--schedules", nargs=2, type=Path, metavar=("GITOPS", "SCRIPT"))
    parser.add_argument("--mutation-verify", action="store_true")
    args = parser.parse_args()
    problems = []
    if args.gitops_values:
        problems.extend(check_values(args.gitops_values, False))
    if args.script_values:
        problems.extend(check_values(args.script_values, True))
    if args.role:
        problems.extend(check_role(args.role))
    if args.script and args.chart:
        problems.extend(check_upgrade(args.script, args.chart))
    if args.role and args.schedules:
        problems.extend(check_schedule_resources(args.role, *args.schedules))
    if args.mutation_verify:
        if not (args.role and args.script and args.chart):
            parser.error("--mutation-verify requires --role, --script, and --chart")
        if not args.schedules:
            parser.error("--mutation-verify requires --schedules GITOPS SCRIPT")
        problems.extend(mutation_verify(args.role, args.script, args.chart, *args.schedules))
    if problems:
        print("\n".join(problems))
        return 1
    print("PASS Velero chart, upgrade path, and generated RBAC")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
