#!/usr/bin/env python3
"""R398: KISA-NET-01 namespace ingress default-deny for iam, storage, and monitoring.

Verifies:
1. Both delivery paths exist for each of iam, storage, and monitoring:
   - gitops/resources/<ns>-ingress-policy.yaml declares a pure namespace-wide
     default-deny Ingress NetworkPolicy (`podSelector: {}`, `policyTypes: [Ingress]`,
     `ingress: []`, `app.kubernetes.io/managed-by: narwhal-gitops`), plus explicit
     per-workload allow policies.
   - gitops/charts/narwhal-apps/templates/<ns>-ingress-policy.yaml declares the
     ArgoCD Application (sync-wave "2", project "platform", path "resources",
     exact directory.include, exact destination.namespace, prune+selfHeal+ServerSideApply).
   - docs/common/upgrade-waves.json lists all three Applications under NOT_YET_MODELLED.
2. Zero allow-all peers anywhere in the three policy files:
   - Every NetworkPolicy ingress rule has a non-empty `from` with no empty
     `namespaceSelector: {}` (without restricted podSelector), no broad ipBlock
     (< /8 IPv4 or < /16 IPv6), and explicit `ports` without `endPort`.
   - Every CiliumNetworkPolicy rule uses only narrow `fromEntities` (never
     `all`/`world`/`cluster`) and explicit `toPorts`.
3. HBONE TCP 15008 handling:
   - Ambient namespaces (`storage`, `monitoring`) include TCP 15008 on every
     port-limited NetworkPolicy ingress rule and never allow peer-less HBONE.
   - `iam` is verified non-ambient (not in `AMBIENT_NAMESPACES` of 09-istio-ambient.sh).
   - `monitoring-ingress-policy.yaml` does not widen existing `alloy-logs` or `tempo`
     pod policies (`monitoring-default-deny-ingress` has `ingress: []` and no allow
     policy targets `alloy-logs` or `tempo`).
4. Host-network webhook callers handled via pod-scoped CiliumNetworkPolicy:
   - `storage/openbao-agent-injector-webhook-ingress` -> TCP 8080 from
     `kube-apiserver`, `host`, `remote-node` (`enableDefaultDeny: {ingress: false, egress: false}`).
   - `monitoring/prometheus-operator-webhook-ingress` -> TCP 10250 from
     `kube-apiserver`, `host`, `remote-node` (`enableDefaultDeny: {ingress: false, egress: false}`).
5. `devtools` is untouched (no `devtools-ingress-policy.yaml` resource or Application,
   and no namespace-wide default-deny Ingress NetworkPolicy in `devtools`).
6. `check-permissive-ingress-isolation.py` removes `monitoring-permissive` from
   KNOWN_ISSUES while keeping `platform-system-permissive`.
7. `scripts/test/regression-check-kakao.sh` wires R398 with `--mutation-verify`.
"""

from __future__ import annotations

import copy
import ipaddress
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]

TARGET_NAMESPACES = ("iam", "storage", "monitoring")
AMBIENT_TARGETS = {"storage", "monitoring"}
BROAD_CILIUM_ENTITIES = {"all", "world", "cluster"}
ALLOWED_WEBHOOK_ENTITIES = {"kube-apiserver", "host", "remote-node"}

FILES = {
    "iam_res": ROOT / "gitops/resources/iam-ingress-policy.yaml",
    "storage_res": ROOT / "gitops/resources/storage-ingress-policy.yaml",
    "monitoring_res": ROOT / "gitops/resources/monitoring-ingress-policy.yaml",
    "iam_app": ROOT / "gitops/charts/narwhal-apps/templates/iam-ingress-policy.yaml",
    "storage_app": ROOT / "gitops/charts/narwhal-apps/templates/storage-ingress-policy.yaml",
    "monitoring_app": ROOT / "gitops/charts/narwhal-apps/templates/monitoring-ingress-policy.yaml",
    "waves": ROOT / "docs/common/upgrade-waves.json",
    "ambient_sh": ROOT / "scripts/cluster/09-istio-ambient.sh",
    "perm_iso": ROOT / "scripts/test/lib/check-permissive-ingress-isolation.py",
    "reg": ROOT / "scripts/test/regression-check-kakao.sh",
}

