#!/usr/bin/env python3
"""R375: Bootstrap Helm release drift check and parser verification (#728).

Invariants:
  1. scripts/test/check-bootstrap-helm-drift.sh is a read-only live check that queries
     Helm releases not managed by ArgoCD Applications (bootstrap-only releases).
  2. The parser extracts literal --set, --set-string, and --set-json flags from
     multi-line helm commands and array assignments (e.g. HELM_ARGS).
  3. Shell variables ($) and sensitive credentials (password, passwd, token, adminKey)
     are skipped and listed by name only; secret values are never printed or compared.
  4. Keys with bracket indexing (e.g. 'tolerations[0].key') and escaped dots
     (e.g. 'backup\\.velero\\.io/backup-volumes-excludes') are correctly tokenized.
  5. Values are compared type-sensitively against 'helm get values <release> -n <ns> -o json'.
  6. Drift is printed one line per drifted key (release, key, script value, live value),
     exiting 1 on drift, 0 when in sync, and 2 when prerequisites or commands fail.
  7. HELM_CMD and KUBECTL_CMD environment variables allow overriding commands for host
     and in-guest execution.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import stat
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
CHECK_SCRIPT_REL = "scripts/test/check-bootstrap-helm-drift.sh"
REGRESSION_REL = "scripts/test/regression-check-kakao.sh"
DOC_OPS_REL = "docs/vagrant/operations.md"
DOC_LESSONS_REL = "docs/common/lessons-log.md"

BOOTSTRAP_RELEASES = [
    {
        "release": "csi-driver-nfs",
        "namespace": "kube-system",
        "script": "scripts/cluster/04-addons.sh",
    },
    {
        "release": "nfs-quota-agent",
        "namespace": "nfs-quota-agent",
        "script": "scripts/cluster/05-nfs-quota-agent.sh",
    },
    {
        "release": "cnpg",
        "namespace": "platform-system",
        "script": "scripts/cluster/07-cnpg.sh",
    },
    {
        "release": "apisix-ingress-controller",
        "namespace": "platform-system",
        "script": "scripts/cluster/08-1-networking.sh",
    },
    {
        "release": "gitea",
        "namespace": "devtools",
        "script": "scripts/cluster/12-gitea.sh",
    },
    {
        "release": "apisix",
        "namespace": "platform-system",
        "script": "scripts/cluster/08-1-networking.sh",
    },
]


class _NotFound:
    def __repr__(self) -> str:
        return "<missing>"

    def __str__(self) -> str:
        return "<missing>"


NOT_FOUND = _NotFound()


def is_secret_key(key: str) -> bool:
    """Return True if the key name indicates a sensitive credential."""
    kl = key.lower()
    if "existingsecret" in kl or kl.endswith("secretname"):
        return False
    if any(s in kl for s in ("password", "passwd", "token", "adminkey", "privatekey", "apikey")):
        return True
    if kl.endswith(".secret") or kl == "secret":
        return True
    return False


def is_shell_var(val: str) -> bool:
    """Return True if the value contains a shell variable expansion."""
    return "$" in val


def parse_helm_key(key: str) -> list[str | int]:
    """Parse a Helm dot-separated key into segments, respecting escaped dots and brackets.

    Examples:
      'resources.requests.cpu' -> ['resources', 'requests', 'cpu']
      'tolerations[0].key' -> ['tolerations', 0, 'key']
      'backup\\.velero\\.io/exclude' -> ['backup.velero.io/exclude']
      'a.b[0][1].c' -> ['a', 'b', 0, 1, 'c']
    """
    tokens: list[str | int] = []
    current: list[str] = []
    i = 0
    n = len(key)
    while i < n:
        c = key[i]
        if c == "\\":
            if i + 1 < n:
                current.append(key[i + 1])
                i += 2
                continue
            current.append(c)
            i += 1
        elif c == ".":
            tokens.append("".join(current))
            current = []
            i += 1
        elif c == "[":
            if current:
                tokens.append("".join(current))
                current = []
            j = key.find("]", i)
            if j != -1:
                idx_str = key[i + 1:j]
                try:
                    tokens.append(int(idx_str))
                except ValueError:
                    tokens.append(idx_str)
                i = j + 1
                if i < n and key[i] == ".":
                    i += 1
            else:
                current.append(c)
                i += 1
        else:
            current.append(c)
            i += 1
    if current:
        tokens.append("".join(current))
    return tokens


def lookup_path(data: Any, tokens: list[str | int]) -> Any:
    """Traverse data using tokenized path segments. Return NOT_FOUND if path is absent."""
    cur = data
    for tok in tokens:
        if isinstance(tok, int):
            if isinstance(cur, list) and 0 <= tok < len(cur):
                cur = cur[tok]
            else:
                return NOT_FOUND
        else:
            if isinstance(cur, dict) and tok in cur:
                cur = cur[tok]
            else:
                return NOT_FOUND
    return cur


def compare_values(kind: str, exp_str: str, act_val: Any) -> bool:
    """Compare expected script string against live json value according to flag kind."""
    if act_val is NOT_FOUND:
        return False
    if act_val is None and exp_str == "":
        return True
    if kind == "--set-json":
        try:
            return json.loads(exp_str) == act_val
        except Exception:
            return False
    if kind == "--set-string":
        return isinstance(act_val, str) and act_val == exp_str
    # kind == "--set"
    if isinstance(act_val, bool):
        if exp_str.lower() in ("true", "false"):
            return act_val == (exp_str.lower() == "true")
        return False
    if isinstance(act_val, int):
        try:
            return act_val == int(exp_str)
        except ValueError:
            return False
    if isinstance(act_val, float):
        try:
            return act_val == float(exp_str)
        except ValueError:
            return False
    if isinstance(act_val, str):
        return act_val == exp_str
    return str(act_val) == exp_str


def extract_set_flags(script_text: str, release: str) -> list[tuple[str, str, str]]:
    """Extract (flag_kind, key, value) tuples for release from script_text."""
    # Match helm (or retry helm) upgrade [--install] <release>
    pattern = rf"(?:retry\s+)?helm\s+upgrade(?:\s+--install)?\s+{re.escape(release)}\b"
    m = re.search(pattern, script_text)
    if not m:
        return []
    start_pos = m.start()

    # Collect lines up to end of statement
    lines = script_text[start_pos:].splitlines()
    cmd_lines = []
    for line in lines:
        cmd_lines.append(line)
        stripped = line.strip()
        if not stripped.endswith("\\"):
            break
    cmd_text = "\n".join(cmd_lines)

    # Check for array variable references like "${HELM_ARGS[@]}" or "$HELM_ARGS"
    extra_text = ""
    for vname in re.findall(r"\"?\$\{?([A-Za-z0-9_]+)(?:\[@\])?\}?\"?", cmd_text):
        if vname in ("CHART_DIR", "release", "ns"):
            continue
        arr_pattern = rf"\b{re.escape(vname)}=\((.*?)\)"
        arr_m = re.search(arr_pattern, script_text, re.DOTALL)
        if arr_m:
            extra_text += "\n" + arr_m.group(1)

    full_text = cmd_text + "\n" + extra_text

    tokens = shlex.split(full_text, comments=True)
    raw_flags: list[tuple[str, str]] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in ("--set", "--set-string", "--set-json"):
            if i + 1 < len(tokens):
                raw_flags.append((t, tokens[i + 1]))
                i += 2
                continue
        elif t.startswith(("--set=", "--set-string=", "--set-json=")):
            kind, val = t.split("=", 1)
            raw_flags.append((kind, val))
        i += 1

    extracted: list[tuple[str, str, str]] = []
    for kind, arg in raw_flags:
        if "=" in arg:
            k, v = arg.split("=", 1)
        else:
            k, v = arg, ""
        extracted.append((kind, k, v.rstrip(";")))
    return extracted


def check_release_drift(
    script_text: str, release: str, live_values: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Compare extracted literal flags against live_values.

    Returns:
      (drifted_items, skipped_keys)
    """
    flags = extract_set_flags(script_text, release)
    drifted: list[dict[str, Any]] = []
    skipped: list[str] = []

    for kind, key, val in flags:
        # Secret check takes precedence: never inspect, print, or leak secret values
        if is_secret_key(key):
            skipped.append(key)
            continue
        # Shell variable check: skip dynamic values
        if is_shell_var(val):
            skipped.append(key)
            continue

        tokens = parse_helm_key(key)
        live_val = lookup_path(live_values, tokens)

        if not compare_values(kind, val, live_val):
            live_repr = "<missing>" if live_val is NOT_FOUND else json.dumps(live_val)
            drifted.append({
                "release": release,
                "key": key,
                "kind": kind,
                "script_value": val,
                "live_value": live_repr,
            })

    return drifted, skipped


