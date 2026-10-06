#!/usr/bin/env python3
"""Post the `independent-review` success status on a PR's CURRENT head SHA.

Run by the independent reviewer after a PASS, with the SHA it reviewed (never the author lane). The status is bound to the
SHA, so any later push (new SHA) has no status and the required check blocks the merge.
Procedural control: any repo writer can post it; it is not identity-proof.
"""
import json, subprocess, sys

REPO = "dasomel/narwhal"
CONTEXT = "independent-review"
HEX = "0123456789abcdef"


def plan_status(pr, reviewed_sha):
  """Return (ok, reason) for `gh pr view --json headRefOid,isDraft,state` output.

  `reviewed_sha` must be the FULL 40-hex SHA the reviewer actually reviewed (case-insensitive) and must
  equal the PR's current head exactly: a short prefix could be ground by an author, and a head that
  moved since review never earns a PASS. On ok, reason is the lowercase SHA to stamp.
  """
  if not isinstance(pr, dict):
    return False, "malformed PR data"
  head = pr.get("headRefOid")
  if not _is_full_sha(head):
    return False, "missing or invalid headRefOid"
  rev = reviewed_sha.lower() if isinstance(reviewed_sha, str) else ""
  if not _is_full_sha(rev):
    return False, "--sha is required and must be the full 40-hex SHA the reviewer actually reviewed"
  if rev != head:
    return False, f"reviewed SHA {rev} does not match current PR head {head}; the head moved, re-review it"
  if pr.get("isDraft") is not False:
    return False, "PR is a draft (or draft state unknown); mark it ready first"
  if pr.get("state") != "OPEN":
    return False, f"PR state is {pr.get('state')!r}, not OPEN"
  return True, head


def _is_full_sha(value):
  return isinstance(value, str) and len(value) == 40 and all(c in HEX for c in value)


def run_gh(args):
  return subprocess.run(["gh"] + args, capture_output=True, text=True, check=True).stdout


def view(number, gh):
  return json.loads(gh(["pr", "view", number, "-R", REPO, "--json", "headRefOid,isDraft,state"]))


def main(argv, gh=run_gh):
  if len(argv) != 4 or not argv[1].isdigit() or argv[2] != "--sha":
    print("usage: mark-review-pass.py <pr-number> --sha <full-40-hex-reviewed-sha>", file=sys.stderr)
    return 2
  number = argv[1]
  try:
    ok, sha = plan_status(view(number, gh), argv[3])
    if not ok:
      print(f"mark-review-pass: REFUSED: {sha}", file=sys.stderr)
      return 1
    gh(["api", f"repos/{REPO}/statuses/{sha}", "-f", "state=success", "-f", f"context={CONTEXT}",
        "-f", f"description=independent review PASS @{sha[:7]}"])
    after = view(number, gh).get("headRefOid")
  except subprocess.CalledProcessError as e:
    detail = ((e.stderr or "").strip().splitlines() or ["gh error"])[-1]
    print(f"mark-review-pass: FAILED: {detail}", file=sys.stderr)
    return 1
  except (ValueError, AttributeError, OSError) as e:
    print(f"mark-review-pass: FAILED: {e}", file=sys.stderr)
    return 1
  if after != sha:
    print(f"WARNING: PR head moved after posting ({sha[:7]} -> {str(after)[:7]}); the status is bound to the old SHA (harmless), the new head is NOT reviewed: re-review it", file=sys.stderr)
    return 1
  print(f"mark-review-pass: posted '{CONTEXT}' success for {sha} (PR #{number})")
  return 0


if __name__ == "__main__":
  sys.exit(main(sys.argv))
