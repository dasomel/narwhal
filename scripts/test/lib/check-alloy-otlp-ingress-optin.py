#!/usr/bin/env python3
"""R377: Opt-in OTLP trace ingest NetworkPolicy for alloy-logs (narwhal#735).

Verifies that:
  - gitops/resources/network-policies.yaml defines alloy-logs-ingress-policy in
    namespace monitoring selecting alloy-logs pods and restricting:
      * OTLP gRPC (4317) + HBONE (15008) from Grafana in monitoring,
      * OTLP gRPC/HTTP (4317/4318) + HBONE (15008) from namespaces labeled
        narwhal.io/otlp-ingest: "true",
      * HTTP metrics scrape (12345) + HBONE (15008) from Prometheus in monitoring.
  - No rule widens namespaceSelector/podSelector to all, uses ipBlock, omits HBONE
    15008, or admits monitoring/waypoint (alloy-logs Service opts out of waypoint).
  - gitops/resources/dev-namespace.yaml opts namespace dev in via
    narwhal.io/otlp-ingest: "true" while preserving istio.io/dataplane-mode: none.
  - gitops/charts/narwhal-apps/templates/k8s-monitoring.yaml and
    scripts/cluster/08-2-monitoring.sh keep istio.io/use-waypoint: none on alloy-logs.
  - docs/common/unified-observability-contract.md and gitops/resources/tenants/README.md
    document the opt-in label and honest residual risks (opted-in flood, hostNetwork bypass).
"""
import argparse
import re
import sys
import tempfile
from pathlib import Path

import yaml

NETPOL_YAML = "gitops/resources/network-policies.yaml"
DEV_NS_YAML = "gitops/resources/dev-namespace.yaml"
K8SMON_YAML = "gitops/charts/narwhal-apps/templates/k8s-monitoring.yaml"
MON_SH = "scripts/cluster/08-2-monitoring.sh"
CONTRACT_MD = "docs/common/unified-observability-contract.md"
TENANTS_README = "gitops/resources/tenants/README.md"

OPTIN_LABEL = "narwhal.io/otlp-ingest"
EXPECTED_POD_SELECTOR = {
    "app.kubernetes.io/name": "alloy-logs",
    "app.kubernetes.io/instance": "k8s-monitoring-alloy-logs",
}
EXPECTED_GRAFANA_PEER = (
    "monitoring",
    frozenset({("app.kubernetes.io/name", "grafana"), ("app.kubernetes.io/instance", "prometheus-stack")}),
)
EXPECTED_PROM_PEER = (
    "monitoring",
    frozenset({("app.kubernetes.io/name", "prometheus"), ("prometheus", "prometheus-stack-kube-prom-prometheus")}),
)


def _extract_k8smon_heredoc(script_text: str) -> dict:
    m = re.search(
        r"cat <<'K8SMONVALUES' > /tmp/k8s-monitoring-values\.yaml\n(.*?)\nK8SMONVALUES",
        script_text,
        re.S,
    )
    assert m, "08-2-monitoring.sh: missing K8SMONVALUES heredoc"
    return yaml.safe_load(m.group(1))


def _check_peer(peer: dict) -> tuple[str, object]:
    assert isinstance(peer, dict) and peer, f"alloy-logs-ingress-policy peer must be non-empty dict: {peer!r}"
    assert "ipBlock" not in peer, f"alloy-logs-ingress-policy must not use ipBlock: {peer!r}"
    ns_sel = peer.get("namespaceSelector")
    pod_sel = peer.get("podSelector")

    if ns_sel is not None:
        assert isinstance(ns_sel, dict), f"namespaceSelector must be a dict: {peer!r}"
        assert not ns_sel.get("matchExpressions"), f"unexpected namespaceSelector matchExpressions: {peer!r}"
        ns_ml = ns_sel.get("matchLabels")
        assert isinstance(ns_ml, dict) and ns_ml, (
            f"namespaceSelector.matchLabels must not be empty/widened: {peer!r}"
        )
        if pod_sel is None:
            assert ns_ml == {OPTIN_LABEL: "true"}, (
                f"namespace-only peer must require {OPTIN_LABEL}=true, got {ns_ml!r}"
            )
            return ("ns_optin", frozenset(ns_ml.items()))
        ns = ns_ml.get("kubernetes.io/metadata.name")
        assert ns, f"cross-ns pod peer must pin kubernetes.io/metadata.name: {peer!r}"
    else:
        ns = "monitoring"

    assert isinstance(pod_sel, dict), f"pod peer must define podSelector: {peer!r}"
    assert not pod_sel.get("matchExpressions"), f"unexpected podSelector matchExpressions: {peer!r}"
    ml = pod_sel.get("matchLabels") or {}
    assert ml, f"podSelector.matchLabels must not be empty: {peer!r}"
    assert "gateway.networking.k8s.io/gateway-name" not in ml, (
        f"alloy-logs-ingress-policy must not admit monitoring/waypoint: {peer!r}"
    )
    return ("pod", (ns, frozenset(ml.items())))


