import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("cww", Path(os.environ.get("CWW_SCRIPT") or ROOT / "scripts/ci/check-workflow-workdir.py"))  # CWW_SCRIPT: mutation runs against an old copy
cww = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cww)

CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"


def wf(steps: str, defaults: bool = True, top: bool = False) -> str:
    """One job `j`; the working-directory default sits on the job, or at workflow level when top=True."""
    d = "    defaults:\n      run:\n        working-directory: portal\n" if defaults and not top else ""
    t = "defaults:\n  run:\n    working-directory: portal\n" if defaults and top else ""
    return f"name: t\non: push\n{t}jobs:\n  j:\n    runs-on: ubuntu-latest\n{d}    steps:\n{steps}"


class WorkdirCheck(unittest.TestCase):
    def check(self, text: str) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "portal-x.yml"
            p.write_text(text, encoding="utf-8")
            return cww.problems(p)

    def test_all_workflows_pass(self):
        # every workflow, not just portal-*: a future non-portal workflow with a default is covered too
        files = sorted((ROOT / ".github/workflows").glob("*.yml"))
        self.assertTrue(any(f.name.startswith("portal-") for f in files))
        for f in files:
            self.assertEqual(cww.problems(f), [], f.name)

    def test_unconditional_checkout_first_passes(self):
        self.assertEqual(self.check(wf(f"      - uses: step-security/harden-runner@x\n      - uses: {CHECKOUT}\n      - run: echo hi\n")), [])

    def test_conditional_checkout_fails(self):
        # the PR #347 regression-gate shape
        out = self.check(wf(f"      - uses: {CHECKOUT}\n        if: needs.changes.outputs.portal == 'true'\n      - run: echo hi\n"))
        self.assertTrue(any("conditional" in x for x in out), out)

    def test_run_before_checkout_fails(self):
        out = self.check(wf(f"      - run: echo early\n      - uses: {CHECKOUT}\n"))
        self.assertTrue(any("before checkout" in x for x in out), out)

    def test_no_checkout_fails(self):
        out = self.check(wf("      - run: echo hi\n"))
        self.assertTrue(any("no actions/checkout" in x for x in out), out)

    def test_no_default_is_ignored(self):
        self.assertEqual(self.check(wf("      - run: echo hi\n", defaults=False)), [])

    # (a) workflow-level default applies to every job
    def test_workflow_level_default_conditional_checkout_fails(self):
        out = self.check(wf(f"      - uses: {CHECKOUT}\n        if: github.event_name == 'push'\n      - run: echo hi\n", top=True))
        self.assertTrue(any("conditional" in x for x in out), out)

    def test_workflow_level_default_no_checkout_fails(self):
        out = self.check(wf("      - run: echo hi\n", top=True))
        self.assertTrue(any("no actions/checkout" in x for x in out), out)

    def test_workflow_level_default_with_checkout_passes(self):
        self.assertEqual(self.check(wf(f"      - uses: {CHECKOUT}\n      - run: echo hi\n", top=True)), [])

    def test_job_default_overrides_workflow_default(self):
        text = (
            "name: t\non: push\ndefaults:\n  run:\n    working-directory: portal\n"
            "jobs:\n  j:\n    runs-on: ubuntu-latest\n    defaults:\n      run:\n        working-directory: .\n"
            "    steps:\n      - run: echo hi\n"
        )
        self.assertEqual(self.check(text), [])

    # (b) first checkout conditional, a later unconditional one exists
    def test_conditional_then_unconditional_checkout_passes(self):
        steps = f"      - uses: {CHECKOUT}\n        if: x\n      - uses: {CHECKOUT}\n      - run: echo hi\n"
        self.assertEqual(self.check(wf(steps)), [])

    def test_run_between_conditional_and_unconditional_checkout_fails(self):
        steps = f"      - uses: {CHECKOUT}\n        if: x\n      - run: echo mid\n      - uses: {CHECKOUT}\n"
        out = self.check(wf(steps))
        self.assertTrue(any("before checkout" in x for x in out), out)

    def test_local_action_before_checkout_fails(self):
        out = self.check(wf(f"      - uses: ./.github/actions/thing\n      - uses: {CHECKOUT}\n"))
        self.assertTrue(any("local action" in x for x in out), out)

    # (c) checkout into a subpath does not create the default working directory
    def test_checkout_into_other_path_fails(self):
        out = self.check(wf(f"      - uses: {CHECKOUT}\n        with:\n          path: elsewhere\n      - run: echo hi\n"))
        self.assertTrue(any("with.path 'elsewhere'" in x for x in out), out)

    def test_checkout_path_equal_to_workdir_passes(self):
        self.assertEqual(self.check(wf(f"      - uses: {CHECKOUT}\n        with:\n          path: portal\n      - run: echo hi\n")), [])

    def test_checkout_path_dot_passes(self):
        self.assertEqual(self.check(wf(f"      - uses: {CHECKOUT}\n        with:\n          path: .\n      - run: echo hi\n")), [])

    def test_other_path_checkout_then_root_checkout_passes(self):
        steps = f"      - uses: {CHECKOUT}\n        with:\n          path: trusted\n      - uses: {CHECKOUT}\n      - run: echo hi\n"
        self.assertEqual(self.check(wf(steps)), [])

    # (d) a step with its own working-directory is unaffected by the default
    def test_step_level_override_before_checkout_passes(self):
        steps = f"      - run: echo early\n        working-directory: .\n      - uses: {CHECKOUT}\n      - run: echo hi\n"
        self.assertEqual(self.check(wf(steps)), [])

    def test_step_without_override_before_checkout_still_fails(self):
        steps = f"      - run: echo early\n        working-directory: .\n      - run: echo bad\n      - uses: {CHECKOUT}\n"
        out = self.check(wf(steps))
        self.assertEqual(len([x for x in out if "before checkout" in x]), 1, out)


if __name__ == "__main__":
    unittest.main()