def run_unit_tests() -> None:
    """Run offline unit-style behavioural tests against fixture snippets and helm JSON."""
    # Test 1: Multi-line command with backslashes
    multiline_script = r"""
helm upgrade --install test-release ./chart \
  --namespace test-ns \
  --set simple.key=hello \
  --set multiline.flag=true \
  --set count=42
"""
    flags = extract_set_flags(multiline_script, "test-release")
    assert len(flags) == 3, f"expected 3 flags, got {len(flags)}"
    assert flags[0] == ("--set", "simple.key", "hello")
    assert flags[1] == ("--set", "multiline.flag", "true")
    assert flags[2] == ("--set", "count", "42")

    # Test 2: Quoted keys with brackets and array indexing
    bracket_script = r"""
helm upgrade --install bracket-app ./chart \
  --set "tolerations[0].key=node-role.kubernetes.io/control-plane" \
  --set 'tolerations[0].operator=Exists' \
  --set "tolerations[0].effect=NoSchedule" \
  --set a.b[0]=X \
  --set 'securityContext.capabilities.drop[0]=ALL'
"""
    b_flags = extract_set_flags(bracket_script, "bracket-app")
    assert len(b_flags) == 5, f"expected 5 bracket flags, got {len(b_flags)}"
    assert b_flags[0][1] == "tolerations[0].key"
    assert b_flags[3][1] == "a.b[0]"
    assert b_flags[4][1] == "securityContext.capabilities.drop[0]"
    t_toks = parse_helm_key(b_flags[0][1])
    assert t_toks == ["tolerations", 0, "key"], f"bracket parsing failed: {t_toks}"
    a_toks = parse_helm_key(b_flags[3][1])
    assert a_toks == ["a", "b", 0], f"bracket parsing failed: {a_toks}"
    live_bracket = {
        "tolerations": [{"key": "node-role.kubernetes.io/control-plane", "operator": "Exists", "effect": "NoSchedule"}],
        "a": {"b": ["X"]},
        "securityContext": {"capabilities": {"drop": ["ALL"]}},
    }
    drift_b, _ = check_release_drift(bracket_script, "bracket-app", live_bracket)
    assert len(drift_b) == 0, f"bracket app must be in sync, got {drift_b}"

    # Test 3: Escaped dots in keys
    escaped_dot_script = r"""
helm upgrade --install gitea ./chart \
  --set-string 'gitea.podAnnotations.backup\.velero\.io/backup-volumes-excludes=data'
"""
    ed_flags = extract_set_flags(escaped_dot_script, "gitea")
    assert len(ed_flags) == 1
    tokens = parse_helm_key(ed_flags[0][1])
    assert tokens == ["gitea", "podAnnotations", "backup.velero.io/backup-volumes-excludes"], (
        f"escaped dot parsing failed: {tokens}"
    )

    # Test 3b: --set-string strictness (rejects non-string live value)
    ss_script = r"""
helm upgrade --install ss-app ./chart \
  --set-string strict.flag=true
"""
    drift_bool, _ = check_release_drift(ss_script, "ss-app", {"strict": {"flag": True}})
    assert len(drift_bool) == 1, "set-string must reject boolean live value"
    drift_ok, _ = check_release_drift(ss_script, "ss-app", {"strict": {"flag": "true"}})
    assert len(drift_ok) == 0, f"set-string must accept string live value, got {drift_ok}"

    # Test 4: --set-json handling
    json_script = r"""
helm upgrade --install json-app ./chart \
  --set-json 'config={"nested": {"val": 123}, "arr": [1, 2]}'
"""
    j_flags = extract_set_flags(json_script, "json-app")
    assert len(j_flags) == 1
    assert j_flags[0][0] == "--set-json"
    live_json_ok = {"config": {"nested": {"val": 123}, "arr": [1, 2]}}
    live_json_bad = {"config": {"nested": {"val": 999}, "arr": [1, 2]}}
    drift_ok, _ = check_release_drift(json_script, "json-app", live_json_ok)
    assert len(drift_ok) == 0, f"expected 0 drift for set-json match, got {drift_ok}"
    drift_bad, _ = check_release_drift(json_script, "json-app", live_json_bad)
    assert len(drift_bad) == 1, "expected 1 drift for set-json mismatch"

    # Test 5: Shell variable skipping and Secret skipping (zero secret leaks)
    secret_var_script = r"""
helm upgrade --install sec-app ./chart \
  --set image.tag="${IMAGE_TAG}" \
  --set config.host="prefix-${DOMAIN}" \
  --set admin.password="SUPER_SECRET_VALUE_12345" \
  --set gitea.config.database.PASSWD="${DB_PASS}" \
  --set config.apisix.adminKey= \
  --set normal.setting=production
"""
    drift_sec, skipped = check_release_drift(secret_var_script, "sec-app", {"normal": {"setting": "production"}})
    assert len(drift_sec) == 0, f"expected 0 drift, got {drift_sec}"
    assert "admin.password" in skipped
    assert "gitea.config.database.PASSWD" in skipped
    assert "config.apisix.adminKey" in skipped
    assert "image.tag" in skipped
    assert "config.host" in skipped
    # Verify secret value is never in representation of skipped keys
    skipped_text = " ".join(skipped)
    assert "SUPER_SECRET_VALUE" not in skipped_text

    # Test 6: In-sync vs drift detection
    test_script = r"""
helm upgrade --install ic-app ./chart \
  --set image.repository=apache/apisix-ingress-controller \
  --set image.tag="1.8.0" \
  --set config.apisixResourceSyncInterval=5m \
  --set config.kubernetes.resyncInterval=15m \
  --set securityContext.runAsNonRoot=true \
  --set securityContext.runAsUser=65532 \
  --set securityContext.readOnlyRootFilesystem=true
"""
    # Healthy live values (in sync)
    healthy_live = {
        "image": {"repository": "apache/apisix-ingress-controller", "tag": "1.8.0"},
        "config": {
            "apisixResourceSyncInterval": "5m",
            "kubernetes": {"resyncInterval": "15m"},
        },
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": 65532,
            "readOnlyRootFilesystem": True,
        },
    }
    drift_healthy, _ = check_release_drift(test_script, "ic-app", healthy_live)
    assert len(drift_healthy) == 0, f"expected 0 drift, got {drift_healthy}"

    # Stale live values (e.g. revision 1 without #715 hardening and with 1h/6h intervals)
    stale_live = {
        "image": {"repository": "apache/apisix-ingress-controller", "tag": "1.8.0"},
        "config": {
            "apisixResourceSyncInterval": "1h",
            "kubernetes": {"resyncInterval": "6h"},
        },
        # securityContext is missing entirely!
    }
    drift_stale, _ = check_release_drift(test_script, "ic-app", stale_live)
    drifted_keys = {d["key"]: (d["script_value"], d["live_value"]) for d in drift_stale}
    assert "config.apisixResourceSyncInterval" in drifted_keys
    assert drifted_keys["config.apisixResourceSyncInterval"] == ("5m", '"1h"')
    assert "config.kubernetes.resyncInterval" in drifted_keys
    assert drifted_keys["config.kubernetes.resyncInterval"] == ("15m", '"6h"')
    assert "securityContext.runAsNonRoot" in drifted_keys
    assert drifted_keys["securityContext.runAsNonRoot"] == ("true", "<missing>")
    assert "securityContext.readOnlyRootFilesystem" in drifted_keys
    assert drifted_keys["securityContext.readOnlyRootFilesystem"] == ("true", "<missing>")

    # Test 7: Array variable extraction (e.g. HELM_ARGS in 05-nfs-quota-agent.sh)
    array_script = r"""
HELM_ARGS=(
  --namespace nfs-quota-agent
  --set image.repository=ghcr.io/dasomel/nfs-quota-agent
  --set image.tag="${NFS_QUOTA_AGENT_VERSION}"
  --set config.nfsBasePath=/export
  --set webUI.enabled=true
  --set-string nodeSelector.nfs-server=true
)

helm upgrade --install nfs-quota-agent "${CHART_DIR}" "${HELM_ARGS[@]}"
"""
    arr_flags = extract_set_flags(array_script, "nfs-quota-agent")
    assert len(arr_flags) == 5, f"expected 5 array flags, got {len(arr_flags)}"
    assert arr_flags[0] == ("--set", "image.repository", "ghcr.io/dasomel/nfs-quota-agent")
    assert arr_flags[4] == ("--set-string", "nodeSelector.nfs-server", "true")

    # Test 8: Empty script command (e.g. apisix with only -f values.yaml)
    empty_script = r"""
helm upgrade --install apisix ./chart \
  --namespace platform-system \
  -f /tmp/apisix-values.yaml
"""
    emp_flags = extract_set_flags(empty_script, "apisix")
    assert len(emp_flags) == 0
    drift_emp, _ = check_release_drift(empty_script, "apisix", {})
    assert len(drift_emp) == 0


