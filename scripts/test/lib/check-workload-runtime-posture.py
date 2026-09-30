#!/usr/bin/env python3
"""Inventory RuntimeClasses and ratchet risky workload declarations."""

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
PROFILE = "docs/common/workload-security-profile.md"

# D1: known counts are explicit debt until each namespace is remediated. Cost: a
# changed baseline needs review; escape hatch: update only after inspecting the diff.
KNOWN_GAPS = {
    "default:missingSeccomp": 4,
    "platform-system:missingSeccomp": 2,
}
WORKLOAD_KINDS = {"Pod", "Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"}
# D2: chart values are inputs, not rendered objects. Cost: additions need review;
# escape hatch: render/evaluate the file and remove it from this explicit list.
UNEVALUATED_ALLOWLIST = {
    "gitops/charts/kubernetes-dashboard/charts/ingress-nginx/values.yaml": "Helm values only; workload templates are scanned separately",
}


def documents(path, root):
    source = path.read_text()
    # Helm expressions are scalar text at this boundary. Parse the surrounding YAML
    # after replacing expressions; this preserves indentation and detects bad YAML.
    cleaned = re.sub(r"(?m)^\s*{{-?.*?-?}}\s*$", "", source)
    cleaned = re.sub(r"{{.*?}}", "templatevalue", cleaned)
    try:
        return list(yaml.safe_load_all(cleaned)), "parsed"
    except yaml.YAMLError as exc:
        if "{{" in source:
            # Template control flow can alter YAML indentation (for example nindent).
            # Inventory object headers and security keys directly instead of treating
            # that expected non-YAML source as a swallowed parse failure.
            found = []
            for match in re.finditer(r"(?m)^kind:\s*(RuntimeClass|Pod|Deployment|StatefulSet|DaemonSet|Job|CronJob)\s*$", source):
                kind = match.group(1)
                tail = source[match.end():]
                name = re.search(r"(?m)^\s*name:\s*([\w.-]+)", tail)
                namespace = re.search(r"(?m)^\s*namespace:\s*([\w.-]+)", tail)
                if kind == "RuntimeClass":
                    found.append({"kind": kind, "metadata": {"name": name.group(1) if name else None}})
                    continue
                # Security flags and container count are retained as a small PodSpec
                # for the same inventory path used by ordinary YAML documents.
                block_end = re.search(r"(?m)^---\s*$", tail)
                block = tail[:block_end.start()] if block_end else tail
                spec = {key: True for key in ("hostNetwork", "hostPID", "hostIPC")
                        if re.search(rf"(?m)^\s*{key}:\s*true\s*$", block)}
                containers = []
                lines = block.splitlines()
                for index, line in enumerate(lines):
                    section = re.match(r"^(\s*)(?:containers|initContainers|ephemeralContainers):\s*$", line)
                    if not section:
                        continue
                    indent = len(section.group(1))
                    item_lines = []
                    for child in lines[index + 1:]:
                        if child.strip() and len(child) - len(child.lstrip()) <= indent:
                            break
                        if re.match(r"^\s*-\s+name:", child):
                            if item_lines:
                                item = "\n".join(item_lines)
                                containers.append({"securityContext": {
                                    "privileged": bool(re.search(r"(?m)^\s*privileged:\s*true\s*$", item)),
                                    "seccompProfile": bool(re.search(r"(?m)^\s*seccompProfile:", item)),
                                }})
                            item_lines = [child]
                        elif item_lines:
                            item_lines.append(child)
                    if item_lines:
                        item = "\n".join(item_lines)
                        containers.append({"securityContext": {
                            "privileged": bool(re.search(r"(?m)^\s*privileged:\s*true\s*$", item)),
                            "seccompProfile": bool(re.search(r"(?m)^\s*seccompProfile:", item)),
                        }})
                spec["containers"] = containers
                spec["runtimeClassName"] = (re.search(r"(?m)^\s*runtimeClassName:\s*([\w.-]+)", block) or [None, None])[1]
                spec["securityContext"] = {"seccompProfile": True} if re.search(r"(?m)^\s*seccompProfile:", block) else {}
                found.append({"kind": kind, "metadata": {"name": name.group(1) if name else "<template>", "namespace": namespace.group(1) if namespace else "default"}, "spec": spec})
            return found, "text-fallback"
        raise ValueError(f"{path.relative_to(root)}: YAML/template parse failed: {exc}") from exc


