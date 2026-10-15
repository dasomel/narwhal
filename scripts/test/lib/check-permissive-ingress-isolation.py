#!/usr/bin/env python3
"""Static check: a namespace-wide PERMISSIVE/DISABLE PeerAuthentication needs an ingress default-deny (#126).

Such a namespace accepts plaintext from every pod in the cluster unless an INGRESS default-deny
compensates. check-peer-authentication.py (R243) governs the exceptions themselves; this check
requires, for each namespace-wide (no workload selector) PERMISSIVE/DISABLE PeerAuthentication in
namespace N (istio-system/default, the mesh root, stays with R243), a policy in N that is either
  - a networking.k8s.io/v1 NetworkPolicy in N with `podSelector: {}` (exactly empty, not matchLabels)
    and `Ingress` in a list-typed `policyTypes`, whose ingress rules are not allow-from-everything
    (a rule without a non-empty `from`, `{}`/namespaceSelector {}/ipBlock 0.0.0.0/0 sources), or
  - a CiliumNetworkPolicy (in N) / CiliumClusterwideNetworkPolicy with `endpointSelector: {}` and
    non-empty ingress rules that each name a restricted `from*` source. Anything unparsable or
    unexpected does NOT count as isolation (fail closed).
Sources: gitops json/yaml/yml/tpl except the vendored third-party sub-charts under
gitops/charts/kubernetes-dashboard/charts/**, plus heredocs in scripts/**/*.sh.
Fails closed on unparsable sources, duplicate keys, non-mapping documents/fields (arrays are not
objects), and a helm-templated or .tpl file declaring a PeerAuthentication. A templated file that
only declares NetworkPolicies cannot be evaluated, so it simply does not count as isolation.
KNOWN_ISSUES is an explicit baseline keyed on (namespace, name) with an exact COUNT: drift in either
direction (incl. a stale entry) fails; it is printed on stdout so the harness WARNs. Default mode only.
"""

from __future__ import annotations

import collections
import json
import os
import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(os.environ.get("INGRESS_ISO_ROOT") or pathlib.Path(__file__).resolve().parents[3]).resolve()  # env: probe trees
MESH = ("istio-system", "default")
MODES = {"STRICT", "PERMISSIVE", "DISABLE", "UNSET"}
EXCLUDED = ("scripts/test/fixtures", "gitops/charts/kubernetes-dashboard/charts/")  # path prefixes
CATCH_ALL = {"0.0.0.0/0", "::/0"}
# Real gaps in the current repo: (namespace, name) -> (count, reason). Remove when fixed.
KNOWN_ISSUES = {
    ("monitoring", "monitoring-permissive"): (1, "namespace-wide PERMISSIVE, no ingress default-deny (#126)"),
    ("platform-system", "platform-system-permissive"): (1, "namespace-wide PERMISSIVE, no ingress default-deny (#126)"),
}
KIND_RE = re.compile(r"""kind["']?\s*:\s*["']?(PeerAuthentication|\w*NetworkPolicy)\b""")
PA_RE = re.compile(r"""kind["']?\s*:\s*["']?PeerAuthentication\b""")


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