def verify_repository(root: Path) -> None:
    """Verify repository integration, script permissions, wiring, and documentation."""
    check_path = root / CHECK_SCRIPT_REL
    assert check_path.is_file(), f"missing check script {CHECK_SCRIPT_REL}"
    mode = os.stat(check_path).st_mode
    assert bool(mode & stat.S_IXUSR), f"{CHECK_SCRIPT_REL} must be executable (+x)"

    script_text = check_path.read_text(encoding="utf-8")
    assert "set -euo pipefail" in script_text, f"{CHECK_SCRIPT_REL} must retain set -euo pipefail"
    assert "${HELM_CMD:-" in script_text, f"{CHECK_SCRIPT_REL} must support HELM_CMD override"
    assert "${KUBECTL_CMD:-" in script_text, f"{CHECK_SCRIPT_REL} must support KUBECTL_CMD override"
    assert re.search(r"FAIL: detected drift.*?exit 1", script_text, re.DOTALL), (
        f"{CHECK_SCRIPT_REL} must exit 1 on drift"
    )
    assert re.search(r"cannot connect[^\n]*\n\s*exit 2", script_text), (
        f"{CHECK_SCRIPT_REL} must exit 2 on cluster unreachable"
    )
    assert "exit 0" in script_text, f"{CHECK_SCRIPT_REL} must exit 0 when in sync"

    reg_path = root / REGRESSION_REL
    reg_text = reg_path.read_text(encoding="utf-8")
    assert "check R375" in reg_text, f"{REGRESSION_REL} must wire check R375"
    assert "check-bootstrap-helm-drift.py --mutation-verify" in reg_text, (
        f"{REGRESSION_REL} R375 must call check-bootstrap-helm-drift.py --mutation-verify"
    )

    ops_path = root / DOC_OPS_REL
    ops_text = ops_path.read_text(encoding="utf-8")
    assert "check-bootstrap-helm-drift.sh" in ops_text, (
        f"{DOC_OPS_REL} must document check-bootstrap-helm-drift.sh"
    )

    lessons_path = root / DOC_LESSONS_REL
    lessons_text = lessons_path.read_text(encoding="utf-8")
    assert "#728" in lessons_text, f"{DOC_LESSONS_REL} must include #728 lesson row"
    assert "check-bootstrap-helm-drift.sh" in lessons_text, (
        f"{DOC_LESSONS_REL} row must mention check-bootstrap-helm-drift.sh"
    )

    # Real repository extraction check across all bootstrap scripts
    for item in BOOTSTRAP_RELEASES:
        sp = root / item["script"]
        assert sp.is_file(), f"missing bootstrap script: {item['script']}"
        st = sp.read_text(encoding="utf-8")
        rel = item["release"]
        flags = extract_set_flags(st, rel)
        if rel == "apisix":
            assert len(flags) == 0, "apisix has no --set flags"
        else:
            assert len(flags) > 0, f"expected flags for {rel} in {item['script']}, got 0"


