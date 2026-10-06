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

  The reviewer must name the SHA it reviewed (full, or a >=7 char prefix). It is accepted only if it
  resolves to the PR's current head, so a push between review and posting never earns a PASS.
  On ok, reason is the full 40-char SHA to stamp.
  """
  if not isinstance(pr, dict):
    return False, "malformed PR data"
  head = pr.get("headRefOid")
  if not isinstance(head, str) or len(head) != 40 or any(c not in HEX for c in head):
    return False, "missing or invalid headRefOid"
  rev = reviewed_sha.lower() if isinstance(reviewed_sha, str) else ""
  if not rev or len(rev) < 7 or len(rev) > 40 or any(c not in HEX for c in rev):
    return False, "--sha is required: the full or >=7 char hex SHA the reviewer actually reviewed"
  if not head.startswith(rev):
    return False, f"reviewed SHA {rev} does not match current PR head {head}; the head moved, re-review it"
  if pr.get("isDraft") is not False:
    return False, "PR is a draft (or draft state unknown); mark it ready first"
  if pr.get("state") != "OPEN":
    return False, f"PR state is {pr.get('state')!r}, not OPEN"
  return True, head


def view(number):
  out = subprocess.run(["gh", "pr", "view", number, "-R", REPO, "--json", "headRefOid,isDraft,state"],
                       capture_output=True, text=True, check=True).stdout
  return json.loads(out)


def main(argv):
  if len(argv) != 4 or not argv[1].isdigit() or argv[2] != "--sha":
    print("usage: mark-review-pass.py <pr-number> --sha <reviewed-sha>", file=sys.stderr)
    return 2
  try:
    ok, sha = plan_status(view(argv[1]), argv[3])
    if not ok:
      print(f"mark-review-pass: REFUSED: {sha}", file=sys.stderr)
      return 1
    subprocess.run(["gh", "api", f"repos/{REPO}/statuses/{sha}", "-f", "state=success", "-f", f"context={CONTEXT}",
                    "-f", f"description=independent review PASS @{sha[:7]}"], check=True, capture_output=True, text=True)
    after = view(argv[1]).get("headRefOid")
  except (subprocess.CalledProcessError, ValueError) as e:
    print(f"mark-review-pass: FAILED: {getattr(e, 'stderr', None) or e}", file=sys.stderr)
    return 1
  print(f"mark-review-pass: posted '{CONTEXT}' success for {sha} (PR #{argv[1]})")
  if after != sha:
    print(f"WARNING: PR head moved after posting ({sha[:7]} -> {str(after)[:7]}); the status is bound to the old SHA (harmless), the new head is NOT reviewed: re-review it", file=sys.stderr)
    return 1
  return 0


if __name__ == "__main__":
  sys.exit(main(sys.argv))