def strip(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


def heredocs(text: str) -> list[tuple[str, str]]:
    """(raw, body) of every shell heredoc; `<<-` strips leading tabs from body and terminator like bash."""
    lines, bodies, i = text.splitlines(), [], 0
    while i < len(lines):
        m = re.search(r"(?<!<)<<(?!<)(-?)\s*(['\"]?)(\w+)\2", lines[i])
        i += 1
        if not m:
            continue
        tabs, delim, body, start = m.group(1) == "-", m.group(3), [], i
        while i < len(lines) and (lines[i].lstrip("\t") if tabs else lines[i]) != delim:
            body.append(lines[i].lstrip("\t") if tabs else lines[i])
            i += 1
        bodies.append(("\n".join(lines[start:i]), "\n".join(body)))
        i += 1  # an unterminated heredoc runs to EOF, so a policy hiding after it is still scanned
    return bodies


def _json_pairs(pairs: list) -> dict:
    if len({k for k, _ in pairs}) != len(pairs):
        raise ValueError("duplicate key in JSON object")
    return dict(pairs)


def documents(rel: str, path: pathlib.Path, text: str) -> list[dict]:
    if not KIND_RE.search(strip(text)):
        return []
    chunks = [text]
    if path.suffix == ".sh":
        bodies = heredocs(text)
        for raw, _ in bodies:
            text = text.replace(raw, "", 1)
        if KIND_RE.search(strip(text)):
            raise ValueError(f"{rel}: declares a policy outside a heredoc (cannot be scanned)")
        chunks = [b for _, b in bodies if KIND_RE.search(strip(b))]
    docs: list[dict] = []
    for chunk in chunks:
        if path.suffix == ".tpl" or "{{" in strip(chunk):
            if PA_RE.search(strip(chunk)):
                raise ValueError(f"{rel}: templated/.tpl PeerAuthentication cannot be evaluated statically")
            continue  # templated NetworkPolicy: unevaluable, so never counts as isolation
        try:
            loaded = [json.loads(chunk, object_pairs_hook=_json_pairs)] if path.suffix == ".json" \
                else list(yaml.load_all(chunk, Loader=UniqueLoader))
        except (yaml.YAMLError, ValueError) as exc:
            raise ValueError(f"{rel}: unparsable source: {exc}") from exc
        for doc in loaded:
            if doc is None:
                continue
            if not isinstance(doc, dict):
                raise ValueError(f"{rel}: non-mapping document")
            if doc.get("kind") == "List":
                if not isinstance(doc.get("items"), list):
                    raise ValueError(f"{rel}: List without an items array")
                docs.extend(doc["items"])
            else:
                docs.append(doc)
    return docs


def obj(rel: str, parent: dict, key: str) -> dict:
    """Optional plain object field: absent/null -> {}, anything but a mapping (arrays!) fails closed."""
    value = parent.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{rel}: {key} is not a mapping")
    return value


def wide_permissive(rel: str, doc: dict) -> tuple[str, str, str] | None:
    """(namespace, name, mode) of a namespace-wide PERMISSIVE/DISABLE PeerAuthentication, else None."""
    if not str(doc.get("apiVersion", "")).startswith("security.istio.io/"):
        raise ValueError(f"{rel}: PeerAuthentication has apiVersion {doc.get('apiVersion')!r}")
    meta, spec = obj(rel, doc, "metadata"), obj(rel, doc, "spec")
    ns, name = meta.get("namespace"), meta.get("name")
    if not (isinstance(ns, str) and ns and isinstance(name, str) and name):
        raise ValueError(f"{rel}: PeerAuthentication lacks a string metadata.name/namespace")
    port_levels = obj(rel, spec, "portLevelMtls")
    modes = [obj(rel, spec, "mtls").get("mode", "UNSET")]
    modes += [obj(rel, port_levels, k).get("mode", "UNSET") for k in port_levels]
    bad = [m for m in modes if m not in MODES]
    if bad:
        raise ValueError(f"{rel}: PeerAuthentication {ns}/{name} has invalid mtls mode {bad[0]!r}")
    selector = obj(rel, spec, "selector")
    workload = any(selector.get(k) for k in ("matchLabels", "matchExpressions"))
    weakest = next((m for m in ("DISABLE", "PERMISSIVE") if m in modes), None)
    return None if workload or weakest is None or (ns, name) == MESH else (ns, name, weakest)


def allows_everyone(rule: object) -> bool:
    """True unless the NetworkPolicy ingress rule restricts its sources."""
    sources = rule.get("from") if isinstance(rule, dict) else None
    if not isinstance(sources, list) or not sources:  # absent/[]/non-list `from` matches every source
        return True
    for src in sources:
        if not isinstance(src, dict) or not src:
            return True
        pod, ns = src.get("podSelector"), src.get("namespaceSelector")
        if (pod is not None and not isinstance(pod, dict)) or (ns is not None and not isinstance(ns, dict)):
            return True
        if ns == {} and pod in (None, {}):
            return True
        block = src.get("ipBlock")
        if block is not None and (not isinstance(block, dict) or str(block.get("cidr", "")).strip() in CATCH_ALL):
            return True
    return False


def cilium_rule_restricted(rule: object) -> bool:
    if not isinstance(rule, dict):
        return False
    froms = {k: v for k, v in rule.items() if k.startswith("from")}
    if not froms or any(not isinstance(v, list) or not v for v in froms.values()):
        return False
    entities = froms.get("fromEntities", [])
    cidrs = [str(c).strip() for c in froms.get("fromCIDR", [])]
    cidrs += [str(c.get("cidr", "")).strip() if isinstance(c, dict) else "" for c in froms.get("fromCIDRSet", [])]
    return not ({"all", "world"} & set(map(str, entities)) or set(cidrs) & CATCH_ALL
                or any(e in ({}, None) or not isinstance(e, dict) for e in froms.get("fromEndpoints", [])))


def isolates(doc: dict, ns: str) -> bool:
    """True if doc is a default-deny ingress policy covering every pod of namespace ns (else fail closed)."""
    meta, spec = doc.get("metadata"), doc.get("spec")
    if not isinstance(meta, dict) or not isinstance(spec, dict):
        return False
    api, kind, rules = str(doc.get("apiVersion", "")), doc.get("kind"), spec.get("ingress")
    if kind == "NetworkPolicy":
        types = spec.get("policyTypes")
        return (api.startswith("networking.k8s.io/") and meta.get("namespace") == ns
                and isinstance(spec.get("podSelector"), dict) and not spec["podSelector"]
                and isinstance(types, list) and "Ingress" in types
                and (rules is None or (isinstance(rules, list) and not any(allows_everyone(r) for r in rules))))
    if kind in ("CiliumNetworkPolicy", "CiliumClusterwideNetworkPolicy"):
        scoped = kind == "CiliumClusterwideNetworkPolicy" or meta.get("namespace") == ns
        deny = spec.get("enableDefaultDeny")
        return (api.startswith("cilium.io/") and scoped and isinstance(spec.get("endpointSelector"), dict)
                and not spec["endpointSelector"] and not (isinstance(deny, dict) and deny.get("ingress") is False)
                and isinstance(rules, list) and bool(rules) and all(cilium_rule_restricted(r) for r in rules))
    return False


def default_files() -> list[pathlib.Path]:
    found = [p for ext in ("yaml", "yml", "json", "tpl") for p in (ROOT / "gitops").rglob(f"*.{ext}")]
    found += (ROOT / "scripts").rglob("*.sh")
    return sorted(p for p in found if not p.relative_to(ROOT).as_posix().startswith(EXCLUDED))


def main() -> int:
    args = [pathlib.Path(a) for a in sys.argv[1:]]
    problems: list[str] = []
    wide: list[tuple[str, str, str, str]] = []
    policies: list[dict] = []
    for path in args or default_files():
        rel = path.resolve().relative_to(ROOT).as_posix() if ROOT in path.resolve().parents else str(path)
        try:
            for doc in documents(rel, path, path.read_text()):
                if not isinstance(doc, dict):
                    raise ValueError(f"{rel}: non-mapping List item")
                if doc.get("kind") == "PeerAuthentication":
                    hit = wide_permissive(rel, doc)
                    wide += [(rel, *hit)] if hit else []
                else:
                    policies.append(doc)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            problems.append(str(exc))
    seen: collections.Counter = collections.Counter()
    for rel, ns, name, mode in wide:
        if any(isolates(p, ns) for p in policies):
            continue
        if not args and (ns, name) in KNOWN_ISSUES:
            seen[(ns, name)] += 1
        else:
            problems.append(f"{rel}: namespace {ns} has namespace-wide {mode} PeerAuthentication {name} "
                            "without a compensating default-deny ingress policy")
    baseline: list[str] = []
    for (ns, name), (want, reason) in sorted(KNOWN_ISSUES.items() if not args else []):
        if seen[(ns, name)] != want:
            problems.append(f"KNOWN_ISSUES drift: {ns}/{name} expected {want}, found {seen[(ns, name)]} "
                            "(remove or update the entry)")
        baseline += [f"KNOWN-ISSUE {ns}/{name}: {reason}"] * seen[(ns, name)]
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print("\n".join(baseline))
    print(f"PASS: permissive namespaces have ingress isolation; {len(baseline)} known-issue baseline entr"
          f"{'y' if len(baseline) == 1 else 'ies'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
