#!/usr/bin/env python3
"""CI gate: no container image reference in GitOps output may use `:latest` or omit
its tag entirely (narwhal#52).

Runtime admission already has a Kyverno `disallow-latest-tag` policy
(gitops/resources/kyverno-policies.yaml), but it runs in `Audit` mode and excludes
most system namespaces — it observes a mutable tag reaching the cluster, it does not
stop a PR from introducing one. This is the pre-merge half: it renders every chart
under gitops/charts/ the same way ArgoCD would (helm template, chart defaults — the
values narwhal-apps/narwhal-platform ship are exactly what a plain `vagrant up`
install uses) and scans both that output and the raw manifests in gitops/resources/
for any `image:` field that is unpinned.

Reuses the same extraction regex 01-generate-image-list.sh's hook_images_from_charts()
uses for the identical problem (image refs embedded in rendered YAML text), rather
than a full structural YAML walk — this repo's established pattern for this class of
check.

A chart whose `helm template` fails is a hard failure by default (narwhal#52 review,
2026-09-08): Kyverno's disallow-latest-tag policy is Enforce now, so a chart this gate
silently skips is a chart nothing else vets pre-merge either. For a local run without
every chart's dependencies available (e.g. missing subchart repos), set
NO_MUTABLE_TAGS_ALLOW_RENDER_FAIL=1 to fall back to the old warn-and-skip behavior.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parent.parent.parent

IMAGE_RE = re.compile(r'^\s*-?\s*image:\s*"?([^"\s]+)"?\s*$', re.M)

ALLOW_RENDER_FAIL_ENV = "NO_MUTABLE_TAGS_ALLOW_RENDER_FAIL"


class ChartRenderError(RuntimeError):
    """`helm template` failed for one chart; carries what main() reports to the caller."""

    def __init__(self, chart_name: str, stderr: str):
        self.chart_name = chart_name
        self.stderr = stderr
        super().__init__(f"helm template failed for chart {chart_name!r}")


def render_chart(chart_dir: Path, cwd: Path) -> str:
    result = subprocess.run(
        ["helm", "template", chart_dir.name, str(chart_dir)],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    if result.returncode != 0:
        if os.environ.get(ALLOW_RENDER_FAIL_ENV) == "1":
            print(
                f"warning: 'helm template {chart_dir.name}' failed, skipping "
                f"({ALLOW_RENDER_FAIL_ENV}=1): {result.stderr.strip()[:200]}",
                file=sys.stderr,
            )
            return ""
        raise ChartRenderError(chart_dir.name, result.stderr)
    return result.stdout


def extract_images(text: str) -> set[str]:
    images = set()
    for m in IMAGE_RE.finditer(text):
        ref = m.group(1)
        # Kyverno match/exclude patterns look like image refs but carry a `*` or `|` —
        # they are policy strings, not something the cluster ever pulls.
        if "*" in ref or "|" in ref:
            continue
        images.add(ref)
    return images


def is_unpinned(image: str) -> str | None:
    """Returns a violation reason, or None if the image is pinned."""
    # A digest reference (@sha256:...) is always pinned regardless of any tag alongside it.
    if "@sha256:" in image:
        return None
    last_segment = image.rsplit("/", 1)[-1]
    if ":" not in last_segment:
        return "no tag (defaults to :latest on pull)"
    tag = last_segment.rsplit(":", 1)[-1]
    if tag == "latest":
        return "uses :latest"
    return None


def main() -> int:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_ROOT
    charts_dir = root / "gitops" / "charts"
    resources_dir = root / "gitops" / "resources"

    if not shutil_which("helm"):
        print("helm not found on PATH — cannot render charts, skipping chart scan", file=sys.stderr)
        chart_images: set[str] = set()
    else:
        chart_images = set()
        for chart_dir in sorted(p for p in charts_dir.iterdir() if (p / "Chart.yaml").exists()):
            try:
                chart_images |= extract_images(render_chart(chart_dir, root))
            except ChartRenderError as exc:
                print(
                    f"ERROR: 'helm template {exc.chart_name}' failed — refusing to silently "
                    f"skip a chart while Kyverno disallow-latest-tag is Enforce (narwhal#52). "
                    f"Set {ALLOW_RENDER_FAIL_ENV}=1 to skip charts with unmet local deps.",
                    file=sys.stderr,
                )
                print(exc.stderr.strip(), file=sys.stderr)
                return 1

    resource_images: set[str] = set()
    for f in sorted(resources_dir.glob("*.yaml")):
        resource_images |= extract_images(f.read_text())

    all_images = chart_images | resource_images
    violations = sorted((img, reason) for img in all_images if (reason := is_unpinned(img)))

    for img, reason in violations:
        print(f"unpinned image reference: {img} — {reason}", file=sys.stderr)

    if not all_images:
        print("no image references found — check helm/chart paths", file=sys.stderr)
        return 1

    if violations:
        return 1
    print(f"{len(all_images)} image reference(s) checked, all pinned", file=sys.stderr)
    return 0


def shutil_which(cmd: str) -> str | None:
    import shutil

    return shutil.which(cmd)


if __name__ == "__main__":
    sys.exit(main())
