#!/usr/bin/env python3
"""Run CI-only portal snapshot and behavior trace gates in the local static suite."""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
VERSION_TRACE = ".agents/evals/traces/2026-08-version-consistency.json"


def run(root, mutation=False):
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node is required for R414 (portal OSS versions snapshot); install Node.js")
    command = [node, "portal/scripts/gen-oss-versions.mjs", "--check"]
    result = subprocess.run(command, cwd=root, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError("portal OSS versions check failed:\n" + result.stdout + result.stderr)

    baseline = root / ".agents/evals/baseline.eval.json"
    gate = root / ".agents/evals/gate.py"
    traces = sorted((root / ".agents/evals/traces").glob("*.json"))
    if not traces:
        raise RuntimeError("behavior-contract found no .agents/evals/traces/*.json")
    with tempfile.TemporaryDirectory(prefix="narwhal-r414-") as tmp:
        tmp = Path(tmp)
        for index, trace in enumerate(traces):
            if trace.relative_to(root).as_posix() == VERSION_TRACE:
                continue
            result = subprocess.run([
                sys.executable, str(gate), "--trace", str(trace), "--baseline", str(baseline),
                "--current-out", str(tmp / f"current-{index}.json"),
                "--comparison-out", str(tmp / f"comparison-{index}.json"),
            ], cwd=root, text=True, capture_output=True)
            if result.returncode:
                raise RuntimeError(f"behavior-contract gate failed for {trace.name}:\n{result.stdout}{result.stderr}")
        # The version trace is bound to a live consistency command in CI and intentionally
        # excluded here, matching the workflow's explicit skip for the checked-in trace.
    return len(traces) - sum(t.relative_to(root).as_posix() == VERSION_TRACE for t in traces)


def mutate_and_check():
    with tempfile.TemporaryDirectory(prefix="narwhal-r414-mutation-") as tmp:
        clone = Path(tmp) / "repo"
        clone.mkdir()
        for name in (".agents/evals", "portal/scripts", "portal/src/data"):
            shutil.copytree(ROOT / name, clone / name)
        shutil.copy2(ROOT / "VERSIONS.md", clone / "VERSIONS.md")
        trace = next(
            path
            for path in sorted((clone / ".agents/evals/traces").glob("*.json"))
            if path.relative_to(clone).as_posix() != VERSION_TRACE
        )
        data = json.loads(trace.read_text())
        data["events"] = [e for e in data.get("events", []) if e.get("type") != "completion_claim"]
        trace.write_text(json.dumps(data))
        try:
            run(clone)
        except RuntimeError as exc:
            if "gate failed" not in str(exc):
                raise
        else:
            raise AssertionError("behavior gate accepted trace without completion_claim")

        # Restore traces and break only the generated snapshot in the disposable copy.
        shutil.rmtree(clone / ".agents/evals/traces")
        shutil.copytree(ROOT / ".agents/evals/traces", clone / ".agents/evals/traces")
        generated = clone / "portal/src/data/oss-versions.generated.json"
        generated.write_text(generated.read_text() + " ")
        try:
            run(clone)
        except RuntimeError as exc:
            if "versions check failed" not in str(exc):
                raise
        else:
            raise AssertionError("portal version check accepted mismatched generated JSON")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mutation-verify", action="store_true")
    args = parser.parse_args()
    try:
        count = run(ROOT)
        if args.mutation_verify:
            mutate_and_check()
            print(f"PASS: R414 CI parity, {count} behavior traces; 2 mutations rejected")
        else:
            print(f"PASS: R414 CI parity, portal snapshot and {count} behavior traces")
    except (RuntimeError, AssertionError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
