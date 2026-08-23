#!/usr/bin/env python3
"""Assert 01-generate-image-list.sh hard-fails on incomplete hook/Job image collection.

narwhal#51: the --live path used to treat a failure to render the bundled charts (which
is how Helm hook/Job images -- cert-manager's startupapicheck, kube-prometheus-stack's
kube-webhook-certgen -- get collected) as "Not fatal": print a WARN and keep going,
silently shipping an airgap bundle that installs most of the way and then dies on
whatever hook was missing. The fix requires the DEFAULT path (no env var set, which is
what CI/release must use) to `exit 1`, with AIRGAP_ALLOW_INCOMPLETE=1 as the one explicit
opt-out for local iterative use.

This checks structure, not just presence of the string "exit 1" somewhere in the file: the
hook_imgs branch must be an if/elif/else where the elif tests AIRGAP_ALLOW_INCOMPLETE and
the else (the branch taken with no env var set) contains `exit 1`. A bundle-dir check or an
unrelated exit elsewhere in the file must not satisfy this. Takes an optional path so the
regression suite can point it at a mutated temp copy and prove the negative case actually
fails.
"""
import re
import sys

DEFAULT_PATH = "scripts/airgap/01-generate-image-list.sh"

BLOCK_RE = re.compile(
    r'hook_imgs=\$\(hook_images_from_charts.*?\n'
    r'  if \[\[ -n "\$\{hook_imgs\}" \]\]; then.*?\n'
    r'  elif \[\[ "\$\{AIRGAP_ALLOW_INCOMPLETE:-0\}" == "1" \]\]; then.*?\n'
    r'  else\n'
    r'(?P<else_block>.*?)\n'
    r'  fi\n',
    re.DOTALL,
)


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    with open(path) as f:
        text = f.read()

    m = BLOCK_RE.search(text)
    if not m:
        print(f"{path}: hook_imgs if/elif(AIRGAP_ALLOW_INCOMPLETE)/else block not found "
              "-- soft-continue-only shape, or the gate was renamed/removed", file=sys.stderr)
        return 1

    else_block = m.group("else_block")
    if "exit 1" not in else_block:
        print(f"{path}: default (no AIRGAP_ALLOW_INCOMPLETE) branch does not exit 1 "
              "-- incomplete hook image collection would silently continue", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
