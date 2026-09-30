#!/usr/bin/env python3
"""Validate offline accelerator capability profiles against the conformance matrix."""
import copy
import json
import pathlib
import re
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[3]
DOC = ROOT / "docs/common/accelerator-backend-conformance-matrix.md"
PROFILE_DIR = ROOT / "scripts/test/fixtures/accelerator-profiles"
SCHEMA = PROFILE_DIR / "profile.schema.json"
SUPPORTED_SCHEMA_KEYWORDS = {
    "$schema", "type", "enum", "const", "properties", "required",
    "additionalProperties", "items", "minLength",
}


def fail(message):
    raise ValueError(message)


def schema_errors(value, schema, path="$"):
    errors = []
    unsupported = schema.keys() - SUPPORTED_SCHEMA_KEYWORDS
    if unsupported:
        fail(f"{path}: unsupported schema keyword(s): {', '.join(sorted(unsupported))}")
    expected = schema.get("type")
    types = expected if isinstance(expected, list) else [expected]
    if expected is not None:
        matches = any(
            (kind == "object" and isinstance(value, dict))
            or (kind == "array" and isinstance(value, list))
            or (kind == "string" and isinstance(value, str))
            or (kind == "null" and value is None)
            for kind in types
        )
        if not matches:
            return [f"{path}: expected {expected}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value is outside enum")
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: value differs from const")
    if "minLength" in schema:
        minimum = schema["minLength"]
        if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 0:
            fail(f"{path}: malformed minLength")
        if isinstance(value, str) and len(value) < minimum:
            errors.append(f"{path}: string is shorter than minLength {minimum}")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required property {key}")
        if schema.get("additionalProperties") is False:
            errors.extend(f"{path}: unexpected property {key}" for key in value.keys() - properties.keys())
        for key, child in value.items():
            child_schema = properties.get(key, schema.get("additionalProperties"))
            if isinstance(child_schema, dict):
                errors.extend(schema_errors(child, child_schema, f"{path}.{key}"))
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, child in enumerate(value):
            errors.extend(schema_errors(child, schema["items"], f"{path}[{index}]"))
    return errors


def validate(profile, backends, capabilities, schema):
    errors = schema_errors(profile, schema)
    if errors:
        fail(errors[0])
    if profile["backend"] not in backends:
        fail(f"unknown backend: {profile['backend']}")
    entries = profile["capabilities"]
    if not isinstance(entries, dict) or set(entries) != capabilities:
        fail(f"{profile['backend']}: capability entries differ from matrix")
    for name, entry in entries.items():
        if entry["status"] == "partial" and not entry.get("limitations"):
            fail(f"{profile['backend']}/{name}: partial requires limitations")
        if entry["status"] == "not-applicable" and not entry.get("reason"):
            fail(f"{profile['backend']}/{name}: not-applicable requires reason")
        if entry["status"] == "supported" and name in HARDWARE_ONLY and entry["evidenceKind"] != "hardware":
            fail(f"{profile['backend']}/{name}: hardware-only capability requires hardware evidence")


def validate_backend_set(found, expected):
    if found != expected:
        fail(f"backend profiles missing: {sorted(expected - found)}")


def validate_matrix_doc(text):
    obsolete = ("currently has no accelerator schema", "after a schema exists")
    if any(statement in text for statement in obsolete):
        fail("conformance matrix contains an obsolete schema-status statement")
    if not all(statement in text for statement in ("five backend-profile fixtures", "R236 static check", "real hardware run")):
        fail("conformance matrix does not describe current profile-check and hardware status")


HARDWARE_ONLY = {"partitioning.hardware", "monitoring.utilization", "monitoring.memory", "monitoring.thermal", "monitoring.power", "health.device", "isolation.compute-memory", "isolation.security-boundary", "storage.fast-path.gds", "network.fast-path.collective", "network.fast-path.rdma"}


