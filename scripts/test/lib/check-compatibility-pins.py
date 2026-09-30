#!/usr/bin/env python3
"""Check compatibility-matrix pins against the source cited in each row."""

import argparse
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DOC = Path("docs/common/compatibility-matrix.md")
VERSION_RE = re.compile(r"(?<![\w.])v?\d+\.\d+(?:\.\d+)?(?![\w.])")
SOURCE_RE = re.compile(r"`([^`]+)`")
VERSIONS_ALIASES = {
    "CSI / storage": ("csi-driver-nfs", "SeaweedFS"),
    "Backup": ("Velero", "velero-plugin-for-aws"),
    "Portal": ("IDP Portal", "Next.js"),
    "Observability": ("Loki",),
    "기타 주요 add-ons": ("Kyverno", "metrics-server", "cert-manager", "CloudNative-PG", "MetalLB", "Headlamp"),
    "OS": ("Ubuntu",),
}
# These direct pins are repeated in every cited direct source for the row.
# Other rows intentionally use union semantics because their sources differ.
EACH_SOURCE_ROWS = {
    "Istio ambient": ("1.30.1",),
    "Backup": ("12.0.3", "1.14.1"),
}


def rows(text):
    marker = "## 현재 저장소 pin 인벤토리"
    if marker not in text:
        raise ValueError(f"{DOC}: inventory heading missing")
    section = text.split(marker, 1)[1].split("\n## ", 1)[0]
    result = []
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) == 4 and cells[0] not in ("계층 / 컴포넌트", "---") and not set(cells[0]) <= {"-", ":"}:
            result.append(cells)
    return result


def cited_paths(root, source_cell, failures, component):
    result = []
    for citation in SOURCE_RE.findall(source_cell):
        if not ("/" in citation or citation.endswith((".yaml", ".yml", ".sh", ".md", "Vagrantfile"))):
            continue
        brace = re.search(r"\{([^}]+)\}", citation)
        patterns = ([citation[:brace.start()] + name + citation[brace.end()]
                    for name in brace.group(1).split(",")] if brace else [citation])
        expanded = []
        for pattern in patterns:
            brace = re.search(r"\{([^}]+)\}", pattern)
            if brace:
                expanded.extend(pattern[:brace.start()] + name + pattern[brace.end():]
                                for name in brace.group(1).split(","))
            else:
                expanded.append(pattern)
        matches = sorted({path for pattern in expanded for path in root.glob(pattern)})
        if not matches and "/" not in citation:
            matches = sorted(root.rglob(citation))
        if not matches:
            failures.append(f"{component}: cited source cannot be resolved: {citation}")
        result.extend(path for path in matches if path.is_file())
    return list(dict.fromkeys(result))


def check(root, document=DOC, source_overrides=None):
    source_overrides = source_overrides or {}
    failures, warnings, evaluated = [], [], 0
    try:
        inventory = rows((root / document).read_text())
    except (OSError, ValueError) as error:
        print(f"FAIL: inventory: {error}")
        return 1
    if not inventory:
        print("FAIL: inventory: zero rows evaluated")
        return 1

    for component, stated, source_cell, _status in inventory:
        paths = cited_paths(root, source_cell, failures, component)
        if stated.startswith("VERSIONS.md-only:"):
            direct_text = ""
            versions_text = stated.split(":", 1)[1]
        else:
            direct_text = stated.split("VERSIONS.md-only", 1)[0]
            versions_text = stated.partition("VERSIONS.md-only")[2]
        direct_tokens = [token.lstrip("v") for token in VERSION_RE.findall(direct_text)]
        versions_tokens = [token.lstrip("v") for token in VERSION_RE.findall(versions_text)]
        if not direct_tokens and not versions_tokens or re.search(r"\b(?:x|default|시도|범위|미설정)\b", stated, re.I):
            warnings.append(f"{component}: not statically decidable ({stated})")
            continue
        direct_paths = [path for path in paths if path.name != "VERSIONS.md"]
        versions_paths = [path for path in paths if path.name == "VERSIONS.md"]
        direct_values = set()
        values_by_path = {}
        for path in direct_paths:
            source_text = source_overrides.get(path, path.read_text(errors="replace"))
            path_values = set()
            for line in source_text.splitlines():
                if component == "Backup" and not any(
                    marker in line for marker in ("targetRevision:", "image:", "--version")
                ):
                    continue
                found_values = {value.lstrip("v") for value in VERSION_RE.findall(line)}
                direct_values.update(found_values)
                path_values.update(found_values)
                if component == "CNI":
                    assignment = re.search(r"\bCILIUM_VERSION=\"\$\{CILIUM_VERSION:-v?(\d+\.\d+(?:\.\d+)?)\}\"", line)
                    if assignment and assignment.group(1) not in direct_tokens:
                        failures.append(f"{component}: CILIUM_VERSION pin {assignment.group(1)} in {path.relative_to(root)} differs from doc")
            values_by_path[path] = path_values
        version_values = set()
        for path in versions_paths:
            source_text = path.read_text(errors="replace")
            aliases = [alias.lower() for alias in VERSIONS_ALIASES.get(component, (component,))]
            for line in source_text.splitlines():
                if line.startswith("|") and any(alias and alias in line.lower() for alias in aliases):
                    version_values.update(value.lstrip("v") for value in VERSION_RE.findall(line))

        for token in direct_tokens:
            if component in EACH_SOURCE_ROWS and token in EACH_SOURCE_ROWS[component]:
                for path, source_values in values_by_path.items():
                    if not any(value == token or value.startswith(token + ".") for value in source_values):
                        failures.append(f"{component}: {token} missing from {path.relative_to(root)}")
                continue
            if any(value == token or value.startswith(token + ".") for value in direct_values):
                continue
            found = ", ".join(sorted(direct_values)) or "none"
            failures.append(f"{component}: stated direct pin {token} absent from cited source values ({found})")
        for token in versions_tokens:
            if not any(value == token or value.startswith(token + ".") for value in version_values):
                failures.append(f"{component}: VERSIONS.md-only token {token} absent from its VERSIONS.md row")
            else:
                warnings.append(f"{component}: {token} is documented in VERSIONS.md only")
        evaluated += 1
        print(f"SOURCE: {component}: direct={', '.join(sorted(direct_values)) or '<none>'}; VERSIONS.md={', '.join(sorted(version_values)) or '<none>'}")

    print(f"Evaluated: rows={len(inventory)}, version comparisons={evaluated}, warnings={len(warnings)}")
    for warning in warnings:
        print(f"WARN: {warning}")
    for failure in failures:
        print(f"FAIL: {failure}")
    return 1 if failures else 0


