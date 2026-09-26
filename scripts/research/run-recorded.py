#!/usr/bin/env python3
"""Run one command and append a secret-safe OpenForge evidence record."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import platform
import re
import subprocess
import sys
import time

REPOSITORY = "dasomel/narwhal"
PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
TASK_EVENTS = {
    "regression-static": "test",
    "research-recorder-unit-tests": "test",
    "cluster-install": "install",
    "cluster-deploy": "deploy",
    "cluster-verification": "test",
    "cluster-recovery": "recovery",
    "runtime-measurement": "runtime",
    "agent-task": "agent_task",
    "release": "release",
    "benchmark": "benchmark",
}
SECRET_PATTERN = re.compile(
    r"(?i)(bearer\s+[a-z0-9._~+/-]{12,}|gh[pousr]_[a-z0-9]{20,}|"
    r"(?:password|token|secret|api[_-]?key)\s*[:=]\s*[\"']?[^\s,\"']{8,}|"
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
)


def environment_label() -> tuple[str, str]:
    system = {"linux": "linux", "darwin": "macos", "windows": "windows"}.get(
        platform.system().lower(), "other"
    )
    machine = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(
        platform.machine().lower(), "other"
    )
    if os.environ.get("GITHUB_ACTIONS") == "true":
        runner_os = {"linux": "linux", "macos": "macos", "windows": "windows"}.get(
            os.environ.get("RUNNER_OS", "").lower(), "other"
        )
        runner_arch = {"x64": "x64", "arm64": "arm64"}.get(
            os.environ.get("RUNNER_ARCH", "unknown").lower(), "unknown"
        )
        return f"github-actions/{runner_os}-{runner_arch}", f"{system}/{machine}"
    return f"local/{system}-{machine}", f"{system}/{machine}"


def git_revision_and_dirty() -> tuple[str | None, bool]:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip() != ""
        return revision, dirty
    except (OSError, subprocess.CalledProcessError):
        return None, False


def evidence_path() -> pathlib.Path:
    configured = os.environ.get("RESEARCH_EVIDENCE_DIR")
    base = pathlib.Path(configured) if configured else PROJECT_ROOT / "research/evidence"
    return base / f"{dt.datetime.now(dt.timezone.utc):%Y-%m}.jsonl"


def append_record(record: dict[str, object], path: pathlib.Path) -> None:
    validate_public_record(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        remaining = memoryview(line.encode("utf-8"))
        while remaining:
            written = os.write(fd, remaining)
            remaining = remaining[written:]
    finally:
        os.close(fd)


def regression_summary(path: pathlib.Path | None) -> dict[str, object]:
    if path is None:
        return {"summary_available": False}
    if not path.is_file():
        raise ValueError("requested regression report is missing")
    report = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(report, dict) or report.get("script") != "scripts/test/regression-check-kakao.sh":
        raise ValueError("unrecognized regression report")
    summary = report.get("summary")
    if not isinstance(summary, dict):
        raise ValueError("regression report has no summary")
    counts: dict[str, object] = {"summary_available": True}
    for source, target in (("pass", "pass_count"), ("fail", "fail_count"), ("warn", "warning_count")):
        count = summary.get(source)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError("regression report contains an invalid count")
        counts[target] = count
    return counts


def validate_public_record(record: dict[str, object]) -> None:
    schema_path = pathlib.Path(__file__).resolve().parents[2] / "research/schemas/evidence-1.0.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    required = set(schema["required"])
    if set(record) != required:
        raise ValueError("evidence record fields do not match the versioned schema")
    if record["schema_version"] != "1.0" or record["repository"] != REPOSITORY:
        raise ValueError("evidence schema/repository identity is invalid")
    if record["event_type"] not in {"build", "test", "install", "deploy", "runtime", "recovery", "agent_task", "release", "benchmark"}:
        raise ValueError("evidence event_type is invalid")
    if record["result"] not in {"pass", "fail", "partial", "cancelled", "skipped"}:
        raise ValueError("evidence result is invalid")
    if not isinstance(record["timestamp"], str) or not re.fullmatch(
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z", record["timestamp"]
    ):
        raise ValueError("evidence timestamp must be UTC ISO-8601")
    for name in ("duration_ms", "attempt", "human_interventions", "review_corrections", "ci_retries"):
        if not isinstance(record[name], int) or isinstance(record[name], bool):
            raise ValueError(f"evidence {name} must be an integer")
    if record["duration_ms"] < 0 or record["attempt"] < 1 or any(
        record[name] < 0 for name in ("human_interventions", "review_corrections", "ci_retries")
    ):
        raise ValueError("evidence counters are out of range")
    for name in ("task_or_test", "environment"):
        if not isinstance(record[name], str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9._/-]{0,79}", record[name]
        ):
            raise ValueError(f"evidence {name} must be a safe normalized label")
    if record["task_or_test"] not in TASK_EVENTS or TASK_EVENTS[record["task_or_test"]] != record["event_type"]:
        raise ValueError("evidence task label and event type do not match the allowlist")
    revision = record["revision"]
    if revision is not None and (not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision)):
        raise ValueError("evidence revision must be a full lowercase Git SHA or null")
    metadata = record["metadata"]
    if not isinstance(metadata, dict) or not set(metadata) <= set(schema["properties"]["metadata"]["properties"]):
        raise ValueError("evidence metadata has fields outside the public allowlist")
    if not isinstance(metadata.get("working_tree_dirty"), bool):
        raise ValueError("evidence dirty-tree state must be boolean")
    if metadata.get("exit_code") is not None and (
        not isinstance(metadata["exit_code"], int) or isinstance(metadata["exit_code"], bool)
    ):
        raise ValueError("evidence exit code must be an integer or null")
    if not isinstance(metadata.get("platform"), str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9._/-]{0,63}", metadata["platform"]
    ):
        raise ValueError("evidence platform must be a normalized label")
    for name in ("pass_count", "fail_count", "skip_count", "warning_count", "cpu_time_ms", "peak_memory_mib", "storage_bytes"):
        if name in metadata and (
            not isinstance(metadata[name], int) or isinstance(metadata[name], bool) or metadata[name] < 0
        ):
            raise ValueError(f"evidence {name} must be a non-negative integer")
    if "summary_available" in metadata and not isinstance(metadata["summary_available"], bool):
        raise ValueError("evidence summary availability must be boolean")
    if "artifact_digest" in metadata and not re.fullmatch(
        r"sha256:[0-9a-f]{64}", str(metadata["artifact_digest"])
    ):
        raise ValueError("evidence artifact digest is invalid")
    failure_stages = {
        "download", "validation", "provision", "install", "deploy", "health-check", "rollback",
        "recovery", "cleanup", "timeout", "authorization", "verification", "other",
    }
    if "failure_stage" in metadata and metadata["failure_stage"] not in failure_stages:
        raise ValueError("evidence failure stage is invalid")
    if metadata.get("recovery_result") not in {None, "recovered", "not_recovered", "not_applicable"}:
        raise ValueError("evidence recovery result is invalid")
    serialized = json.dumps(record, sort_keys=True)
    if SECRET_PATTERN.search(serialized):
        raise ValueError("public evidence secret-pattern check failed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, choices=sorted(TASK_EVENTS), help="controlled task label")
    parser.add_argument("--event-type", required=True, choices=(
        "build", "test", "install", "deploy", "runtime", "recovery", "agent_task", "release", "benchmark"
    ))
    parser.add_argument("--attempt", type=int, help="measured attempt number; defaults to GitHub run attempt or 1")
    parser.add_argument("--human-interventions", type=int)
    parser.add_argument("--review-corrections", type=int)
    parser.add_argument("--ci-retries", type=int, help="defaults to GitHub run attempt minus one, or 0 locally")
    parser.add_argument("--pass-count", type=int)
    parser.add_argument("--fail-count", type=int)
    parser.add_argument("--skip-count", type=int)
    parser.add_argument("--cpu-time-ms", type=int)
    parser.add_argument("--peak-memory-mib", type=int)
    parser.add_argument("--storage-bytes", type=int)
    parser.add_argument("--artifact-digest", help="sha256:<64 lowercase hex digits>")
    parser.add_argument(
        "--failure-stage",
        choices=("download", "validation", "provision", "install", "deploy", "health-check", "rollback", "recovery", "cleanup", "timeout", "authorization", "verification", "other"),
    )
    parser.add_argument("--recovery-result", choices=("recovered", "not_recovered", "not_applicable"))
    parser.add_argument("--test-report", type=pathlib.Path, help="regression JSON report; only summary counts are copied")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="command argv after --; never stored")
    args = parser.parse_args()
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("provide a command after --")
    if args.event_type == "agent_task" and (
        args.human_interventions is None or args.review_corrections is None
    ):
        parser.error("agent_task records require measured --human-interventions and --review-corrections")
    if args.human_interventions is None:
        args.human_interventions = 0
    if args.review_corrections is None:
        args.review_corrections = 0
    github_attempt = int(os.environ.get("GITHUB_RUN_ATTEMPT", "1"))
    args.attempt = args.attempt if args.attempt is not None else github_attempt
    args.ci_retries = args.ci_retries if args.ci_retries is not None else max(github_attempt - 1, 0)
    for name in ("attempt", "human_interventions", "review_corrections", "ci_retries"):
        value = getattr(args, name)
        if value < (1 if name == "attempt" else 0):
            parser.error(f"--{name.replace('_', '-')} must be a non-negative count (attempt starts at 1)")
    for name in (
        "pass_count", "fail_count", "skip_count", "cpu_time_ms", "peak_memory_mib", "storage_bytes"
    ):
        value = getattr(args, name)
        if value is not None and value < 0:
            parser.error(f"--{name.replace('_', '-')} must be non-negative")
    if args.artifact_digest and not re.fullmatch(r"sha256:[0-9a-f]{64}", args.artifact_digest):
        parser.error("--artifact-digest must use sha256:<64 lowercase hex digits>")
    return args


def main() -> int:
    args = parse_args()
    start = time.monotonic_ns()
    timestamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    label, host_platform = environment_label()
    revision, dirty = git_revision_and_dirty()
    try:
        completed = subprocess.run(args.command, check=False)
        exit_code = completed.returncode
        result = "pass" if exit_code == 0 else "fail"
    except KeyboardInterrupt:
        exit_code = None
        result = "cancelled"
    except OSError as error:
        print(f"could not start recorded command: {error.strerror}", file=sys.stderr)
        exit_code = 127
        result = "fail"
    report_error = False
    try:
        summary_metadata = regression_summary(args.test_report)
        if summary_metadata.get("fail_count", 0) > 0:
            result = "fail"
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        summary_metadata = {"summary_available": False}
        report_error = True
        result = "fail"
    duration_ms = (time.monotonic_ns() - start) // 1_000_000
    metadata: dict[str, object] = {
        "exit_code": exit_code,
        "working_tree_dirty": dirty,
        "platform": host_platform,
        **summary_metadata,
    }
    if report_error:
        metadata["failure_stage"] = "validation"
    for name in (
        "pass_count", "fail_count", "skip_count", "cpu_time_ms", "peak_memory_mib", "storage_bytes",
        "artifact_digest", "failure_stage", "recovery_result",
    ):
        value = getattr(args, name)
        if value is not None:
            metadata[name] = value
    record = {
        "schema_version": "1.0",
        "timestamp": timestamp,
        "repository": REPOSITORY,
        "revision": revision,
        "event_type": args.event_type,
        "task_or_test": args.task,
        "result": result,
        "duration_ms": duration_ms,
        "environment": label,
        "attempt": args.attempt,
        "human_interventions": args.human_interventions,
        "review_corrections": args.review_corrections,
        "ci_retries": args.ci_retries,
        "metadata": metadata,
    }
    path = evidence_path()
    append_record(record, path)
    print(f"research evidence: {path} ({result}, {duration_ms} ms)", file=sys.stderr)
    if result == "fail" and exit_code == 0:
        return 1
    return exit_code if exit_code is not None else 130


if __name__ == "__main__":
    raise SystemExit(main())