def check_profiles(profile_dir):
    text = DOC.read_text()
    validate_matrix_doc(text)
    backends = {
        "Apple Silicon / KubeMetal reference": "apple-metal-mps",
        "NVIDIA Linux": "nvidia-linux",
        "AMD Linux class": "amd-linux",
        "Intel Linux class": "intel-linux",
        "Other accelerator": "other",
    }
    matrix_rows = [name for name in backends if re.search(r"^\| " + re.escape(name) + r" \|", text, re.M)]
    if len(matrix_rows) != len(backends):
        fail("backend matrix rows could not be fully parsed")
    capabilities = set(re.findall(r"^\| `([a-z][a-z0-9.-]+)` \|", text, re.M))
    capabilities = {name for name in capabilities if "." in name and name.split(".")[0] in {"inventory", "allocation", "partitioning", "sharing", "monitoring", "health", "isolation", "storage", "network", "benchmark", "offline"}}
    if not capabilities:
        fail("capability taxonomy could not be parsed")
    schema_path = profile_dir / "profile.schema.json"
    schema = json.loads(schema_path.read_text())
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        fail("unsupported/malformed schema")
    paths = sorted(path for path in profile_dir.glob("*.json") if path.name != schema_path.name)
    if not paths:
        fail("zero profile files evaluated")
    found = set()
    for path in paths:
        try:
            profile = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            fail(f"unreadable profile {path.name}: {exc}")
        validate(profile, set(backends.values()), capabilities, schema)
        if profile["backend"] in found:
            fail(f"duplicate backend profile: {profile['backend']}")
        found.add(profile["backend"])
    validate_backend_set(found, set(backends.values()))
    return len(paths), len(found), len(matrix_rows), len(capabilities)


def main(profile_dir=PROFILE_DIR):
    counts = check_profiles(profile_dir)
    if "--mutation-verify" in sys.argv:
        with tempfile.TemporaryDirectory(prefix="accelerator-profile-mutation-") as temporary:
            temp_dir = pathlib.Path(temporary) / "profiles"
            shutil.copytree(profile_dir, temp_dir)
            sample_path = next(path for path in temp_dir.glob("*.json") if path.name != SCHEMA.name)
            original = json.loads(sample_path.read_text())
            mutants = []
            missing = copy.deepcopy(original); missing.pop("backendVersion"); mutants.append(("required", missing))
            invalid = copy.deepcopy(original); invalid["capabilities"][next(iter(original["capabilities"]))]["status"] = "bogus"; mutants.append(("enum", invalid))
            hardware = copy.deepcopy(original)
            hardware["capabilities"]["partitioning.hardware"].update(status="supported", evidenceKind="static")
            mutants.append(("hardware-evidence", hardware))
            schema_mutation = json.loads((temp_dir / SCHEMA.name).read_text())
            schema_mutation["properties"]["backend"]["enum"] = ["not-a-backend"]
            mutants.append(("schema", schema_mutation))
            empty_version = copy.deepcopy(original); empty_version["backendVersion"] = ""; mutants.append(("min-length", empty_version))
            unsupported_schema = json.loads((temp_dir / SCHEMA.name).read_text())
            unsupported_schema["unsupportedKeyword"] = True
            mutants.append(("unsupported-keyword", unsupported_schema))
            for label, mutant in mutants:
                shutil.rmtree(temp_dir)
                shutil.copytree(profile_dir, temp_dir)
                if label in {"schema", "unsupported-keyword"}:
                    (temp_dir / SCHEMA.name).write_text(json.dumps(mutant))
                else:
                    sample_path = next(path for path in temp_dir.glob("*.json") if path.name != SCHEMA.name)
                    sample_path.write_text(json.dumps(mutant))
                try:
                    check_profiles(temp_dir)
                except ValueError:
                    continue
                fail(f"mutation was not detected: {label}")
            try:
                validate_matrix_doc(DOC.read_text().replace("five backend-profile fixtures", "no accelerator schema"))
            except ValueError:
                pass
            else:
                fail("mutation was not detected: obsolete-document-status")
            mutants.append(("obsolete-document-status", None))
        print(f"mutation verify: {len(mutants)}/{len(mutants)} rejected through profile directory")
    print(f"profiles evaluated: {counts[0]}; backends: {counts[1]}/{counts[2]}; capabilities: {counts[3]}; profile entries: {counts[0] * counts[3]}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as exc:
        print(f"accelerator profile check: {exc}", file=sys.stderr)
        sys.exit(1)
