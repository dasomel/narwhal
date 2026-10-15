import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("cww", ROOT / "scripts/ci/check-workflow-workdir.py")
cww = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cww)

CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"


def wf(steps: str, defaults: bool = True) -> str:
    d = "    defaults:\n      run:\n        working-directory: portal\n" if defaults else ""
    return f"name: t\non: push\njobs:\n  j:\n    runs-on: ubuntu-latest\n{d}    steps:\n{steps}"


class WorkdirCheck(unittest.TestCase):
    def check(self, text: str) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "portal-x.yml"
            p.write_text(text, encoding="utf-8")
            return cww.problems(p)

    def test_real_portal_workflows_pass(self):
        for f in sorted((ROOT / ".github/workflows").glob("portal-*.yml")):
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


if __name__ == "__main__":
    unittest.main()
