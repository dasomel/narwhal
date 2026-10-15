#!/usr/bin/env python3
"""Verify upgrade bundle manifest schema and metadata policy compliance (narwhal#45).

Validates an offline upgrade bundle manifest against:
1. JSON Schema (schemas/upgrade-bundle-1.0.schema.json)
2. License policy (bans source-available licenses like RSALv2/SSPLv1)
3. Integrity rules (digest syntax, version pinning, dependency references)
"""
import argparse
import datetime
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


RFC3339 = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})")
CLOCK_SKEW = datetime.timedelta(minutes=5)


def parse_timestamp(value: Any) -> datetime.datetime | None:
    if not isinstance(value, str) or not RFC3339.fullmatch(value):
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


MAX_AGE_CEILING_DAYS = 3650  # ten years; above this the ceiling is meaningless


def validate_freshness(manifest: dict, now: datetime.datetime, max_age_days: float | None) -> list[str]:
    """narwhal#124: bundle generation time and producer must be present and sane.

    D1: absent/unparsable/future evidence fails closed; the age ceiling is opt-in
    (--max-age-days) so the committed manifests do not rot in CI. Cost: promotion
    needs a fresh bundle; escape hatch: regenerate it or raise the ceiling.
    """
    errors: list[str] = []
    created = parse_timestamp(manifest.get("created_at"))
    if created is None:
        errors.append(f"created_at {manifest.get('created_at')!r} is not an RFC 3339 timestamp with timezone")
    elif created - now > CLOCK_SKEW:
        errors.append(f"created_at {manifest['created_at']} is in the future")
    elif max_age_days is not None and now - created > datetime.timedelta(days=max_age_days):
        errors.append(f"bundle is {(now - created).days}d old (max {max_age_days:g}d): created_at={manifest['created_at']}")
    provenance = manifest.get("provenance")
    for key in ("author", "source_repo"):
        value = provenance.get(key) if isinstance(provenance, dict) else None
        if not isinstance(value, str) or not value.strip():
            errors.append(f"provenance.{key} (bundle producer/source) must be a non-empty string")
    return errors


def verify_bundle(manifest_path: Path, schema_path: Path, now: datetime.datetime, max_age_days: float | None) -> int:
    schema_doc = read_json(schema_path, "schema")
    manifest_doc = read_json(manifest_path, "manifest")

    schema_errs = validate_schema(manifest_doc, schema_doc)
    meta_errs = validate_metadata(manifest_doc)
    fresh_errs = validate_freshness(manifest_doc, now, max_age_days)

    all_errs = schema_errs + meta_errs + fresh_errs
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
    parser.add_argument("--max-age-days", type=float, default=None, help="Reject bundles whose created_at is older than this")
    parser.add_argument("--now", default=None, help="RFC 3339 clock override for offline replay")
    args = parser.parse_args()

    now = datetime.datetime.now(datetime.timezone.utc)
    if args.now is not None:
        now = parse_timestamp(args.now)
        if now is None:
            print(f"ERROR: --now {args.now!r} is not an RFC 3339 timestamp with timezone", file=sys.stderr)
            return 2
    if args.max_age_days is not None and not 0 < args.max_age_days <= MAX_AGE_CEILING_DAYS:
        # Also rejects nan/inf; huge values overflow timedelta (OverflowError traceback).
        print(f"ERROR: --max-age-days must be a finite number in (0, {MAX_AGE_CEILING_DAYS}]", file=sys.stderr)
        return 2
    return verify_bundle(args.manifest, args.schema, now, args.max_age_days)


if __name__ == "__main__":
    sys.exit(main())
