import importlib.util, unittest
from pathlib import Path

SCRIPT_PATH = str(Path(__file__).resolve().parent.parent / "check-independent-review.py")


def _load_module():
  spec = importlib.util.spec_from_file_location("check_independent_review_mod", SCRIPT_PATH)
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


M = _load_module()


def pr(draft=False, labels=("review:pass",)):
  return {"draft": draft, "labels": [{"name": n} for n in labels]}


def commit(date, sha="abcdef1234"):
  return {"sha": sha, "commit": {"committer": {"date": date}}}


def labeled(at, name="review:pass", login="reviewer"):
  return {"event": "labeled", "created_at": at, "label": {"name": name}, "actor": {"login": login}}


def unlabeled(at, name="review:pass"):
  return {"event": "unlabeled", "created_at": at, "label": {"name": name}}


def force_push(at):
  return {"event": "head_ref_force_pushed", "created_at": at}


class EvaluateTest(unittest.TestCase):
  def test_pass_label_after_last_commit(self):
    ok, reason = M.evaluate(pr(), [commit("2026-10-01T10:00:00Z")], [labeled("2026-10-01T11:00:00Z")])
    self.assertTrue(ok, reason)
    self.assertIn("reviewer", reason)

  def test_fail_no_label(self):
    ok, reason = M.evaluate(pr(labels=("bug",)), [commit("2026-10-01T10:00:00Z")], [labeled("2026-10-01T11:00:00Z")])
    self.assertFalse(ok)
    self.assertIn("not on the PR", reason)

  def test_fail_stale_label_older_than_newest_commit(self):
    commits = [commit("2026-10-01T10:00:00Z"), commit("2026-10-01T12:00:00Z", "b" * 10)]
    ok, reason = M.evaluate(pr(), commits, [labeled("2026-10-01T11:00:00Z")])
    self.assertFalse(ok)
    self.assertIn("NOT after", reason)

  def test_fail_equal_timestamp_is_strict(self):
    ok, _ = M.evaluate(pr(), [commit("2026-10-01T10:00:00Z")], [labeled("2026-10-01T10:00:00Z")])
    self.assertFalse(ok)

  def test_pass_label_removed_then_readded_after_push(self):
    commits = [commit("2026-10-01T10:00:00Z"), commit("2026-10-01T13:00:00Z", "b" * 10)]
    timeline = [labeled("2026-10-01T11:00:00Z"), unlabeled("2026-10-01T13:30:00Z"), labeled("2026-10-01T14:00:00Z", login="second")]
    ok, reason = M.evaluate(pr(), commits, timeline)
    self.assertTrue(ok, reason)
    self.assertIn("second", reason)

  def test_fail_label_not_readded_after_push(self):
    commits = [commit("2026-10-01T10:00:00Z"), commit("2026-10-01T13:00:00Z", "b" * 10)]
    ok, _ = M.evaluate(pr(), commits, [labeled("2026-10-01T11:00:00Z")])
    self.assertFalse(ok)

  def test_fail_draft(self):
    ok, reason = M.evaluate(pr(draft=True), [commit("2026-10-01T10:00:00Z")], [labeled("2026-10-01T11:00:00Z")])
    self.assertFalse(ok)
    self.assertIn("draft", reason)

  def test_fail_force_push_after_label(self):
    timeline = [labeled("2026-10-01T11:00:00Z"), force_push("2026-10-01T12:00:00Z")]
    ok, reason = M.evaluate(pr(), [commit("2026-10-01T10:00:00Z")], timeline)
    self.assertFalse(ok)
    self.assertIn("force-push", reason)

  def test_fail_closed_empty_timeline(self):
    ok, reason = M.evaluate(pr(), [commit("2026-10-01T10:00:00Z")], [])
    self.assertFalse(ok)
    self.assertIn("no 'labeled' event", reason)

  def test_fail_closed_malformed_data(self):
    self.assertFalse(M.evaluate(None, [], [])[0])
    self.assertFalse(M.evaluate(pr(), [], [labeled("2026-10-01T11:00:00Z")])[0])
    self.assertFalse(M.evaluate(pr(), [{"sha": "x"}], [labeled("2026-10-01T11:00:00Z")])[0])
    self.assertFalse(M.evaluate(pr(), [commit("garbage")], [labeled("2026-10-01T11:00:00Z")])[0])
    self.assertFalse(M.evaluate({"labels": [{"name": "review:pass"}]}, [commit("2026-10-01T10:00:00Z")], [labeled("2026-10-01T11:00:00Z")])[0])

  def test_fail_other_label_event_does_not_count(self):
    ok, _ = M.evaluate(pr(), [commit("2026-10-01T10:00:00Z")], [labeled("2026-10-01T11:00:00Z", name="other")])
    self.assertFalse(ok)


class PaginationTest(unittest.TestCase):
  def test_concatenated_pages_are_merged(self):
    self.assertEqual(M.parse_concatenated_json('[{"a":1}]\n[{"a":2}]'), [{"a": 1}, {"a": 2}])

  def test_single_object(self):
    self.assertEqual(M.parse_concatenated_json('{"draft": false}'), {"draft": False})

  def test_empty_pages(self):
    self.assertEqual(M.parse_concatenated_json("[]"), [])


if __name__ == "__main__":
  unittest.main()