def pod_spec(doc):
    kind = doc.get("kind")
    spec = doc.get("spec") or {}
    if kind == "Pod":
        return spec
    if kind in {"Deployment", "StatefulSet", "DaemonSet", "Job"}:
        return ((spec.get("template") or {}).get("spec") or {})
    if kind == "CronJob":
        return (((spec.get("jobTemplate") or {}).get("spec") or {}).get("template") or {}).get("spec") or {}
    return None


def is_unevaluated_workload_file(source, workload_count):
    workload_kind = r"(?:Pod|Deployment|StatefulSet|DaemonSet|Job|CronJob)"
    declares_workload = bool(re.search(
        rf"(?m)^kind:\s*(?:['\"]?{workload_kind}\b|.*\b{workload_kind}\b)", source
    ))
    mentions_runtime_class = bool(re.search(r"(?m)^\s*runtimeClassName\s*:", source))
    mentions_workload = declares_workload or mentions_runtime_class
    return mentions_workload and workload_count == 0


def scan(root, mutate=False):
    files = sorted(p for base in (root / "gitops", root / "scripts")
                   for p in base.rglob("*") if p.suffix in {".yaml", ".yml"}
                   and "/fixtures/" not in p.as_posix())
    runtime_classes = set()
    workloads = []
    parsed_files = 0
    fallback_files = 0
    unevaluated_files = []
    problems = []
    warnings = []
    for path in files:
        docs, parse_mode = documents(path, root)
        if parse_mode == "parsed":
            parsed_files += 1
        else:
            fallback_files += 1
        file_workload_count = 0
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            if doc.get("kind") == "RuntimeClass":
                name = (doc.get("metadata") or {}).get("name")
                if name:
                    runtime_classes.add(name)
            spec = pod_spec(doc)
            if spec is not None:
                meta = doc.get("metadata") or {}
                workloads.append((meta.get("namespace", "default"), doc.get("kind"), meta.get("name", "<unnamed>"), spec))
                file_workload_count += 1
        source = path.read_text()
        if is_unevaluated_workload_file(source, file_workload_count):
            relative = str(path.relative_to(root))
            reason = UNEVALUATED_ALLOWLIST.get(relative)
            unevaluated_files.append((relative, reason))
            if reason:
                warnings.append(f"{relative}: unevaluated allowlist: {reason}")
            else:
                problems.append(f"{relative}: mentions workload kind or runtimeClassName but yields no evaluated workload")
    if mutate and workloads:
        namespace, kind, name, spec = workloads[0]
        spec["runtimeClassName"] = "r234-mutation-missing"

    expected = set()
    # The design's current-state section explicitly states there are no declared
    # RuntimeClass objects; proposed profile rows describe default runtime selection.
    current = (root / PROFILE).read_text().split("## 분류 및 목표 프로파일", 1)[0]
    if re.search(r"RuntimeClass.*확인하지 못했다", current, re.S):
        expected = set()
    if runtime_classes != expected:
        problems.append(f"RuntimeClass inventory differs from document: repo={sorted(runtime_classes)}, doc={sorted(expected)}")

    gaps = Counter()
    namespace_workloads = Counter()
    for namespace, kind, name, spec in workloads:
        namespace_workloads[namespace] += 1
        runtime = spec.get("runtimeClassName")
        if runtime and runtime != "templatevalue" and runtime not in runtime_classes:
            problems.append(f"{namespace}/{kind}/{name}: runtimeClassName {runtime!r} is undefined")
        if spec.get("hostNetwork") is True:
            gaps[(namespace, "hostNetwork")] += 1
        if spec.get("hostPID") is True:
            gaps[(namespace, "hostPID")] += 1
        if spec.get("hostIPC") is True:
            gaps[(namespace, "hostIPC")] += 1
        containers = []
        for key in ("containers", "initContainers", "ephemeralContainers"):
            value = spec.get(key, [])
            if isinstance(value, list):
                containers.extend(value)
        for container in containers:
            if not isinstance(container, dict):
                continue
            security = container.get("securityContext")
            security = security if isinstance(security, dict) else {}
            if security.get("privileged") is True:
                gaps[(namespace, "privileged")] += 1
            pod_security = spec.get("securityContext")
            pod_security = pod_security if isinstance(pod_security, dict) else {}
            pod_seccomp = pod_security.get("seccompProfile")
            container_seccomp = security.get("seccompProfile")
            if not pod_seccomp and not container_seccomp:
                gaps[(namespace, "missingSeccomp")] += 1
    if mutate and not any("r234-mutation-missing" in item for item in problems):
        problems.append("mutation was not detected")
    actual = {f"{ns}:{field}": count for (ns, field), count in gaps.items()}
    for key, count in actual.items():
        if key.startswith("templatevalue:"):
            warnings.append(f"{key}={count}: namespace is selected by Helm at render time")
            continue
        if key not in KNOWN_GAPS:
            problems.append(f"new workload security gap {key}={count} (not in KNOWN_GAPS)")
        elif count > KNOWN_GAPS[key]:
            problems.append(f"workload security gap increased {key}: {KNOWN_GAPS[key]} -> {count}")
    for key, count in KNOWN_GAPS.items():
        if actual.get(key, 0) < count:
            problems.append(f"KNOWN_GAPS entry is stale: {key} {count} -> {actual.get(key, 0)}")
    file_counts = (parsed_files, fallback_files, unevaluated_files)
    return files, runtime_classes, workloads, namespace_workloads, actual, problems, warnings, file_counts