EXPECTED_WEBHOOK_CNPS = {
    "storage": (
        "openbao-agent-injector-webhook-ingress",
        {
            "app.kubernetes.io/name": "openbao-agent-injector",
            "app.kubernetes.io/instance": "openbao",
            "component": "webhook",
        },
        "8080",
    ),
    "monitoring": (
        "prometheus-operator-webhook-ingress",
        {
            "app": "kube-prometheus-stack-operator",
            "release": "prometheus-stack",
        },
        "10250",
    ),
}


def _restricts_selector(sel: object) -> bool:
    if not isinstance(sel, dict):
        return False
    ml = sel.get("matchLabels")
    me = sel.get("matchExpressions")
    if isinstance(ml, dict) and bool(ml):
        return True
    if isinstance(me, list) and me:
        for e in me:
            if not isinstance(e, dict) or not isinstance(e.get("key"), str):
                return False
            if e.get("operator") in ("In", "Exists"):
                if e["operator"] == "In" and not e.get("values"):
                    return False
                return True
    return False


def _cidr_restricted(cidr: object) -> bool:
    try:
        net = ipaddress.ip_network(str(cidr).strip(), strict=False)
        return net.prefixlen >= (8 if net.version == 4 else 16)
    except ValueError:
        return False


def _check_resource_file(ns: str, text: str, errors: list[str]) -> None:
    try:
        docs = [d for d in yaml.safe_load_all(text) if d is not None]
    except yaml.YAMLError as exc:
        errors.append(f"{ns}-ingress-policy.yaml YAML parse error: {exc}")
        return

    if not docs:
        errors.append(f"{ns}-ingress-policy.yaml is empty")
        return

    expected_dd_name = f"{ns}-default-deny-ingress"
    found_dd = False
    found_webhook_cnp = False

    for doc in docs:
        if not isinstance(doc, dict):
            errors.append(f"{ns}-ingress-policy.yaml contains non-mapping document")
            continue
        kind = doc.get("kind")
        api = doc.get("apiVersion")
        meta = doc.get("metadata") or {}
        spec = doc.get("spec") or {}
        name = meta.get("name")

        if meta.get("namespace") != ns:
            errors.append(f"{ns}-ingress-policy.yaml doc {name!r} has wrong namespace {meta.get('namespace')!r}")
        if (meta.get("labels") or {}).get("app.kubernetes.io/managed-by") != "narwhal-gitops":
            errors.append(f"{ns}/{name} missing label app.kubernetes.io/managed-by=narwhal-gitops")

        if kind == "NetworkPolicy":
            if api != "networking.k8s.io/v1":
                errors.append(f"{ns}/{name} wrong apiVersion {api!r}")
            if spec.get("policyTypes") != ["Ingress"]:
                errors.append(f"{ns}/{name} policyTypes must be ['Ingress'] (got {spec.get('policyTypes')!r})")

            pod_sel = spec.get("podSelector")
            if pod_sel == {}:
                if name != expected_dd_name:
                    errors.append(f"{ns}/{name} uses empty podSelector outside {expected_dd_name}")
                if spec.get("ingress") != []:
                    errors.append(f"{ns}/{name} default-deny must have empty ingress: [] (got {spec.get('ingress')!r})")
                else:
                    found_dd = True
                continue

            if not _restricts_selector(pod_sel):
                errors.append(f"{ns}/{name} non-default-deny policy must have restricted podSelector")

            # Preserve dedicated alloy-logs and tempo policies in monitoring (never widen them).
            if ns == "monitoring" and isinstance(pod_sel, dict):
                ml = pod_sel.get("matchLabels") or {}
                if ml.get("app.kubernetes.io/name") in ("alloy-logs", "tempo"):
                    errors.append(f"{ns}/{name} must not target alloy-logs or tempo (governed by dedicated policies)")

            rules = spec.get("ingress")
            if not isinstance(rules, list) or not rules:
                errors.append(f"{ns}/{name} allow policy must have non-empty ingress rules")
                continue

            for idx, rule in enumerate(rules):
                if not isinstance(rule, dict):
                    errors.append(f"{ns}/{name} rule #{idx} is not a mapping")
                    continue
                peers = rule.get("from")
                if not isinstance(peers, list) or not peers:
                    errors.append(f"{ns}/{name} rule #{idx} has missing/empty 'from' (allow-all)")
                else:
                    for pidx, peer in enumerate(peers):
                        if not isinstance(peer, dict) or not peer:
                            errors.append(f"{ns}/{name} rule #{idx} peer #{pidx} is empty/invalid")
                            continue
                        ns_sel = peer.get("namespaceSelector")
                        p_sel = peer.get("podSelector")
                        ipb = peer.get("ipBlock")
                        if ns_sel is None and p_sel is None and ipb is None:
                            errors.append(f"{ns}/{name} rule #{idx} peer #{pidx} has no selector key")
                        if ns_sel is not None:
                            if not _restricts_selector(ns_sel):
                                errors.append(f"{ns}/{name} rule #{idx} peer #{pidx} has allow-all namespaceSelector")
                            if not _restricts_selector(p_sel):
                                errors.append(f"{ns}/{name} rule #{idx} peer #{pidx} cross-namespace peer missing restricted podSelector")
                        elif p_sel is not None and not _restricts_selector(p_sel):
                            errors.append(f"{ns}/{name} rule #{idx} peer #{pidx} same-namespace peer has unrestricted podSelector")
                        if ipb is not None and not (isinstance(ipb, dict) and _cidr_restricted(ipb.get("cidr"))):
                            errors.append(f"{ns}/{name} rule #{idx} peer #{pidx} has broad/invalid ipBlock")

                ports = rule.get("ports")
                if not isinstance(ports, list) or not ports:
                    errors.append(f"{ns}/{name} rule #{idx} must specify explicit ports")
                    continue
                has_hbone = False
                for p in ports:
                    if not isinstance(p, dict) or "port" not in p:
                        errors.append(f"{ns}/{name} rule #{idx} invalid port entry {p!r}")
                        continue
                    if "endPort" in p:
                        errors.append(f"{ns}/{name} rule #{idx} must not use endPort")
                    if p.get("protocol", "TCP") == "TCP" and str(p.get("port")) == "15008":
                        has_hbone = True
                if ns in AMBIENT_TARGETS and not has_hbone:
                    errors.append(f"{ns}/{name} rule #{idx} in ambient namespace lacks TCP 15008 (HBONE)")

        elif kind == "CiliumNetworkPolicy":
            if api != "cilium.io/v2":
                errors.append(f"{ns}/{name} CiliumNetworkPolicy wrong apiVersion {api!r}")
            if spec.get("enableDefaultDeny") != {"ingress": False, "egress": False}:
                errors.append(f"{ns}/{name} CNP must set enableDefaultDeny: {{ingress: false, egress: false}}")
            ep_sel = spec.get("endpointSelector")
            if not _restricts_selector(ep_sel):
                errors.append(f"{ns}/{name} CNP must use restricted endpointSelector")
            c_rules = spec.get("ingress")
            if not isinstance(c_rules, list) or not c_rules:
                errors.append(f"{ns}/{name} CNP must declare ingress rules")
                continue
            for idx, r in enumerate(c_rules):
                entities = r.get("fromEntities")
                if not isinstance(entities, list) or not entities:
                    errors.append(f"{ns}/{name} CNP rule #{idx} must declare fromEntities")
                    continue
                ent_set = set(map(str, entities))
                if ent_set & BROAD_CILIUM_ENTITIES:
                    errors.append(f"{ns}/{name} CNP rule #{idx} uses broad entity {sorted(ent_set & BROAD_CILIUM_ENTITIES)}")
                if not ent_set.issubset(ALLOWED_WEBHOOK_ENTITIES):
                    errors.append(f"{ns}/{name} CNP rule #{idx} uses unexpected entity {sorted(ent_set)}")
            if ns in EXPECTED_WEBHOOK_CNPS:
                exp_name, exp_sel, exp_port = EXPECTED_WEBHOOK_CNPS[ns]
                if name == exp_name:
                    if (ep_sel or {}).get("matchLabels") != exp_sel:
                        errors.append(f"{ns}/{name} CNP endpointSelector mismatch: got {ep_sel!r}")
                    ent_all = {str(e) for r in c_rules for e in (r.get("fromEntities") or [])}
                    if not {"kube-apiserver", "host", "remote-node"}.issubset(ent_all):
                        errors.append(f"{ns}/{name} CNP must allow kube-apiserver, host, and remote-node")
                    got_ports = {
                        (str(p.get("port")), p.get("protocol", "TCP"))
                        for r in c_rules
                        for tp in (r.get("toPorts") or [])
                        for p in (tp.get("ports") or [])
                    }
                    if got_ports != {(exp_port, "TCP")}:
                        errors.append(f"{ns}/{name} CNP toPorts must be exact {{({exp_port!r}, 'TCP')}}, got {got_ports!r}")
                    found_webhook_cnp = True
        else:
            errors.append(f"{ns}-ingress-policy.yaml unexpected kind {kind!r}")

    if not found_dd:
        errors.append(f"{ns}-ingress-policy.yaml missing pure default-deny NetworkPolicy {expected_dd_name}")
    if ns in EXPECTED_WEBHOOK_CNPS and not found_webhook_cnp:
        errors.append(f"{ns}-ingress-policy.yaml missing host-network webhook CNP {EXPECTED_WEBHOOK_CNPS[ns][0]}")


