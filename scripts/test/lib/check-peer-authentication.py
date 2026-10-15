#!/usr/bin/env python3
"""Static check: Istio PeerAuthentication PERMISSIVE/DISABLE only via a reviewed allowlist (#126).

Cilium L3/L4 policy plus mesh mTLS only compose to a closed default if the mesh-wide
PeerAuthentication is STRICT and every exception is a deliberate, reviewed one. Scans every
GitOps YAML/JSON/tpl and every shell heredoc under scripts/**/*.sh that mentions PeerAuthentication.

Fails closed: unreadable/unparsable source, duplicate keys, non-mapping documents, wrong
apiVersion, an mtls mode outside the CRD set, helm-templated values ({{ }} outside comments in
a file that declares a PeerAuthentication, and any .tpl that declares one: neither can be
evaluated statically), no mesh-wide STRICT policy (exactly istio-system/default, no selector,
no weaker portLevelMtls) found.
KNOWN_ISSUES is an explicit baseline keyed on (file, namespace, name, mode, selector) with an
exact COUNT: more or fewer occurrences fail, so a changed mode, an added selector or a second
copy cannot hide behind an existing entry, and a fixed finding cannot leave a stale entry.
It is printed on stdout (the regression harness surfaces it as a WARN). Default mode only.
"""

from __future__ import annotations

import collections
import json
import os
import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(os.environ.get("PEER_AUTH_ROOT") or pathlib.Path(__file__).resolve().parents[3]).resolve()  # env: probe trees
MESH_NS, MESH_NAME = "istio-system", "default"
MODES = {"STRICT", "PERMISSIVE", "DISABLE", "UNSET"}  # PeerAuthentication CRD mtls.mode enum
NS_SCOPE = "namespace"  # selector signature of a policy without a workload selector
EXCLUDED = "scripts/test/fixtures/"  # path prefix; deliberately bad negative probes, never scanned by default
# (namespace, name) -> exact selector signature: reviewed PERMISSIVE exceptions; the reason lives
# next to the manifest. A workload entry pins its exact matchLabels (the real Harbor selector).
ALLOWLIST = {
    ("platform-system", "platform-system-permissive"): NS_SCOPE,  # ingress/kyverno/keycloak non-mesh clients
    ("database", "database-permissive"): NS_SCOPE,  # CNPG reached by non-mesh clients
    ("devtools", "harbor-permissive"): 'matchLabels:app=harbor',  # mixed mesh enrollment inside Harbor
}
# Real gaps found in the current repo: (file, namespace, name, mode, selector) -> (count, reason).
# Remove the entry when fixed; any count drift in either direction fails.
KNOWN_ISSUES = {
    ("scripts/cluster/09-istio-ambient.sh", "istio-system", "default", "PERMISSIVE", NS_SCOPE):
        (1, "mesh-wide PERMISSIVE bootstrap, fail-open until Argo reconciles STRICT"),
    ("gitops/charts/narwhal-platform/templates/istio-ambient-policies.yaml", "monitoring", "monitoring-permissive",
     "PERMISSIVE", NS_SCOPE):
        (1, "namespace-wide PERMISSIVE; its own comment says to scope per workload"),
}
KIND_RE = re.compile(r"""kind["']?\s*:\s*["']?PeerAuthentication\b""")


class UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader: UniqueLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    loader.flatten_mapping(node)
    seen: set = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in seen:
            raise ValueError(f"duplicate key {key!r}")
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def heredocs(text: str) -> list[tuple[str, str]]:
    """(raw, body) of every shell heredoc; `<<-` strips leading tabs from body and terminator like bash.

    An unterminated one swallows the rest of the file, so a PeerAuthentication hiding after it
    is still scanned (and fails closed if it does not parse).
    """
    lines, bodies, i = text.splitlines(), [], 0
    while i < len(lines):
        m = re.search(r"(?<!<)<<(?!<)(-?)\s*(['\"]?)(\w+)\2", lines[i])
        i += 1
        if not m:
            continue
        strip, delim, body, start = m.group(1) == "-", m.group(3), [], i
        while i < len(lines) and (lines[i].lstrip("\t") if strip else lines[i]) != delim:
            body.append(lines[i].lstrip("\t") if strip else lines[i])
            i += 1
        raw = "\n".join(lines[start:i])
        i += 1  # unterminated (e.g. `<<EOF` inside a quoted ssh command): body runs to EOF; sources() decides
        bodies.append((raw, "\n".join(body)))
    return bodies


def sources(rel: str, path: pathlib.Path, text: str) -> list[str]:
    if path.suffix in (".sh", ".txt"):  # .txt: fixtures stand in for scripts (CI lints scripts/**/*.sh)
        bodies = heredocs(text)
        rest = text
        for raw, _ in bodies:
            rest = rest.replace(raw, "", 1)
        live = "\n".join(l for l in rest.splitlines() if not l.lstrip().startswith("#"))
        if KIND_RE.search(live):
            raise ValueError(f"{rel}: declares a PeerAuthentication outside a heredoc (cannot be scanned)")
        return [body for _, body in bodies if KIND_RE.search(body)]
    return [text]


def _json_pairs(pairs: list) -> dict:
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate key in JSON object")
    return dict(pairs)


def documents(rel: str, path: pathlib.Path, text: str) -> list[dict]:
    live = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    if "PeerAuthentication" not in live:
        return []
    if path.suffix == ".tpl":
        raise ValueError(f"{rel}: .tpl declares a PeerAuthentication; it cannot be evaluated statically")
    chunks = sources(rel, path, text)
    docs: list[dict] = []
    for chunk in chunks:
        body = "\n".join(l for l in chunk.splitlines() if not l.lstrip().startswith("#"))
        if "{{" in body:
            raise ValueError(f"{rel}: helm-templated PeerAuthentication cannot be evaluated statically")
        try:
            if path.suffix == ".json":
                loaded = [json.loads(chunk, object_pairs_hook=_json_pairs)]
            else:
                loaded = list(yaml.load_all(chunk, Loader=UniqueLoader))
        except (yaml.YAMLError, ValueError) as exc:
            raise ValueError(f"{rel}: unparsable source: {exc}") from exc
        for doc in loaded:
            if doc is None:
                continue
            if not isinstance(doc, dict):
                raise ValueError(f"{rel}: non-mapping document")
            docs.extend(doc["items"] if doc.get("kind") == "List" and isinstance(doc.get("items"), list) else [doc])
    return docs


def selector_signature(rel: str, spec: dict) -> str:
    """NS_SCOPE without a selector, else a canonical form of it (labels and expressions)."""
    selector = spec.get("selector")
    if not selector:
        return NS_SCOPE
    if not isinstance(selector, dict):
        raise ValueError(f"{rel}: PeerAuthentication selector is not a mapping")
    labels = selector.get("matchLabels") or {}
    parts = [f"matchLabels:{k}={v}" for k, v in sorted(labels.items())]
    parts += [f"matchExpressions:{json.dumps(e, sort_keys=True)}" for e in selector.get("matchExpressions") or []]
    return ",".join(parts) or NS_SCOPE


