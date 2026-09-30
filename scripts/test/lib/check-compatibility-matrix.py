#!/usr/bin/env python3
"""Validate the machine-readable compatibility support matrix."""

import argparse
import importlib.util
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DOC = Path("docs/common/compatibility-matrix.md")
DATA = Path("docs/common/compatibility-matrix.json")
PIN_CHECK = Path("scripts/test/lib/check-compatibility-pins.py")
COMPONENTS = ("cilium", "csi-driver-nfs", "istio")
VERSION = re.compile(r"^\d+\.\d+(?:\.\d+)?$")


def load_pin_checker():
    spec = importlib.util.spec_from_file_location("compatibility_pins", ROOT / PIN_CHECK)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load {PIN_CHECK}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_markdown(text):
    marker = "| Kubernetes minor | Cilium | csi-driver-nfs | Istio |"
    if marker not in text:
        raise ValueError("support matrix table header missing")
    section = text.split(marker, 1)[1].split("\n\n", 1)[0]
    result = {}
    for line in section.splitlines():
        if not line.startswith("|") or set(line.replace("|", "").replace("-", "").replace(":", "").strip()) == set():
            continue
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if len(cells) != 4 or cells[0] == "Kubernetes minor":
            continue
        match = re.fullmatch(r"v(\d+\.\d+)", cells[0])
        if not match or match.group(1) in result:
            raise ValueError(f"invalid or duplicate Kubernetes row: {cells[0]}")
        row = {}
        for component, cell in zip(COMPONENTS, cells[1:]):
            range_match = re.fullmatch(r"v?(\d+\.\d+)(?:\+|–v?(\d+\.\d+))", cell)
            if not range_match:
                raise ValueError(f"invalid {component} table cell: {cell}")
            row[component] = {"min": range_match.group(1)}
            if range_match.group(2):
                row[component]["max"] = range_match.group(2)
        result[match.group(1)] = row
    if not result:
        raise ValueError("zero support matrix rows evaluated")
    return result


def validate(data):
    if not isinstance(data, dict) or set(data) != {"schemaVersion", "supportedRows", "pinned"}:
        raise ValueError("top-level keys must be schemaVersion, supportedRows, pinned")
    if data["schemaVersion"] != 1:
        raise ValueError("schemaVersion must be 1")
    rows = data["supportedRows"]
    if not isinstance(rows, dict) or not rows:
        raise ValueError("supportedRows must be a non-empty object")
    for kube, row in rows.items():
        if not VERSION.fullmatch(kube) or not isinstance(row, dict) or set(row) != set(COMPONENTS):
            raise ValueError(f"invalid row {kube!r}: expected Kubernetes minor and all components")
        for component, bounds in row.items():
            if not isinstance(bounds, dict) or set(bounds) not in ({"min"}, {"min", "max"}):
                raise ValueError(f"{kube}/{component}: bounds must contain min and optional max")
            if any(not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+", value) for value in bounds.values()):
                raise ValueError(f"{kube}/{component}: versions must be minor strings")
            if "max" in bounds and tuple(map(int, bounds["min"].split("."))) > tuple(map(int, bounds["max"].split("."))):
                raise ValueError(f"{kube}/{component}: min exceeds max")
    pinned = data["pinned"]
    required = {"kubernetes", *COMPONENTS}
    if not isinstance(pinned, dict) or set(pinned) != required:
        raise ValueError("pinned must contain Kubernetes and all three components")
    if any(not isinstance(value, str) or not VERSION.fullmatch(value) for value in pinned.values()):
        raise ValueError("pinned versions must be numeric version strings")
    return len(rows)


def resolve_pins(pin_checker):
    inventory = pin_checker.rows((ROOT / DOC).read_text())
    aliases = {"cilium": "CNI", "csi-driver-nfs": "CSI / storage", "istio": "Istio ambient", "kubernetes": "Kubernetes"}
    values = {}
    source_markers = {"cilium": "CILIUM_VERSION", "csi-driver-nfs": "CSI_DRIVER_NFS_VERSION", "istio": "targetRevision:"}
    version_re = pin_checker.VERSION_RE
    for component, label in aliases.items():
        inventory_row = next((row for row in inventory if row[0] == label), None)
        if inventory_row is None:
            raise ValueError(f"R229 inventory row missing: {label}")
        tokens = [value.lstrip("v") for value in version_re.findall(inventory_row[1])]
        sources = pin_checker.cited_paths(ROOT, inventory_row[2], [], label)
        matches = set()
        for path in sources:
            text = path.read_text(errors="replace")
            for token in tokens:
                scoped = "\n".join(line for line in text.splitlines()
                                     if component == "kubernetes" or source_markers[component] in line)
                if re.search(rf"(?<![\w.])v?{re.escape(token)}(?:\.\d+)?(?![\w.])", scoped):
                    matches.add(token)
        if not matches:
            raise ValueError(f"cannot resolve current {component} pin through R229 sources")
        values[component] = max(matches, key=lambda value: tuple(map(int, value.split("."))))
    return values


def check(data_text=None, markdown_text=None, pin_override=None):
    failures = []
    try:
        data = json.loads(data_text if data_text is not None else (ROOT / DATA).read_text())
        row_count = validate(data)
        markdown = parse_markdown(markdown_text if markdown_text is not None else (ROOT / DOC).read_text())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL: matrix input: {error}")
        return 1
    if data["supportedRows"] != markdown:
        failures.append("JSON supportedRows differ from Markdown table")
    actual_pins = pin_override if pin_override is not None else resolve_pins(load_pin_checker())
    if data["pinned"] != actual_pins:
        failures.append(f"JSON pinned combination differs from resolved sources: {actual_pins}")
    kube_minor = actual_pins["kubernetes"].rsplit(".", 1)[0]
    row = data["supportedRows"].get(kube_minor)
    if row is None:
        failures.append(f"pinned Kubernetes {kube_minor} has no supported row")
    else:
        for component in COMPONENTS:
            version = tuple(map(int, actual_pins[component].split(".")))
            bounds = row[component]
            lower = tuple(map(int, bounds["min"].split(".")))
            upper = tuple(map(int, bounds.get("max", "999.999").split(".")))
            if not lower <= version[:2] <= upper:
                failures.append(f"pinned {component} {actual_pins[component]} outside Kubernetes {kube_minor} support row")
    print(f"Evaluated: JSON rows={row_count}, Markdown rows={len(markdown)}, component cells={len(markdown) * len(COMPONENTS)}, pinned components={len(actual_pins) - 1}")
    for failure in failures:
        print(f"FAIL: {failure}")
    if not failures:
        print("PASS: compatibility matrix is valid, synchronized, and covers current pins")
    return 1 if failures else 0


def copy_inputs(root):
    (root / DOC).parent.mkdir(parents=True, exist_ok=True)
    (root / DATA).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / DOC, root / DOC)
    shutil.copy2(ROOT / DATA, root / DATA)
    (root / PIN_CHECK).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / PIN_CHECK, root / PIN_CHECK)

    pin_checker = load_pin_checker()
    inventory = pin_checker.rows((ROOT / DOC).read_text())
    for row in inventory:
        for path in pin_checker.cited_paths(ROOT, row[2], [], row[0]):
            destination = root / path.relative_to(ROOT)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)