def mutation_verify(root: Path) -> int:
    """Run mutation tests to verify that every invariant actively rejects mutations."""
    mutations: list[tuple[str, str, str]] = [
        # 1. Break --set-string handling
        ("check-bootstrap-helm-drift.py", 'if kind == "--set-string":', 'if kind == "--invalid-string":'),
        # 2. Break --set-json handling
        ("check-bootstrap-helm-drift.py", 'if kind == "--set-json":', 'if kind == "--invalid-json":'),
        # 3. Disable shell variable skipping
        ("check-bootstrap-helm-drift.py", 'return "$" in val', 'return False'),
        # 4. Disable secret key skipping
        ("check-bootstrap-helm-drift.py", '"password", "passwd"', '"disabled_pwd"'),
        # 5. Disable bracket indexing in parse_helm_key
        ("check-bootstrap-helm-drift.py", 'elif c == "[":', 'elif c == "{":'),
        # 6. Disable escaped dot unescaping in parse_helm_key
        ("check-bootstrap-helm-drift.py", r'if c == "\\":', 'if c == "§":'),
        # 7. Check script: drop HELM_CMD override
        (CHECK_SCRIPT_REL, 'HELM_CMD="${HELM_CMD:-helm}"', 'HELM_CMD="helm"'),
        # 8. Check script: drop KUBECTL_CMD override
        (CHECK_SCRIPT_REL, 'KUBECTL_CMD="${KUBECTL_CMD:-kubectl}"', 'KUBECTL_CMD="kubectl"'),
        # 9. Check script: drop exit 1 on drift
        (CHECK_SCRIPT_REL, "  echo \"FAIL: detected drift across ${DRIFT_COUNT} bootstrap Helm release(s).\"\n  exit 1", "  echo \"FAIL: detected drift across ${DRIFT_COUNT} bootstrap Helm release(s).\"\n  exit 0"),
        # 10. Check script: drop exit 2 on cannot run
        (CHECK_SCRIPT_REL, "  echo \"ERROR: cannot connect to Kubernetes cluster using '${KUBECTL_CMD}'\" >&2\n  exit 2", "  echo \"ERROR: cannot connect to Kubernetes cluster using '${KUBECTL_CMD}'\" >&2\n  exit 0"),
        # 11. Regression wiring: drop R375
        (REGRESSION_REL, "check R375", "check R9999"),
        # 12. Docs: drop check-bootstrap-helm-drift.sh from operations doc
        (DOC_OPS_REL, "check-bootstrap-helm-drift.sh", "other-check-script.sh"),
        # 13. Lessons log: drop #728
        (DOC_LESSONS_REL, "#728", "#99999"),
        # 14. Value comparison: break boolean string comparison
        ("check-bootstrap-helm-drift.py", 'exp_str.lower() in ("true", "false")', 'exp_str.lower() in ()'),
    ]

    caught = 0
    # Save original sources
    py_path = Path(__file__).resolve()
    py_orig = py_path.read_text(encoding="utf-8")
    check_orig = (root / CHECK_SCRIPT_REL).read_text(encoding="utf-8")
    reg_orig = (root / REGRESSION_REL).read_text(encoding="utf-8")
    ops_orig = (root / DOC_OPS_REL).read_text(encoding="utf-8")
    lessons_orig = (root / DOC_LESSONS_REL).read_text(encoding="utf-8")

    source_map = {
        "check-bootstrap-helm-drift.py": (py_path, py_orig),
        CHECK_SCRIPT_REL: (root / CHECK_SCRIPT_REL, check_orig),
        REGRESSION_REL: (root / REGRESSION_REL, reg_orig),
        DOC_OPS_REL: (root / DOC_OPS_REL, ops_orig),
        DOC_LESSONS_REL: (root / DOC_LESSONS_REL, lessons_orig),
    }

    try:
        for file_key, old_frag, new_frag in mutations:
            target_path, original_text = source_map[file_key]
            assert old_frag in original_text, f"mutation fragment missing in {file_key}: {old_frag!r}"
            mutated_text = original_text.replace(old_frag, new_frag)
            target_path.write_text(mutated_text, encoding="utf-8")

            survived = False
            try:
                # Reload functions if this file itself was mutated
                if file_key == "check-bootstrap-helm-drift.py":
                    # Run a subprocess to test the mutated file
                    res = subprocess_verify(target_path, root)
                    if res == 0:
                        survived = True
                    else:
                        caught += 1
                else:
                    verify_repository(root)
                    run_unit_tests()
                    survived = True
            except (AssertionError, KeyError, ValueError):
                caught += 1
            finally:
                # Restore immediately
                target_path.write_text(original_text, encoding="utf-8")

            if survived:
                raise AssertionError(f"mutation survived in {file_key}: {old_frag!r} -> {new_frag!r}")

    finally:
        # Guarantee full restoration
        for target_path, orig in source_map.values():
            target_path.write_text(orig, encoding="utf-8")

    return caught


