#!/usr/bin/env python3
"""R288: Headlamp >= 0.46.0 version parity, built-in OIDC autoLogin/hasScopes, and no gateway login-injection route.

2026-10-27 (#491): Headlamp v0.46.0 adds built-in config.oidc.autoLogin (-oidc-auto-login)
and completes main-tab OIDC returns in frontend/src/components/oidcauth/index.tsx, replacing
the APISIX HTML-injection routes (headlamp-login-autoredirect, headlamp-auth-return). Chart
>= 0.43.0 also requires config.oidc.externalSecret.hasScopes: true so -oidc-scopes=$(OIDC_SCOPES)
is still passed when externalSecret.enabled is true.

Usage: check-headlamp-autologin.py [--mutation-verify]
"""
import re
import sys
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[3]
FILES = {
    "headlamp_app": ROOT / "gitops/charts/narwhal-apps/templates/headlamp.yaml",
    "sec_script": ROOT / "scripts/cluster/08-3-security.sh",
    "images": ROOT / "scripts/airgap/images.txt",
    "digests": ROOT / "scripts/airgap/lib/image-digests.tsv",
    "versions": ROOT / "VERSIONS.md",
    "routes": ROOT / "gitops/charts/narwhal-platform/templates/apisix-routes.yaml",
}
MIN_VERSION = (0, 46, 0)
IMAGE_REPO = "ghcr.io/headlamp-k8s/headlamp"


def parse_semver(ver: str) -> tuple[int, ...] | None:
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", ver.strip())
    if not m:
        return None
    return tuple(int(part) for part in m.groups())


def extract_image_ver(image_ref: str) -> str | None:
    prefix = f"{IMAGE_REPO}:v"
    if not isinstance(image_ref, str) or not image_ref.startswith(prefix):
        return None
    ver = image_ref[len(prefix):]
    return ver if parse_semver(ver) else None