def _check_netpol(root: Path) -> None:
    docs = [
        d for d in yaml.safe_load_all((root / NETPOL_YAML).read_text(encoding="utf-8"))
        if isinstance(d, dict)
    ]
    matches = [
        d for d in docs
        if d.get("kind") == "NetworkPolicy"
        and (d.get("metadata") or {}).get("name") == "alloy-logs-ingress-policy"
    ]
    assert len(matches) == 1, f"expected exactly 1 alloy-logs-ingress-policy, found {len(matches)}"
    np = matches[0]
    meta = np.get("metadata") or {}
    assert meta.get("namespace") == "monitoring", "alloy-logs-ingress-policy namespace must be monitoring"
    assert (meta.get("labels") or {}).get("app.kubernetes.io/managed-by") == "narwhal-gitops", (
        "alloy-logs-ingress-policy must carry app.kubernetes.io/managed-by: narwhal-gitops"
    )

    spec = np.get("spec") or {}
    assert spec.get("policyTypes") == ["Ingress"], (
        f"alloy-logs-ingress-policy policyTypes must be ['Ingress'], got {spec.get('policyTypes')!r}"
    )
    assert (spec.get("podSelector") or {}).get("matchLabels") == EXPECTED_POD_SELECTOR, (
        f"alloy-logs-ingress-policy must target alloy-logs pods, got {spec.get('podSelector')!r}"
    )

    rules = spec.get("ingress") or []
    assert rules, "alloy-logs-ingress-policy must define ingress rules"

    seen_grafana = False
    seen_optin = False
    seen_prom = False

    for idx, rule in enumerate(rules):
        from_list = rule.get("from")
        assert isinstance(from_list, list) and from_list, (
            f"alloy-logs-ingress-policy rule #{idx} must specify non-empty 'from' peers"
        )
        ports_raw = rule.get("ports")
        assert isinstance(ports_raw, list) and ports_raw, (
            f"alloy-logs-ingress-policy rule #{idx} must specify explicit ports"
        )
        ports = {(p.get("protocol", "TCP"), p.get("port")) for p in ports_raw}
        assert ("TCP", 15008) in ports, (
            f"alloy-logs-ingress-policy rule #{idx} must allow ambient HBONE TCP/15008 (R200): {ports}"
        )
        app_ports = ports - {("TCP", 15008)}
        assert app_ports, f"alloy-logs-ingress-policy rule #{idx} missing application port: {ports}"

        for peer in from_list:
            kind, key = _check_peer(peer)
            if kind == "ns_optin":
                assert app_ports == {("TCP", 4317), ("TCP", 4318)}, (
                    f"opt-in namespace rule must allow only 4317/4318, got {app_ports!r}"
                )
                seen_optin = True
            elif key == EXPECTED_GRAFANA_PEER:
                assert app_ports == {("TCP", 4317)}, (
                    f"Grafana rule must allow only 4317, got {app_ports!r}"
                )
                seen_grafana = True
            elif key == EXPECTED_PROM_PEER:
                assert app_ports == {("TCP", 12345)}, (
                    f"Prometheus metrics rule must allow only 12345, got {app_ports!r}"
                )
                seen_prom = True
            else:
                raise AssertionError(f"unexpected peer in alloy-logs-ingress-policy: {peer!r}")

    assert seen_grafana, "alloy-logs-ingress-policy missing Grafana OTLP rule (4317)"
    assert seen_optin, f"alloy-logs-ingress-policy missing {OPTIN_LABEL}=true opt-in rule (4317/4318)"
    assert seen_prom, "alloy-logs-ingress-policy missing Prometheus metrics scrape rule (12345)"


