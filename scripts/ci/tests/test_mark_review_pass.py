import importlib.util, io, json, subprocess, unittest
from contextlib import redirect_stderr, redirect_stdout
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

  def test_uppercase_sha_accepted_and_lowercased(self):
    self.assertEqual(M.plan_status(pr(), SHA.upper()), (True, SHA))

  def test_prefix_wrong_length_or_non_hex_refused(self):
    self.assertFalse(M.plan_status(pr(), SHA[:7])[0])
    self.assertFalse(M.plan_status(pr(), SHA[:39])[0])
    self.assertFalse(M.plan_status(pr(), SHA + "a")[0])
    self.assertFalse(M.plan_status(pr(), "z" * 40)[0])

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


class FakeGh:
  """Records gh calls; `heads` is consumed one per `pr view`, the last repeating."""
  def __init__(self, heads, draft=False, state="OPEN", fail_on=None):
    self.heads, self.calls, self.draft, self.state, self.fail_on = list(heads), [], draft, state, fail_on

  def __call__(self, args):
    self.calls.append(args)
    if self.fail_on and args[0] == self.fail_on:
      raise subprocess.CalledProcessError(1, ["gh"] + args, stderr="HTTP 403: forbidden\n")
    if args[0] == "pr":
      head = self.heads.pop(0) if len(self.heads) > 1 else self.heads[0]
      return json.dumps({"headRefOid": head, "isDraft": self.draft, "state": self.state})
    return "{}"

  def posts(self):
    return [c for c in self.calls if c[0] == "api"]


def run_main(gh, sha=SHA, number="7"):
  out, err = io.StringIO(), io.StringIO()
  with redirect_stdout(out), redirect_stderr(err):
    rc = M.main(["mark-review-pass.py", number, "--sha", sha], gh=gh)
  return rc, out.getvalue(), err.getvalue()


class MainTest(unittest.TestCase):
  def test_posts_exact_status_then_reports_success(self):
    gh = FakeGh([SHA])
    rc, out, err = run_main(gh, SHA.upper())
    self.assertEqual(rc, 0, err)
    self.assertEqual(gh.posts(), [["api", f"repos/dasomel/narwhal/statuses/{SHA}", "-f", "state=success",
                                   "-f", "context=independent-review", "-f", f"description=independent review PASS @{SHA[:7]}"]])
    self.assertIn(SHA, out)

  def test_mismatch_posts_nothing(self):
    gh = FakeGh(["b" * 40])
    rc, out, err = run_main(gh)
    self.assertEqual(rc, 1)
    self.assertEqual(gh.posts(), [])
    self.assertNotIn("posted", out)
    self.assertIn("b" * 40, err)

  def test_head_moved_after_post_warns_without_success_line(self):
    gh = FakeGh([SHA, "c" * 40])
    rc, out, err = run_main(gh)
    self.assertEqual(rc, 1)
    self.assertEqual(len(gh.posts()), 1)
    self.assertNotIn("posted", out)
    self.assertIn("WARNING", err)

  def test_gh_failure_on_post_is_one_line_error_no_success(self):
    gh = FakeGh([SHA], fail_on="api")
    rc, out, err = run_main(gh)
    self.assertEqual(rc, 1)
    self.assertEqual(out, "")
    self.assertEqual(len(err.strip().splitlines()), 1)

  def test_gh_failure_on_view_and_bad_json_no_success(self):
    rc, out, _ = run_main(FakeGh([SHA], fail_on="pr"))
    self.assertEqual((rc, out), (1, ""))
    bad = lambda args: "not json"
    rc, out, _ = run_main(bad)
    self.assertEqual((rc, out), (1, ""))

  def test_usage_errors(self):
    self.assertEqual(M.main(["x", "7"], gh=FakeGh([SHA])), 2)
    self.assertEqual(M.main(["x", "7", "--sha"], gh=FakeGh([SHA])), 2)

  def test_abbreviated_repeated_or_valueless_sha_flag_exit_2_with_no_gh_calls(self):
    other = "b" * 40
    cases = [["--s", SHA], ["--sh", SHA], ["--sha=" + SHA], ["--sha", SHA, "--sha", other], ["--sha", other, "--sha", SHA],
             ["--sha", SHA, "--sha", SHA], ["--sha"], ["--sha", ""], [SHA]]
    for extra in cases:
      gh = FakeGh([SHA])
      err = io.StringIO()
      with redirect_stderr(err):
        rc = M.main(["x", "7"] + extra, gh=gh)
      self.assertEqual(rc, 2, extra)
      self.assertEqual(gh.calls, [], extra)

  def test_non_ascii_or_non_numeric_pr_number_exit_2_with_no_gh_calls(self):
    for number in ["-1", "1e3", "12;id", "\u00b2", "\u0661\u0662", "", " 12", "12 ", "12\n"]:
      gh = FakeGh([SHA])
      err = io.StringIO()
      with redirect_stderr(err):
        rc = M.main(["x", number, "--sha", SHA], gh=gh)
      self.assertEqual(rc, 2, repr(number))
      self.assertEqual(gh.calls, [], repr(number))

if __name__ == "__main__":
  unittest.main()