def mutation_verify(root):
    targets = {
        Path("gitops/charts/narwhal-apps/templates/velero.yaml"),
        Path("scripts/cluster/08-4-storage.sh"),
        Path("scripts/cluster/03-cni-install.sh"),
        DOC,
    }
    saved = {relative: (root / relative).read_bytes() for relative in targets}

    def mutate(edits, label):
        for relative, old, new in edits:
            path = root / relative
            original = path.read_bytes()
            if old.encode() not in original:
                raise ValueError(f"cannot construct {label} mutation in {relative}")
            path.write_bytes(original.replace(old.encode(), new.encode(), 1))
        try:
            result = check(root)
            if result == 0:
                raise ValueError(f"{label} mutation was accepted")
            print(f"PASS: {label} rejected (exit {result})")
        finally:
            for relative in {edit[0] for edit in edits}:
                original = saved[relative]
                (root / relative).write_bytes(original)
                if (root / relative).read_bytes() != original:
                    raise OSError(f"failed byte-identical restore: {relative}")
        print(f"PASS: {label} targets restored byte-identical")

    def mutate_in_memory(relative, old, new, label):
        path = root / relative
        source_text = path.read_text(errors="replace")
        if old not in source_text:
            raise ValueError(f"cannot construct {label} mutation in {relative}")
        result = check(root, source_overrides={path: source_text.replace(old, new, 1)})
        if result == 0:
            raise ValueError(f"{label} mutation was accepted")
        print(f"PASS: {label} rejected in memory (exit {result})")

    try:
        mutate([
            (Path("gitops/charts/narwhal-apps/templates/velero.yaml"), "targetRevision: 12.0.3", "targetRevision: 99.0.0"),
            (Path("scripts/cluster/08-4-storage.sh"), "--version 12.0.3", "--version 99.0.0"),
        ], "Velero chart pin mutation")
        mutate([
            (Path("gitops/charts/narwhal-apps/templates/velero.yaml"), "velero-plugin-for-aws:v1.14.1", "velero-plugin-for-aws:v9.9.9"),
            (Path("scripts/cluster/08-4-storage.sh"), "velero-plugin-for-aws:v1.14.1", "velero-plugin-for-aws:v9.9.9"),
        ], "Velero plugin app pin mutation")
        mutate([(Path("scripts/cluster/03-cni-install.sh"), "CILIUM_VERSION:-1.19.4", "CILIUM_VERSION:-9.9.9")], "Cilium source mutation")
        mutate_in_memory(Path("gitops/charts/narwhal-apps/templates/ztunnel.yaml"), "targetRevision: 1.30.1", "targetRevision: 1.29.0", "single Istio template mutation")
        mutate([(DOC, "Cilium `v1.19.4`", "Cilium `v0.0.0`")], "doc-side mutation")
    finally:
        for relative, original in saved.items():
            (root / relative).write_bytes(original)
            if (root / relative).read_bytes() != original:
                raise OSError(f"failed byte-identical final restore: {relative}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mutation-verify", action="store_true")
    args = parser.parse_args()
    result = check(ROOT)
    if result:
        return result
    if args.mutation_verify:
        mutation_verify(ROOT)
    print("PASS: compatibility inventory pins match cited source evidence")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        print(f"FAIL: mutation verification: {error}")
        sys.exit(1)
