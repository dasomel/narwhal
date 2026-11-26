#!/usr/bin/env python3
"""R421: Falco least-privileged modern_ebpf securityContext, chart render, and narrowed CIS acceptance.

Verifies (D-FALCO-LEAST-PRIV):
1. gitops/charts/narwhal-apps/templates/falco.yaml pins chart 9.2.1 with driver.kind=modern_ebpf,
   driver.modernEbpf.leastPrivileged=true, podSecurityContext.seccompProfile.type=RuntimeDefault,
   and containerSecurityContext setting allowPrivilegeEscalation=false, readOnlyRootFilesystem=true,
   capabilities.drop=[ALL], capabilities.add=[BPF, SYS_RESOURCE, PERFMON, SYS_PTRACE], and no
   privileged flag, while keeping D-falco-rofs and D-FALCO-NOISE intact.
2. Offline `helm template` against .review/charts/falco (or falco-9.2.1.tgz when present) renders:
   - pod annotation container.apparmor.security.beta.kubernetes.io/falco=unconfined (from chart
     pod-template.tpl:18 when leastPrivileged=true so containerd AppArmor does not block /host/proc
     ptrace reads for container/K8s metadata),
   - pod securityContext.seccompProfile.type=RuntimeDefault,
   - container securityContext with allowPrivilegeEscalation=false, readOnlyRootFilesystem=true,
     capabilities.drop=[ALL], capabilities.add=[BPF, SYS_RESOURCE, PERFMON, SYS_PTRACE], and no
     privileged key,
   - /root/.falco emptyDir and /host/run/containerd CRI socket volumeMounts.
3. portal/src/data/compliance-acceptance-rules.json narrows cfg:security-system:DaemonSet:falco:accept
   to ["KSV002", "KSV012", "KSV020", "KSV021", "KSV022", "KSV023", "KSV106", "KSV121"] (removing
   KSV001, KSV003, KSV017 and keeping KSV004, KSV030, KSV104 actionable).
4. portal/src/lib/i18n.ts (en + ko) and docs/common/compliance-hardening.md preserve the original
   D-falco-rofs caveat text and append the D-FALCO-LEAST-PRIV KSV002/KSV022 rationale, with the
   Markdown table cell matching the English i18n text exactly.
5. --mutation-verify rejects broken variants across all verified files and chart render inputs.
"""

from __future__ import annotations

import copy
import glob
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
FILES = {
    "falco_yaml": ROOT / "gitops/charts/narwhal-apps/templates/falco.yaml",
    "rules": ROOT / "portal/src/data/compliance-acceptance-rules.json",
    "i18n": ROOT / "portal/src/lib/i18n.ts",
    "doc": ROOT / "docs/common/compliance-hardening.md",
    "reg": ROOT / "scripts/test/regression-check-kakao.sh",
}

HELM_COMMENT_RE = re.compile(r"\A\{\{-\s*/\*.*?\*/\s*-?\}\}\s*", re.S)
EXPECTED_CAPS_ADD = ["BPF", "SYS_RESOURCE", "PERFMON", "SYS_PTRACE"]
EXPECTED_FALCO_CHECKS = ["KSV002", "KSV012", "KSV020", "KSV021", "KSV022", "KSV023", "KSV106", "KSV121"]
FORBIDDEN_FALCO_CHECKS = ("KSV001", "KSV003", "KSV004", "KSV014", "KSV017", "KSV030", "KSV104", "KSV118")
REASON_KEY = "compliance.acceptedReason.falcoAccept"
ORIG_EN_PREFIX = (
    "Falco's privileged modern_ebpf mode is accepted after the initialization permission errors "
    "recorded in falco.yaml D-falco-rofs. The chart’s driver.modernEbpf.leastPrivileged path "
    "remains untested and is the follow-up."
)
ORIG_KO_PREFIX = (
    "falco.yaml D-falco-rofs에 기록된 초기화 권한 오류로 privileged modern_ebpf 모드를 수용합니다. "
    "차트의 driver.modernEbpf.leastPrivileged 경로는 미검증 상태이며 후속 검증 대상입니다."
)


