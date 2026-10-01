#!/usr/bin/env python3
"""Validate the component wave graph against Argo CD Applications (Narwhal#46)."""

from __future__ import annotations

import argparse
import copy
import json
import pathlib
import re
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[3]
MODEL = "docs/common/upgrade-waves.json"
TEMPLATES = "gitops/charts/narwhal-apps/templates"
APP_RE = re.compile(r"^kind:\s*Application\s*$", re.MULTILINE)
NAME_RE = re.compile(r"^  name:\s*([^\s#]+)", re.MULTILINE)


def applications(root: pathlib.Path) -> set[str]:
    names: set[str] = set()
    directory = root / TEMPLATES
    if not directory.is_dir():
        raise ValueError(f"missing Application template directory: {directory}")
    for path in sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        if not APP_RE.search(text):
            continue
        match = NAME_RE.search(text)
        if not match:
            raise ValueError(f"{path}: Application has no metadata.name")
        if match.group(1) in names:
            raise ValueError(f"duplicate Application name: {match.group(1)}")
        names.add(match.group(1))
    return names


def validate(root: pathlib.Path) -> tuple[list[str], int, int]:
    path = root / MODEL
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read valid wave model {path}: {exc}") from exc
    components = data.get("components")
    excluded = data.get("NOT_YET_MODELLED")
    if not isinstance(components, dict) or not isinstance(excluded, dict):
        raise ValueError("components and NOT_YET_MODELLED must be objects")
    apps = applications(root)
    problems: list[str] = []
    overlap = set(components) & set(excluded)
    if overlap:
        problems.append(f"components also listed as NOT_YET_MODELLED: {sorted(overlap)}")
    for name, item in components.items():
        if not isinstance(item, dict):
            problems.append(f"{name}: component entry must be an object")
            continue
        wave = item.get("wave")
        if not isinstance(wave, int) or wave < 0:
            problems.append(f"{name}: wave must be a nonnegative integer")
        deps = item.get("depends_on")
        if not isinstance(deps, list):
            problems.append(f"{name}: depends_on must be a list")
            deps = []
        for dependency in deps:
            if dependency not in components:
                problems.append(f"{name}: unknown dependency {dependency!r}")
            elif isinstance(wave, int) and wave <= components[dependency].get("wave", -1):
                problems.append(f"{name}: wave {wave} must exceed dependency {dependency} wave {components[dependency].get('wave')}")
        if not isinstance(item.get("strategy"), str) or not item["strategy"].strip():
            problems.append(f"{name}: strategy is required")
        for field in ("health_gate", "rollback_ref"):
            ref = item.get(field)
            if not isinstance(ref, str) or not ref.strip():
                problems.append(f"{name}: {field} is required")
            elif not (root / ref).is_file():
                problems.append(f"{name}: {field} points to missing repo path {ref!r}")
    states: dict[str, int] = {}

    def visit(name: str) -> None:
        if states.get(name) == 1:
            problems.append(f"dependency cycle includes {name}")
            return
        if states.get(name) == 2:
            return
        states[name] = 1
        item = components.get(name, {})
        for dependency in item.get("depends_on", []) if isinstance(item, dict) else []:
            if dependency in components:
                visit(dependency)
        states[name] = 2

    for name in components:
        visit(name)
    missing = apps - set(components) - set(excluded)
    stale = set(excluded) - apps
    if missing:
        problems.append(f"unmodelled Applications: {sorted(missing)}")
    if stale:
        problems.append(f"NOT_YET_MODELLED entries without an Application: {sorted(stale)}")
    for name, reason in excluded.items():
        if not isinstance(reason, str) or not reason.strip():
            problems.append(f"{name}: NOT_YET_MODELLED needs a reason")
    return problems, len(components), len(apps)


def mutation_verify(root: pathlib.Path) -> None:
    with tempfile.TemporaryDirectory(prefix="upgrade-waves-") as temp:
        temp_root = pathlib.Path(temp)
        shutil.copytree(root / TEMPLATES, temp_root / TEMPLATES)
        (temp_root / "docs/common").mkdir(parents=True)
        shutil.copy2(root / MODEL, temp_root / MODEL)
        refs = (
            "docs/common/upgrade-orchestration.md",
            "docs/common/apisix-upgrade-sequence.md",
            "scripts/cluster/capture-cert-manager-upgrade-checkpoint.sh",
        )
        for ref in refs:
            target = temp_root / ref
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / ref, target)
        model_path = temp_root / MODEL
        original = json.loads(model_path.read_text(encoding="utf-8"))

        def rejected(label: str, mutate, add_app: bool = False) -> None:
            changed = copy.deepcopy(original)
            mutate(changed)
            model_path.write_text(json.dumps(changed), encoding="utf-8")
            if add_app:
                (temp_root / TEMPLATES / "new-component.yaml").write_text(
                    "apiVersion: argoproj.io/v1alpha1\nkind: Application\nmetadata:\n  name: new-component\n",
                    encoding="utf-8",
                )
            problems, _, _ = validate(temp_root)
            if not problems:
                raise AssertionError(f"mutation was accepted: {label}")
            if add_app:
                (temp_root / TEMPLATES / "new-component.yaml").unlink()
            model_path.write_text(json.dumps(original), encoding="utf-8")

        rejected("cycle", lambda d: d["components"]["cilium"]["depends_on"].append("cert-manager"))
        rejected("unknown dependency", lambda d: d["components"]["cilium"]["depends_on"].append("missing-component"))
        rejected("wave inversion", lambda d: d["components"]["cert-manager"].update(wave=0))
        rejected("missing health gate", lambda d: d["components"]["cert-manager"].update(health_gate=""))
        rejected("dangling rollback reference", lambda d: d["components"]["cert-manager"].update(rollback_ref="docs/missing.md"))
        rejected("new unmodelled Application", lambda d: None, add_app=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, default=ROOT)
    parser.add_argument("--mutation-verify", action="store_true")
    args = parser.parse_args()
    try:
        problems, component_count, app_count = validate(args.root)
        if problems:
            print("upgrade wave check failed:", file=sys.stderr)
            print("\n".join(f"  - {problem}" for problem in problems), file=sys.stderr)
            print(f"evaluated {component_count} components and {app_count} Applications", file=sys.stderr)
            return 1
        if not component_count or not app_count:
            print("upgrade wave check failed: evaluated zero components or Applications", file=sys.stderr)
            return 1
        print(f"PASS: {component_count} modeled components and {app_count} Argo CD Applications evaluated")
        if args.mutation_verify:
            mutation_verify(args.root)
            print("PASS: cycle, unknown dependency, wave inversion, missing health gate, dangling rollback_ref, and new Application mutations rejected through temporary repo roots")
        return 0
    except (OSError, ValueError, AssertionError) as exc:
        print(f"upgrade wave check failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