def _check_dev_namespace(root: Path) -> None:
    docs = [
        d for d in yaml.safe_load_all((root / DEV_NS_YAML).read_text(encoding="utf-8"))
        if isinstance(d, dict)
    ]
    ns = next((d for d in docs if d.get("kind") == "Namespace" and (d.get("metadata") or {}).get("name") == "dev"), None)
    assert ns is not None, "dev-namespace.yaml missing Namespace/dev"
    labels = (ns.get("metadata") or {}).get("labels") or {}
    assert labels.get("istio.io/dataplane-mode") == "none", "dev must preserve istio.io/dataplane-mode: none"
    assert labels.get(OPTIN_LABEL) == "true", f"dev Namespace must carry {OPTIN_LABEL}: 'true'"


def _check_waypoint_opt_out(root: Path) -> None:
    k8smon_doc = yaml.safe_load((root / K8SMON_YAML).read_text(encoding="utf-8"))
    k8smon_labels = (((k8smon_doc["spec"]["source"]["helm"]["valuesObject"]["collectors"]["alloy-logs"]).get("service") or {}).get("labels") or {})
    assert k8smon_labels.get("istio.io/use-waypoint") == "none", (
        "k8s-monitoring.yaml alloy-logs service.labels must set istio.io/use-waypoint: none"
    )
    boot_vals = _extract_k8smon_heredoc((root / MON_SH).read_text(encoding="utf-8"))
    boot_labels = (((boot_vals["collectors"]["alloy-logs"]).get("service") or {}).get("labels") or {})
    assert boot_labels.get("istio.io/use-waypoint") == "none", (
        "08-2-monitoring.sh alloy-logs service.labels must set istio.io/use-waypoint: none"
    )


def _check_docs(root: Path) -> None:
    contract = (root / CONTRACT_MD).read_text(encoding="utf-8")
    assert OPTIN_LABEL in contract, f"{CONTRACT_MD} must document {OPTIN_LABEL}"
    assert "alloy-logs-ingress-policy" in contract, f"{CONTRACT_MD} must reference alloy-logs-ingress-policy"
    assert "옵트인된 네임스페이스" in contract, f"{CONTRACT_MD} must state opted-in namespace flood residual risk"
    assert "hostNetwork` 파드**는 Pod `NetworkPolicy`를 우회" in contract, (
        f"{CONTRACT_MD} must state hostNetwork platform bypass residual risk"
    )

    tenants_readme = (root / TENANTS_README).read_text(encoding="utf-8")
    assert OPTIN_LABEL in tenants_readme, f"{TENANTS_README} must document {OPTIN_LABEL} opt-in"


def verify(root: Path) -> None:
    _check_netpol(root)
    _check_dev_namespace(root)
    _check_waypoint_opt_out(root)
    _check_docs(root)


