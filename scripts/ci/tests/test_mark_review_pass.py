import importlib.util, unittest
from pathlib import Path

SCRIPT_PATH = str(Path(__file__).resolve().parent.parent / "mark-review-pass.py")


def _load_module():
  spec = importlib.util.spec_from_file_location("mark_review_pass_mod", SCRIPT_PATH)
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


M = _load_module()
SHA = "a" * 40


def pr(**kw):
  d = {"headRefOid": SHA, "isDraft": False, "state": "OPEN"}
  d.update(kw)
  return d


class PlanStatusTest(unittest.TestCase):
  def test_open_ready_matching_sha_ok(self):
    self.assertEqual(M.plan_status(pr(), SHA), (True, SHA))

  def test_sha_mismatch_refused_naming_both(self):
    other = "b" * 40
    ok, reason = M.plan_status(pr(), other)
    self.assertFalse(ok)
    self.assertIn(other, reason)
    self.assertIn(SHA, reason)

  def test_short_prefix_resolves_to_full_sha(self):
    head = "abc1234" + "0" * 33
    self.assertEqual(M.plan_status(pr(headRefOid=head), "abc1234"), (True, head))
    self.assertEqual(M.plan_status(pr(headRefOid=head), "ABC1234"), (True, head))

  def test_too_short_or_non_hex_prefix_refused(self):
    self.assertFalse(M.plan_status(pr(), "aaaaaa")[0])
    self.assertFalse(M.plan_status(pr(), "zzzzzzz")[0])
    self.assertFalse(M.plan_status(pr(), "a" * 41)[0])

  def test_missing_sha_refused(self):
    self.assertFalse(M.plan_status(pr(), None)[0])
    self.assertFalse(M.plan_status(pr(), "")[0])

  def test_draft_refused(self):
    ok, reason = M.plan_status(pr(isDraft=True), SHA)
    self.assertFalse(ok)
    self.assertIn("draft", reason)

  def test_closed_and_merged_refused(self):
    self.assertFalse(M.plan_status(pr(state="CLOSED"), SHA)[0])
    self.assertFalse(M.plan_status(pr(state="MERGED"), SHA)[0])

  def test_missing_or_bad_head_refused(self):
    self.assertFalse(M.plan_status({"isDraft": False, "state": "OPEN"}, SHA)[0])
    self.assertFalse(M.plan_status(pr(headRefOid="abc123"), "abc123")[0])

  def test_unknown_draft_state_and_garbage_refused(self):
    self.assertFalse(M.plan_status({"headRefOid": SHA, "state": "OPEN"}, SHA)[0])
    self.assertFalse(M.plan_status(None, SHA)[0])


if __name__ == "__main__":
  unittest.main()