def _check_app_and_waves(ns: str, app_text: str, waves: dict, errors: list[str]) -> None:
    rendered = app_text.replace("{{ .Values.repoURL }}", "http://example.invalid/repo.git")
    doc = yaml.safe_load(rendered)
    app_name = f"{ns}-ingress-policy"
    if not isinstance(doc, dict) or doc.get("kind") != "Application":
        errors.append(f"narwhal-apps/templates/{app_name}.yaml must declare an ArgoCD Application")
        return
    meta = doc.get("metadata") or {}
    if meta.get("name") != app_name or meta.get("namespace") != "devtools":
        errors.append(f"{app_name} Application must be devtools/{app_name}")
    if (meta.get("annotations") or {}).get("argocd.argoproj.io/sync-wave") != "2":
        errors.append(f"{app_name} Application must use sync-wave '2'")
    spec = doc.get("spec") or {}
    if spec.get("project") != "platform":
        errors.append(f"{app_name} Application must use project 'platform'")
    src = spec.get("source") or {}
    if src.get("path") != "resources" or (src.get("directory") or {}).get("include") != f"{app_name}.yaml":
        errors.append(f"{app_name} Application must include resources/{app_name}.yaml")
    if (spec.get("destination") or {}).get("namespace") != ns:
        errors.append(f"{app_name} Application destination.namespace must be {ns}")
    sync = spec.get("syncPolicy") or {}
    auto = sync.get("automated") or {}
    if auto.get("prune") is not True or auto.get("selfHeal") is not True:
        errors.append(f"{app_name} Application must enable automated prune and selfHeal")
    if "ServerSideApply=true" not in (sync.get("syncOptions") or []):
        errors.append(f"{app_name} Application must set ServerSideApply=true")

    if app_name not in (waves.get("NOT_YET_MODELLED") or {}):
        errors.append(f"docs/common/upgrade-waves.json must list {app_name} under NOT_YET_MODELLED")