def mutation_verify(root: Path) -> int:
    rels = (NETPOL_YAML, DEV_NS_YAML, K8SMON_YAML, MON_SH, CONTRACT_MD, TENANTS_README)
    sources = {rel: (root / rel).read_text(encoding="utf-8") for rel in rels}
    mutations = [
        # 1. Remove alloy-logs-ingress-policy completely.
        (NETPOL_YAML, "  name: alloy-logs-ingress-policy", "  name: alloy-logs-ingress-policy-disabled"),
        # 2. Widen namespaceSelector to all namespaces ({} / matchLabels: {}).
        (
            NETPOL_YAML,
            "        - namespaceSelector:\n            matchLabels:\n              narwhal.io/otlp-ingest: \"true\"",
            "        - namespaceSelector: {}",
        ),
        # 3. Drop metrics port 12345 from Prometheus scrape rule.
        (
            NETPOL_YAML,
            "      ports:\n        - protocol: TCP\n          port: 12345\n        - protocol: TCP\n          port: 15008",
            "      ports:\n        - protocol: TCP\n          port: 15008",
        ),
        # 4. Drop Prometheus rule completely.
        (
            NETPOL_YAML,
            "    # 3. Prometheus (monitoring) -> alloy-logs HTTP metrics scrape (12345).\n    - from:\n        - podSelector:\n            matchLabels:\n              app.kubernetes.io/name: prometheus\n              prometheus: prometheus-stack-kube-prom-prometheus\n      ports:\n        - protocol: TCP\n          port: 12345\n        - protocol: TCP\n          port: 15008\n",
            "",
        ),
        # 5. Drop HBONE 15008 from opt-in rule (violates R200 ambient NetworkPolicy contract).
        (
            NETPOL_YAML,
            "        - protocol: TCP\n          port: 4318\n        - protocol: TCP\n          port: 15008",
            "        - protocol: TCP\n          port: 4318",
        ),
        # 6. Drop Grafana OTLP rule.
        (
            NETPOL_YAML,
            "    # 1. Grafana (monitoring) -> alloy-logs OTLP gRPC self-tracing (4317).\n    - from:\n        - podSelector:\n            matchLabels:\n              app.kubernetes.io/name: grafana\n              app.kubernetes.io/instance: prometheus-stack\n      ports:\n        - protocol: TCP\n          port: 4317\n        - protocol: TCP\n          port: 15008\n",
            "",
        ),
        # 7. Admit monitoring/waypoint (breaks source pod/namespace identity).
        (
            NETPOL_YAML,
            "              app.kubernetes.io/name: grafana\n              app.kubernetes.io/instance: prometheus-stack",
            "              gateway.networking.k8s.io/gateway-name: waypoint",
        ),
        # 8. Open 4317/4318 to all sources via peerless rule.
        (
            NETPOL_YAML,
            "    # 2. Explicitly opted-in namespaces -> alloy-logs OTLP gRPC/HTTP ingest (4317/4318).\n    - from:\n        - namespaceSelector:\n            matchLabels:\n              narwhal.io/otlp-ingest: \"true\"",
            "    # 2. Widened to all pods\n    - {} #",
        ),
        # 9. Break podSelector target on alloy-logs-ingress-policy.
        (
            NETPOL_YAML,
            "  podSelector:\n    matchLabels:\n      app.kubernetes.io/name: alloy-logs",
            "  podSelector:\n    matchLabels:\n      app.kubernetes.io/name: other-pod",
        ),
        # 10. Remove opt-in label from dev-namespace.yaml.
        (DEV_NS_YAML, '    narwhal.io/otlp-ingest: "true"\n', ""),
        # 11. Remove waypoint opt-out from k8s-monitoring.yaml.
        (K8SMON_YAML, '                istio.io/use-waypoint: "none"\n', ""),
        # 12. Remove residual risk documentation from contract.
        (CONTRACT_MD, "hostNetwork` 파드**는 Pod `NetworkPolicy`를 우회", "hostNetwork blocked everywhere"),
        # 13. Remove opt-in documentation from tenants/README.md.
        (TENANTS_README, OPTIN_LABEL, "other-label"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        fixture = Path(tmp)
        for rel in sources:
            (fixture / rel).parent.mkdir(parents=True, exist_ok=True)
        for rel, old, new in mutations:
            for k, v in sources.items():
                (fixture / k).write_text(v, encoding="utf-8")
            assert old in sources[rel], f"mutation anchor missing in {rel}: {old!r}"
            if rel == NETPOL_YAML and "alloy-logs-ingress-policy-disabled" in new:
                # Simulate removing alloy-logs-ingress-policy completely.
                head, tail = sources[rel].split("# D-ALLOY-OTLP-INGRESS", 1)
                _, rest = tail.split("# D-TEMPO-INGRESS", 1)
                (fixture / rel).write_text(head + "# D-TEMPO-INGRESS" + rest, encoding="utf-8")
            elif rel == NETPOL_YAML and "# 2. Widened to all pods" in new:
                # Replace the opt-in rule's from with a peerless ports-only rule.
                text = sources[rel].replace(old, "    - ports:\n        - protocol: TCP\n          port: 4317\n        - protocol: TCP\n          port: 15008\n      #", 1)
                (fixture / rel).write_text(text, encoding="utf-8")
            else:
                (fixture / rel).write_text(sources[rel].replace(old, new, 1), encoding="utf-8")
            try:
                verify(fixture)
            except AssertionError:
                continue
            raise AssertionError(f"mutation survived: {rel}: {old!r}")
    return len(mutations)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mutation-verify", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[3]
    verify(root)
    if args.mutation_verify:
        caught = mutation_verify(root)
        print(f"PASS: R377 alloy-logs opt-in OTLP ingress policy + dev opt-in + docs ({caught} mutations rejected)")
    else:
        print("PASS: R377 alloy-logs opt-in OTLP ingress policy + dev opt-in + docs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
