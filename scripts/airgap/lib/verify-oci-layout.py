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

Save-time digest inventory (narwhal#124): the checks above only prove a layout matches its
OWN index.json. 02-save-images.sh therefore runs this with --inventory-out, recording what
it saved (image -> top-level digest, layout dir, platform mode, tool version) in a sorted,
timestamp-free images.digests.json. 09 runs it with --inventory and fails closed on: an
absent/symlinked/malformed/duplicate-key/unsorted/unknown-field inventory, an image with no
entry, an oci/ directory no entry accounts for, a layout that is not a real directory, or a
digest/platform that differs from the record. A bundle saved before this file existed has
no inventory and fails; re-run 02-save-images.sh (never synthesize one from the disk, that
would just attest whatever is there).
"""
import argparse
import hashlib
import json
import os
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
INVENTORY_SCHEMA = "narwhal-airgap-image-digests/v1"
INVENTORY_KEYS = {"image", "layout", "digest", "platforms", "collapsed_to"}
PLATFORM_MODES = {"full-index-preserved", "pinned-manifest-preserved", "single-platform"}
COLLAPSED_RE = re.compile(r"[a-z0-9]+/[a-z0-9]+")
REF_NAME = "org.opencontainers.image.ref.name"


class LayoutError(Exception):
    pass


def no_duplicate_keys(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate key")
    return dict(pairs)


def read_json(path: Path, label: str, strict_keys: bool = False):
    if path.is_symlink() or not path.is_file():
        raise LayoutError(f"{label} missing or not a regular file")
    if path.stat().st_size > MAX_JSON_BYTES:
        raise LayoutError(f"{label} exceeds {MAX_JSON_BYTES} bytes")
    try:
        return json.loads(path.read_text(encoding="utf-8"),
                          object_pairs_hook=no_duplicate_keys if strict_keys else None)
    except (UnicodeDecodeError, ValueError) as exc:
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


def layout_name(img: str) -> str:
    return img.replace("/", "_").replace(":", "_")


def verify_image(bundle: Path, img: str):
    layout = bundle / "oci" / layout_name(img)
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
        "layout": "oci/" + layout_name(img),
        "pinned": bool(pin),
        "platforms": platforms,
        "referrers_in_layout": stats["referrers"],
        "referrers_preserved": False,
    }


def platform_mode(row: dict) -> str:
    return row["platforms"].split(" ")[0]


def write_inventory(path: Path, rows: list, platform: str, tool: str):
    entries = []
    for r in sorted(rows, key=lambda r: r["image"]):
        mode = platform_mode(r)
        entries.append({"image": r["image"], "layout": r["layout"], "digest": r["layout_digest"],
                        "platforms": mode, "collapsed_to": platform if mode == "single-platform" else None})
    doc = {"schema": INVENTORY_SCHEMA, "tool": tool, "images": entries}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_inventory(path: Path) -> dict:
    """Return {image: entry}; raise LayoutError on anything but a well-formed sorted inventory."""
    doc = read_json(path, "inventory", strict_keys=True)
    if not isinstance(doc, dict) or set(doc) != {"schema", "tool", "images"}:
        raise LayoutError("inventory must be an object with exactly schema, tool, images")
    if doc["schema"] != INVENTORY_SCHEMA:
        raise LayoutError(f"inventory schema {doc['schema']!r} is not {INVENTORY_SCHEMA}")
    if not isinstance(doc["tool"], str) or not doc["tool"].strip() or len(doc["tool"]) > 200:
        raise LayoutError("inventory tool must be a non-empty string of at most 200 chars")
    if not isinstance(doc["images"], list):
        raise LayoutError("inventory images is not a list")
    entries, prev = {}, None
    for i, e in enumerate(doc["images"]):
        label = f"inventory.images[{i}]"
        if not isinstance(e, dict) or set(e) != INVENTORY_KEYS:
            raise LayoutError(f"{label}: must be an object with exactly {sorted(INVENTORY_KEYS)}")
        img = e["image"]
        if not isinstance(img, str) or not img or img != img.strip():
            raise LayoutError(f"{label}: malformed image {img!r}")
        if prev is not None and img <= prev:
            raise LayoutError(f"{label}: {img!r} is duplicate or out of sorted order after {prev!r}")
        prev = img
        if e["layout"] != "oci/" + layout_name(img) or "\n" in e["layout"]:
            raise LayoutError(f"{label}: layout {e['layout']!r} is not oci/{layout_name(img)}")
        if not isinstance(e["digest"], str) or not DIGEST_RE.fullmatch(e["digest"]):
            raise LayoutError(f"{label}: malformed digest {e['digest']!r}")
        if e["platforms"] not in PLATFORM_MODES:
            raise LayoutError(f"{label}: unknown platforms {e['platforms']!r}")
        collapsed = e["collapsed_to"]
        if e["platforms"] == "single-platform":
            if not isinstance(collapsed, str) or not COLLAPSED_RE.fullmatch(collapsed):
                raise LayoutError(f"{label}: collapsed_to {collapsed!r} is not <os>/<arch>")
        elif collapsed is not None:
            raise LayoutError(f"{label}: collapsed_to must be null for {e['platforms']}")
        entries[img] = e
    layouts = [e["layout"] for e in entries.values()]
    if len(set(layouts)) != len(layouts):
        raise LayoutError("inventory: two images map to the same layout directory")
    return entries


def check_inventory(bundle: Path, entries: dict, rows: list) -> list:
    """Compare verified layouts with the save-time record; return error strings."""
    errors = []
    for r in rows:
        e = entries.get(r["image"])
        if e is None:
            errors.append(f"{r['image']}: no entry in the digest inventory")
        elif e["digest"] != r["layout_digest"]:
            errors.append(f"{r['image']}: layout digest {r['layout_digest']} != saved {e['digest']}")
        elif e["platforms"] != platform_mode(r):
            errors.append(f"{r['image']}: layout is {platform_mode(r)} but saved as {e['platforms']}")
    known = {e["layout"].split("/", 1)[1] for e in entries.values()}
    oci = bundle / "oci"
    for child in sorted(oci.iterdir(), key=lambda p: p.name) if oci.is_dir() and not oci.is_symlink() else []:
        if child.is_symlink() or not child.is_dir():
            errors.append(f"oci/{child.name}: not a real directory (symlink or stray file)")
        elif child.name not in known:
            errors.append(f"oci/{child.name}: layout not recorded in the digest inventory")
    return errors


def read_list(path: Path) -> set:
    lines = path.read_text(encoding="utf-8").splitlines()
    return {ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bundle", required=True, type=Path)
    ap.add_argument("--images", required=True, type=Path, help="file with one image ref per line")
    ap.add_argument("--report-out", type=Path)
    ap.add_argument("--inventory", type=Path, help="verify layouts against this save-time inventory")
    ap.add_argument("--manifest", type=Path, help="manifest.txt: every ref in it must be inventoried")
    ap.add_argument("--inventory-out", type=Path, help="write the inventory from the verified layouts (02)")
    ap.add_argument("--platform", help="<os>/<arch> --override-arch collapsed to (with --inventory-out)")
    ap.add_argument("--tool", default="unknown", help="tool version string to record (with --inventory-out)")
    args = ap.parse_args()
    if args.inventory and args.inventory_out:
        print("ERROR: --inventory and --inventory-out are mutually exclusive", file=sys.stderr)
        return 2
    if args.inventory_out and not (args.platform and COLLAPSED_RE.fullmatch(args.platform)):
        print("ERROR: --inventory-out needs --platform <os>/<arch>", file=sys.stderr)
        return 2
    try:
        images = read_list(args.images)
        if args.manifest:
            images |= read_list(args.manifest)
    except (OSError, UnicodeDecodeError) as exc:
        print(f"ERROR: cannot read image list: {exc}", file=sys.stderr)
        return 2
    entries = None
    if args.inventory:
        try:
            entries = load_inventory(args.inventory)
        except (LayoutError, OSError) as exc:
            print(f"ERROR: image digest inventory {args.inventory}: {exc}. Bundles saved before the "
                  "inventory existed must be re-saved with 02-save-images.sh.", file=sys.stderr)
            return 1
        images |= set(entries)
    images = sorted(images)
    rows, failed = [], 0
    for img in images:
        try:
            rows.append(verify_image(args.bundle, img))
        except (LayoutError, OSError) as exc:
            print(f"ERROR: {img}: {exc}", file=sys.stderr)
            failed += 1
    if entries is not None:
        for msg in check_inventory(args.bundle, entries, rows):
            print(f"ERROR: {msg}", file=sys.stderr)
            failed += 1
    if failed:
        print(f"ERROR: {failed} of {len(images)} OCI layouts failed byte-level verification", file=sys.stderr)
        return 1
    collapsed = sum(1 for r in rows if r["platforms"].startswith("single"))
    if args.inventory_out:
        write_inventory(args.inventory_out, rows, args.platform, args.tool)
        print(f"Digest inventory written: {len(rows)} images -> {args.inventory_out}", file=sys.stderr)
    print(f"OCI layouts verified: {len(rows)} images (blob digests/sizes, pin identity"
          f"{', saved-digest inventory' if entries is not None else ''}).", file=sys.stderr)
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