def _check_iam_non_ambient(ambient_sh: str, errors: list[str]) -> None:
    m = re.search(r"AMBIENT_NAMESPACES=\((.*?)\)", ambient_sh, re.S)
    if not m:
        errors.append("09-istio-ambient.sh missing AMBIENT_NAMESPACES array")
        return
    enrolled = {tok.strip() for tok in m.group(1).split() if tok.strip() and not tok.strip().startswith("#")}
    if "iam" in enrolled:
        errors.append("iam is in AMBIENT_NAMESPACES; iam-ingress-policy assumes iam is non-ambient")
    for amb_ns in sorted(AMBIENT_TARGETS):
        if amb_ns not in enrolled:
            errors.append(f"{amb_ns} missing from 09-istio-ambient.sh AMBIENT_NAMESPACES")


def _check_devtools_untouched(devtools_files: list[tuple[str, str]], errors: list[str]) -> None:
    for rel, text in devtools_files:
        if rel.endswith("devtools-ingress-policy.yaml"):
            errors.append(f"devtools is out of scope: unexpected file {rel}")
            continue
        if "kind: NetworkPolicy" not in text:
            continue
        for doc in yaml.safe_load_all(text):
            if not isinstance(doc, dict) or doc.get("kind") != "NetworkPolicy":
                continue
            meta = doc.get("metadata") or {}
            spec = doc.get("spec") or {}
            if (
                meta.get("namespace") == "devtools"
                and spec.get("podSelector") == {}
                and "Ingress" in (spec.get("policyTypes") or [])
            ):
                errors.append(f"devtools is out of scope: {rel} defines namespace-wide Ingress policy {meta.get('name')!r}")


