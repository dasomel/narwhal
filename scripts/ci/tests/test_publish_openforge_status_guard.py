import re
import unittest
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[3] / ".github/workflows/publish-openforge-status.yml"


class StatusPublishWorkflowRunGuard(unittest.TestCase):
    """workflow_run matches the upstream workflow by NAME only: a fork PR can add its own
    workflow named "Release" on pull_request, and its completion would hand this job
    OPENFORGE_STATUS_TOKEN. The guard must bind the run to a tag push in this repository."""

    @classmethod
    def setUpClass(cls):
        cls.job = yaml.safe_load(WORKFLOW.read_text())["jobs"]["publish"]
        cls.cond = re.sub(r"\s+", " ", cls.job["if"])

    def test_workflow_run_requires_push_event(self):
        self.assertIn("github.event.workflow_run.event == 'push'", self.cond)

    def test_workflow_run_requires_same_repository(self):
        self.assertIn("github.event.workflow_run.head_repository.full_name == github.repository", self.cond)

    def test_workflow_run_requires_release_tag_name(self):
        self.assertIn("startsWith(github.event.workflow_run.head_branch, 'v')", self.cond)

    def test_workflow_run_requires_success(self):
        self.assertIn("github.event.workflow_run.conclusion == 'success'", self.cond)

    def test_dispatch_is_the_only_unguarded_path(self):
        dispatch, sep, rest = self.cond.partition(" || ")
        self.assertEqual(dispatch.strip(), "github.event_name == 'workflow_dispatch'")
        self.assertTrue(sep)
        self.assertNotIn("||", rest)


if __name__ == "__main__":
    unittest.main()