def _load_falco_doc(text: str) -> dict:
    body = HELM_COMMENT_RE.sub("", text, count=1)
    return yaml.safe_load(body) or {}


def _find_falco_chart(root: Path) -> Path | None:
    candidates = [
        root / ".review/charts/falco",
        root / ".review/charts/falco-9.2.1.tgz",
    ]
    for c in candidates:
        if c.exists():
            return c
    for base in (root, root.parents[2]):
        matches = sorted(glob.glob(str(base / "narwhal-airgap-bundle-*/charts/falco-9.2.1.tgz")))
        if matches:
            return Path(matches[0])
    return None


def _extract_i18n_pair(i18n_text: str, key: str) -> tuple[str | None, str | None]:
    pattern = re.compile(r'"' + re.escape(key) + r'":\s*"([^"\n]+)"')
    hits = pattern.findall(i18n_text)
    if len(hits) != 2:
        return None, None
    return hits[0], hits[1]


def _extract_doc_falco_row(doc_text: str) -> tuple[list[str], str] | None:
    for line in doc_text.splitlines():
        trimmed = line.strip()
        if not trimmed.startswith("|"):
            continue
        cells = [c.strip() for c in trimmed[1:-1].split("|")]
        if len(cells) == 5 and cells[0] == "security-system" and cells[1] == "DaemonSet/falco":
            checks = [c.strip() for c in cells[2].split(",") if c.strip()]
            return checks, cells[4]
    return None