def _check_known_issues(perm_iso_text: str, errors: list[str]) -> None:
    if '("monitoring", "monitoring-permissive")' in perm_iso_text:
        errors.append("check-permissive-ingress-isolation.py still lists monitoring-permissive in KNOWN_ISSUES")
    if '("platform-system", "platform-system-permissive")' not in perm_iso_text:
        errors.append("check-permissive-ingress-isolation.py must retain platform-system-permissive in KNOWN_ISSUES")


def check(texts: dict[str, str], devtools_scan: list[tuple[str, str]]) -> list[str]:
    errors: list[str] = []
    waves = json.loads(texts["waves"])
    for ns in TARGET_NAMESPACES:
        _check_resource_file(ns, texts[f"{ns}_res"], errors)
        _check_app_and_waves(ns, texts[f"{ns}_app"], waves, errors)
    _check_iam_non_ambient(texts["ambient_sh"], errors)
    _check_devtools_untouched(devtools_scan, errors)
    _check_known_issues(texts["perm_iso"], errors)
    if "check R398 " not in texts["reg"] or "check-kisa-ingress-default-deny.py --mutation-verify" not in texts["reg"]:
        errors.append("regression-check-kakao.sh must wire R398 with --mutation-verify")
    return errors


MUTATIONS: list[tuple[str, str, str, str]] = [
    ("drop iam default-deny", "iam_res", "  name: iam-default-deny-ingress\n", "  name: iam-other\n"),
    ("widen iam keycloak peer by dropping podSelector", "iam_res", "          podSelector:\n            matchLabels:\n              app.kubernetes.io/name: apisix\n", ""),
    ("widen iam keycloak peer to allow-all namespaceSelector", "iam_res", "            matchLabels:\n              kubernetes.io/metadata.name: platform-system\n", "            matchLabels: {}\n"),
    ("drop storage default-deny", "storage_res", "  name: storage-default-deny-ingress\n", "  name: storage-other\n"),
    ("drop HBONE 15008 from storage openbao rule", "storage_res", "        - protocol: TCP\n          port: 8200\n        - protocol: TCP\n          port: 15008\n", "        - protocol: TCP\n          port: 8200\n"),
    ("drop storage host-network webhook CNP", "storage_res", "  name: openbao-agent-injector-webhook-ingress\n", "  name: wrong-cnp\n"),
    ("widen storage webhook CNP entity to world", "storage_res", "        - remote-node\n", "        - world\n"),
    ("drop monitoring default-deny", "monitoring_res", "  name: monitoring-default-deny-ingress\n", "  name: monitoring-other\n"),
    ("allow non-empty ingress on monitoring default-deny", "monitoring_res", "  ingress: []\n", "  ingress:\n    - from:\n        - podSelector: {}\n"),
    ("drop HBONE 15008 from monitoring grafana rule", "monitoring_res", "        - protocol: TCP\n          port: 3000\n        - protocol: TCP\n          port: 15008\n", "        - protocol: TCP\n          port: 3000\n"),
    ("drop monitoring host-network webhook CNP", "monitoring_res", "  name: prometheus-operator-webhook-ingress\n", "  name: wrong-cnp\n"),
    ("widen monitoring webhook CNP port", "monitoring_res", '            - port: "10250"\n', '            - port: "443"\n'),
    ("target alloy-logs from monitoring-ingress-policy", "monitoring_res", "      app.kubernetes.io/name: grafana\n      app.kubernetes.io/instance: prometheus-stack\n", "      app.kubernetes.io/name: alloy-logs\n      app.kubernetes.io/instance: k8s-monitoring-alloy-logs\n"),
    ("break iam Application include", "iam_app", 'include: "iam-ingress-policy.yaml"', 'include: "wrong.yaml"'),
    ("break storage Application sync-wave", "storage_app", 'argocd.argoproj.io/sync-wave: "2"', 'argocd.argoproj.io/sync-wave: "9"'),
    ("break monitoring Application destination", "monitoring_app", "    namespace: monitoring\n", "    namespace: devtools\n"),
    ("drop monitoring-ingress-policy from upgrade-waves", "waves", '    "monitoring-ingress-policy": "Network policy configuration, not a separately upgraded component.",\n', ""),
    ("re-add monitoring-permissive to KNOWN_ISSUES", "perm_iso", 'KNOWN_ISSUES = {\n', 'KNOWN_ISSUES = {\n    ("monitoring", "monitoring-permissive"): (1, "stale"),\n'),
    ("drop R398 wiring in regression-check-kakao.sh", "reg", "check R398 ", "check R9999 "),
]