def policies(rel: str, docs: list[dict]) -> list[tuple[str, str, str, str, bool]]:
    """Return (namespace, name, selector signature, weakest mode, strict-everywhere) per PeerAuthentication."""
    found = []
    for doc in docs:
        if not isinstance(doc, dict):
            raise ValueError(f"{rel}: non-mapping List item")
        if doc.get("kind") != "PeerAuthentication":
            continue
        if not str(doc.get("apiVersion", "")).startswith("security.istio.io/"):
            raise ValueError(f"{rel}: PeerAuthentication has apiVersion {doc.get('apiVersion')!r}")
        meta, spec = doc.get("metadata"), doc.get("spec")
        if not isinstance(meta, dict) or not isinstance(spec, dict) or not meta.get("name") or not meta.get("namespace"):
            raise ValueError(f"{rel}: PeerAuthentication lacks metadata.name/namespace or spec")
        mtls = spec.get("mtls") or {}
        port_levels = spec.get("portLevelMtls") or {}
        modes = [mtls.get("mode", "UNSET")] + [(p or {}).get("mode", "UNSET") for p in port_levels.values()]
        bad = [m for m in modes if m not in MODES]  # `permissive`, `PERMISIVE`, non-strings: fail closed
        if bad:
            raise ValueError(f"{rel}: PeerAuthentication {meta['namespace']}/{meta['name']} has invalid mtls mode "
                             f"{bad[0]!r} (CRD allows {sorted(MODES)})")
        weakest = next((m for m in ("DISABLE", "PERMISSIVE") if m in modes), None) or ("STRICT" if "STRICT" in modes else "UNSET")
        mesh_wide = (mtls.get("mode") == "STRICT" and weakest == "STRICT")
        found.append((str(meta["namespace"]), str(meta["name"]), selector_signature(rel, spec), weakest, mesh_wide))
    return found


def default_files() -> list[pathlib.Path]:
    gitops = [p for ext in ("yaml", "yml", "json", "tpl") for p in (ROOT / "gitops").rglob(f"*.{ext}")]
    scripts = [p for p in (ROOT / "scripts").rglob("*.sh") if not p.relative_to(ROOT).as_posix().startswith(EXCLUDED)]
    return sorted([*gitops, *scripts])


def main() -> int:
    args = [pathlib.Path(a) for a in sys.argv[1:]]
    paths = args or default_files()
    problems: list[str] = []
    mesh_strict = False
    seen: collections.Counter = collections.Counter()
    for path in paths:
        rel = path.resolve().relative_to(ROOT).as_posix() if ROOT in path.resolve().parents else str(path)
        try:
            found = policies(rel, documents(rel, path, path.read_text()))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            problems.append(str(exc))
            continue
        for ns, name, sel, mode, mesh_wide in found:
            if (ns, name, sel) == (MESH_NS, MESH_NAME, NS_SCOPE) and mesh_wide:
                mesh_strict = True
            if mode in ("STRICT", "UNSET"):
                continue
            key = (rel, ns, name, mode, sel)
            if not args and key in KNOWN_ISSUES:
                seen[key] += 1
            elif mode == "PERMISSIVE" and ALLOWLIST.get((ns, name)) == sel:
                continue
            else:
                problems.append(f"{rel}: PeerAuthentication {ns}/{name} ({sel}) is {mode}, not in the reviewed allowlist")
    if not mesh_strict:
        problems.append(f"no mesh-wide STRICT PeerAuthentication ({MESH_NS}/{MESH_NAME}, no selector, "
                        "no weaker portLevelMtls) found (absent evidence is not compliant)")
    baseline: list[str] = []
    if not args:
        for key, (want, reason) in sorted(KNOWN_ISSUES.items()):
            got = seen[key]
            if got != want:
                problems.append(f"KNOWN_ISSUES drift: {key[0]} {key[1]}/{key[2]} {key[3]} ({key[4]}) expected {want}, "
                                f"found {got} (remove or update the entry)")
            baseline.extend(f"KNOWN-ISSUE {key[0]} {key[1]}/{key[2]} {key[3]}: {reason}" for _ in range(got))
        for key in sorted(set(seen) - set(KNOWN_ISSUES)):
            problems.append(f"unexpected PeerAuthentication {key}")
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print("\n".join(baseline))
    print(f"PASS: PeerAuthentication exceptions are allowlisted; {len(baseline)} known-issue baseline entr{'y' if len(baseline) == 1 else 'ies'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
