#!/usr/bin/env python3
"""Offline Upgrade Bundle Dry-Run Diff Tool (narwhal#45).

Compares a current upgrade bundle manifest against a new candidate upgrade bundle manifest
to produce a dry-run diff report detailing added, removed, version-bumped, and unchanged
artifacts prior to air-gap intake or promotion.
"""
import argparse
import json
from pathlib import Path
import sys
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CURRENT = REPOSITORY_ROOT / "scripts" / "airgap" / "lib" / "upgrade-bundle-v1.0.0.json"
DEFAULT_NEW = REPOSITORY_ROOT / "scripts" / "airgap" / "lib" / "upgrade-bundle-v1.1.0.json"


def load_manifest(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        print(f"ERROR: failed to load manifest {path}: {exc}", file=sys.stderr)
        sys.exit(2)


def generate_diff(current_doc: dict, new_doc: dict) -> dict:
    current_artifacts = {a["name"]: a for a in current_doc.get("artifacts", []) if "name" in a}
    new_artifacts = {a["name"]: a for a in new_doc.get("artifacts", []) if "name" in a}

    added = []
    removed = []
    updated = []
    unchanged = []

    for name, new_art in new_artifacts.items():
        if name not in current_artifacts:
            added.append(new_art)
        else:
            old_art = current_artifacts[name]
            old_ver, new_ver = old_art.get("version"), new_art.get("version")
            old_dig, new_dig = old_art.get("digest"), new_art.get("digest")

            if old_ver != new_ver or old_dig != new_dig:
                updated.append({
                    "name": name,
                    "artifact_type": new_art.get("artifact_type"),
                    "old_version": old_ver,
                    "new_version": new_ver,
                    "old_digest": old_dig,
                    "new_digest": new_dig,
                    "source_ref": new_art.get("source_ref"),
                    "license": new_art.get("license"),
                })
            else:
                unchanged.append(new_art)

    for name, old_art in current_artifacts.items():
        if name not in new_artifacts:
            removed.append(old_art)

    # Missing dependency analysis
    all_available = set(new_artifacts.keys()) | set(current_artifacts.keys())
    missing_dependencies = []

    for name, new_art in new_artifacts.items():
        for dep in new_art.get("dependencies", []):
            if dep not in all_available:
                missing_dependencies.append({"artifact": name, "missing_dependency": dep})

    summary = {
        "current_bundle_id": current_doc.get("bundle_id"),
        "current_version": current_doc.get("target_version"),
        "new_bundle_id": new_doc.get("bundle_id"),
        "new_version": new_doc.get("target_version"),
        "promotion_stage": new_doc.get("promotion_stage"),
        "compatibility_matrix_ref": new_doc.get("compatibility_matrix_ref"),
        "counts": {
            "added": len(added),
            "removed": len(removed),
            "updated": len(updated),
            "unchanged": len(unchanged),
            "missing_dependencies": len(missing_dependencies),
        },
        "added": added,
        "removed": removed,
        "updated": updated,
        "unchanged": unchanged,
        "missing_dependencies": missing_dependencies,
    }
    return summary


def format_text_report(diff: dict) -> str:
    lines = []
    lines.append("=====================================================================")
    lines.append("                 AIR-GAP UPGRADE BUNDLE DRY-RUN DIFF REPORT")
    lines.append("=====================================================================")
    lines.append(f"Current Bundle : {diff['current_bundle_id']} ({diff['current_version']})")
    lines.append(f"New Candidate  : {diff['new_bundle_id']} ({diff['new_version']}) [Stage: {diff['promotion_stage']}]")
    lines.append(f"Compat Matrix  : {diff['compatibility_matrix_ref']}")
    lines.append("---------------------------------------------------------------------")
    lines.append(
        f"Summary: {diff['counts']['added']} added, {diff['counts']['removed']} removed, "
        f"{diff['counts']['updated']} updated, {diff['counts']['unchanged']} unchanged"
    )
    if diff['counts']['missing_dependencies'] > 0:
        lines.append(f"WARNING: {diff['counts']['missing_dependencies']} missing dependencies detected!")
    lines.append("---------------------------------------------------------------------")

    if diff["updated"]:
        lines.append("\n[UPDATED / VERSION BUMPED ARTIFACTS]")
        for item in diff["updated"]:
            lines.append(
                f"  * {item['name']} ({item['artifact_type']}): "
                f"{item['old_version']} ({item['old_digest'][:19]}...) -> "
                f"{item['new_version']} ({item['new_digest'][:19]}...)"
            )

    if diff["added"]:
        lines.append("\n[NEWLY ADDED ARTIFACTS]")
        for item in diff["added"]:
            lines.append(
                f"  + {item['name']} ({item['artifact_type']}) version {item['version']} "
                f"[{item['license']}] ({item['source_ref']})"
            )

    if diff["removed"]:
        lines.append("\n[REMOVED ARTIFACTS]")
        for item in diff["removed"]:
            lines.append(f"  - {item['name']} ({item['artifact_type']}) version {item['version']}")

    if diff["missing_dependencies"]:
        lines.append("\n[MISSING DEPENDENCY WARNINGS]")
        for item in diff["missing_dependencies"]:
            lines.append(f"  ! {item['artifact']} requires missing artifact: '{item['missing_dependency']}'")

    if diff["unchanged"]:
        lines.append("\n[UNCHANGED ARTIFACTS]")
        for item in diff["unchanged"]:
            lines.append(f"  = {item['name']} ({item['artifact_type']}) version {item['version']}")

    lines.append("\n=====================================================================")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run diff tool for upgrade bundle manifests")
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT, help="Path to current bundle manifest JSON")
    parser.add_argument("--new", type=Path, default=DEFAULT_NEW, help="Path to new candidate bundle manifest JSON")
    parser.add_argument("--json", action="store_true", help="Output report as JSON")
    args = parser.parse_args()

    current_doc = load_manifest(args.current)
    new_doc = load_manifest(args.new)

    diff = generate_diff(current_doc, new_doc)

    if args.json:
        print(json.dumps(diff, indent=2))
    else:
        print(format_text_report(diff))

    return 0


if __name__ == "__main__":
    sys.exit(main())
