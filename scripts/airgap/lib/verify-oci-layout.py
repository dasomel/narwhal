#!/usr/bin/env python3
"""Verify the saved OCI layouts of an air-gap bundle byte-for-byte (narwhal#124).

09-verify-bundle-completeness.sh used to accept any file named oci/<name>/index.json,
so an empty index, a missing/truncated blob, or a digest-pinned image whose layout
holds different bytes than the pin all passed the "release gate". This walks each
listed image's layout and fails closed on:
  - missing/invalid oci-layout, index.json, or ambiguous `latest` entry
  - a manifest/index/config blob that is absent, a symlink, the wrong size, or whose
    sha256 differs from its descriptor (layers: presence + size only, not re-hashed)
  - a digest-pinned image (<ref>@sha256:<hex>) whose top-level digest != the pin
    (02 copies those with --all --preserve-digests, so the digest must survive)

It also records, per image, what the bundle does NOT carry: `skopeo copy` never copies
OCI referrers (SBOM/signature/attestation), and --override-arch collapses an index to
one platform. The report states both instead of leaving the loss silent. Offline only.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

DIGEST_RE = re.compile(r"sha256:([0-9a-f]{64})")
PIN_RE = re.compile(r"@(sha256:[0-9a-f]{64})$")
INDEX_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}
MANIFEST_TYPES = INDEX_TYPES | {
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
}
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_DEPTH = 4
REF_NAME = "org.opencontainers.image.ref.name"


class LayoutError(Exception):
    pass


def read_json(path: Path, label: str):
    if path.is_symlink() or not path.is_file():
        raise LayoutError(f"{label} missing or not a regular file")
    if path.stat().st_size > MAX_JSON_BYTES:
        raise LayoutError(f"{label} exceeds {MAX_JSON_BYTES} bytes")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LayoutError(f"{label} is not valid JSON ({exc})")


def check_descriptor(layout: Path, desc, label: str, hash_it: bool) -> Path:
    if not isinstance(desc, dict):
        raise LayoutError(f"{label}: descriptor is not an object")
    digest, size = desc.get("digest"), desc.get("size")
    m = DIGEST_RE.fullmatch(digest) if isinstance(digest, str) else None
    if not m:
        raise LayoutError(f"{label}: malformed digest {digest!r}")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise LayoutError(f"{label}: malformed size {size!r}")
    blob = layout / "blobs" / "sha256" / m.group(1)
    if blob.is_symlink() or not blob.is_file():
        raise LayoutError(f"{label}: blob {digest} missing")
    if blob.stat().st_size != size:
        raise LayoutError(f"{label}: blob {digest} size {blob.stat().st_size} != descriptor {size}")
    if hash_it:
        h = hashlib.sha256()
        with blob.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != m.group(1):
            raise LayoutError(f"{label}: blob content hashes to sha256:{h.hexdigest()}, not {digest}")
    return blob


def walk(layout: Path, desc, label: str, depth: int, stats: dict):
    """Verify one manifest/index descriptor and everything it references."""
    if depth > MAX_DEPTH:
        raise LayoutError(f"{label}: index nesting deeper than {MAX_DEPTH}")
    media = desc.get("mediaType") if isinstance(desc, dict) else None
    if media not in MANIFEST_TYPES:
        raise LayoutError(f"{label}: unsupported mediaType {media!r}")
    blob = check_descriptor(layout, desc, label, hash_it=True)
    doc = read_json(blob, label)
    if not isinstance(doc, dict):
        raise LayoutError(f"{label}: manifest is not a JSON object")
    if "subject" in doc:
        stats["referrers"] += 1
    if media in INDEX_TYPES:
        children = doc.get("manifests")
        if not isinstance(children, list) or not children:
            raise LayoutError(f"{label}: index has no child manifests")
        for i, child in enumerate(children):
            stats["children"] += 1
            walk(layout, child, f"{label}.manifests[{i}]", depth + 1, stats)
        return
    check_descriptor(layout, doc.get("config"), f"{label}.config", hash_it=True)
    layers = doc.get("layers")
    if not isinstance(layers, list):
        raise LayoutError(f"{label}: manifest has no layers array")
    for i, layer in enumerate(layers):
        check_descriptor(layout, layer, f"{label}.layers[{i}]", hash_it=False)


def verify_image(bundle: Path, img: str):
    layout = bundle / "oci" / img.replace("/", "_").replace(":", "_")
    read_json(layout / "oci-layout", "oci-layout")
    index = read_json(layout / "index.json", "index.json")
    entries = index.get("manifests") if isinstance(index, dict) else None
    if not isinstance(entries, list) or not entries:
        raise LayoutError("index.json lists no manifests")
    named = [e for e in entries
             if isinstance(e, dict) and isinstance(e.get("annotations"), dict)
             and e["annotations"].get(REF_NAME) == "latest"]
    top = named if named else entries
    if len(top) != 1:
        raise LayoutError(f"index.json has {len(top)} candidate entries for tag latest (want exactly 1)")
    stats = {"children": 0, "referrers": 0}
    walk(layout, top[0], "index.json.manifests[latest]", 0, stats)
    pin = PIN_RE.search(img)
    if pin and top[0]["digest"] != pin.group(1):
        raise LayoutError(f"pinned {pin.group(1)} but layout top-level digest is {top[0]['digest']}")
    if top[0]["mediaType"] in INDEX_TYPES:
        platforms = "full-index-preserved"
    elif pin:
        platforms = "pinned-manifest-preserved (--all)"
    else:
        platforms = "single-platform (--override-arch)"
    return {
        "image": img,
        "layout_digest": top[0]["digest"],
        "pinned": bool(pin),
        "platforms": platforms,
        "referrers_in_layout": stats["referrers"],
        "referrers_preserved": False,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bundle", required=True, type=Path)
    ap.add_argument("--images", required=True, type=Path, help="file with one image ref per line")
    ap.add_argument("--report-out", type=Path)
    args = ap.parse_args()
    try:
        lines = args.images.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        print(f"ERROR: cannot read image list: {exc}", file=sys.stderr)
        return 2
    images = sorted({ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")})
    rows, failed = [], 0
    for img in images:
        try:
            rows.append(verify_image(args.bundle, img))
        except (LayoutError, OSError) as exc:
            print(f"ERROR: {img}: {exc}", file=sys.stderr)
            failed += 1
    if failed:
        print(f"ERROR: {failed} of {len(images)} OCI layouts failed byte-level verification", file=sys.stderr)
        return 1
    collapsed = sum(1 for r in rows if r["platforms"].startswith("single"))
    print(f"OCI layouts verified: {len(rows)} images (blob digests/sizes, pin identity).", file=sys.stderr)
    print(f"NOTE: referrers (SBOM/signature/attestation) preserved for 0 of {len(rows)} images -- "
          "skopeo copy does not copy them (#124 AC open).", file=sys.stderr)
    print(f"NOTE: {collapsed} images collapsed to one platform by --override-arch; "
          f"{len(rows) - collapsed} keep the source manifest/index as pinned.", file=sys.stderr)
    if args.report_out:
        report = {"schema": "narwhal-airgap-layout-report/v1", "images": rows}
        args.report_out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
