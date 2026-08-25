#!/usr/bin/env python3
"""Minimal offline Narwhal control-plane CLI contract validator."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from jsonschema import Draft202012Validator, FormatChecker
    from jsonschema.exceptions import SchemaError
except ImportError:
    print(
        "ERROR: narwhalctl requires the Python 'jsonschema' package; install the repo's "
        "validated tooling before running this offline contract check.",
        file=sys.stderr,
    )
    sys.exit(2)


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA = REPOSITORY_ROOT / "schemas" / "event-envelope-1.0.schema.json"


def read_json(path: Path, description: str) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise ValueError(f"{description} not found: {path}") from None
    except OSError as exc:
        raise ValueError(f"{description} cannot be read: {path}: {exc.strerror}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"{description} is not valid JSON: {path}: {exc.msg}") from None


def json_path(error_path: Any) -> str:
    return "$" + "".join(f"[{item}]" if isinstance(item, int) else f".{item}" for item in error_path)


def declared_schema_version(schema: Any) -> str:
    if isinstance(schema, dict):
        properties = schema.get("properties")
        if isinstance(properties, dict):
            schema_version = properties.get("schema_version")
            if isinstance(schema_version, dict) and isinstance(schema_version.get("const"), str):
                return schema_version["const"]
    return "unspecified"


def validate_event(event_file: Path, schema_file: Path) -> int:
    try:
        schema = read_json(schema_file, "schema")
        event = read_json(event_file, "event")
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
    except SchemaError as exc:
        print(f"ERROR: invalid schema: {schema_file}: {exc.message}", file=sys.stderr)
        return 2
    errors = sorted(validator.iter_errors(event), key=lambda error: list(error.absolute_path))
    if errors:
        error = errors[0]
        print(
            f"ERROR: event validation failed at {json_path(error.absolute_path)}: {error.message}",
            file=sys.stderr,
        )
        return 1

    print(
        f"VALID: {event_file} conforms to schema {schema_file} "
        f"(schema_version {declared_schema_version(schema)})"
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="narwhalctl")
    commands = parser.add_subparsers(dest="command", required=True)
    events = commands.add_parser("events", help="offline canonical event contract commands")
    event_commands = events.add_subparsers(dest="event_command", required=True)
    emit = event_commands.add_parser("emit", help="validate an event without sending it")
    emit.add_argument("--file", required=True, type=Path, help="event JSON file to validate")
    emit.add_argument(
        "--schema",
        default=DEFAULT_SCHEMA,
        type=Path,
        help="Draft 2020-12 schema path (default: repository canonical schema)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "events" and args.event_command == "emit":
        return validate_event(args.file, args.schema)
    raise AssertionError("argparse accepted an unsupported command")


if __name__ == "__main__":
    sys.exit(main())
