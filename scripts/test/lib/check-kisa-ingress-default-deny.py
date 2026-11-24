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
2. Zero allow-all peers anywhere in the three policy files AND across all repo
   NetworkPolicies/CiliumNetworkPolicies targeting iam, storage, or monitoring
   (including heredocs in scripts/cluster/*.sh):
   - Every NetworkPolicy ingress rule in the three files has a non-empty `from`
     with no empty `namespaceSelector: {}`, cross-ns peers carry a restricted
     `podSelector`, no broad ipBlock (< /8 IPv4 or < /16 IPv6), and explicit
     `ports` without `endPort`.
   - Every CiliumNetworkPolicy rule is checked PER RULE (not union): narrow
     `fromEntities` (never `all`/`world`/`cluster`) AND explicit non-empty `toPorts`
     on every rule (an extra rule without `toPorts` is rejected).
   - No other policy in `gitops/resources/*.yaml` or `scripts/cluster/*.sh` for
     `iam`/`storage`/`monitoring` opens any port from all sources (preventing
     additive bypasses like legacy `keycloak-allow-hbone`).
   - Both `scripts/cluster/11-keycloak.sh` and
     `gitops/charts/narwhal-platform/templates/keycloak-cr.yaml` set
     `spec.networkPolicy.enabled: false` so `keycloak-operator` does not recreate
     the allow-all `keycloak-network-policy` (8080/9000).
3. Required caller allow rules exist (mirrors `.review/caller-matrix.md`):
   - Every required workload policy and verified caller/port mapping in `iam`,
     `storage`, and `monitoring` must be present (deleting e.g.
     `grafana-ingress-policy` or APISIX -> Keycloak is rejected).
4. HBONE TCP 15008 handling:
   - Ambient namespaces (`storage`, `monitoring`) include TCP 15008 on every
     port-limited NetworkPolicy ingress rule and never allow peer-less HBONE.
   - `iam` is verified non-ambient (not in `AMBIENT_NAMESPACES` of 09-istio-ambient.sh).
   - `monitoring-ingress-policy.yaml` does not widen existing `alloy-logs` or `tempo`
     pod policies (`monitoring-default-deny-ingress` has `ingress: []` and no allow
     policy targets `alloy-logs` or `tempo`).
5. Host-network webhook callers handled via pod-scoped CiliumNetworkPolicy:
   - `storage/openbao-agent-injector-webhook-ingress` -> TCP 8080 per rule from
     `kube-apiserver`, `host`, `remote-node` (`enableDefaultDeny: {ingress: false, egress: false}`).
   - `monitoring/prometheus-operator-webhook-ingress` -> TCP 10250 per rule from
     `kube-apiserver`, `host`, `remote-node` (`enableDefaultDeny: {ingress: false, egress: false}`).
6. `devtools` is untouched (no `devtools-ingress-policy.yaml` resource or Application,
   and no namespace-wide default-deny Ingress NetworkPolicy in `devtools`).
7. `check-permissive-ingress-isolation.py` removes `monitoring-permissive` from
   KNOWN_ISSUES while keeping `platform-system-permissive`.
8. `scripts/test/regression-check-kakao.sh` wires R398 with `--mutation-verify`.
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
TARGET_NS_SET = set(TARGET_NAMESPACES)
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
    "keycloak_sh": ROOT / "scripts/cluster/11-keycloak.sh",
    "keycloak_cr": ROOT / "gitops/charts/narwhal-platform/templates/keycloak-cr.yaml",
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

# Common peer selector fixtures mirroring `.review/caller-matrix.md`.
_PEER_APISIX = {
    "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "platform-system"}},
    "podSelector": {"matchLabels": {"app.kubernetes.io/name": "apisix"}},
}
_PEER_PORTAL = {
    "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "devtools"}},
    "podSelector": {"matchLabels": {"app": "narwhal-portal"}},
}
_PEER_FALCOSIDEKICK = {
    "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "security-system"}},
    "podSelector": {"matchLabels": {"app.kubernetes.io/name": "falcosidekick", "app.kubernetes.io/instance": "falco"}},
}
_PEER_PROMETHEUS = {
    "podSelector": {
        "matchLabels": {
            "app.kubernetes.io/name": "prometheus",
            "prometheus": "prometheus-stack-kube-prom-prometheus",
        }
    }
}
_PEER_GRAFANA = {
    "podSelector": {
        "matchLabels": {
            "app.kubernetes.io/name": "grafana",
            "app.kubernetes.io/instance": "prometheus-stack",
        }
    }
}
_PEER_ALLOY_LOGS = {
    "podSelector": {
        "matchLabels": {
            "app.kubernetes.io/name": "alloy-logs",
            "app.kubernetes.io/instance": "k8s-monitoring-alloy-logs",
        }
    }
}
_PEER_LOKI_SELF = {
    "podSelector": {"matchLabels": {"app.kubernetes.io/name": "loki", "app.kubernetes.io/instance": "loki"}}
}
_PEER_WAYPOINT = {
    "podSelector": {"matchLabels": {"gateway.networking.k8s.io/gateway-name": "waypoint"}}
}

# Required workload caller matrix — mirrors `.review/caller-matrix.md`.
# Keeping this in lockstep with `.review/caller-matrix.md` ensures deleting any
# workload policy or critical caller rule (e.g. grafana-ingress-policy or
# APISIX -> Keycloak) fails R398 immediately.
# Entry format: (policy_name, target_pod_match_labels, peer_dict, required_ports)
REQUIRED_CALLERS: dict[str, list[tuple[str, dict[str, str], dict, set[tuple[str, str]]]]] = {
    "iam": [
        (
            "keycloak-ingress-policy",
            {
                "app": "keycloak",
                "app.kubernetes.io/instance": "keycloak",
                "app.kubernetes.io/managed-by": "keycloak-operator",
            },
            _PEER_APISIX,
            {("8080", "TCP")},
        ),
        (
            "keycloak-ingress-policy",
            {
                "app": "keycloak",
                "app.kubernetes.io/instance": "keycloak",
                "app.kubernetes.io/managed-by": "keycloak-operator",
            },
            _PEER_PORTAL,
            {("8080", "TCP")},
        ),
        (
            "keycloak-ingress-policy",
            {
                "app": "keycloak",
                "app.kubernetes.io/instance": "keycloak",
                "app.kubernetes.io/managed-by": "keycloak-operator",
            },
            {
                "podSelector": {
                    "matchLabels": {
                        "app": "keycloak",
                        "app.kubernetes.io/instance": "keycloak",
                        "app.kubernetes.io/managed-by": "keycloak-operator",
                    }
                }
            },
            {("7800", "TCP"), ("57800", "TCP")},
        ),
    ],
    "storage": [
        (
            "openbao-ingress-policy",
            {"app.kubernetes.io/name": "openbao", "app.kubernetes.io/instance": "openbao", "component": "server"},
            _PEER_APISIX,
            {("8200", "TCP")},
        ),
        (
            "openbao-ingress-policy",
            {"app.kubernetes.io/name": "openbao", "app.kubernetes.io/instance": "openbao", "component": "server"},
            _PEER_PORTAL,
            {("8200", "TCP")},
        ),
        (
            "openbao-ingress-policy",
            {"app.kubernetes.io/name": "openbao", "app.kubernetes.io/instance": "openbao", "component": "server"},
            {"podSelector": {"matchLabels": {"app.kubernetes.io/name": "openbao-auto-unseal"}}},
            {("8200", "TCP")},
        ),
        (
            "openbao-ingress-policy",
            {"app.kubernetes.io/name": "openbao", "app.kubernetes.io/instance": "openbao", "component": "server"},
            {"podSelector": {"matchLabels": {"app.kubernetes.io/name": "openbao-snapshot"}}},
            {("8200", "TCP")},
        ),
        (
            "openbao-ingress-policy",
            {"app.kubernetes.io/name": "openbao", "app.kubernetes.io/instance": "openbao", "component": "server"},
            {
                "podSelector": {
                    "matchLabels": {
                        "app.kubernetes.io/name": "openbao-agent-injector",
                        "app.kubernetes.io/instance": "openbao",
                        "component": "webhook",
                    }
                }
            },
            {("8200", "TCP")},
        ),
        (
            "openbao-ingress-policy",
            {"app.kubernetes.io/name": "openbao", "app.kubernetes.io/instance": "openbao", "component": "server"},
            {
                "podSelector": {
                    "matchLabels": {
                        "app.kubernetes.io/name": "openbao",
                        "app.kubernetes.io/instance": "openbao",
                        "component": "server",
                    }
                }
            },
            {("8200", "TCP"), ("8201", "TCP")},
        ),
        (
            "seaweedfs-internal-ingress-policy",
            {"app.kubernetes.io/name": "seaweedfs", "app.kubernetes.io/instance": "seaweedfs"},
            {"podSelector": {"matchLabels": {"app.kubernetes.io/name": "seaweedfs", "app.kubernetes.io/instance": "seaweedfs"}}},
            {
                ("8080", "TCP"),
                ("18080", "TCP"),
                ("8888", "TCP"),
                ("18888", "TCP"),
                ("9333", "TCP"),
                ("19333", "TCP"),
                ("8333", "TCP"),
                ("9327", "TCP"),
            },
        ),
        (
            "seaweedfs-s3-ingress-policy",
            {
                "app.kubernetes.io/name": "seaweedfs",
                "app.kubernetes.io/instance": "seaweedfs",
                "app.kubernetes.io/component": "s3",
            },
            {
                "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "monitoring"}},
                "podSelector": {"matchLabels": {"app.kubernetes.io/name": "loki", "app.kubernetes.io/instance": "loki"}},
            },
            {("8333", "TCP")},
        ),
        (
            "seaweedfs-s3-ingress-policy",
            {
                "app.kubernetes.io/name": "seaweedfs",
                "app.kubernetes.io/instance": "seaweedfs",
                "app.kubernetes.io/component": "s3",
            },
            {
                "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "monitoring"}},
                "podSelector": {"matchLabels": {"app.kubernetes.io/name": "tempo", "app.kubernetes.io/instance": "tempo"}},
            },
            {("8333", "TCP")},
        ),
        (
            "seaweedfs-s3-ingress-policy",
            {
                "app.kubernetes.io/name": "seaweedfs",
                "app.kubernetes.io/instance": "seaweedfs",
                "app.kubernetes.io/component": "s3",
            },
            {
                "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "database"}},
                "podSelector": {"matchLabels": {"cnpg.io/cluster": "narwhal-db"}},
            },
            {("8333", "TCP")},
        ),
        (
            "seaweedfs-s3-ingress-policy",
            {
                "app.kubernetes.io/name": "seaweedfs",
                "app.kubernetes.io/instance": "seaweedfs",
                "app.kubernetes.io/component": "s3",
            },
            {"podSelector": {"matchLabels": {"app.kubernetes.io/name": "velero", "app.kubernetes.io/instance": "velero"}}},
            {("8333", "TCP")},
        ),
        (
            "seaweedfs-s3-ingress-policy",
            {
                "app.kubernetes.io/name": "seaweedfs",
                "app.kubernetes.io/instance": "seaweedfs",
                "app.kubernetes.io/component": "s3",
            },
            {"podSelector": {"matchLabels": {"app.kubernetes.io/name": "velero-data-mover"}}},
            {("8333", "TCP")},
        ),
        (
            "seaweedfs-s3-ingress-policy",
            {
                "app.kubernetes.io/name": "seaweedfs",
                "app.kubernetes.io/instance": "seaweedfs",
                "app.kubernetes.io/component": "s3",
            },
            {"podSelector": {"matchExpressions": [{"key": "velero.io/repo-name", "operator": "Exists"}]}},
            {("8333", "TCP")},
        ),
        (
            "velero-ui-ingress-policy",
            {"app.kubernetes.io/name": "velero-ui", "app.kubernetes.io/instance": "velero-ui"},
            _PEER_APISIX,
            {("3000", "TCP")},
        ),
    ],
    "monitoring": [
        (
            "prometheus-ingress-policy",
            {"app.kubernetes.io/name": "prometheus", "prometheus": "prometheus-stack-kube-prom-prometheus"},
            _PEER_APISIX,
            {("9090", "TCP")},
        ),
        (
            "prometheus-ingress-policy",
            {"app.kubernetes.io/name": "prometheus", "prometheus": "prometheus-stack-kube-prom-prometheus"},
            _PEER_PORTAL,
            {("9090", "TCP")},
        ),
        (
            "prometheus-ingress-policy",
            {"app.kubernetes.io/name": "prometheus", "prometheus": "prometheus-stack-kube-prom-prometheus"},
            _PEER_GRAFANA,
            {("9090", "TCP")},
        ),
        (
            "prometheus-ingress-policy",
            {"app.kubernetes.io/name": "prometheus", "prometheus": "prometheus-stack-kube-prom-prometheus"},
            _PEER_PROMETHEUS,
            {("9090", "TCP"), ("8080", "TCP")},
        ),
        (
            "alertmanager-ingress-policy",
            {"app.kubernetes.io/name": "alertmanager", "alertmanager": "prometheus-stack-kube-prom-alertmanager"},
            _PEER_APISIX,
            {("9093", "TCP")},
        ),
        (
            "alertmanager-ingress-policy",
            {"app.kubernetes.io/name": "alertmanager", "alertmanager": "prometheus-stack-kube-prom-alertmanager"},
            _PEER_PORTAL,
            {("9093", "TCP")},
        ),
        (
            "alertmanager-ingress-policy",
            {"app.kubernetes.io/name": "alertmanager", "alertmanager": "prometheus-stack-kube-prom-alertmanager"},
            _PEER_FALCOSIDEKICK,
            {("9093", "TCP")},
        ),
        (
            "alertmanager-ingress-policy",
            {"app.kubernetes.io/name": "alertmanager", "alertmanager": "prometheus-stack-kube-prom-alertmanager"},
            _PEER_PROMETHEUS,
            {("9093", "TCP"), ("8080", "TCP")},
        ),
        (
            "alertmanager-ingress-policy",
            {"app.kubernetes.io/name": "alertmanager", "alertmanager": "prometheus-stack-kube-prom-alertmanager"},
            _PEER_GRAFANA,
            {("9093", "TCP")},
        ),
        (
            "alertmanager-ingress-policy",
            {"app.kubernetes.io/name": "alertmanager", "alertmanager": "prometheus-stack-kube-prom-alertmanager"},
            {
                "podSelector": {
                    "matchLabels": {
                        "app.kubernetes.io/name": "alertmanager",
                        "alertmanager": "prometheus-stack-kube-prom-alertmanager",
                    }
                }
            },
            {("9094", "TCP"), ("9094", "UDP")},
        ),
        (
            "grafana-ingress-policy",
            {"app.kubernetes.io/name": "grafana", "app.kubernetes.io/instance": "prometheus-stack"},
            _PEER_APISIX,
            {("3000", "TCP")},
        ),
        (
            "grafana-ingress-policy",
            {"app.kubernetes.io/name": "grafana", "app.kubernetes.io/instance": "prometheus-stack"},
            _PEER_PROMETHEUS,
            {("3000", "TCP")},
        ),
        (
            "loki-ingress-policy",
            {
                "app.kubernetes.io/name": "loki",
                "app.kubernetes.io/instance": "loki",
                "app.kubernetes.io/component": "single-binary",
            },
            _PEER_ALLOY_LOGS,
            {("3100", "TCP")},
        ),
        (
            "loki-ingress-policy",
            {
                "app.kubernetes.io/name": "loki",
                "app.kubernetes.io/instance": "loki",
                "app.kubernetes.io/component": "single-binary",
            },
            _PEER_WAYPOINT,
            {("3100", "TCP"), ("9095", "TCP")},
        ),
        (
            "loki-ingress-policy",
            {
                "app.kubernetes.io/name": "loki",
                "app.kubernetes.io/instance": "loki",
                "app.kubernetes.io/component": "single-binary",
            },
            _PEER_GRAFANA,
            {("3100", "TCP")},
        ),
        (
            "loki-ingress-policy",
            {
                "app.kubernetes.io/name": "loki",
                "app.kubernetes.io/instance": "loki",
                "app.kubernetes.io/component": "single-binary",
            },
            _PEER_PROMETHEUS,
            {("3100", "TCP")},
        ),
        (
            "loki-ingress-policy",
            {
                "app.kubernetes.io/name": "loki",
                "app.kubernetes.io/instance": "loki",
                "app.kubernetes.io/component": "single-binary",
            },
            _PEER_PORTAL,
            {("3100", "TCP")},
        ),
        (
            "loki-ingress-policy",
            {
                "app.kubernetes.io/name": "loki",
                "app.kubernetes.io/instance": "loki",
                "app.kubernetes.io/component": "single-binary",
            },
            _PEER_FALCOSIDEKICK,
            {("3100", "TCP")},
        ),
        (
            "loki-ingress-policy",
            {
                "app.kubernetes.io/name": "loki",
                "app.kubernetes.io/instance": "loki",
                "app.kubernetes.io/component": "single-binary",
            },
            _PEER_LOKI_SELF,
            {("3100", "TCP"), ("9095", "TCP"), ("7946", "TCP")},
        ),
        (
            "kube-state-metrics-ingress-policy",
            {"app.kubernetes.io/name": "kube-state-metrics", "app.kubernetes.io/instance": "prometheus-stack"},
            _PEER_PROMETHEUS,
            {("8080", "TCP")},
        ),
        (
            "prometheus-operator-ingress-policy",
            {"app": "kube-prometheus-stack-operator", "release": "prometheus-stack"},
            _PEER_PROMETHEUS,
            {("10250", "TCP")},
        ),
        (
            "monitoring-waypoint-ingress-policy",
            {"gateway.networking.k8s.io/gateway-name": "waypoint"},
            _PEER_PROMETHEUS,
            {("15020", "TCP")},
        ),
        (
            "monitoring-waypoint-ingress-policy",
            {"gateway.networking.k8s.io/gateway-name": "waypoint"},
            _PEER_ALLOY_LOGS,
            {("15008", "TCP")},
        ),
        (
            "monitoring-waypoint-ingress-policy",
            {"gateway.networking.k8s.io/gateway-name": "waypoint"},
            _PEER_LOKI_SELF,
            {("15008", "TCP")},
        ),
    ],
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


def _extract_cnp_rule_ports(
    ns: str, name: str, idx: int, rule: dict, errors: list[str]
) -> set[tuple[str, str]] | None:
    to_ports = rule.get("toPorts")
    if not isinstance(to_ports, list) or not to_ports:
        errors.append(f"{ns}/{name} CNP rule #{idx} must specify non-empty toPorts (no unrestricted ports)")
        return None
    ports_set: set[tuple[str, str]] = set()
    for tp_idx, tp in enumerate(to_ports):
        if not isinstance(tp, dict):
            errors.append(f"{ns}/{name} CNP rule #{idx} toPorts[{tp_idx}] is not a mapping")
            return None
        plist = tp.get("ports")
        if not isinstance(plist, list) or not plist:
            errors.append(f"{ns}/{name} CNP rule #{idx} toPorts[{tp_idx}] has missing/empty ports")
            return None
        for p in plist:
            if not isinstance(p, dict) or "port" not in p:
                errors.append(f"{ns}/{name} CNP rule #{idx} toPorts[{tp_idx}] invalid port {p!r}")
                return None
            if "endPort" in p:
                errors.append(f"{ns}/{name} CNP rule #{idx} toPorts[{tp_idx}] must not use endPort")
                return None
            ports_set.add((str(p["port"]), str(p.get("protocol", "TCP"))))
    return ports_set


def _rule_ports_set(rule: dict) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for p in rule.get("ports") or []:
        if isinstance(p, dict) and "port" in p:
            out.add((str(p["port"]), str(p.get("protocol", "TCP"))))
    return out


def _check_required_callers(ns: str, np_by_name: dict[str, dict], errors: list[str]) -> None:
    for pol_name, target_labels, req_peer, req_ports in REQUIRED_CALLERS[ns]:
        doc = np_by_name.get(pol_name)
        if doc is None:
            errors.append(f"{ns}-ingress-policy.yaml missing required caller policy {pol_name!r}")
            continue
        spec = doc.get("spec") or {}
        if (spec.get("podSelector") or {}).get("matchLabels") != target_labels:
            errors.append(f"{ns}/{pol_name} podSelector mismatch: expected {target_labels!r}")
            continue
        matched = False
        for rule in spec.get("ingress") or []:
            if not isinstance(rule, dict):
                continue
            peers = rule.get("from") or []
            if req_peer in peers and req_ports.issubset(_rule_ports_set(rule)):
                matched = True
                break
        if not matched:
            errors.append(
                f"{ns}/{pol_name} missing required caller rule from={req_peer!r} ports={sorted(req_ports)!r}"
            )


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
    np_by_name: dict[str, dict] = {}

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
            if isinstance(name, str):
                np_by_name[name] = doc
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

            is_expected_webhook = ns in EXPECTED_WEBHOOK_CNPS and name == EXPECTED_WEBHOOK_CNPS[ns][0]
            exp_port = EXPECTED_WEBHOOK_CNPS[ns][2] if is_expected_webhook else None

            for idx, r in enumerate(c_rules):
                if not isinstance(r, dict):
                    errors.append(f"{ns}/{name} CNP rule #{idx} is not a mapping")
                    continue
                entities = r.get("fromEntities")
                if not isinstance(entities, list) or not entities:
                    errors.append(f"{ns}/{name} CNP rule #{idx} must declare fromEntities")
                else:
                    ent_set = set(map(str, entities))
                    if ent_set & BROAD_CILIUM_ENTITIES:
                        errors.append(f"{ns}/{name} CNP rule #{idx} uses broad entity {sorted(ent_set & BROAD_CILIUM_ENTITIES)}")
                    if not ent_set.issubset(ALLOWED_WEBHOOK_ENTITIES):
                        errors.append(f"{ns}/{name} CNP rule #{idx} uses unexpected entity {sorted(ent_set)}")
                rule_ports = _extract_cnp_rule_ports(ns, str(name), idx, r, errors)
                if is_expected_webhook and rule_ports is not None and rule_ports != {(exp_port, "TCP")}:
                    errors.append(
                        f"{ns}/{name} CNP rule #{idx} toPorts must be exact {{({exp_port!r}, 'TCP')}}, got {rule_ports!r}"
                    )

            if is_expected_webhook:
                exp_name, exp_sel, _ = EXPECTED_WEBHOOK_CNPS[ns]
                if (ep_sel or {}).get("matchLabels") != exp_sel:
                    errors.append(f"{ns}/{exp_name} CNP endpointSelector mismatch: got {ep_sel!r}")
                ent_all = {str(e) for r in c_rules if isinstance(r, dict) for e in (r.get("fromEntities") or [])}
                if not {"kube-apiserver", "host", "remote-node"}.issubset(ent_all):
                    errors.append(f"{ns}/{exp_name} CNP must allow kube-apiserver, host, and remote-node")
                found_webhook_cnp = True
        else:
            errors.append(f"{ns}-ingress-policy.yaml unexpected kind {kind!r}")

    if not found_dd:
        errors.append(f"{ns}-ingress-policy.yaml missing pure default-deny NetworkPolicy {expected_dd_name}")
    if ns in EXPECTED_WEBHOOK_CNPS and not found_webhook_cnp:
        errors.append(f"{ns}-ingress-policy.yaml missing host-network webhook CNP {EXPECTED_WEBHOOK_CNPS[ns][0]}")
    _check_required_callers(ns, np_by_name, errors)


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


def _check_keycloak_operator_netpol_disabled(keycloak_sh: str, keycloak_cr: str, errors: list[str]) -> None:
    m = re.search(r"kubectl apply -f - <<EOF\n(.*?)\nEOF", keycloak_sh, re.S)
    if not m:
        errors.append("11-keycloak.sh missing Keycloak CR heredoc")
    else:
        boot_cr = yaml.safe_load(m.group(1).replace("${DOMAIN}", "local.narwhal.internal"))
        boot_np = ((boot_cr or {}).get("spec") or {}).get("networkPolicy") or {}
        if boot_np.get("enabled") is not False:
            errors.append(
                "11-keycloak.sh Keycloak CR must set spec.networkPolicy.enabled: false "
                "(operator default opens 8080/9000 to all sources)"
            )

    cleaned = re.sub(r"\{\{.*?\}\}", "placeholder", keycloak_cr)
    gitops_cr = yaml.safe_load(cleaned)
    gitops_np = ((gitops_cr or {}).get("spec") or {}).get("networkPolicy") or {}
    if gitops_np.get("enabled") is not False:
        errors.append(
            "gitops/charts/narwhal-platform/templates/keycloak-cr.yaml must set spec.networkPolicy.enabled: false "
            "(operator default opens 8080/9000 to all sources)"
        )


def _extract_policy_docs(rel: str, text: str) -> list[dict]:
    docs: list[dict] = []
    if rel.endswith(".sh"):
        for _, body in re.findall(r"<<-?['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?\s*\n(.*?)\n\1\b", text, re.S):
            if "kind: NetworkPolicy" in body or "kind: CiliumNetworkPolicy" in body:
                cleaned = re.sub(r"\$\{[^}]+\}", "placeholder", body)
                docs.extend(d for d in yaml.safe_load_all(cleaned) if isinstance(d, dict))
    elif rel.endswith((".yaml", ".yml")):
        if "kind: NetworkPolicy" in text or "kind: CiliumNetworkPolicy" in text:
            if "{{" not in text:
                docs.extend(d for d in yaml.safe_load_all(text) if isinstance(d, dict))
    return docs


def _check_no_repo_allow_all_in_targets(policy_sources: list[tuple[str, str]], errors: list[str]) -> None:
    """Reject ANY NetworkPolicy/CiliumNetworkPolicy in iam/storage/monitoring anywhere
    in gitops/resources/*.yaml or scripts/cluster/*.sh that allows a port from all sources
    (prevents additive bypasses like legacy scripts/cluster/11-keycloak.sh keycloak-allow-hbone).
    """
    for rel, text in policy_sources:
        for doc in _extract_policy_docs(rel, text):
            kind = doc.get("kind")
            meta = doc.get("metadata") or {}
            spec = doc.get("spec") or {}
            ns = meta.get("namespace")
            name = meta.get("name")
            if ns not in TARGET_NS_SET:
                continue
            if kind == "NetworkPolicy":
                policy_types = spec.get("policyTypes") or []
                if "Ingress" not in policy_types and "ingress" not in spec:
                    continue
                rules = spec.get("ingress")
                if not rules:
                    continue
                for idx, rule in enumerate(rules):
                    if not isinstance(rule, dict):
                        errors.append(f"{rel} ({ns}/{name}) ingress rule #{idx} is not a mapping")
                        continue
                    peers = rule.get("from")
                    if not isinstance(peers, list) or not peers:
                        errors.append(
                            f"{rel} ({ns}/{name}) ingress rule #{idx} allows all sources (missing/empty 'from'), "
                            f"defeating {ns} default-deny"
                        )
                        continue
                    for pidx, peer in enumerate(peers):
                        if not isinstance(peer, dict) or not peer:
                            errors.append(f"{rel} ({ns}/{name}) ingress rule #{idx} peer #{pidx} is empty (allow-all)")
                            continue
                        ns_sel = peer.get("namespaceSelector")
                        p_sel = peer.get("podSelector")
                        ipb = peer.get("ipBlock")
                        if ns_sel is None and p_sel is None and ipb is None:
                            errors.append(f"{rel} ({ns}/{name}) ingress rule #{idx} peer #{pidx} has no selector (allow-all)")
                        elif ns_sel is not None and not _restricts_selector(ns_sel) and not _restricts_selector(p_sel):
                            errors.append(
                                f"{rel} ({ns}/{name}) ingress rule #{idx} peer #{pidx} allows all namespaces/pods"
                            )
                        elif ns_sel is None and p_sel is not None and not _restricts_selector(p_sel):
                            errors.append(
                                f"{rel} ({ns}/{name}) ingress rule #{idx} peer #{pidx} allows all pods in {ns}"
                            )
                        if ipb is not None and not (isinstance(ipb, dict) and _cidr_restricted(ipb.get("cidr"))):
                            errors.append(f"{rel} ({ns}/{name}) ingress rule #{idx} peer #{pidx} has broad ipBlock")
            elif kind == "CiliumNetworkPolicy":
                for idx, rule in enumerate(spec.get("ingress") or []):
                    if not isinstance(rule, dict):
                        continue
                    entities = {str(e) for e in (rule.get("fromEntities") or [])}
                    if entities & BROAD_CILIUM_ENTITIES:
                        errors.append(
                            f"{rel} ({ns}/{name}) CNP rule #{idx} uses broad entity {sorted(entities & BROAD_CILIUM_ENTITIES)}"
                        )
                    has_source = any(
                        rule.get(k) for k in ("fromEntities", "fromEndpoints", "fromCIDR", "fromCIDRSet", "fromRequires")
                    )
                    if not has_source:
                        errors.append(f"{rel} ({ns}/{name}) CNP rule #{idx} allows all sources (missing from*)")


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


def check(
    texts: dict[str, str],
    devtools_scan: list[tuple[str, str]],
    policy_sources: list[tuple[str, str]],
) -> list[str]:
    errors: list[str] = []
    waves = json.loads(texts["waves"])
    for ns in TARGET_NAMESPACES:
        _check_resource_file(ns, texts[f"{ns}_res"], errors)
        _check_app_and_waves(ns, texts[f"{ns}_app"], waves, errors)
    _check_iam_non_ambient(texts["ambient_sh"], errors)
    _check_keycloak_operator_netpol_disabled(texts["keycloak_sh"], texts["keycloak_cr"], errors)
    # Keep policy_sources synced with mutated keycloak_sh/res texts during mutation-verify.
    effective_sources = []
    overrides = {
        "scripts/cluster/11-keycloak.sh": texts["keycloak_sh"],
        "gitops/resources/iam-ingress-policy.yaml": texts["iam_res"],
        "gitops/resources/storage-ingress-policy.yaml": texts["storage_res"],
        "gitops/resources/monitoring-ingress-policy.yaml": texts["monitoring_res"],
    }
    for rel, txt in policy_sources:
        effective_sources.append((rel, overrides.get(rel, txt)))
    _check_no_repo_allow_all_in_targets(effective_sources, errors)
    _check_devtools_untouched(devtools_scan, errors)
    _check_known_issues(texts["perm_iso"], errors)
    if "check R398 " not in texts["reg"] or "check-kisa-ingress-default-deny.py --mutation-verify" not in texts["reg"]:
        errors.append("regression-check-kakao.sh must wire R398 with --mutation-verify")
    return errors


MUTATIONS: list[tuple[str, str, str, str]] = [
    ("drop iam default-deny", "iam_res", "  name: iam-default-deny-ingress\n", "  name: iam-other\n"),
    ("widen iam keycloak peer by dropping podSelector", "iam_res", "          podSelector:\n            matchLabels:\n              app.kubernetes.io/name: apisix\n", ""),
    ("widen iam keycloak peer to allow-all namespaceSelector", "iam_res", "            matchLabels:\n              kubernetes.io/metadata.name: platform-system\n", "            matchLabels: {}\n"),
    (
        "delete APISIX -> Keycloak allow rule",
        "iam_res",
        "    # 1. APISIX gateway (platform-system) -> Keycloak HTTP (:8080).\n"
        "    - from:\n"
        "        - namespaceSelector:\n"
        "            matchLabels:\n"
        "              kubernetes.io/metadata.name: platform-system\n"
        "          podSelector:\n"
        "            matchLabels:\n"
        "              app.kubernetes.io/name: apisix\n"
        "      ports:\n"
        "        - protocol: TCP\n"
        "          port: 8080\n",
        "",
    ),
    ("drop storage default-deny", "storage_res", "  name: storage-default-deny-ingress\n", "  name: storage-other\n"),
    ("drop HBONE 15008 from storage openbao rule", "storage_res", "        - protocol: TCP\n          port: 8200\n        - protocol: TCP\n          port: 15008\n", "        - protocol: TCP\n          port: 8200\n"),
    ("drop storage host-network webhook CNP", "storage_res", "  name: openbao-agent-injector-webhook-ingress\n", "  name: wrong-cnp\n"),
    ("widen storage webhook CNP entity to world", "storage_res", "        - remote-node\n", "        - world\n"),
    (
        "add extra storage webhook CNP rule without toPorts",
        "storage_res",
        '            - port: "8080"\n              protocol: TCP\n',
        '            - port: "8080"\n              protocol: TCP\n    - fromEntities:\n        - host\n',
    ),
    (
        "delete Loki -> SeaweedFS S3 allow rule",
        "storage_res",
        "    # 1. Loki (monitoring) -> SeaweedFS S3 (:8333).\n"
        "    - from:\n"
        "        - namespaceSelector:\n"
        "            matchLabels:\n"
        "              kubernetes.io/metadata.name: monitoring\n"
        "          podSelector:\n"
        "            matchLabels:\n"
        "              app.kubernetes.io/name: loki\n"
        "              app.kubernetes.io/instance: loki\n"
        "      ports:\n"
        "        - protocol: TCP\n"
        "          port: 8333\n"
        "        - protocol: TCP\n"
        "          port: 15008\n",
        "",
    ),
    ("drop monitoring default-deny", "monitoring_res", "  name: monitoring-default-deny-ingress\n", "  name: monitoring-other\n"),
    ("allow non-empty ingress on monitoring default-deny", "monitoring_res", "  ingress: []\n", "  ingress:\n    - from:\n        - podSelector: {}\n"),
    ("drop HBONE 15008 from monitoring grafana rule", "monitoring_res", "        - protocol: TCP\n          port: 3000\n        - protocol: TCP\n          port: 15008\n", "        - protocol: TCP\n          port: 3000\n"),
    ("drop monitoring host-network webhook CNP", "monitoring_res", "  name: prometheus-operator-webhook-ingress\n", "  name: wrong-cnp\n"),
    ("widen monitoring webhook CNP port", "monitoring_res", '            - port: "10250"\n', '            - port: "443"\n'),
    (
        "add extra monitoring webhook CNP rule without toPorts",
        "monitoring_res",
        '            - port: "10250"\n              protocol: TCP\n',
        '            - port: "10250"\n              protocol: TCP\n    - fromEntities:\n        - host\n',
    ),
    (
        "delete entire grafana-ingress-policy",
        "monitoring_res",
        "---\n"
        "apiVersion: networking.k8s.io/v1\n"
        "kind: NetworkPolicy\n"
        "metadata:\n"
        "  name: grafana-ingress-policy\n"
        "  namespace: monitoring\n"
        "  labels:\n"
        "    app.kubernetes.io/managed-by: narwhal-gitops\n"
        "spec:\n"
        "  podSelector:\n"
        "    matchLabels:\n"
        "      app.kubernetes.io/name: grafana\n"
        "      app.kubernetes.io/instance: prometheus-stack\n"
        "  policyTypes:\n"
        "    - Ingress\n"
        "  ingress:\n"
        "    # 1. APISIX gateway (platform-system) -> Grafana HTTP (Service :80 -> pod :3000).\n"
        "    - from:\n"
        "        - namespaceSelector:\n"
        "            matchLabels:\n"
        "              kubernetes.io/metadata.name: platform-system\n"
        "          podSelector:\n"
        "            matchLabels:\n"
        "              app.kubernetes.io/name: apisix\n"
        "      ports:\n"
        "        - protocol: TCP\n"
        "          port: 3000\n"
        "        - protocol: TCP\n"
        "          port: 15008\n"
        "    # 2. Prometheus (monitoring) -> Grafana /metrics scrape (:3000).\n"
        "    - from:\n"
        "        - podSelector:\n"
        "            matchLabels:\n"
        "              app.kubernetes.io/name: prometheus\n"
        "              prometheus: prometheus-stack-kube-prom-prometheus\n"
        "      ports:\n"
        "        - protocol: TCP\n"
        "          port: 3000\n"
        "        - protocol: TCP\n"
        "          port: 15008\n",
        "",
    ),
    ("target alloy-logs from monitoring-ingress-policy", "monitoring_res", "      app.kubernetes.io/name: grafana\n      app.kubernetes.io/instance: prometheus-stack\n", "      app.kubernetes.io/name: alloy-logs\n      app.kubernetes.io/instance: k8s-monitoring-alloy-logs\n"),
    ("re-enable operator NetworkPolicy in keycloak-cr.yaml", "keycloak_cr", "  networkPolicy:\n    enabled: false\n", "  networkPolicy:\n    enabled: true\n"),
    ("re-enable operator NetworkPolicy in 11-keycloak.sh", "keycloak_sh", "  networkPolicy:\n    enabled: false\n", "  networkPolicy:\n    enabled: true\n"),
    (
        "re-introduce allow-all keycloak-allow-hbone in 11-keycloak.sh",
        "keycloak_sh",
        "kubectl delete networkpolicy keycloak-allow-hbone -n iam --ignore-not-found\n",
        "kubectl apply -f - <<'EOF'\napiVersion: networking.k8s.io/v1\nkind: NetworkPolicy\nmetadata:\n  name: keycloak-allow-hbone\n  namespace: iam\nspec:\n  podSelector:\n    matchLabels:\n      app: keycloak\n  ingress:\n    - ports:\n        - protocol: TCP\n          port: 8080\n  policyTypes:\n    - Ingress\nEOF\n",
    ),
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


def _collect_policy_sources() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for p in sorted((ROOT / "gitops/resources").glob("*.yaml")):
        out.append((p.relative_to(ROOT).as_posix(), p.read_text(encoding="utf-8")))
    for p in sorted((ROOT / "gitops/resources").glob("*.yml")):
        out.append((p.relative_to(ROOT).as_posix(), p.read_text(encoding="utf-8")))
    for p in sorted((ROOT / "scripts/cluster").glob("*.sh")):
        if p.is_file():
            out.append((p.relative_to(ROOT).as_posix(), p.read_text(encoding="utf-8")))
    return out


def main() -> int:
    texts = {k: p.read_text(encoding="utf-8") for k, p in FILES.items()}
    devtools_scan = _collect_devtools_scan()
    policy_sources = _collect_policy_sources()
    errors = check(texts, devtools_scan, policy_sources)
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
            if not check(mutated, devtools_scan, policy_sources):
                print(f"FAIL: mutation not rejected: {name}", file=sys.stderr)
                return 1

        # Additional mutation 1: ensure a devtools default-deny is rejected.
        bad_devtools = devtools_scan + [
            (
                "gitops/resources/devtools-ingress-policy.yaml",
                "apiVersion: networking.k8s.io/v1\nkind: NetworkPolicy\nmetadata:\n  name: devtools-default-deny-ingress\n  namespace: devtools\nspec:\n  podSelector: {}\n  policyTypes: [Ingress]\n  ingress: []\n",
            )
        ]
        if not check(texts, bad_devtools, policy_sources):
            print("FAIL: mutation adding devtools default-deny was not rejected", file=sys.stderr)
            return 1

        # Additional mutation 2: ensure an external allow-all NetworkPolicy in storage is rejected.
        bad_sources = policy_sources + [
            (
                "gitops/resources/stray-allow-all.yaml",
                "apiVersion: networking.k8s.io/v1\nkind: NetworkPolicy\nmetadata:\n  name: stray-open\n  namespace: storage\nspec:\n  podSelector:\n    matchLabels:\n      app.kubernetes.io/name: openbao\n  policyTypes: [Ingress]\n  ingress:\n    - ports:\n        - protocol: TCP\n          port: 8200\n",
            )
        ]
        if not check(texts, devtools_scan, bad_sources):
            print("FAIL: mutation adding external allow-all NetworkPolicy in storage was not rejected", file=sys.stderr)
            return 1

        total = len(MUTATIONS) + 2
        print(f"PASS: R398 KISA-NET-01 iam/storage/monitoring ingress default-deny ({total} mutations rejected)")
        return 0

    print("PASS: R398 KISA-NET-01 iam/storage/monitoring ingress default-deny")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
