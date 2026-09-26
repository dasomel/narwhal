import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/research/run-recorded.py"
SCHEMA = ROOT / "research/schemas/evidence-1.0.schema.json"


class RunRecordedTests(unittest.TestCase):
    def run_recorded(self, exit_code: int) -> tuple[subprocess.CompletedProcess[str], dict]:
        with tempfile.TemporaryDirectory() as evidence_dir:
            environment = os.environ.copy()
            environment["RESEARCH_EVIDENCE_DIR"] = evidence_dir
            command = [
                sys.executable,
                str(SCRIPT),
                "--task",
                "research-recorder-unit-tests",
                "--event-type",
                "test",
                "--",
                sys.executable,
                "-c",
                f"raise SystemExit({exit_code})",
                "do-not-record-this-argument",
            ]
            process = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True)
            files = list(pathlib.Path(evidence_dir).glob("*.jsonl"))
            self.assertEqual(len(files), 1)
            rows = files[0].read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rows), 1)
            return process, json.loads(rows[0])

    def test_success_is_recorded_without_command_arguments(self) -> None:
        process, record = self.run_recorded(0)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(record["result"], "pass")
        self.assertGreaterEqual(record["duration_ms"], 0)
        self.assertNotIn("do-not-record-this-argument", json.dumps(record))
        self.assertEqual(record["schema_version"], "1.0")

    def test_failure_is_recorded_and_exit_code_is_preserved(self) -> None:
        process, record = self.run_recorded(7)
        self.assertEqual(process.returncode, 7)
        self.assertEqual(record["result"], "fail")
        self.assertEqual(record["metadata"]["exit_code"], 7)

    def test_schema_and_writer_have_the_same_top_level_fields(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        _, record = self.run_recorded(0)
        self.assertEqual(set(schema["required"]), set(record))
        self.assertTrue(schema["additionalProperties"] is False)

    def test_agent_task_requires_observed_intervention_counts(self) -> None:
        process = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--task",
                "agent-task",
                "--event-type",
                "agent_task",
                "--",
                sys.executable,
                "-c",
                "pass",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(process.returncode, 2)
        self.assertIn("require measured", process.stderr)

    def test_only_summary_counts_are_copied_from_regression_report(self) -> None:
        with tempfile.TemporaryDirectory() as evidence_dir:
            report_path = pathlib.Path(evidence_dir) / "report.json"
            report_path.write_text(
                json.dumps(
                    {
                        "script": "scripts/test/regression-check-kakao.sh",
                        "summary": {"pass": 2, "fail": 1, "warn": 3, "total": 6},
                        "checks": [{"description": "token=do-not-publish-this-value"}],
                    }
                ),
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["RESEARCH_EVIDENCE_DIR"] = evidence_dir
            process = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--task",
                    "regression-static",
                    "--event-type",
                    "test",
                    "--test-report",
                    str(report_path),
                    "--",
                    sys.executable,
                    "-c",
                    "pass",
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
            )
            record_path = next(pathlib.Path(evidence_dir).glob("*.jsonl"))
            contents = record_path.read_text(encoding="utf-8")
            record = json.loads(contents)
        self.assertEqual(process.returncode, 1)
        self.assertEqual(record["result"], "fail")
        self.assertEqual(record["metadata"]["pass_count"], 2)
        self.assertEqual(record["metadata"]["fail_count"], 1)
        self.assertEqual(record["metadata"]["warning_count"], 3)
        self.assertNotIn("do-not-publish-this-value", contents)


if __name__ == "__main__":
    unittest.main()