def subprocess_verify(script_file: Path, root: Path) -> int:
    """Helper to run verification in a fresh python subprocess for self-mutation."""
    import subprocess
    cmd = [sys.executable, str(script_file), "--self-test", "--root", str(root)]
    return subprocess.run(cmd, capture_output=True, text=True).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--mutation-verify", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--list-releases", action="store_true")
    parser.add_argument("--compare-stdin", action="store_true")
    parser.add_argument("--release", type=str)
    parser.add_argument("--script", type=Path)
    args = parser.parse_args()

    if args.list_releases:
        for item in BOOTSTRAP_RELEASES:
            print(f"{item['release']}\t{item['namespace']}\t{item['script']}")
        return 0

    if args.compare_stdin:
        if not args.release or not args.script:
            print("ERROR: --compare-stdin requires --release and --script", file=sys.stderr)
            return 2
        try:
            live_data = json.load(sys.stdin)
            if not isinstance(live_data, dict):
                live_data = {}
        except Exception as e:
            print(f"ERROR: invalid JSON input: {e}", file=sys.stderr)
            return 2

        script_text = args.script.read_text(encoding="utf-8")
        drifted, skipped = check_release_drift(script_text, args.release, live_data)

        if skipped:
            # Report skipped keys by name only (never leak values)
            print(f"SKIPPED: release={args.release} keys={', '.join(skipped)} (shell variable or secret)")

        if drifted:
            for d in drifted:
                print(
                    f"DRIFT: release={d['release']} key={d['key']} "
                    f"script={d['script_value']} live={d['live_value']}"
                )
            return 1

        print(f"IN_SYNC: release={args.release}")
        return 0

    if args.self_test:
        verify_repository(args.root)
        run_unit_tests()
        return 0

    # Normal verification run
    verify_repository(args.root)
    run_unit_tests()

    if args.mutation_verify:
        caught = mutation_verify(args.root)
        print(f"PASS: R375 bootstrap helm drift check + offline parser behavioural tests ({caught} mutations rejected)")
    else:
        print("PASS: R375 bootstrap helm drift check + offline parser behavioural tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