def problems(texts: dict[str, str]) -> list[str]:
    out: list[str] = []
    found_versions: dict[str, str] = {}

    # 1. gitops/charts/narwhal-apps/templates/headlamp.yaml
    try:
        app_doc = yaml.safe_load(
            texts["headlamp_app"].replace("{{ .Values.baseDomain }}", "local.narwhal.internal")
        )
    except Exception as exc:
        return [f"headlamp.yaml YAML parse failed: {exc}"]

    source = ((app_doc or {}).get("spec") or {}).get("source") or {}
    chart_ver = str(source.get("targetRevision") or "")
    if not parse_semver(chart_ver):
        out.append(f"headlamp.yaml targetRevision invalid or missing: {chart_ver!r}")
    else:
        found_versions["headlamp.yaml:targetRevision"] = chart_ver

    values = (source.get("helm") or {}).get("valuesObject") or {}
    img_cfg = values.get("image") or {}
    main_img = f"{img_cfg.get('registry', '')}/{img_cfg.get('repository', '')}:{img_cfg.get('tag', '')}"
    main_ver = extract_image_ver(main_img)
    if not main_ver:
        out.append(f"headlamp.yaml valuesObject.image invalid or missing: {main_img!r}")
    else:
        found_versions["headlamp.yaml:image"] = main_ver

    init_containers = values.get("initContainers") or []
    init_img = init_containers[0].get("image", "") if init_containers else ""
    init_ver = extract_image_ver(init_img)
    if not init_ver:
        out.append(f"headlamp.yaml initContainers[0].image invalid or missing: {init_img!r}")
    else:
        found_versions["headlamp.yaml:initContainer"] = init_ver

    oidc = ((values.get("config") or {}).get("oidc")) or {}
    if oidc.get("autoLogin") is not True:
        out.append(f"headlamp.yaml config.oidc.autoLogin must be true, got {oidc.get('autoLogin')!r}")
    ext_sec = oidc.get("externalSecret") or {}
    if ext_sec.get("enabled") is not True or ext_sec.get("hasScopes") is not True:
        out.append(
            "headlamp.yaml config.oidc.externalSecret must set enabled: true and hasScopes: true "
            f"(got enabled={ext_sec.get('enabled')!r}, hasScopes={ext_sec.get('hasScopes')!r})"
        )

    # 2. scripts/cluster/08-3-security.sh
    sec = texts["sec_script"]
    m_heredoc = re.search(
        r"cat > /tmp/headlamp-values\.yaml << EOF\n(.*?)\nEOF\n", sec, re.S
    )
    if not m_heredoc:
        out.append("08-3-security.sh /tmp/headlamp-values.yaml heredoc not found")
    else:
        try:
            sec_values = yaml.safe_load(
                m_heredoc.group(1).replace("${DOMAIN}", "local.narwhal.internal")
            ) or {}
        except Exception as exc:
            out.append(f"08-3-security.sh headlamp-values.yaml parse failed: {exc}")
            sec_values = {}
        sec_img_cfg = sec_values.get("image") or {}
        sec_main_img = (
            f"{sec_img_cfg.get('registry', '')}/{sec_img_cfg.get('repository', '')}:{sec_img_cfg.get('tag', '')}"
        )
        sec_main_ver = extract_image_ver(sec_main_img)
        if not sec_main_ver:
            out.append(f"08-3-security.sh image invalid or missing: {sec_main_img!r}")
        else:
            found_versions["08-3-security.sh:image"] = sec_main_ver

        sec_inits = sec_values.get("initContainers") or []
        sec_init_img = sec_inits[0].get("image", "") if sec_inits else ""
        sec_init_ver = extract_image_ver(sec_init_img)
        if not sec_init_ver:
            out.append(f"08-3-security.sh initContainers[0].image invalid or missing: {sec_init_img!r}")
        else:
            found_versions["08-3-security.sh:initContainer"] = sec_init_ver

        sec_oidc = ((sec_values.get("config") or {}).get("oidc")) or {}
        if sec_oidc.get("autoLogin") is not True:
            out.append(
                f"08-3-security.sh config.oidc.autoLogin must be true, got {sec_oidc.get('autoLogin')!r}"
            )
        sec_ext = sec_oidc.get("externalSecret") or {}
        if sec_ext.get("enabled") is not True or sec_ext.get("hasScopes") is not True:
            out.append(
                "08-3-security.sh config.oidc.externalSecret must set enabled: true and hasScopes: true "
                f"(got enabled={sec_ext.get('enabled')!r}, hasScopes={sec_ext.get('hasScopes')!r})"
            )

    m_helm = re.search(
        r'helm upgrade --install headlamp\b.*?--version\s+([^\s\\]+)', sec, re.S
    )
    if not m_helm or not parse_semver(m_helm.group(1)):
        out.append("08-3-security.sh helm upgrade --install headlamp --version not found or invalid")
    else:
        found_versions["08-3-security.sh:chart"] = m_helm.group(1)

    # 3. scripts/airgap/images.txt
    img_lines = [
        line.strip()
        for line in texts["images"].splitlines()
        if line.strip().startswith(f"{IMAGE_REPO}:")
    ]
    if len(img_lines) != 1 or not extract_image_ver(img_lines[0]):
        out.append(f"images.txt must contain exactly one valid {IMAGE_REPO}:v<semver> line, got {img_lines}")
    else:
        found_versions["images.txt"] = extract_image_ver(img_lines[0])  # type: ignore[assignment]

    # 4. scripts/airgap/lib/image-digests.tsv
    digest_matches = re.findall(
        rf"^({re.escape(IMAGE_REPO)}:v\d+\.\d+\.\d+)\t(sha256:[0-9a-f]{{64}})\t",
        texts["digests"],
        re.M,
    )
    if len(digest_matches) != 1:
        out.append(f"image-digests.tsv must have exactly one {IMAGE_REPO} digest row, got {digest_matches}")
    else:
        found_versions["image-digests.tsv"] = extract_image_ver(digest_matches[0][0])  # type: ignore[assignment]

    # 5. VERSIONS.md
    m_ver = re.search(r"^\|\s*Headlamp\s*\|\s*v(\d+\.\d+\.\d+)\s*\(chart\)\s*\|", texts["versions"], re.M)
    if not m_ver:
        out.append("VERSIONS.md '| Headlamp | v<semver> (chart) |' row missing or invalid")
    else:
        found_versions["VERSIONS.md"] = m_ver.group(1)

    # Check version agreement and minimum version >= 0.46.0
    for loc, ver in sorted(found_versions.items()):
        parsed = parse_semver(ver)
        if not parsed or parsed < MIN_VERSION:
            out.append(f"{loc} version {ver!r} is below minimum 0.46.0")
    distinct = set(found_versions.values())
    if len(distinct) > 1:
        out.append(f"Headlamp version drift across sources: {found_versions}")

    # 6. gitops/charts/narwhal-platform/templates/apisix-routes.yaml
    routes = texts["routes"]
    if "name: headlamp" not in routes:
        out.append("apisix-routes.yaml is missing the headlamp route")
    for forbidden in ("headlamp-login-autoredirect", "headlamp-auth-return"):
        if forbidden in routes:
            out.append(f"apisix-routes.yaml still contains retired workaround route {forbidden!r}")

    return out


