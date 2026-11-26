#!/usr/bin/env python3
"""R376: Bootstrap Helm release drift check and parser verification (#728).

Invariants:
  1. scripts/test/check-bootstrap-helm-drift.sh is a read-only live check that queries
     Helm releases not managed by ArgoCD Applications (bootstrap-only releases).
  2. The parser extracts literal --set, --set-string, and --set-json flags from
     multi-line helm commands, wrapped invocations (sudo, env VAR=x, VAR=x, if/!/then,
     $(...)-free command prefixes, global flags between helm and upgrade, variable forms
     "$HELM"/${HELM_CMD}), and array assignments (e.g. HELM_ARGS).
  3. Releases whose values come solely from -f <file> or heredocs are reported as
     UNCHECKED (values file not parsed) and counted separately; they never claim IN_SYNC.
     Releases with both values files and --set flags check the --set flags and add a note.
  4. Comma-separated assignments (--set a=x,b=y) are split into individual key-value pairs,
     preserving backslash-escaped commas (\\,) and list braces ({a,b}) as single values.
  5. Values are compared type-sensitively against 'helm get values <release> -n <ns> -o json':
     --set values undergo Helm-style strvals coercion: exact true/false/null, and
     integers matching ^-?(0|[1-9][0-9]*)$ in signed 64-bit range; floats (1.0), leading
     zeros (01), exponent forms (1e3), hex (0x10), and out-of-range integers stay strings;
     braces lists ({a,b}) are supported; --set-string strictly requires string type.
  6. Shell variables ($) and sensitive credentials (password, passwd, token, adminKey)
     are skipped and listed by name only; secret values are never printed or compared.
     On helm failure, invalid JSON, or drift, secret values are never leaked in stdout/stderr,
     and no temporary files are left in TMPDIR.
  7. Multi-word command overrides via HELM_CMD and KUBECTL_CMD (e.g.
     HELM_CMD="sudo helm --kubeconfig /etc/kubernetes/admin.conf") are supported via
     read -r -a array splitting (arguments with spaces are not supported).
  8. --mutation-verify executes mutations in an isolated temporary directory, never
     mutating tracked repository files.
  9. Completeness assertion fail-closed: counts every logical line in scripts/cluster/*.sh
     containing 'helm' ... 'upgrade' or variable upgrade; each must be parsed into a
     bootstrap release or listed in CLUSTER_HELM_ALLOWLIST with a reason; unparsed or
     untracked occurrences fail the check.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
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

INT64_MIN = -9223372036854775808
INT64_MAX = 9223372036854775807
INT_RE = re.compile(r"^-?(0|[1-9][0-9]*)$")

# Explicit allowlist of Helm upgrade commands in scripts/cluster/*.sh that are
# not bootstrap drift targets (managed by ArgoCD Applications or operational scripts).
CLUSTER_HELM_ALLOWLIST: dict[tuple[str, str], str] = {
    ("scripts/cluster/08-1-networking.sh", "metallb"): (
        "Managed by ArgoCD Application (gitops/charts/narwhal-apps/templates/metallb.yaml)"
    ),
    ("scripts/cluster/08-1-networking.sh", "cert-manager"): (
        "Managed by ArgoCD Application (gitops/charts/narwhal-apps/templates/cert-manager.yaml)"
    ),
    ("scripts/cluster/08-2-monitoring.sh", "prometheus-stack"): (
        "Managed by ArgoCD Application (gitops/charts/narwhal-apps/templates/prometheus-stack.yaml)"
    ),
    ("scripts/cluster/08-2-monitoring.sh", "loki"): (
        "Managed by ArgoCD Application (gitops/charts/narwhal-apps/templates/loki.yaml)"
    ),
    ("scripts/cluster/08-2-monitoring.sh", "k8s-monitoring"): (
        "Managed by ArgoCD Application (gitops/charts/narwhal-apps/templates/k8s-monitoring.yaml)"
    ),
    ("scripts/cluster/08-2-monitoring.sh", "tempo"): (
        "Managed by ArgoCD Application (gitops/charts/narwhal-apps/templates/tempo.yaml)"
    ),
    ("scripts/cluster/08-3-security.sh", "kyverno"): (
        "Managed by ArgoCD Application (gitops/charts/narwhal-apps/templates/kyverno.yaml)"
    ),
    ("scripts/cluster/08-3-security.sh", "headlamp"): (
        "Managed by ArgoCD Application (gitops/charts/narwhal-apps/templates/headlamp.yaml)"
    ),
    ("scripts/cluster/08-4-storage.sh", "seaweedfs"): (
        "Managed by ArgoCD Application (gitops/charts/narwhal-apps/templates/seaweedfs.yaml)"
    ),
    ("scripts/cluster/08-4-storage.sh", "openbao"): (
        "Managed by ArgoCD Application (gitops/charts/narwhal-apps/templates/openbao.yaml)"
    ),
    ("scripts/cluster/08-4-storage.sh", "velero"): (
        "Managed by ArgoCD Application (gitops/charts/narwhal-apps/templates/velero.yaml)"
    ),
    ("scripts/cluster/08-5-registry.sh", "harbor"): (
        "Managed by ArgoCD Application (gitops/charts/narwhal-apps/templates/harbor.yaml)"
    ),
    ("scripts/cluster/09-istio-ambient.sh", "istio-base"): (
        "Managed by ArgoCD Application (gitops/charts/narwhal-apps/templates/istio-ambient.yaml)"
    ),
    ("scripts/cluster/09-istio-ambient.sh", "istiod"): (
        "Managed by ArgoCD Application (gitops/charts/narwhal-apps/templates/istio-ambient.yaml)"
    ),
    ("scripts/cluster/09-istio-ambient.sh", "istio-cni"): (
        "Managed by ArgoCD Application (gitops/charts/narwhal-apps/templates/istio-ambient.yaml)"
    ),
    ("scripts/cluster/09-istio-ambient.sh", "ztunnel"): (
        "Managed by ArgoCD Application (gitops/charts/narwhal-apps/templates/ztunnel.yaml)"
    ),
    ("scripts/cluster/rotate-apisix-admin-key.sh", "apisix-ingress-controller"): (
        "Operational admin key rotation maintenance script, not initial bootstrap"
    ),
}


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


def split_helm_set_args(arg: str) -> list[str]:
    """Split comma-separated Helm --set assignments.

    Backslash-escaped commas (\\,) and braces ({a,b}) list syntax stay one value.
    """
    items: list[str] = []
    curr: list[str] = []
    brace_depth = 0
    i = 0
    n = len(arg)
    while i < n:
        c = arg[i]
        if c == "\\" and i + 1 < n and arg[i + 1] == ",":
            # Backslash-escaped comma stays in the value
            curr.append("\\,")
            i += 2
            continue
        elif c == "{":
            brace_depth += 1
            curr.append(c)
            i += 1
        elif c == "}":
            if brace_depth > 0:
                brace_depth -= 1
            curr.append(c)
            i += 1
        elif c == "," and brace_depth == 0:
            items.append("".join(curr))
            curr = []
            i += 1
        else:
            curr.append(c)
            i += 1
    if curr:
        items.append("".join(curr))
    return items


def strip_comments(text: str) -> str:
    """Strip bash comments (# to end of line) preserving quotes and backslash escapes."""
    out: list[str] = []
    in_single = False
    in_double = False
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\" and not in_single:
            out.append(c)
            if i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            i += 1
            continue
        if c == "'" and not in_double:
            in_single = not in_single
            out.append(c)
            i += 1
            continue
        if c == '"' and not in_single:
            in_double = not in_double
            out.append(c)
            i += 1
            continue
        if c == "#" and not in_single and not in_double:
            break
        out.append(c)
        i += 1
    return "".join(out)


def get_logical_lines(script_text: str) -> list[tuple[int, int, str, str]]:
    """Split script into logical lines by joining physical lines ending with backslash.

    Returns:
      List of (start_line, end_line, raw_text, joined_text) tuples.
    """
    lines = script_text.splitlines()
    logical_lines: list[tuple[int, int, str, str]] = []
    curr: list[str] = []
    start_lineno = 1
    for i, line in enumerate(lines, 1):
        if not curr:
            start_lineno = i
        stripped = line.rstrip()
        if stripped.endswith("\\"):
            curr.append(stripped[:-1].rstrip())
        else:
            curr.append(stripped)
            raw_text = "\n".join(curr)
            joined_text = " ".join(c.strip() for c in curr)
            logical_lines.append((start_lineno, i, raw_text, joined_text))
            curr = []
    if curr:
        raw_text = "\n".join(curr)
        joined_text = " ".join(c.strip() for c in curr)
        logical_lines.append((start_lineno, len(lines), raw_text, joined_text))
    return logical_lines


def is_helm_upgrade_line(line: str) -> bool:
    """Return True if logical line invokes helm upgrade (directly or via variable)."""
    if re.search(r"\bhelm\b.*?\bupgrade\b", line):
        return True
    if re.search(r"(?:^|[\s;&|])(?:\$|\"\$)(\{?[A-Za-z0-9_]*HELM[A-Za-z0-9_]*\}?)\"?.*?\bupgrade\b", line, re.IGNORECASE):
        return True
    return False


def parse_helm_command_release(logical_line: str) -> str | None:
    """Extract release name from a logical line invoking helm upgrade.

    Recognises:
      - Wrappers: sudo, env VAR=x, VAR=x, if/!/then/elif/retry, and $(...)-free prefixes.
      - Helm invocation: helm or variable forms ("$HELM", ${HELM_CMD}, etc.).
      - Global flags between helm and upgrade (e.g. helm --kubeconfig X upgrade).
      - Subcommand flags between upgrade and release (e.g. upgrade --install <release>).
    Returns release name if successfully parsed, or None if unparsed / rejected.
    """
    try:
        toks = shlex.split(logical_line, comments=True)
    except Exception:
        return None
    if not toks:
        return None

    helm_idx = -1
    for i, t in enumerate(toks):
        if t == "helm" or re.match(r"^\"?\$(\{?[A-Za-z0-9_]*HELM[A-Za-z0-9_]*\}?)\"?$", t, re.IGNORECASE):
            helm_idx = i
            break
    if helm_idx == -1:
        return None

    # Check that prefix on the logical line is $(...)-free
    helm_token_raw = toks[helm_idx]
    m = re.search(r"(?:^|[\s;&|])" + re.escape(helm_token_raw) + r"(?=\s|$)", logical_line)
    if m:
        prefix_str = logical_line[:m.start()]
        if "$(" in prefix_str:
            return None

    upgrade_idx = -1
    for i in range(helm_idx + 1, len(toks)):
        if toks[i] == "upgrade":
            upgrade_idx = i
            break
    if upgrade_idx == -1:
        return None

    # Verify all tokens between helm and upgrade are global flags
    global_flags_with_arg = {
        "--namespace", "-n", "--kubeconfig", "--burst-limit", "-v",
        "--registry-config", "--repository-config", "--repository-cache"
    }
    j = helm_idx + 1
    while j < upgrade_idx:
        t = toks[j]
        if t in global_flags_with_arg:
            j += 2
            continue
        if t.startswith("-"):
            j += 1
            continue
        return None

    # Find release positional argument after upgrade
    upgrade_flags_with_arg = {
        "--namespace", "-n", "--kubeconfig", "--timeout", "--version",
        "--ca-file", "--cert-file", "--key-file", "--history-max", "--output", "-o",
        "--post-renderer", "--repo", "--username", "--password"
    }
    k = upgrade_idx + 1
    while k < len(toks):
        t = toks[k]
        if t in upgrade_flags_with_arg:
            k += 2
            continue
        if t.startswith("-"):
            k += 1
            continue
        return t
    return None


def assert_cluster_helm_completeness(root: Path) -> int:
    """Verify fail-closed completeness of Helm command parsing across scripts/cluster/*.sh.

    Counts every logical line containing tokens 'helm' ... 'upgrade' (after stripping comments)
    or invoking helm through a variable ("$HELM", ${HELM_CMD} followed by upgrade).
    Requires that each one is either parsed into a release or listed in CLUSTER_HELM_ALLOWLIST with a reason.
    An unparsed/untracked occurrence fails the check.
    """
    cluster_dir = root / "scripts" / "cluster"
    assert cluster_dir.is_dir(), f"missing directory: {cluster_dir}"

    bootstrap_map = {(item["script"], item["release"]): item for item in BOOTSTRAP_RELEASES}

    total_occurrences = 0
    parsed_bootstrap = 0
    allowlisted_count = 0

    for script_path in sorted(cluster_dir.glob("*.sh")):
        script_rel = f"scripts/cluster/{script_path.name}"
        logical_lines = get_logical_lines(script_path.read_text(encoding="utf-8"))
        for start_line, end_line, raw_text, joined_text in logical_lines:
            stripped = strip_comments(joined_text).strip()
            if not stripped:
                continue
            if not is_helm_upgrade_line(stripped):
                continue

            total_occurrences += 1
            rel = parse_helm_command_release(stripped)

            if rel is not None and (script_rel, rel) in bootstrap_map:
                parsed_bootstrap += 1
                continue

            if rel is not None and (script_rel, rel) in CLUSTER_HELM_ALLOWLIST:
                allowlisted_count += 1
                continue

            if (script_rel, stripped) in CLUSTER_HELM_ALLOWLIST:
                allowlisted_count += 1
                continue

            if rel is None:
                raise AssertionError(
                    f"FAIL CLOSED: unparsed Helm command in {script_rel}:{start_line}: {stripped!r}. "
                    "Must be parseable or listed in CLUSTER_HELM_ALLOWLIST with a reason."
                )
            else:
                raise AssertionError(
                    f"FAIL CLOSED: untracked Helm release {rel!r} in {script_rel}:{start_line}: {stripped!r}. "
                    "Must be in BOOTSTRAP_RELEASES or listed in CLUSTER_HELM_ALLOWLIST with a reason."
                )

    assert total_occurrences == parsed_bootstrap + allowlisted_count, (
        f"completeness mismatch: total={total_occurrences}, parsed_bootstrap={parsed_bootstrap}, "
        f"allowlisted={allowlisted_count}"
    )
    assert parsed_bootstrap == len(BOOTSTRAP_RELEASES), (
        f"expected {len(BOOTSTRAP_RELEASES)} parsed bootstrap releases, got {parsed_bootstrap}"
    )
    return total_occurrences


def coerce_helm_set_value(val: str) -> Any:
    """Coerce a --set string value according to Helm's strvals parser rules.

    Helm strvals coercion rules:
      - exact 'true' -> True (bool)
      - exact 'false' -> False (bool)
      - exact 'null' -> None
      - integer matching ^-?(0|[1-9][0-9]*)$ within signed 64-bit range -> int
      - everything else (floats like 1.0, leading zeros like 01, exponent forms like 1e3,
        hex like 0x10, and numbers exceeding int64 range) stays string.
    """
    if val == "true":
        return True
    if val == "false":
        return False
    if val == "null":
        return None
    if INT_RE.match(val):
        try:
            n = int(val)
            if INT64_MIN <= n <= INT64_MAX:
                return n
        except ValueError:
            pass
    return val


def compare_values(kind: str, exp_str: str, act_val: Any) -> bool:
    """Compare expected script string against live json value according to flag kind.

    Type rules:
      - --set: values are compared after Helm strvals coercion:
               exact booleans ('true'/'false'), exact 'null', integers matching
               ^-?(0|[1-9][0-9]*)$ in int64 range, and braces list syntax ({a,b}).
               Floats (1.0), leading zeros (01), exponent forms (1e3), hex (0x10),
               and out-of-range integers stay strings.
      - --set-string: strictly requires string type.
      - --set-json: parsed as JSON and compared structurally.
    """
    if act_val is NOT_FOUND:
        return False
    if kind == "--set-json":
        try:
            return json.loads(exp_str) == act_val
        except Exception:
            return False
    if kind == "--set-string":
        # Strict string comparison: live value must be a string and match
        exp_unescaped = exp_str.replace(r"\,", ",")
        return isinstance(act_val, str) and (act_val == exp_str or act_val == exp_unescaped)

    # kind == "--set"
    # Empty string in Helm --set can represent null or empty string
    if exp_str == "" and (act_val is None or act_val == ""):
        return True

    # Check for braces list syntax, e.g. {a,b}
    if exp_str.startswith("{") and exp_str.endswith("}") and isinstance(act_val, list):
        inner = exp_str[1:-1].strip()
        items = split_helm_set_args(inner) if inner else []
        exp_list = [coerce_helm_set_value(x.strip().replace(r"\,", ",")) for x in items]
        return exp_list == act_val

    coerced = coerce_helm_set_value(exp_str)
    # If coerced is string, also check unescaped comma
    if isinstance(coerced, str) and r"\," in coerced:
        if act_val == coerced.replace(r"\,", ","):
            return True

    # Strict type and value matching (int vs int, bool vs bool, None vs None, str vs str)
    if type(coerced) is type(act_val):
        return coerced == act_val

    return False


def extract_release_flags_and_values(
    script_text: str, release: str
) -> tuple[list[tuple[str, str, str]], bool]:
    """Extract (flag_kind, key, value) tuples and whether a values file is used for release."""
    logical_lines = get_logical_lines(script_text)
    target_cmd: str | None = None

    for start_line, end_line, raw_text, joined_text in logical_lines:
        stripped = strip_comments(joined_text).strip()
        if not stripped:
            continue
        rel = parse_helm_command_release(stripped)
        if rel == release:
            target_cmd = joined_text
            break

    if target_cmd is None:
        return [], False

    # Check for array variable references like "${HELM_ARGS[@]}" or "$HELM_ARGS"
    extra_text = ""
    for vname in re.findall(r"\"?\$\{?([A-Za-z0-9_]+)(?:\[@\])?\}?\"?", target_cmd):
        if vname in ("CHART_DIR", "release", "ns"):
            continue
        arr_pattern = rf"\b{re.escape(vname)}=\((.*?)\)"
        arr_m = re.search(arr_pattern, script_text, re.DOTALL)
        if arr_m:
            extra_text += "\n" + arr_m.group(1)

    full_text = target_cmd + "\n" + extra_text

    tokens = shlex.split(full_text, comments=True)
    has_values_file = False
    raw_flags: list[tuple[str, str]] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in ("-f", "--values"):
            has_values_file = True
            if i + 1 < len(tokens):
                i += 2
                continue
        elif t.startswith(("-f=", "--values=")):
            has_values_file = True
        elif t in ("--set", "--set-string", "--set-json"):
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
        for sub in split_helm_set_args(arg):
            if "=" in sub:
                k, v = sub.split("=", 1)
            else:
                k, v = sub, ""
            extracted.append((kind, k, v.rstrip(";")))

    return extracted, has_values_file


def extract_set_flags(script_text: str, release: str) -> list[tuple[str, str, str]]:
    """Extract (flag_kind, key, value) tuples for release from script_text."""
    flags, _ = extract_release_flags_and_values(script_text, release)
    return flags


class DriftCheckResult(tuple):
    """Result tuple supporting (drifted, skipped) unpacking while carrying metadata."""

    drifted: list[dict[str, Any]]
    skipped: list[str]
    has_values_file: bool
    flag_count: int

    def __new__(
        cls,
        drifted: list[dict[str, Any]],
        skipped: list[str],
        has_values_file: bool = False,
        flag_count: int = 0,
    ) -> DriftCheckResult:
        inst = super().__new__(cls, (drifted, skipped))
        inst.drifted = drifted
        inst.skipped = skipped
        inst.has_values_file = has_values_file
        inst.flag_count = flag_count
        return inst


def check_release_drift(
    script_text: str, release: str, live_values: dict[str, Any]
) -> DriftCheckResult:
    """Compare extracted literal flags against live_values.

    Returns:
      DriftCheckResult(drifted_items, skipped_keys, has_values_file, flag_count)
    """
    flags, has_values_file = extract_release_flags_and_values(script_text, release)
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

    return DriftCheckResult(
        drifted=drifted,
        skipped=skipped,
        has_values_file=has_values_file,
        flag_count=len(flags),
    )


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

    # Stale live values
    stale_live = {
        "image": {"repository": "apache/apisix-ingress-controller", "tag": "1.8.0"},
        "config": {
            "apisixResourceSyncInterval": "1h",
            "kubernetes": {"resyncInterval": "6h"},
        },
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

    # Test 8: Empty script command (values file only e.g. apisix) -> UNCHECKED, not IN_SYNC
    empty_script = r"""
helm upgrade --install apisix ./chart \
  --namespace platform-system \
  -f /tmp/apisix-values.yaml
"""
    res_emp = check_release_drift(empty_script, "apisix", {})
    assert len(res_emp.drifted) == 0
    assert res_emp.has_values_file is True
    assert res_emp.flag_count == 0

    # Test 8b: Script with BOTH values file and literal flags
    both_script = r"""
helm upgrade --install both-app ./chart \
  -f /tmp/values.yaml \
  --set key1=val1 \
  --set key2=val2
"""
    res_both = check_release_drift(both_script, "both-app", {"key1": "val1", "key2": "val2"})
    assert len(res_both.drifted) == 0
    assert res_both.has_values_file is True
    assert res_both.flag_count == 2

    # Test 9: Comma-separated assignments, escaped commas, and braces list syntax
    comma_script = r"""
helm upgrade --install comma-app ./chart \
  --set a=x,b=y \
  --set "tolerations[0].key=foo\,bar,tolerations[0].operator=Exists" \
  --set "items={a,b},count=2" \
  --set-string 'tag=v1\,beta,env=prod'
"""
    c_flags = extract_set_flags(comma_script, "comma-app")
    c_dict = {f[1]: (f[0], f[2]) for f in c_flags}
    assert "a" in c_dict and c_dict["a"] == ("--set", "x")
    assert "b" in c_dict and c_dict["b"] == ("--set", "y")
    assert "tolerations[0].key" in c_dict and c_dict["tolerations[0].key"] == ("--set", r"foo\,bar")
    assert "tolerations[0].operator" in c_dict and c_dict["tolerations[0].operator"] == ("--set", "Exists")
    assert "items" in c_dict and c_dict["items"] == ("--set", "{a,b}")
    assert "count" in c_dict and c_dict["count"] == ("--set", "2")
    assert "tag" in c_dict and c_dict["tag"] == ("--set-string", r"v1\,beta")
    assert "env" in c_dict and c_dict["env"] == ("--set-string", "prod")

    live_comma = {
        "a": "x",
        "b": "y",
        "tolerations": [{"key": "foo,bar", "operator": "Exists"}],
        "items": ["a", "b"],
        "count": 2,
        "tag": "v1,beta",
        "env": "prod",
    }
    c_drift, _ = check_release_drift(comma_script, "comma-app", live_comma)
    assert len(c_drift) == 0, f"comma app must be in sync, got: {c_drift}"

    # Drift test: b drifts
    live_comma_drift = dict(live_comma, b="drifted")
    c_drift_b, _ = check_release_drift(comma_script, "comma-app", live_comma_drift)
    assert len(c_drift_b) == 1 and c_drift_b[0]["key"] == "b"

    # Test 10: Type coercion rules (--set vs --set-string: 1 vs "1", true vs "true", null)
    type_script = r"""
helm upgrade --install type-app ./chart \
  --set int_val=1 \
  --set-string str_int_val=1 \
  --set bool_val=true \
  --set-string str_bool_val=true \
  --set null_val=null
"""
    live_type_match = {
        "int_val": 1,
        "str_int_val": "1",
        "bool_val": True,
        "str_bool_val": "true",
        "null_val": None,
    }
    t_drift, _ = check_release_drift(type_script, "type-app", live_type_match)
    assert len(t_drift) == 0, f"type match failed: {t_drift}"

    # 1 vs "1" boundary: int_val=1 drifts against live string "1"
    live_1_str = dict(live_type_match, int_val="1")
    t_drift_1, _ = check_release_drift(type_script, "type-app", live_1_str)
    assert any(d["key"] == "int_val" for d in t_drift_1), "int_val=1 must drift against live string '1'"

    # str_int_val=1 drifts against live integer 1
    live_str_int = dict(live_type_match, str_int_val=1)
    t_drift_si, _ = check_release_drift(type_script, "type-app", live_str_int)
    assert any(d["key"] == "str_int_val" for d in t_drift_si), "set-string must drift against live int 1"

    # true vs "true" boundary: bool_val=true drifts against live string "true"
    live_bool_str = dict(live_type_match, bool_val="true")
    t_drift_bs, _ = check_release_drift(type_script, "type-app", live_bool_str)
    assert any(d["key"] == "bool_val" for d in t_drift_bs), "bool_val=true must drift against live string 'true'"

    # str_bool_val=true drifts against live boolean True
    live_str_bool = dict(live_type_match, str_bool_val=True)
    t_drift_sb, _ = check_release_drift(type_script, "type-app", live_str_bool)
    assert any(d["key"] == "str_bool_val" for d in t_drift_sb), "set-string must drift against live bool True"

    # null_val=null drifts against live string "null"
    live_null_str = dict(live_type_match, null_val="null")
    t_drift_ns, _ = check_release_drift(type_script, "type-app", live_null_str)
    assert any(d["key"] == "null_val" for d in t_drift_ns), "null_val=null must drift against live string 'null'"

    # Test 11: Wrapped helm commands and global flags / variables
    # 11a: sudo wrapper
    sudo_snippet = r"""
sudo helm upgrade --install sudo-app ./chart \
  --set sudo.enabled=true
"""
    assert parse_helm_command_release(sudo_snippet) == "sudo-app"
    s_flags = extract_set_flags(sudo_snippet, "sudo-app")
    assert s_flags == [("--set", "sudo.enabled", "true")]
    drift_sudo, _ = check_release_drift(sudo_snippet, "sudo-app", {"sudo": {"enabled": True}})
    assert len(drift_sudo) == 0

    # 11b: env VAR=x wrapper and VAR=x prefix
    env_snippet = r"""
env KUBECONFIG=/etc/admin.conf MY_VAR=1 helm upgrade --install env-app ./chart \
  --set env.mode=production
"""
    assert parse_helm_command_release(env_snippet) == "env-app"
    e_flags = extract_set_flags(env_snippet, "env-app")
    assert e_flags == [("--set", "env.mode", "production")]

    var_prefix_snippet = r"""
FOO=bar BAR=baz helm upgrade --install prefix-app ./chart \
  --set config.foo=bar
"""
    assert parse_helm_command_release(var_prefix_snippet) == "prefix-app"
    vp_flags = extract_set_flags(var_prefix_snippet, "prefix-app")
    assert vp_flags == [("--set", "config.foo", "bar")]

    # 11c: if / ! / then wrapper
    if_snippet = r"""
if ! helm upgrade --install if-app ./chart \
  --set active=true; then
  echo "failed"
fi
"""
    assert parse_helm_command_release(if_snippet) == "if-app"
    if_flags = extract_set_flags(if_snippet, "if-app")
    assert if_flags == [("--set", "active", "true")]

    # 11d: global flags between helm and upgrade
    gf_snippet = r"""
helm --kubeconfig /etc/kubernetes/admin.conf --burst-limit 100 -v 2 upgrade --install gf-app ./chart \
  --set gf.key=value
"""
    assert parse_helm_command_release(gf_snippet) == "gf-app"
    gf_flags = extract_set_flags(gf_snippet, "gf-app")
    assert gf_flags == [("--set", "gf.key", "value")]

    # 11e: Variable forms ("$HELM", ${HELM_CMD})
    var_snippet1 = r"""
"$HELM" upgrade --install var1-app ./chart \
  --set var.one=1
"""
    assert parse_helm_command_release(var_snippet1) == "var1-app"
    v1_flags = extract_set_flags(var_snippet1, "var1-app")
    assert v1_flags == [("--set", "var.one", "1")]

    var_snippet2 = r"""
${HELM_CMD} --kubeconfig /etc/k.conf upgrade --install var2-app ./chart \
  --set var.two=2
"""
    assert parse_helm_command_release(var_snippet2) == "var2-app"
    v2_flags = extract_set_flags(var_snippet2, "var2-app")
    assert v2_flags == [("--set", "var.two", "2")]

    # 11f: Combined wrappers, variables, and global flags
    combo_snippet = r"""
sudo env KUBECONFIG=/etc/admin.conf "$HELM" --kubeconfig /etc/admin.conf upgrade --install combo-app ./chart \
  --set combo.flag=true
"""
    assert parse_helm_command_release(combo_snippet) == "combo-app"
    c_flags = extract_set_flags(combo_snippet, "combo-app")
    assert c_flags == [("--set", "combo.flag", "true")]

    # 11g: Rejection of prefix with $(...)
    bad_prefix = r"""
$(echo sudo) helm upgrade --install bad-app ./chart --set a=b
"""
    assert parse_helm_command_release(bad_prefix) is None
    bad_flags, _ = extract_release_flags_and_values(bad_prefix, "bad-app")
    assert bad_flags == []

    # Test 12: Helm strvals numeric coercion rules (1.0, 01, 1e3, 0x10, 20-digit number)
    # Direct coercion check
    assert coerce_helm_set_value("1.0") == "1.0"
    assert isinstance(coerce_helm_set_value("1.0"), str)
    assert coerce_helm_set_value("01") == "01"
    assert isinstance(coerce_helm_set_value("01"), str)
    assert coerce_helm_set_value("1e3") == "1e3"
    assert isinstance(coerce_helm_set_value("1e3"), str)
    assert coerce_helm_set_value("0x10") == "0x10"
    assert isinstance(coerce_helm_set_value("0x10"), str)
    big_20_digit = "12345678901234567890"
    assert coerce_helm_set_value(big_20_digit) == big_20_digit
    assert isinstance(coerce_helm_set_value(big_20_digit), str)

    # Int64 boundary check
    max_int64 = str(INT64_MAX)
    min_int64 = str(INT64_MIN)
    assert coerce_helm_set_value(max_int64) == INT64_MAX
    assert isinstance(coerce_helm_set_value(max_int64), int)
    assert coerce_helm_set_value(min_int64) == INT64_MIN
    assert isinstance(coerce_helm_set_value(min_int64), int)
    overflow_int64 = str(INT64_MAX + 1)
    assert coerce_helm_set_value(overflow_int64) == overflow_int64
    assert isinstance(coerce_helm_set_value(overflow_int64), str)

    # Exact boolean/null vs case-variant
    assert coerce_helm_set_value("true") is True
    assert coerce_helm_set_value("false") is False
    assert coerce_helm_set_value("null") is None
    assert coerce_helm_set_value("True") == "True"
    assert coerce_helm_set_value("FALSE") == "FALSE"
    assert coerce_helm_set_value("NULL") == "NULL"

    # Drift check with 1.0, 01, 1e3, 0x10, 20-digit number
    num_script = r"""
helm upgrade --install num-app ./chart \
  --set float_val=1.0 \
  --set leading_zero=01 \
  --set exponent=1e3 \
  --set hex_val=0x10 \
  --set big_num=12345678901234567890
"""
    # Matches when live values are strings (as Helm strvals produces)
    live_strings = {
        "float_val": "1.0",
        "leading_zero": "01",
        "exponent": "1e3",
        "hex_val": "0x10",
        "big_num": "12345678901234567890",
    }
    drift_str, _ = check_release_drift(num_script, "num-app", live_strings)
    assert len(drift_str) == 0, f"expected 0 drift for string match, got {drift_str}"

    # Drifts against live float 1.0
    live_float = dict(live_strings, float_val=1.0)
    drift_f, _ = check_release_drift(num_script, "num-app", live_float)
    assert any(d["key"] == "float_val" for d in drift_f), "1.0 must drift against live float 1.0"

    # Drifts against live int 1 for 01
    live_lz = dict(live_strings, leading_zero=1)
    drift_lz, _ = check_release_drift(num_script, "num-app", live_lz)
    assert any(d["key"] == "leading_zero" for d in drift_lz), "01 must drift against live int 1"

    # Drifts against live float/int for 1e3
    live_exp = dict(live_strings, exponent=1000.0)
    drift_exp, _ = check_release_drift(num_script, "num-app", live_exp)
    assert any(d["key"] == "exponent" for d in drift_exp), "1e3 must drift against live float 1000.0"

    # Drifts against live int 16 for 0x10
    live_hex = dict(live_strings, hex_val=16)
    drift_hex, _ = check_release_drift(num_script, "num-app", live_hex)
    assert any(d["key"] == "hex_val" for d in drift_hex), "0x10 must drift against live int 16"

    # Drifts against live int for 20-digit number
    live_big = dict(live_strings, big_num=12345678901234567890)
    drift_big, _ = check_release_drift(num_script, "num-app", live_big)
    assert any(d["key"] == "big_num" for d in drift_big), "20-digit number must drift against live int"

    # Test 13: Completeness assertion behavior (fail closed on unparsed / untracked)
    with tempfile.TemporaryDirectory() as tmp_c_str:
        tmp_c = Path(tmp_c_str)
        c_dir = tmp_c / "scripts" / "cluster"
        c_dir.mkdir(parents=True)

        # File with untracked release -> must fail
        bad_script = c_dir / "01-untracked.sh"
        bad_script.write_text("helm upgrade --install mystery-app ./chart\n", encoding="utf-8")
        failed = False
        try:
            assert_cluster_helm_completeness(tmp_c)
        except AssertionError as e:
            failed = True
            assert "untracked Helm release 'mystery-app'" in str(e)
        assert failed, "untracked release must fail completeness assertion"

        # File with unparsed command -> must fail
        bad_script.write_text("$(dynamic_subshell) helm upgrade --install bad-app ./chart\n", encoding="utf-8")
        failed_unparsed = False
        try:
            assert_cluster_helm_completeness(tmp_c)
        except AssertionError as e:
            failed_unparsed = True
            assert "unparsed Helm command" in str(e)
        assert failed_unparsed, "unparsed command must fail completeness assertion"


def run_cli_tests(root: Path) -> None:
    """Run CLI behavioural tests for multi-word commands and secrecy invariants."""
    check_script = root / CHECK_SCRIPT_REL
    if not check_script.is_file():
        return

    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_path = Path(tmp_dir_str)
        stub_bin = tmp_path / "bin"
        stub_bin.mkdir()

        # Stub sudo: executes command
        sudo_script = stub_bin / "sudo"
        sudo_script.write_text("#!/usr/bin/env bash\nexec \"$@\"\n", encoding="utf-8")
        sudo_script.chmod(0o755)

        # Stub kubectl: returns 0 on 'get nodes'
        kubectl_script = stub_bin / "kubectl"
        kubectl_script.write_text(
            "#!/usr/bin/env bash\n"
            "if [ \"$1\" = \"get\" ] && [ \"$2\" = \"nodes\" ]; then\n"
            "  exit 0\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        kubectl_script.chmod(0o755)

        # Base environment
        base_env = dict(os.environ)
        base_env["PATH"] = f"{stub_bin}:{base_env.get('PATH', '')}"

        # 1. Multi-word command test: sudo helm --kubeconfig /etc/kubernetes/admin.conf
        helm_script = stub_bin / "helm"
        helm_script.write_text(
            "#!/usr/bin/env bash\n"
            "for (( i=1; i<=$#; i++ )); do\n"
            "  if [ \"${!i}\" = \"get\" ]; then\n"
            "    echo '{\"controller\":{\"replicas\":1,\"resources\":{\"csiProvisioner\":{\"limits\":{\"cpu\":\"100m\"}},\"csiResizer\":{\"limits\":{\"cpu\":\"100m\"}},\"csiSnapshotter\":{\"limits\":{\"cpu\":\"100m\"}},\"livenessProbe\":{\"limits\":{\"cpu\":\"100m\"}},\"nfs\":{\"limits\":{\"cpu\":\"100m\"}}}},\"feature\":{\"enableFSGroupPolicy\":false},\"node\":{\"resources\":{\"livenessProbe\":{\"limits\":{\"cpu\":\"100m\"}},\"nodeDriverRegistrar\":{\"limits\":{\"cpu\":\"100m\"}},\"nfs\":{\"limits\":{\"cpu\":\"100m\"}}}}}'\n"
            "    exit 0\n"
            "  fi\n"
            "done\n"
            "exit 1\n",
            encoding="utf-8",
        )
        helm_script.chmod(0o755)

        env1 = dict(base_env)
        env1["HELM_CMD"] = "sudo helm --kubeconfig /etc/kubernetes/admin.conf"
        env1["KUBECTL_CMD"] = "sudo kubectl --kubeconfig /etc/kubernetes/admin.conf"
        res1 = subprocess.run(
            [str(check_script), "--release=csi-driver-nfs"],
            env=env1,
            capture_output=True,
            text=True,
        )
        assert res1.returncode == 0, f"multi-word sudo helm failed (rc={res1.returncode}): {res1.stderr}\n{res1.stdout}"
        assert "IN_SYNC: release=csi-driver-nfs" in res1.stdout
        assert "sudo kubectl --kubeconfig /etc/kubernetes/admin.conf" in res1.stdout

        # 1b. Unchecked release test: apisix (uses values file only)
        res_unchecked = subprocess.run(
            [str(check_script), "--release=apisix"],
            env=env1,
            capture_output=True,
            text=True,
        )
        assert res_unchecked.returncode == 0, f"apisix check failed: {res_unchecked.stderr}\n{res_unchecked.stdout}"
        assert "UNCHECKED: release=apisix (values file not parsed)" in res_unchecked.stdout
        assert "IN_SYNC: release=apisix" not in res_unchecked.stdout
        assert "1 unchecked (values file not parsed)" in res_unchecked.stdout

        # Secrecy tests
        SECRET_TOKEN = "SUPER_SECRET_TOKEN_XYZ_98765"

        # 2a. Drift on another key + secret in live JSON
        iso_tmp_a = tmp_path / "tmp_a"
        iso_tmp_a.mkdir()
        env_a = dict(base_env)
        env_a["TMPDIR"] = str(iso_tmp_a)
        helm_script.write_text(
            f"#!/usr/bin/env bash\n"
            f"echo '{{\"controller\":{{\"replicas\":99}},\"feature\":{{\"enableFSGroupPolicy\":false}},\"adminPassword\":\"{SECRET_TOKEN}\"}}'\n"
            f"exit 0\n",
            encoding="utf-8",
        )
        res_a = subprocess.run(
            [str(check_script), "--release=csi-driver-nfs"],
            env=env_a,
            capture_output=True,
            text=True,
        )
        assert res_a.returncode == 1, f"expected drift rc=1, got {res_a.returncode}"
        combined_a = res_a.stdout + res_a.stderr
        assert SECRET_TOKEN not in combined_a, f"secret leaked in drift run: {combined_a}"
        assert "DRIFT: release=csi-driver-nfs key=controller.replicas" in combined_a
        assert os.listdir(iso_tmp_a) == [], f"TMPDIR not empty after drift test: {os.listdir(iso_tmp_a)}"

        # 2b. Helm fails + secret in output
        iso_tmp_b = tmp_path / "tmp_b"
        iso_tmp_b.mkdir()
        env_b = dict(base_env)
        env_b["TMPDIR"] = str(iso_tmp_b)
        helm_script.write_text(
            f"#!/usr/bin/env bash\n"
            f"echo 'Error: connection refused secret={SECRET_TOKEN}' >&2\n"
            f"echo 'stdout secret={SECRET_TOKEN}'\n"
            f"exit 1\n",
            encoding="utf-8",
        )
        res_b = subprocess.run(
            [str(check_script), "--release=csi-driver-nfs"],
            env=env_b,
            capture_output=True,
            text=True,
        )
        assert res_b.returncode == 2, f"expected failure rc=2, got {res_b.returncode}"
        combined_b = res_b.stdout + res_b.stderr
        assert SECRET_TOKEN not in combined_b, f"secret leaked on helm failure: {combined_b}"
        assert os.listdir(iso_tmp_b) == [], f"TMPDIR not empty after helm failure: {os.listdir(iso_tmp_b)}"

        # 2c. Invalid JSON from helm + secret in output
        iso_tmp_c = tmp_path / "tmp_c"
        iso_tmp_c.mkdir()
        env_c = dict(base_env)
        env_c["TMPDIR"] = str(iso_tmp_c)
        helm_script.write_text(
            f"#!/usr/bin/env bash\n"
            f"echo '{{ invalid JSON text secret={SECRET_TOKEN}'\n"
            f"exit 0\n",
            encoding="utf-8",
        )
        res_c = subprocess.run(
            [str(check_script), "--release=csi-driver-nfs"],
            env=env_c,
            capture_output=True,
            text=True,
        )
        assert res_c.returncode == 2, f"expected failure rc=2, got {res_c.returncode}"
        combined_c = res_c.stdout + res_c.stderr
        assert SECRET_TOKEN not in combined_c, f"secret leaked on invalid JSON: {combined_c}"
        assert os.listdir(iso_tmp_c) == [], f"TMPDIR not empty after invalid JSON: {os.listdir(iso_tmp_c)}"


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
    assert "read -r -a HELM_BIN" in script_text, f"{CHECK_SCRIPT_REL} must split HELM_CMD into argv array"
    assert "read -r -a KUBECTL_BIN" in script_text, f"{CHECK_SCRIPT_REL} must split KUBECTL_CMD into argv array"
    assert re.search(r"FAIL: detected drift.*?exit 1", script_text, re.DOTALL), (
        f"{CHECK_SCRIPT_REL} must exit 1 on drift"
    )
    assert re.search(r"cannot connect[^\n]*\n\s*exit 2", script_text), (
        f"{CHECK_SCRIPT_REL} must exit 2 on cluster unreachable"
    )
    assert "UNCHECKED" in script_text, f"{CHECK_SCRIPT_REL} must track UNCHECKED releases in summary"
    assert "exit 0" in script_text, f"{CHECK_SCRIPT_REL} must exit 0 when in sync"

    reg_path = root / REGRESSION_REL
    reg_text = reg_path.read_text(encoding="utf-8")
    assert "check R376" in reg_text, f"{REGRESSION_REL} must wire check R376"
    assert "check-bootstrap-helm-drift.py --mutation-verify" in reg_text, (
        f"{REGRESSION_REL} R376 must call check-bootstrap-helm-drift.py --mutation-verify"
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
        flags, has_vf = extract_release_flags_and_values(st, rel)
        if rel == "apisix":
            assert len(flags) == 0, "apisix has no --set flags"
            assert has_vf is True, "apisix must use -f values file"
        else:
            assert len(flags) > 0, f"expected flags for {rel} in {item['script']}, got 0"

    # Fail-closed completeness check across all scripts/cluster/*.sh
    assert_cluster_helm_completeness(root)


def mutation_verify(root: Path) -> int:
    """Run mutation tests in an isolated temporary directory without touching tracked files."""
    import shutil

    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_root = Path(tmp_dir_str)

        # Files needed for verification
        files_to_copy = [
            CHECK_SCRIPT_REL,
            REGRESSION_REL,
            DOC_OPS_REL,
            DOC_LESSONS_REL,
            "scripts/test/lib/check-bootstrap-helm-drift.py",
        ]
        for item in BOOTSTRAP_RELEASES:
            files_to_copy.append(item["script"])
        # Include all cluster scripts for completeness assertion
        for p in (root / "scripts" / "cluster").glob("*.sh"):
            rel_p = f"scripts/cluster/{p.name}"
            if rel_p not in files_to_copy:
                files_to_copy.append(rel_p)

        # Copy each file to tmp_root preserving relative structure
        for rel_path in files_to_copy:
            src = root / rel_path
            dst = tmp_root / rel_path
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            if os.access(src, os.X_OK):
                dst.chmod(dst.stat().st_mode | stat.S_IXUSR)

        # Baseline check on unmutated copy
        verify_repository(tmp_root)
        run_unit_tests()
        run_cli_tests(tmp_root)

        mutations: list[tuple[str, str, str]] = [
            # 1. Break --set-string handling
            ("scripts/test/lib/check-bootstrap-helm-drift.py", 'if kind == "--set-string":', 'if kind == "--invalid-string":'),
            # 2. Break --set-json handling
            ("scripts/test/lib/check-bootstrap-helm-drift.py", 'if kind == "--set-json":', 'if kind == "--invalid-json":'),
            # 3. Disable shell variable skipping
            ("scripts/test/lib/check-bootstrap-helm-drift.py", 'return "$" in val', 'return False'),
            # 4. Disable secret key skipping
            ("scripts/test/lib/check-bootstrap-helm-drift.py", '"password", "passwd"', '"disabled_pwd"'),
            # 5. Disable bracket indexing in parse_helm_key
            ("scripts/test/lib/check-bootstrap-helm-drift.py", 'elif c == "[":', 'elif c == "{":'),
            # 6. Disable escaped dot unescaping in parse_helm_key
            ("scripts/test/lib/check-bootstrap-helm-drift.py", r'if c == "\\":', 'if c == "§":'),
            # 7. Check script: drop HELM_CMD override
            (CHECK_SCRIPT_REL, 'HELM_CMD="${HELM_CMD:-helm}"', 'HELM_CMD="helm"'),
            # 8. Check script: drop KUBECTL_CMD override
            (CHECK_SCRIPT_REL, 'KUBECTL_CMD="${KUBECTL_CMD:-kubectl}"', 'KUBECTL_CMD="kubectl"'),
            # 9. Check script: drop exit 1 on drift
            (CHECK_SCRIPT_REL, 'echo "FAIL: detected drift', 'echo "PASS: fake in sync'),
            # 10. Check script: drop exit 2 on cannot run
            (CHECK_SCRIPT_REL, 'cannot connect to Kubernetes cluster', 'fake cluster connected'),
            # 11. Regression wiring: drop R376
            (REGRESSION_REL, "check R376", "check R9999"),
            # 12. Docs: drop check-bootstrap-helm-drift.sh from operations doc
            (DOC_OPS_REL, "check-bootstrap-helm-drift.sh", "other-check-script.sh"),
            # 13. Lessons log: drop #728
            (DOC_LESSONS_REL, "#728", "#99999"),
            # 14. Value comparison: break boolean string comparison
            ("scripts/test/lib/check-bootstrap-helm-drift.py", 'if val == "true":', 'if val == "invalid_true":'),
            # 15. Comma splitting in --set
            ("scripts/test/lib/check-bootstrap-helm-drift.py", 'split_helm_set_args(arg)', '[arg]'),
            # 16. Unchecked reporting for values file
            ("scripts/test/lib/check-bootstrap-helm-drift.py", 'UNCHECKED: release=', 'IN_SYNC: release='),
            # 17. Strvals integer regex: disable integer parsing rule
            ("scripts/test/lib/check-bootstrap-helm-drift.py", 'INT_RE = re.compile(r"^-?(0|[1-9][0-9]*)$")', 'INT_RE = re.compile(r"^§$")'),
            # 18. Cluster completeness fail-closed: break allowlist entry
            ("scripts/test/lib/check-bootstrap-helm-drift.py", '("scripts/cluster/08-1-networking.sh", "metallb")', '("scripts/cluster/08-1-networking.sh", "mutated_metallb")'),
        ]

        caught = 0
        for rel_file, old_frag, new_frag in mutations:
            target_path = tmp_root / rel_file
            original_text = target_path.read_text(encoding="utf-8")
            assert old_frag in original_text, f"mutation fragment missing in {rel_file}: {old_frag!r}"
            mutated_text = original_text.replace(old_frag, new_frag)
            target_path.write_text(mutated_text, encoding="utf-8")

            survived = False
            try:
                if rel_file == "scripts/test/lib/check-bootstrap-helm-drift.py":
                    res = subprocess_verify(target_path, tmp_root)
                    if res == 0:
                        survived = True
                    else:
                        caught += 1
                else:
                    verify_repository(tmp_root)
                    run_unit_tests()
                    run_cli_tests(tmp_root)
                    survived = True
            except (AssertionError, KeyError, ValueError):
                caught += 1
            finally:
                # Restore in tmp_root
                target_path.write_text(original_text, encoding="utf-8")

            if survived:
                raise AssertionError(f"mutation survived in {rel_file}: {old_frag!r} -> {new_frag!r}")

        return caught


def subprocess_verify(script_file: Path, root: Path) -> int:
    """Helper to run verification in a fresh python subprocess for self-mutation."""
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
        except Exception:
            # Zero secret leaks: never print input content or raw exception
            print("ERROR: invalid JSON input from helm get values", file=sys.stderr)
            return 2

        script_text = args.script.read_text(encoding="utf-8")
        result = check_release_drift(script_text, args.release, live_data)

        if result.skipped:
            # Report skipped keys by name only (never leak values)
            print(f"SKIPPED: release={args.release} keys={', '.join(result.skipped)} (shell variable or secret)")

        # Case 1: Release configured solely via values file
        if result.flag_count == 0 and result.has_values_file:
            print(f"UNCHECKED: release={args.release} (values file not parsed)")
            return 0

        # Case 2: Release has literal flags
        if result.drifted:
            for d in result.drifted:
                print(
                    f"DRIFT: release={d['release']} key={d['key']} "
                    f"script={d['script_value']} live={d['live_value']}"
                )
            if result.has_values_file:
                print(f"NOTE: release={args.release} (values file not parsed; only literal --set flags checked)")
            return 1

        if result.has_values_file:
            print(f"IN_SYNC: release={args.release} (note: values file not parsed; only literal --set flags checked)")
        elif result.flag_count == 0:
            print(f"UNCHECKED: release={args.release} (no configuration flags found in script)")
        else:
            print(f"IN_SYNC: release={args.release}")
        return 0

    if args.self_test:
        verify_repository(args.root)
        run_unit_tests()
        run_cli_tests(args.root)
        return 0

    # Normal verification run
    verify_repository(args.root)
    run_unit_tests()
    run_cli_tests(args.root)

    if args.mutation_verify:
        caught = mutation_verify(args.root)
        print(f"PASS: R376 bootstrap helm drift check + offline parser behavioural tests ({caught} mutations rejected)")
    else:
        print("PASS: R376 bootstrap helm drift check + offline parser behavioural tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