def _collect_devtools_scan() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for base in (ROOT / "gitops/resources", ROOT / "gitops/charts/narwhal-apps/templates"):
        for p in sorted(base.glob("*.yaml")):
            rel = p.relative_to(ROOT).as_posix()
            text = p.read_text(encoding="utf-8")
            if rel.endswith("devtools-ingress-policy.yaml") or ("kind: NetworkPolicy" in text and "{{" not in text):
                out.append((rel, text))
    return out


def main() -> int:
    texts = {k: p.read_text(encoding="utf-8") for k, p in FILES.items()}
    devtools_scan = _collect_devtools_scan()
    errors = check(texts, devtools_scan)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1

    if "--mutation-verify" in sys.argv:
        for name, key, old, new in MUTATIONS:
            mutated = copy.deepcopy(texts)
            if old not in mutated[key]:
                print(f"FAIL: mutation target not found for {name}", file=sys.stderr)
                return 1
            mutated[key] = mutated[key].replace(old, new, 1)
            if not check(mutated, devtools_scan):
                print(f"FAIL: mutation not rejected: {name}", file=sys.stderr)
                return 1
        # Additional mutation: ensure a devtools default-deny is rejected.
        bad_devtools = devtools_scan + [
            (
                "gitops/resources/devtools-ingress-policy.yaml",
                "apiVersion: networking.k8s.io/v1\nkind: NetworkPolicy\nmetadata:\n  name: devtools-default-deny-ingress\n  namespace: devtools\nspec:\n  podSelector: {}\n  policyTypes: [Ingress]\n  ingress: []\n",
            )
        ]
        if not check(texts, bad_devtools):
            print("FAIL: mutation adding devtools default-deny was not rejected", file=sys.stderr)
            return 1
        print(f"PASS: R398 KISA-NET-01 iam/storage/monitoring ingress default-deny ({len(MUTATIONS) + 1} mutations rejected)")
        return 0

    print("PASS: R398 KISA-NET-01 iam/storage/monitoring ingress default-deny")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