def check(texts: dict[str, str]) -> list[str]:
    errors: list[str] = []

    # 1. falco.yaml values & decision markers
    falco_text = texts["falco_yaml"]
    for marker in ("D-FALCO-LEAST-PRIV", "D-falco-rofs", "D-FALCO-NOISE"):
        if marker not in falco_text:
            errors.append(f"falco.yaml missing decision marker {marker}")
    if falco_text.count("D-FALCO-LEAST-PRIV") < 2:
        errors.append("falco.yaml must reference D-FALCO-LEAST-PRIV on both driver.modernEbpf.leastPrivileged and podSecurityContext/containerSecurityContext")
    try:
        doc = _load_falco_doc(falco_text)
    except yaml.YAMLError as exc:
        return [f"falco.yaml YAML parse error: {exc}"]

    source = (doc.get("spec") or {}).get("source") or {}
    if str(source.get("targetRevision")) != "9.2.1":
        errors.append(f"falco.yaml targetRevision must be 9.2.1, got {source.get('targetRevision')!r}")
    vals = (source.get("helm") or {}).get("valuesObject") or {}

    driver = vals.get("driver") or {}
    if driver.get("enabled") is not True or driver.get("kind") != "modern_ebpf":
        errors.append("falco.yaml driver must set enabled: true and kind: modern_ebpf")
    if (driver.get("modernEbpf") or {}).get("leastPrivileged") is not True:
        errors.append("falco.yaml driver.modernEbpf.leastPrivileged must be true")

    psc = vals.get("podSecurityContext") or {}
    if psc.get("seccompProfile") != {"type": "RuntimeDefault"}:
        errors.append(f"falco.yaml podSecurityContext.seccompProfile must be {{type: RuntimeDefault}}, got {psc.get('seccompProfile')!r}")

    csc = vals.get("containerSecurityContext") or {}
    if "privileged" in csc:
        errors.append("falco.yaml containerSecurityContext must not set privileged")
    if csc.get("allowPrivilegeEscalation") is not False:
        errors.append("falco.yaml containerSecurityContext.allowPrivilegeEscalation must be false")
    if csc.get("readOnlyRootFilesystem") is not True:
        errors.append("falco.yaml containerSecurityContext.readOnlyRootFilesystem must be true")
    caps = csc.get("capabilities") or {}
    if caps.get("drop") != ["ALL"]:
        errors.append(f"falco.yaml containerSecurityContext.capabilities.drop must be ['ALL'], got {caps.get('drop')!r}")
    if caps.get("add") != EXPECTED_CAPS_ADD:
        errors.append(f"falco.yaml containerSecurityContext.capabilities.add must be {EXPECTED_CAPS_ADD}, got {caps.get('add')!r}")
    if (csc.get("seccompProfile") or {}).get("type") == "Unconfined":
        errors.append("falco.yaml containerSecurityContext must not override pod seccomp with Unconfined")

    # 2. compliance-acceptance-rules.json
    try:
        rules_data = json.loads(texts["rules"])
    except json.JSONDecodeError as exc:
        return [f"compliance-acceptance-rules.json invalid JSON: {exc}"]

    falco_rules = [
        r for r in rules_data.get("configAudit", [])
        if (r.get("namespace"), r.get("kind"), r.get("name")) == ("security-system", "DaemonSet", "falco")
    ]
    if len(falco_rules) != 1:
        errors.append(f"expected exactly 1 configAudit rule for security-system/DaemonSet/falco, found {len(falco_rules)}")
    else:
        frule = falco_rules[0]
        checks = frule.get("checks") or []
        for forbidden in FORBIDDEN_FALCO_CHECKS:
            if forbidden in checks:
                errors.append(f"security-system/DaemonSet/falco must leave fixed check {forbidden} actionable")
        if checks != EXPECTED_FALCO_CHECKS:
            errors.append(f"security-system/DaemonSet/falco checks must be {EXPECTED_FALCO_CHECKS}, got {checks}")
        if frule.get("reasonKey") != REASON_KEY:
            errors.append(f"security-system/DaemonSet/falco reasonKey must be {REASON_KEY!r}")

    # 3. i18n.ts (ko + en) preserves original text and appends D-FALCO-LEAST-PRIV rationale
    ko_reason, en_reason = _extract_i18n_pair(texts["i18n"], REASON_KEY)
    if not ko_reason or not en_reason:
        errors.append(f"i18n.ts must define both ko and en strings for {REASON_KEY}")
    else:
        if not en_reason.startswith(ORIG_EN_PREFIX):
            errors.append("i18n.ts en falcoAccept must preserve the original text as prefix before appending")
        if not ko_reason.startswith(ORIG_KO_PREFIX):
            errors.append("i18n.ts ko falcoAccept must preserve the original text as prefix before appending")
        for label, text in (("en", en_reason), ("ko", ko_reason)):
            for token in (
                "D-FALCO-LEAST-PRIV",
                "container.apparmor.security.beta.kubernetes.io/falco: unconfined",
                "BPF, SYS_RESOURCE, PERFMON, SYS_PTRACE",
                "KSV002",
                "KSV022",
            ):
                if token not in text:
                    errors.append(f"i18n.ts {label} falcoAccept missing {token!r}")

    # 4. docs/common/compliance-hardening.md parity
    doc_row = _extract_doc_falco_row(texts["doc"])
    if doc_row is None:
        errors.append("docs/common/compliance-hardening.md missing security-system | DaemonSet/falco table row")
    else:
        doc_checks, doc_reason = doc_row
        if doc_checks != EXPECTED_FALCO_CHECKS:
            errors.append(f"docs/common/compliance-hardening.md falco checks {doc_checks} != {EXPECTED_FALCO_CHECKS}")
        if en_reason and doc_reason != en_reason:
            errors.append("docs/common/compliance-hardening.md falco reason cell must match English i18n text exactly")
    if "D-FALCO-LEAST-PRIV" not in texts["doc"]:
        errors.append("docs/common/compliance-hardening.md must document D-FALCO-LEAST-PRIV")

    # 5. regression-check-kakao.sh registration
    if "check R421 " not in texts["reg"] or "check-falco-least-privileged.py --mutation-verify" not in texts["reg"]:
        errors.append("scripts/test/regression-check-kakao.sh must register R421 with --mutation-verify")

    return errors


