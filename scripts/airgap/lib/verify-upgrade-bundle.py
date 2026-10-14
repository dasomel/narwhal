#!/usr/bin/env python3
"""Verify upgrade bundle manifest schema and metadata policy compliance (narwhal#45).

Validates an offline upgrade bundle manifest against:
1. JSON Schema (schemas/upgrade-bundle-1.0.schema.json)
2. License policy (bans source-available licenses like RSALv2/SSPLv1)
3. Integrity rules (digest syntax, version pinning, dependency references)
"""
import argparse
import json
import re
from pathlib import Path
from runpy import run_path
import sys
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SCHEMA = REPOSITORY_ROOT / "schemas" / "upgrade-bundle-1.0.schema.json"
DEFAULT_MANIFEST = REPOSITORY_ROOT / "scripts" / "airgap" / "lib" / "upgrade-bundle-v1.1.0.json"

# D1: Promotion and inventory share the reviewed license policy. Cost: the
# checker travels with this verifier; escape hatch: distribute the lib directory
# together instead of copying this entrypoint and inventing another allowlist.
LICENSE_POLICY = run_path(str(Path(__file__).with_name("check-license-policy.py")))
ALLOWED_LICENSES = LICENSE_POLICY["ALLOWED"]
FORBIDDEN_LICENSES = LICENSE_POLICY["FORBIDDEN"]


def read_json(path: Path, label: str) -> Any:
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"ERROR: {label} not found: {path}", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as exc:
        print(f"ERROR: {label} is invalid JSON ({path}): {exc.msg}", file=sys.stderr)
        sys.exit(2)


def validate_schema(manifest: dict, schema: dict) -> list[str]:
    errors: list[str] = []
    try:
        from jsonschema import Draft202012Validator, FormatChecker
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        for err in validator.iter_errors(manifest):
            path_str = "$" + "".join(f"[{item}]" if isinstance(item, int) else f".{item}" for item in err.absolute_path)
            errors.append(f"Schema violation at {path_str}: {err.message}")
    except ImportError:
        # Fallback structural checks if jsonschema is not installed
        for req in schema.get("required", []):
            if req not in manifest:
                errors.append(f"Missing required top-level field: '{req}'")
        if manifest.get("schema_version") != "1.0":
            errors.append(f"Invalid schema_version '{manifest.get('schema_version')}', expected '1.0'")
    return errors


def validate_metadata(manifest: dict) -> list[str]:
    errors: list[str] = []
    artifacts = manifest.get("artifacts", [])
    if not isinstance(artifacts, list):
        errors.append("Field 'artifacts' must be a list")
        return errors

    artifact_names = {
        a["name"].strip()
        for a in artifacts
        if isinstance(a, dict) and isinstance(a.get("name"), str) and a.get("name").strip()
    }
    seen_names = set()

    for idx, art in enumerate(artifacts):
        if not isinstance(art, dict):
            errors.append(f"Artifact at index {idx} is not an object")
            continue

        raw_name = art.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            errors.append(f"Artifact at index {idx} has invalid or missing name")
            continue

        name = raw_name.strip()
        if name in seen_names:
            errors.append(f"Artifact at index {idx} declares duplicate artifact name '{name}'")
        seen_names.add(name)

        # D1: Metadata integrity cannot depend on optional jsonschema availability.
        # Cost: malformed pins fail intake; escape hatch: regenerate the digest
        # from the actual artifact using the declared supported algorithm.
        digest = art.get("digest")
        if not isinstance(digest, str) or not re.fullmatch(
            r"sha256:[a-fA-F0-9]{64}|sha512:[a-fA-F0-9]{128}", digest
        ):
            errors.append(f"Artifact '{name}' requires an exact SHA-256 or SHA-512 digest")

        raw_license = art.get("license", "")
        if not isinstance(raw_license, str):
            errors.append(f"Artifact '{name}' license must be a string")
            continue
        lic = raw_license.strip()

        if not lic:
            errors.append(f"Artifact '{name}' has a blank license")
        elif lic in FORBIDDEN_LICENSES:
            errors.append(f"Artifact '{name}' uses forbidden license '{lic}'")
        elif lic not in ALLOWED_LICENSES:
            errors.append(f"Artifact '{name}' license '{lic}' needs review in check-license-policy.py")

        # Dependency check: all declared dependencies must exist in the bundle
        for dep in art.get("dependencies", []):
            if dep not in artifact_names:
                errors.append(f"Artifact '{name}' declares missing dependency '{dep}'")

    return errors


def verify_bundle(manifest_path: Path, schema_path: Path) -> int:
    schema_doc = read_json(schema_path, "schema")
    manifest_doc = read_json(manifest_path, "manifest")

    schema_errs = validate_schema(manifest_doc, schema_doc)
    meta_errs = validate_metadata(manifest_doc)

    all_errs = schema_errs + meta_errs
    if all_errs:
        for err in all_errs:
            print(f"ERROR: {err}", file=sys.stderr)
        print(f"FAIL: {manifest_path} failed verification with {len(all_errs)} error(s)", file=sys.stderr)
        return 1

    print(
        f"OK: {manifest_path.name} conforms to schema {schema_path.name} "
        f"(bundle_id: {manifest_doc.get('bundle_id')}, stage: {manifest_doc.get('promotion_stage')}, "
        f"artifacts: {len(manifest_doc.get('artifacts', []))})"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify upgrade bundle manifest compliance")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help="Path to upgrade bundle manifest JSON")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA, help="Path to JSON Schema file")
    args = parser.parse_args()

    return verify_bundle(args.manifest, args.schema)


if __name__ == "__main__":
    sys.exit(main())