def run_main_in(root):
    global ROOT
    original_root = ROOT
    ROOT = root
    try:
        try:
            return main([])
        except (OSError, ValueError, StopIteration) as error:
            print(f"FAIL: compatibility matrix: {error}")
            return 1
    finally:
        ROOT = original_root


def mutation_verify():
    data_text = (ROOT / DATA).read_text()
    markdown = (ROOT / DOC).read_text()
    pins = resolve_pins(load_pin_checker())
    outside_pins = {**pins, "istio": "1.31.1"}
    outside_data = json.loads(data_text)
    outside_data["pinned"]["istio"] = "1.31.1"
    mutations = [
        ("JSON cell", data_text.replace('"min": "1.19"', '"min": "1.18"', 1), markdown, pins),
        ("Markdown cell", data_text, markdown.replace("`v1.19+`", "`v1.18+`", 1), pins),
        ("pin outside row", json.dumps(outside_data), markdown, outside_pins),
    ]
    for label, changed_json, changed_markdown, changed_pins in mutations:
        if check(changed_json, changed_markdown, changed_pins) == 0:
            raise ValueError(f"{label} mutation was accepted")
        print(f"PASS: {label} mutation rejected in memory")

    source_script = Path("scripts/cluster/03-cni-install.sh")
    source_text = (ROOT / source_script).read_text()
    source_mutation = source_text.replace(
        'CILIUM_VERSION="${CILIUM_VERSION:-1.19.4}"',
        'CILIUM_VERSION="${CILIUM_VERSION:-1.18.4}"',
        1,
    )
    if source_mutation == source_text:
        raise ValueError("pin source mutation target not found")

    main_mutations = [
        ("main JSON cell", lambda path: (path / DATA).write_text(data_text.replace('"min": "1.19"', '"min": "1.18"', 1))),
        ("main Markdown row", lambda path: (path / DOC).write_text(markdown.replace("`v1.19+`", "`v1.18+`", 1))),
        ("main R229 pin source", lambda path: (path / source_script).write_text(source_mutation)),
    ]
    for label, mutate in main_mutations:
        with tempfile.TemporaryDirectory(prefix="compatibility-matrix-") as temporary:
            temp_root = Path(temporary)
            copy_inputs(temp_root)
            mutate(temp_root)
            if run_main_in(temp_root) == 0:
                raise ValueError(f"{label} mutation was accepted by main")
        print(f"PASS: {label} mutation rejected through main inputs")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--mutation-verify", action="store_true")
    args = parser.parse_args(argv)
    result = check()
    if result:
        return result
    if args.mutation_verify:
        mutation_verify()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, StopIteration) as error:
        print(f"FAIL: compatibility matrix: {error}")
        sys.exit(1)