def _render_falco_daemonset(chart_path: Path, vals: dict) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
        yaml.dump(vals, f)
        f.flush()
        out = subprocess.run(
            [
                "helm", "template", "falco", str(chart_path),
                "-n", "security-system", "-f", f.name,
                "--show-only", "templates/daemonset.yaml",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    return yaml.safe_load(out)


def verify_rendered_daemonset(ds: dict) -> list[str]:
    errs: list[str] = []
    tpl_meta = ((ds.get("spec") or {}).get("template") or {}).get("metadata") or {}
    ann = tpl_meta.get("annotations") or {}
    if ann.get("container.apparmor.security.beta.kubernetes.io/falco") != "unconfined":
        errs.append("rendered DaemonSet missing container.apparmor.security.beta.kubernetes.io/falco: unconfined annotation")

    pspec = ((ds.get("spec") or {}).get("template") or {}).get("spec") or {}
    if pspec.get("securityContext") != {"seccompProfile": {"type": "RuntimeDefault"}}:
        errs.append(f"rendered DaemonSet pod securityContext mismatch: {pspec.get('securityContext')!r}")

    containers = pspec.get("containers") or []
    falco_c = next((c for c in containers if c.get("name") == "falco"), None)
    if not falco_c:
        errs.append("rendered DaemonSet missing falco container")
        return errs

    sc = falco_c.get("securityContext") or {}
    if "privileged" in sc:
        errs.append(f"rendered falco container must not set privileged: {sc!r}")
    if sc.get("allowPrivilegeEscalation") is not False:
        errs.append("rendered falco container must set allowPrivilegeEscalation: false")
    if sc.get("readOnlyRootFilesystem") is not True:
        errs.append("rendered falco container must set readOnlyRootFilesystem: true")
    caps = sc.get("capabilities") or {}
    if caps.get("drop") != ["ALL"] or caps.get("add") != EXPECTED_CAPS_ADD:
        errs.append(f"rendered falco container capabilities mismatch: {caps!r}")

    mounts = {m.get("mountPath"): m.get("name") for m in (falco_c.get("volumeMounts") or []) if isinstance(m, dict)}
    if mounts.get("/root/.falco") != "root-falco-fs":
        errs.append("rendered falco container missing /root/.falco volumeMount")
    if mounts.get("/host/run/containerd") != "container-engine-socket-0":
        errs.append("rendered falco container missing /host/run/containerd CRI socket mount")
    return errs


MUTATIONS: list[tuple[str, str, str, str]] = [
    ("leastPrivileged reverted to false", "falco_yaml", "            leastPrivileged: true", "            leastPrivileged: false"),
    ("pod seccomp removed", "falco_yaml", "        podSecurityContext:\n          seccompProfile:\n            type: RuntimeDefault\n", ""),
    ("pod seccomp Unconfined", "falco_yaml", "            type: RuntimeDefault", "            type: Unconfined"),
    ("privileged re-added to containerSecurityContext", "falco_yaml", "        containerSecurityContext:\n          allowPrivilegeEscalation: false", "        containerSecurityContext:\n          privileged: true\n          allowPrivilegeEscalation: false"),
    ("allowPrivilegeEscalation set to true", "falco_yaml", "          allowPrivilegeEscalation: false", "          allowPrivilegeEscalation: true"),
    ("readOnlyRootFilesystem set to false", "falco_yaml", "          readOnlyRootFilesystem: true", "          readOnlyRootFilesystem: false"),
    ("drop ALL removed", "falco_yaml", "            drop:\n              - ALL\n", ""),
    ("SYS_PTRACE removed from capabilities.add", "falco_yaml", "              - SYS_PTRACE\n", ""),
    ("SYS_ADMIN added to capabilities.add", "falco_yaml", "              - SYS_PTRACE\n", "              - SYS_PTRACE\n              - SYS_ADMIN\n"),
    ("D-FALCO-LEAST-PRIV comment removed", "falco_yaml", "D-FALCO-LEAST-PRIV", "D-REMOVED"),
    ("KSV017 re-accepted in rules JSON", "rules", '        "KSV002",\n', '        "KSV002",\n        "KSV017",\n'),
    ("KSV001 re-accepted in rules JSON", "rules", '        "KSV002",\n', '        "KSV001",\n        "KSV002",\n'),
    ("KSV003 re-accepted in rules JSON", "rules", '        "KSV002",\n', '        "KSV002",\n        "KSV003",\n'),
    ("KSV002 dropped from rules JSON", "rules", '        "KSV002",\n', ""),
    (
        "KSV022 dropped from rules JSON",
        "rules",
        '      "name": "falco",\n      "checks": [\n        "KSV002",\n        "KSV012",\n        "KSV020",\n        "KSV021",\n        "KSV022",\n',
        '      "name": "falco",\n      "checks": [\n        "KSV002",\n        "KSV012",\n        "KSV020",\n        "KSV021",\n',
    ),
    ("original en reason prefix dropped", "i18n", ORIG_EN_PREFIX, "Replaced prefix."),
    ("original ko reason prefix dropped", "i18n", ORIG_KO_PREFIX, "대체된 문구."),
    ("doc table checks out of sync", "doc", "| security-system | DaemonSet/falco | KSV002, KSV012, KSV020, KSV021, KSV022, KSV023, KSV106, KSV121 |", "| security-system | DaemonSet/falco | KSV012, KSV020, KSV021, KSV023, KSV106, KSV121 |"),
    ("R421 registration removed", "reg", "check R421 ", "check R9999 "),
]


def main() -> int:
    texts = {k: p.read_text(encoding="utf-8") for k, p in FILES.items()}
    errors = check(texts)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1

    chart_path = _find_falco_chart(ROOT)
    render_note = "helm render SKIPPED (no chart)"
    if chart_path is not None:
        vals = ((_load_falco_doc(texts["falco_yaml"]).get("spec") or {}).get("source") or {}).get("helm", {}).get("valuesObject") or {}
        ds = _render_falco_daemonset(chart_path, vals)
        r_errs = verify_rendered_daemonset(ds)
        if r_errs:
            print("\n".join(r_errs), file=sys.stderr)
            return 1
        render_note = f"helm render verified ({chart_path.name})"

    if "--mutation-verify" in sys.argv:
        caught = 0
        for name, key, old, new in MUTATIONS:
            if old not in texts[key]:
                print(f"FAIL: mutation anchor not found for {name}", file=sys.stderr)
                return 1
            mutated = copy.deepcopy(texts)
            mutated[key] = mutated[key].replace(old, new, 1)
            if not check(mutated):
                print(f"FAIL: mutation survived: {name}", file=sys.stderr)
                return 1
            caught += 1

        if chart_path is not None:
            # Behavioral chart render mutations: prove empty containerSecurityContext or leastPrivileged=false fail render check
            base_vals = ((_load_falco_doc(texts["falco_yaml"]).get("spec") or {}).get("source") or {}).get("helm", {}).get("valuesObject") or {}
            empty_csc_vals = copy.deepcopy(base_vals)
            empty_csc_vals["containerSecurityContext"] = {}
            if not verify_rendered_daemonset(_render_falco_daemonset(chart_path, empty_csc_vals)):
                print("FAIL: chart render mutation survived: empty containerSecurityContext", file=sys.stderr)
                return 1
            caught += 1

            no_lp_vals = copy.deepcopy(base_vals)
            no_lp_vals["driver"]["modernEbpf"]["leastPrivileged"] = False
            if not verify_rendered_daemonset(_render_falco_daemonset(chart_path, no_lp_vals)):
                print("FAIL: chart render mutation survived: leastPrivileged=false (missing AppArmor annotation)", file=sys.stderr)
                return 1
            caught += 1

        print(f"PASS: R421 Falco least-privileged modern_ebpf + {render_note} ({caught} mutations rejected)")
        return 0

    print(f"PASS: R421 Falco least-privileged modern_ebpf + {render_note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