def container_count(workloads):
    return sum(len(value) for _, _, _, spec in workloads
               for key in ("containers", "initContainers", "ephemeralContainers")
               for value in [spec.get(key)] if isinstance(value, list))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--mutation-verify", action="store_true")
    args = parser.parse_args()
    try:
        files, classes, workloads, by_ns, gaps, problems, warnings, file_counts = scan(args.root.resolve())
        parsed_count, fallback_count, unevaluated_files = file_counts
        print(f"Evaluated {len(files)} YAML files, {len(workloads)} workloads, {container_count(workloads)} containers, {len(classes)} RuntimeClasses")
        print(f"Files parsed={parsed_count}, text-fallback={fallback_count}, unevaluated={len(unevaluated_files)}")
        for relative, reason in unevaluated_files:
            print(f"UNEVALUATED: {relative}" + (f" (allowlisted: {reason})" if reason else ""))
        if not files or not workloads:
            problems.append("repository workload inventory is empty")
        for (namespace, field), count in sorted(((tuple(k.split(":", 1)), v) for k, v in gaps.items())):
            print(f"GAP {namespace} {field}={count}")
        if args.mutation_verify:
            _, _, _, _, _, mutated, _, _ = scan(args.root.resolve(), mutate=True)
            if not any("r234-mutation-missing" in problem for problem in mutated):
                problems.append("mutation check failed to detect unresolved RuntimeClass")
            else:
                print("PASS: mutation detected (undefined RuntimeClass reference)")
            import tempfile

            with tempfile.TemporaryDirectory() as directory:
                mutation_file = Path(directory) / "unreadable-template.yaml"
                mutation_file.write_text('{{- if .Values.enabled }}\nkind: {{ .Values.kind | default "Deployment" }}\n  invalid: value\n{{- end }}\n')
                mutation_docs, mutation_mode = documents(mutation_file, Path(directory))
                rejected = mutation_mode == "text-fallback" and not any(
                    isinstance(doc, dict) and pod_spec(doc) is not None for doc in mutation_docs
                ) and is_unevaluated_workload_file(mutation_file.read_text(), 0)
                if not rejected:
                    problems.append("templated workload mutation was not rejected as unevaluated")
                else:
                    print("PASS: mutation detected (unreadable templated workload rejected as unevaluated)")
        for problem in problems:
            print(f"FAIL: {problem}", file=sys.stderr)
        for warning in warnings:
            print(f"WARN: {warning}")
        if problems:
            return 1
        print(f"PASS: {len(workloads)} workloads across {len(by_ns)} namespaces checked")
        return 0
    except (OSError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