def main(argv: list[str]) -> int:
    mutation = "--mutation-verify" in argv
    texts = {k: p.read_text() for k, p in FILES.items()}

    found = problems(texts)
    for p in found:
        print(f"FAIL {p}")
    if found:
        return 1
    print(
        "OK Headlamp >= 0.46.0 version parity across 9 pins, config.oidc.autoLogin/hasScopes enabled in GitOps and 08-3, and gateway login-injection routes absent"
    )

    if mutation:
        mutants: list[tuple[str, str, str]] = [
            (
                "autoLogin false in headlamp.yaml",
                "headlamp_app",
                texts["headlamp_app"].replace("autoLogin: true", "autoLogin: false", 1),
            ),
            (
                "autoLogin false in 08-3-security.sh",
                "sec_script",
                texts["sec_script"].replace("autoLogin: true", "autoLogin: false", 1),
            ),
            (
                "hasScopes false in headlamp.yaml",
                "headlamp_app",
                texts["headlamp_app"].replace("hasScopes: true", "hasScopes: false", 1),
            ),
            (
                "hasScopes false in 08-3-security.sh",
                "sec_script",
                texts["sec_script"].replace("hasScopes: true", "hasScopes: false", 1),
            ),
            (
                "headlamp.yaml targetRevision back to 0.42.0",
                "headlamp_app",
                texts["headlamp_app"].replace("targetRevision: 0.46.0", "targetRevision: 0.42.0", 1),
            ),
            (
                "headlamp.yaml main image tag back to v0.42.0",
                "headlamp_app",
                texts["headlamp_app"].replace("tag: v0.46.0", "tag: v0.42.0", 1),
            ),
            (
                "headlamp.yaml initContainer image back to v0.42.0",
                "headlamp_app",
                texts["headlamp_app"].replace(
                    "image: ghcr.io/headlamp-k8s/headlamp:v0.46.0",
                    "image: ghcr.io/headlamp-k8s/headlamp:v0.42.0",
                    1,
                ),
            ),
            (
                "08-3-security.sh chart version back to 0.42.0",
                "sec_script",
                texts["sec_script"].replace("--version 0.46.0", "--version 0.42.0", 1),
            ),
            (
                "08-3-security.sh main image tag back to v0.42.0",
                "sec_script",
                texts["sec_script"].replace("tag: v0.46.0", "tag: v0.42.0", 1),
            ),
            (
                "08-3-security.sh initContainer image back to v0.42.0",
                "sec_script",
                texts["sec_script"].replace(
                    "image: ghcr.io/headlamp-k8s/headlamp:v0.46.0",
                    "image: ghcr.io/headlamp-k8s/headlamp:v0.42.0",
                    1,
                ),
            ),
            (
                "images.txt back to v0.42.0",
                "images",
                texts["images"].replace(
                    "ghcr.io/headlamp-k8s/headlamp:v0.46.0",
                    "ghcr.io/headlamp-k8s/headlamp:v0.42.0",
                    1,
                ),
            ),
            (
                "image-digests.tsv back to v0.42.0",
                "digests",
                texts["digests"].replace(
                    "ghcr.io/headlamp-k8s/headlamp:v0.46.0",
                    "ghcr.io/headlamp-k8s/headlamp:v0.42.0",
                    1,
                ),
            ),
            (
                "VERSIONS.md back to v0.42.0",
                "versions",
                texts["versions"].replace("| Headlamp | v0.46.0 (chart) |", "| Headlamp | v0.42.0 (chart) |", 1),
            ),
            (
                "headlamp-login-autoredirect re-added to apisix-routes.yaml",
                "routes",
                texts["routes"].replace(
                    "    - name: headlamp\n",
                    "    - name: headlamp-login-autoredirect\n    - name: headlamp\n",
                    1,
                ),
            ),
            (
                "headlamp-auth-return re-added to apisix-routes.yaml",
                "routes",
                texts["routes"].replace(
                    "    - name: headlamp\n",
                    "    - name: headlamp-auth-return\n    - name: headlamp\n",
                    1,
                ),
            ),
        ]
        for name, key, mutated_val in mutants:
            if mutated_val == texts[key]:
                print(f"FAIL mutation '{name}' did not apply (anchor drifted)")
                return 1
            candidate = {**texts, key: mutated_val}
            if not problems(candidate):
                print(f"FAIL mutation '{name}' was not caught")
                return 1
            print(f"OK mutation caught: {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
