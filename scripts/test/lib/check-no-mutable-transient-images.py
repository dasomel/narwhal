#!/usr/bin/env python3
"""Assert images.txt and TRANSIENT_IMAGES carry no undocumented `:latest` tag.

narwhal#52 D3-A pinned the two third-party build helpers (kaniko executor,
alpine/git) that images.txt and 01-generate-image-list.sh's TRANSIENT_IMAGES
heredoc used to leave at `:latest` "to match the deploy manifests" — a comment is a
coupling, not a fix, and the pair had already drifted from narwhal-portal's
deploy/kaniko-build-job.yaml once. This checks the pin actually holds: neither list
may carry a bare `:latest` reference except the two documented, deliberate
exceptions — the custom multi-arch `ghcr.io/dasomel/goharbor/*` rebuild (republished
to `:latest` on purpose so new builds are picked up automatically) and the
in-cluster-built image that 01-generate-image-list.sh's INCLUSTER_BUILT_RE already
excludes from every generated list.

Checks structure (every offending reference), not just presence, so a NEW
undocumented `:latest` sneaking into either file is caught the same way the old
kaniko/alpine-git pair would have been.
"""
import re
import sys

DEFAULT_IMAGES_TXT = "scripts/airgap/images.txt"
DEFAULT_GENERATOR = "scripts/airgap/01-generate-image-list.sh"

ALLOWED_LATEST_RE = re.compile(
    r"^(ghcr\.io/dasomel/goharbor/|harbor\.local\.narwhal\.internal/library/narwhal-portal)"
)


def images_txt_lines(path: str) -> list[str]:
    with open(path) as f:
        return [
            line.strip()
            for line in f
            if line.strip() and not line.strip().startswith("#")
        ]


def transient_images_lines(path: str) -> list[str]:
    with open(path) as f:
        text = f.read()
    m = re.search(r"TRANSIENT_IMAGES=\$\(cat <<'EOF'\n(.*?)\nEOF\n\)", text, re.S)
    if not m:
        raise SystemExit(f"could not find TRANSIENT_IMAGES heredoc in {path}")
    return [line.strip() for line in m.group(1).splitlines() if line.strip()]


def find_violations(images: list[str]) -> list[str]:
    return [
        img
        for img in images
        if img.endswith(":latest") and not ALLOWED_LATEST_RE.match(img)
    ]


def main() -> int:
    images_txt = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_IMAGES_TXT
    generator = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_GENERATOR

    violations = find_violations(images_txt_lines(images_txt)) + find_violations(
        transient_images_lines(generator)
    )

    if violations:
        print("Undocumented :latest image reference(s):", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        return 1

    print("no undocumented :latest image references", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
