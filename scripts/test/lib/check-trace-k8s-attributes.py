#!/usr/bin/env python3
"""R373: OTLP trace ingestion & trustworthy k8s.namespace.name enrichment via Alloy (narwhal#691).

Verifies that:
  - gitops/charts/narwhal-apps/templates/k8s-monitoring.yaml disables
    applicationObservability (which lacks a pre-k8sattributes strip hook) and
    configures collectors.alloy-logs via extraConfig + includeDestinations: [tempo]
    + alloy.extraPorts (4317/4318) + service.internalTrafficPolicy: Local
    + service.labels.istio.io/use-waypoint: none.
  - scripts/cluster/08-2-monitoring.sh K8SMONVALUES, Tempo flags, and Grafana
    OTLP address match GitOps values.
  - gitops/charts/narwhal-apps/templates/prometheus-stack.yaml routes Grafana
    tracing through k8s-monitoring-alloy-logs.monitoring.svc.cluster.local:4317.
  - gitops/charts/narwhal-apps/templates/tempo.yaml disables OTLP/HTTP (4318) and
    sets service.labels.istio.io/use-waypoint: none so alloy-logs reaches Tempo
    directly over ztunnel HBONE 15008 with its own pod IP.
  - gitops/resources/network-policies.yaml defines tempo-ingress-policy restricting
    Tempo ingest (4317 + HBONE 15008) strictly to alloy-logs pods and query/metrics
    (3200 + HBONE 15008) to Grafana, Prometheus, narwhal-portal, and Tempo itself.
  - Offline `helm template` of k8s-monitoring-4.2.0.tgz and tempo-2.2.3.tgz
    verifies rendered Alloy pipeline order/security and Tempo Service/ConfigMap.
"""
import argparse
import glob
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

K8SMON_YAML = "gitops/charts/narwhal-apps/templates/k8s-monitoring.yaml"
TEMPO_YAML = "gitops/charts/narwhal-apps/templates/tempo.yaml"
PROM_YAML = "gitops/charts/narwhal-apps/templates/prometheus-stack.yaml"
NETPOL_YAML = "gitops/resources/network-policies.yaml"
MON_SH = "scripts/cluster/08-2-monitoring.sh"
ALLOY_OTLP_ADDR = "k8s-monitoring-alloy-logs.monitoring.svc.cluster.local:4317"

EXPECTED_READ_PEERS = {
    ("monitoring", frozenset({("app.kubernetes.io/name", "grafana"), ("app.kubernetes.io/instance", "prometheus-stack")})),
    ("monitoring", frozenset({("app.kubernetes.io/name", "prometheus"), ("prometheus", "prometheus-stack-kube-prom-prometheus")})),
    ("devtools", frozenset({("app", "narwhal-portal")})),
    ("monitoring", frozenset({("app.kubernetes.io/name", "tempo"), ("app.kubernetes.io/instance", "tempo")})),
}
EXPECTED_INGEST_PEER = (
    "monitoring",
    frozenset({("app.kubernetes.io/name", "alloy-logs"), ("app.kubernetes.io/instance", "k8s-monitoring-alloy-logs")}),
)


def _extract_k8smon_heredoc(script_text: str) -> dict:
    m = re.search(
        r"cat <<'K8SMONVALUES' > /tmp/k8s-monitoring-values\.yaml\n(.*?)\nK8SMONVALUES",
        script_text,
        re.S,
    )
    assert m, "08-2-monitoring.sh: missing K8SMONVALUES heredoc"
    return yaml.safe_load(m.group(1))


def _extract_alloy_block(cfg: str, comp_type: str, comp_name: str) -> str:
    header = f'{comp_type} "{comp_name}" {{'
    idx = cfg.find(header)
    assert idx != -1, f"missing Alloy component: {header}"
    depth = 0
    for pos in range(idx + len(header) - 1, len(cfg)):
        ch = cfg[pos]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return cfg[idx : pos + 1]
    raise AssertionError(f"unclosed Alloy component block: {header}")


def _assert_output_traces(block: str, expected_target: str, label: str, comp: str) -> None:
    m = re.search(r"output\s*\{\s*traces\s*=\s*\[(.*?)\]\s*\}", block, re.S)
    assert m, f"{label}: {comp} missing output.traces block"
    targets = [t.strip() for t in m.group(1).split(",") if t.strip()]
    assert targets == [expected_target], (
        f"{label}: {comp} output.traces must be [{expected_target}], got {targets!r}"
    )


def _assert_rendered_alloy_security(cfg: str, label: str) -> None:
    assert "otelcol.processor.filter" not in cfg, (
        f"{label}: unexpected otelcol.processor.filter in rendered Alloy config"
    )
    assert 'declare "application_observability"' not in cfg, (
        f"{label}: application_observability module must be disabled"
    )
    assert "connector.host_info" not in cfg and "spanmetrics" not in cfg and "spanlogs" not in cfg, (
        f"{label}: unexpected extra connectors rendered"
    )

    recv = _extract_alloy_block(cfg, "otelcol.receiver.otlp", "otlp_traces")
    assert 'endpoint = "0.0.0.0:4317"' in recv, f"{label}: missing 4317 grpc listener"
    assert 'endpoint = "0.0.0.0:4318"' in recv, f"{label}: missing 4318 http listener"
    assert "include_metadata = false" in recv, f"{label}: receiver must not trust client metadata"
    _assert_output_traces(
        recv, "otelcol.processor.memory_limiter.otlp_traces.input", label, "otelcol.receiver.otlp"
    )

    mem = _extract_alloy_block(cfg, "otelcol.processor.memory_limiter", "otlp_traces")
    assert 'check_interval = "1s"' in mem, f"{label}: memory_limiter missing check_interval"
    assert 'limit = "256MiB"' in mem, f"{label}: memory_limiter must set limit = \"256MiB\""
    assert 'spike_limit = "64MiB"' in mem, f"{label}: memory_limiter must set spike_limit = \"64MiB\""
    _assert_output_traces(
        mem,
        "otelcol.processor.transform.strip_client_k8s.input",
        label,
        "otelcol.processor.memory_limiter",
    )

    strip = _extract_alloy_block(cfg, "otelcol.processor.transform", "strip_client_k8s")
    assert 'error_mode = "propagate"' in strip, (
        f"{label}: strip_client_k8s must use error_mode = \"propagate\" (fail closed)"
    )
    assert 'context = "resource"' in strip, f"{label}: strip_client_k8s must act on resource context"
    assert (
        'delete_matching_keys(attributes, "^k8s[.]")' in strip
        or 'delete_matching_keys(attributes, "^k8s\\\\.")' in strip
    ), f"{label}: strip_client_k8s must delete client-supplied ^k8s.* resource attributes"
    assert 'delete_key(attributes, "service.namespace")' in strip, (
        f"{label}: strip_client_k8s must delete client-supplied service.namespace"
    )
    _assert_output_traces(
        strip,
        "otelcol.processor.k8sattributes.otlp_traces.input",
        label,
        "otelcol.processor.transform.strip_client_k8s",
    )

    k8s = _extract_alloy_block(cfg, "otelcol.processor.k8sattributes", "otlp_traces")
    assert "passthrough = false" in k8s, f"{label}: k8sattributes passthrough must be false"
    assert re.search(r'filter\s*\{[^}]*node\s*=\s*sys\.env\("HOSTNAME"\)', k8s, re.S), (
        f"{label}: k8sattributes must filter cache to own node (sys.env(\"HOSTNAME\"))"
    )
    for required_attr in ("k8s.namespace.name", "k8s.pod.name", "k8s.deployment.name"):
        assert f'"{required_attr}"' in k8s, f"{label}: k8sattributes missing {required_attr}"
    assert '"k8s.cronjob.name"' not in k8s, (
        f"{label}: k8sattributes must omit k8s.cronjob.name (no batch/jobs ClusterRole rule)"
    )
    assoc_blocks = re.findall(r"pod_association\s*\{.*?\n\s*\}", k8s, re.S)
    assert len(assoc_blocks) == 1, (
        f"{label}: k8sattributes must define exactly 1 pod_association block, got {len(assoc_blocks)}"
    )
    assert 'from = "connection"' in assoc_blocks[0], (
        f"{label}: pod_association must use from = \"connection\""
    )
    for forbidden in ("resource_attribute", "k8s.pod.ip", "k8s.pod.uid"):
        assert forbidden not in assoc_blocks[0], (
            f"{label}: pod_association must not use {forbidden}"
        )
    _assert_output_traces(
        k8s,
        "otelcol.processor.attributes.tempo.input",
        label,
        "otelcol.processor.k8sattributes",
    )

    assert 'otelcol.exporter.otlp "tempo"' in cfg, f"{label}: missing tempo otlp exporter"
    assert 'endpoint = "tempo.monitoring.svc.cluster.local:4317"' in cfg, (
        f"{label}: missing tempo endpoint"
    )


def _check_values(vals: dict, label: str) -> None:
    dests = vals.get("destinations") or {}
    tempo = dests.get("tempo") or {}
    assert tempo.get("type") == "otlp", f"{label}: destinations.tempo.type must be otlp"
    assert tempo.get("url") == "tempo.monitoring.svc.cluster.local:4317", (
        f"{label}: destinations.tempo.url mismatch"
    )
    assert tempo.get("protocol") == "grpc", f"{label}: destinations.tempo.protocol must be grpc"
    assert (tempo.get("tls") or {}).get("insecure") is True, (
        f"{label}: destinations.tempo.tls.insecure must be true"
    )
    assert (tempo.get("metrics") or {}).get("enabled") is False, (
        f"{label}: destinations.tempo.metrics.enabled must be false"
    )
    assert (tempo.get("logs") or {}).get("enabled") is False, (
        f"{label}: destinations.tempo.logs.enabled must be false"
    )
    assert (tempo.get("traces") or {}).get("enabled") is True, (
        f"{label}: destinations.tempo.traces.enabled must be true"
    )

    ao = vals.get("applicationObservability") or {}
    assert ao.get("enabled") is False, (
        f"{label}: applicationObservability.enabled must be false (extraConfig owns OTLP pipeline)"
    )

    alloy_logs = (vals.get("collectors") or {}).get("alloy-logs") or {}
    assert alloy_logs.get("includeDestinations") == ["tempo"], (
        f"{label}: collectors.alloy-logs.includeDestinations must be ['tempo']"
    )
    svc = alloy_logs.get("service") or {}
    assert svc.get("internalTrafficPolicy") == "Local", (
        f"{label}: collectors.alloy-logs.service.internalTrafficPolicy must be Local"
    )
    assert (svc.get("labels") or {}).get("istio.io/use-waypoint") == "none", (
        f"{label}: collectors.alloy-logs.service.labels must set istio.io/use-waypoint=none"
    )

    extra_ports = (alloy_logs.get("alloy") or {}).get("extraPorts") or []
    ports = {(p.get("port"), p.get("targetPort"), p.get("protocol")) for p in extra_ports}
    assert (4317, 4317, "TCP") in ports and (4318, 4318, "TCP") in ports, (
        f"{label}: collectors.alloy-logs.alloy.extraPorts missing 4317/4318 TCP: {ports}"
    )

    extra_cfg = alloy_logs.get("extraConfig") or ""
    assert extra_cfg, f"{label}: collectors.alloy-logs.extraConfig must be defined"
    _assert_rendered_alloy_security(
        extra_cfg + '\notelcol.exporter.otlp "tempo" {\n  endpoint = "tempo.monitoring.svc.cluster.local:4317"\n}\n',
        label,
    )


def _peer_key(peer: dict) -> tuple[str, frozenset[tuple[str, str]]]:
    assert "ipBlock" not in peer, f"tempo-ingress-policy must not use ipBlock: {peer!r}"
    ns_sel = peer.get("namespaceSelector")
    if ns_sel is not None:
        assert not peer.get("namespaceSelector", {}).get("matchExpressions"), (
            f"unexpected namespaceSelector matchExpressions: {peer!r}"
        )
        ns = (ns_sel.get("matchLabels") or {}).get("kubernetes.io/metadata.name")
        assert ns, f"namespaceSelector must pin kubernetes.io/metadata.name: {peer!r}"
    else:
        ns = "monitoring"

    pod_sel = peer.get("podSelector")
    assert isinstance(pod_sel, dict), f"every peer must define podSelector: {peer!r}"
    assert not pod_sel.get("matchExpressions"), f"unexpected matchExpressions: {peer!r}"
    ml = pod_sel.get("matchLabels") or {}
    assert ml, f"podSelector.matchLabels must not be empty: {peer!r}"
    return (ns, frozenset(ml.items()))


def _check_tempo_netpol(root: Path) -> None:
    docs = [
        d for d in yaml.safe_load_all((root / NETPOL_YAML).read_text(encoding="utf-8"))
        if isinstance(d, dict)
    ]
    matches = [
        d for d in docs
        if d.get("kind") == "NetworkPolicy"
        and (d.get("metadata") or {}).get("name") == "tempo-ingress-policy"
    ]
    assert len(matches) == 1, f"expected exactly 1 tempo-ingress-policy, found {len(matches)}"
    np = matches[0]
    meta = np.get("metadata") or {}
    assert meta.get("namespace") == "monitoring", "tempo-ingress-policy namespace must be monitoring"
    assert (meta.get("labels") or {}).get("app.kubernetes.io/managed-by") == "narwhal-gitops", (
        "tempo-ingress-policy must carry app.kubernetes.io/managed-by: narwhal-gitops"
    )

    spec = np.get("spec") or {}
    assert spec.get("policyTypes") == ["Ingress"], (
        f"tempo-ingress-policy policyTypes must be ['Ingress'], got {spec.get('policyTypes')!r}"
    )
    assert (spec.get("podSelector") or {}).get("matchLabels") == {
        "app.kubernetes.io/name": "tempo",
        "app.kubernetes.io/instance": "tempo",
    }, f"tempo-ingress-policy must target only tempo pods, got {spec.get('podSelector')!r}"

    rules = spec.get("ingress") or []
    assert rules, "tempo-ingress-policy must define ingress rules"

    ingest_peers: set[tuple[str, frozenset[tuple[str, str]]]] = set()
    read_peers: set[tuple[str, frozenset[tuple[str, str]]]] = set()
    for idx, rule in enumerate(rules):
        from_list = rule.get("from")
        assert isinstance(from_list, list) and from_list, (
            f"tempo-ingress-policy rule #{idx} must specify non-empty 'from' peers"
        )
        ports_raw = rule.get("ports")
        assert isinstance(ports_raw, list) and ports_raw, (
            f"tempo-ingress-policy rule #{idx} must specify explicit ports"
        )
        ports = {(p.get("protocol", "TCP"), p.get("port")) for p in ports_raw}
        assert ("TCP", 15008) in ports, (
            f"tempo-ingress-policy rule #{idx} must allow ambient HBONE TCP/15008 (R200): {ports}"
        )
        app_ports = ports - {("TCP", 15008)}
        assert app_ports in ({("TCP", 4317)}, {("TCP", 3200)}), (
            f"tempo-ingress-policy rule #{idx} has disallowed ports {app_ports!r}"
        )
        for peer in from_list:
            key = _peer_key(peer)
            if app_ports == {("TCP", 4317)}:
                ingest_peers.add(key)
            else:
                read_peers.add(key)

    assert ingest_peers == {EXPECTED_INGEST_PEER}, (
        f"tempo-ingress-policy port 4317 must allow only alloy-logs, got {ingest_peers!r}"
    )
    assert read_peers == EXPECTED_READ_PEERS, (
        f"tempo-ingress-policy port 3200 peers mismatch: {read_peers!r}"
    )


def verify(root: Path) -> None:
    k8smon_doc = yaml.safe_load((root / K8SMON_YAML).read_text(encoding="utf-8"))
    k8smon_vals = k8smon_doc["spec"]["source"]["helm"]["valuesObject"]
    _check_values(k8smon_vals, "k8s-monitoring.yaml")

    mon_text = (root / MON_SH).read_text(encoding="utf-8")
    boot_vals = _extract_k8smon_heredoc(mon_text)
    _check_values(boot_vals, "08-2-monitoring.sh")

    assert k8smon_vals["destinations"]["tempo"] == boot_vals["destinations"]["tempo"], (
        "destinations.tempo parity mismatch between k8s-monitoring.yaml and 08-2-monitoring.sh"
    )
    assert k8smon_vals["collectors"]["alloy-logs"] == boot_vals["collectors"]["alloy-logs"], (
        "collectors.alloy-logs parity mismatch between k8s-monitoring.yaml and 08-2-monitoring.sh"
    )

    tempo_doc = yaml.safe_load((root / TEMPO_YAML).read_text(encoding="utf-8"))
    tempo_vals = tempo_doc["spec"]["source"]["helm"]["valuesObject"]
    otlp_protos = (((tempo_vals.get("tempo") or {}).get("receivers") or {}).get("otlp") or {}).get("protocols") or {}
    assert "http" in otlp_protos and otlp_protos["http"] is None, (
        f"tempo.yaml must disable OTLP/HTTP receiver via http: null, got {otlp_protos!r}"
    )
    assert (otlp_protos.get("grpc") or {}).get("endpoint") == "0.0.0.0:4317", (
        f"tempo.yaml OTLP/gRPC endpoint must be 0.0.0.0:4317, got {otlp_protos!r}"
    )
    assert ((tempo_vals.get("service") or {}).get("labels") or {}).get("istio.io/use-waypoint") == "none", (
        "tempo.yaml service.labels must set istio.io/use-waypoint: none"
    )

    for flag in (
        "--set tempo.receivers.otlp.protocols.http=null",
        "--set 'service.labels.istio\\.io/use-waypoint=none'",
        f"--set 'grafana.grafana\\.ini.tracing\\.opentelemetry\\.otlp.address={ALLOY_OTLP_ADDR}'",
    ):
        assert flag in mon_text, f"08-2-monitoring.sh missing parity flag: {flag}"

    prom_doc = yaml.safe_load((root / PROM_YAML).read_text(encoding="utf-8"))
    graf_ini = prom_doc["spec"]["source"]["helm"]["valuesObject"]["grafana"]["grafana.ini"]
    otlp_addr = (graf_ini.get("tracing.opentelemetry.otlp") or {}).get("address")
    assert otlp_addr == ALLOY_OTLP_ADDR, (
        f"prometheus-stack.yaml Grafana tracing address must route via Alloy, got {otlp_addr!r}"
    )

    _check_tempo_netpol(root)


def _find_chart(root: Path, pattern: str) -> str | None:
    for base in (root, *root.parents[:3]):
        matches = sorted(glob.glob(str(base / f"narwhal-airgap-bundle-*/charts/{pattern}")))
        if matches:
            return matches[0]
    return None


def verify_helm_renders(root: Path, chart_tgz: str | None = None, tempo_tgz: str | None = None) -> bool:
    chart_tgz = chart_tgz or _find_chart(root, "k8s-monitoring-4.2.0.tgz")
    tempo_tgz = tempo_tgz or _find_chart(root, "tempo-2.2.3.tgz")
    if not chart_tgz:
        return False

    k8smon_vals = yaml.safe_load((root / K8SMON_YAML).read_text(encoding="utf-8"))["spec"]["source"]["helm"]["valuesObject"]
    boot_vals = _extract_k8smon_heredoc((root / MON_SH).read_text(encoding="utf-8"))

    for label, vals in (("gitops", k8smon_vals), ("08-2", boot_vals)):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml") as tf:
            yaml.dump(vals, tf)
            tf.flush()
            out = subprocess.run(
                [
                    "helm", "template", "k8s-monitoring", chart_tgz,
                    "-n", "monitoring", "-f", tf.name,
                    "--show-only", "templates/alloy-config.yaml",
                    "--show-only", "templates/alloy.yaml",
                ],
                capture_output=True, text=True, check=True,
            ).stdout

        docs = [d for d in yaml.safe_load_all(out) if isinstance(d, dict)]
        cm = next(d for d in docs if d["kind"] == "ConfigMap")
        alloy_cr = next(d for d in docs if d["kind"] == "Alloy")

        cfg = cm["data"]["config.alloy"]
        _assert_rendered_alloy_security(cfg, label)

        spec = alloy_cr["spec"]
        assert (spec.get("service") or {}).get("internalTrafficPolicy") == "Local", (
            f"{label}: Alloy CR service.internalTrafficPolicy must be Local"
        )
        assert ((spec.get("service") or {}).get("labels") or {}).get("istio.io/use-waypoint") == "none", (
            f"{label}: Alloy CR service.labels missing istio.io/use-waypoint=none"
        )
        ports = {(p["port"], p["protocol"]) for p in spec["alloy"]["extraPorts"]}
        assert (4317, "TCP") in ports and (4318, "TCP") in ports, (
            f"{label}: missing OTLP extraPorts: {ports}"
        )
        assert spec["alloy"]["securityContext"]["readOnlyRootFilesystem"] is True, (
            f"{label}: ro-rootfs regressed"
        )
        assert "NET_RAW" not in spec["alloy"]["securityContext"]["capabilities"]["add"], (
            f"{label}: NET_RAW regressed"
        )

    if tempo_tgz:
        tempo_vals = yaml.safe_load((root / TEMPO_YAML).read_text(encoding="utf-8"))["spec"]["source"]["helm"]["valuesObject"]
        with tempfile.NamedTemporaryFile("w", suffix=".yaml") as tf:
            yaml.dump(tempo_vals, tf)
            tf.flush()
            tout = subprocess.run(
                [
                    "helm", "template", "tempo", tempo_tgz,
                    "-n", "monitoring", "-f", tf.name,
                    "--show-only", "templates/configmap-tempo.yaml",
                    "--show-only", "templates/service.yaml",
                ],
                capture_output=True, text=True, check=True,
            ).stdout
        tdocs = [d for d in yaml.safe_load_all(tout) if isinstance(d, dict)]
        tcm = next(d for d in tdocs if d["kind"] == "ConfigMap")
        tsvc = next(d for d in tdocs if d["kind"] == "Service")
        tcfg = yaml.safe_load(tcm["data"]["tempo.yaml"])
        assert "http" not in (tcfg["distributor"]["receivers"]["otlp"]["protocols"] or {}), (
            "rendered tempo.yaml must not enable OTLP/HTTP (4318)"
        )
        svc_ports = {p["port"] for p in tsvc["spec"]["ports"]}
        assert 4318 not in svc_ports and 4317 in svc_ports and 3200 in svc_ports, (
            f"rendered tempo Service ports unexpected: {svc_ports!r}"
        )
        assert (tsvc["metadata"].get("labels") or {}).get("istio.io/use-waypoint") == "none", (
            "rendered tempo Service missing istio.io/use-waypoint=none"
        )
    return True


def mutation_verify(root: Path) -> int:
    sources = {
        rel: (root / rel).read_text(encoding="utf-8")
        for rel in (K8SMON_YAML, TEMPO_YAML, PROM_YAML, NETPOL_YAML, MON_SH)
    }
    chart_tgz = _find_chart(root, "k8s-monitoring-4.2.0.tgz")
    tempo_tgz = _find_chart(root, "tempo-2.2.3.tgz")
    mutations = [
        (K8SMON_YAML, '                    `delete_matching_keys(attributes, "^k8s[.]")`,\n', ""),
        (K8SMON_YAML, '                    `delete_key(attributes, "service.namespace")`,\n', ""),
        (K8SMON_YAML, '                error_mode = "propagate"', '                error_mode = "ignore"'),
        (
            K8SMON_YAML,
            '                pod_association {\n                  source {\n                    from = "connection"\n                  }\n                }',
            '                pod_association {\n                  source {\n                    from = "resource_attribute"\n                    name = "k8s.pod.ip"\n                  }\n                }',
        ),
        (
            K8SMON_YAML,
            '                  traces = [otelcol.processor.transform.strip_client_k8s.input]',
            '                  traces = [otelcol.processor.k8sattributes.otlp_traces.input]',
        ),
        (
            K8SMON_YAML,
            '                filter {\n                  node = sys.env("HOSTNAME")\n                }\n',
            "",
        ),
        (
            K8SMON_YAML,
            '                  traces = [otelcol.processor.memory_limiter.otlp_traces.input]',
            '                  traces = [otelcol.processor.transform.strip_client_k8s.input]',
        ),
        (K8SMON_YAML, '                limit = "256MiB"', '                limit = "384MiB"'),
        (K8SMON_YAML, '                spike_limit = "64MiB"\n', ""),
        (K8SMON_YAML, "              internalTrafficPolicy: Local", "              internalTrafficPolicy: Cluster"),
        (K8SMON_YAML, '                istio.io/use-waypoint: "none"\n', ""),
        (K8SMON_YAML, 'metadata = ["k8s.namespace.name",', 'metadata = ["k8s.pod.uid",'),
        (K8SMON_YAML, '"k8s.deployment.name",', '"k8s.cronjob.name",'),
        (K8SMON_YAML, "                  port: 4317", "                  port: 14317"),
        (K8SMON_YAML, "                  port: 4318", "                  port: 14318"),
        (K8SMON_YAML, "url: tempo.monitoring.svc.cluster.local:4317", "url: tempo.monitoring.svc.cluster.local:4318"),
        (
            K8SMON_YAML,
            "        applicationObservability:\n          enabled: false",
            "        applicationObservability:\n          enabled: true\n          collector: alloy-logs\n          destinations: [tempo]\n          receivers:\n            otlp:\n              grpc:\n                enabled: true\n                port: 4317\n          traces:\n            enabled: true\n            filters:\n              span: [\"true\"]",
        ),
        (
            K8SMON_YAML,
            '                output {\n                  traces = [otelcol.processor.attributes.tempo.input]\n                }',
            '                output {\n                  traces = [otelcol.processor.filter.drop_all.input]\n                }\n              }\n\n              otelcol.processor.filter "drop_all" {\n                traces {\n                  span = ["true"]\n                }\n                output {\n                  traces = [otelcol.processor.attributes.tempo.input]\n                }',
        ),
        (TEMPO_YAML, "                http: null", '                http:\n                  endpoint: "0.0.0.0:4318"'),
        (TEMPO_YAML, '            istio.io/use-waypoint: "none"\n', ""),
        (MON_SH, '            `delete_matching_keys(attributes, "^k8s[.]")`,\n', ""),
        (MON_SH, '            `delete_key(attributes, "service.namespace")`,\n', ""),
        (MON_SH, '        limit = "256MiB"', '        limit = "384MiB"'),
        (MON_SH, '        spike_limit = "64MiB"\n', ""),
        (MON_SH, "    --set tempo.receivers.otlp.protocols.http=null \\\n", ""),
        (MON_SH, "    --set 'service.labels.istio\\.io/use-waypoint=none' \\\n", ""),
        (
            MON_SH,
            f"    --set 'grafana.grafana\\.ini.tracing\\.opentelemetry\\.otlp.address={ALLOY_OTLP_ADDR}' \\\n",
            "",
        ),
        (
            PROM_YAML,
            f"address: {ALLOY_OTLP_ADDR}",
            "address: tempo.monitoring.svc.cluster.local:4317",
        ),
        (NETPOL_YAML, "  name: tempo-ingress-policy", "  name: tempo-ingress-policy-disabled\n  labels: {}\n  #"),
        (
            NETPOL_YAML,
            "  ingress:\n    # 1. alloy-logs collector pods (monitoring) -> Tempo OTLP gRPC ingest (4317).\n    - from:\n        - podSelector:\n            matchLabels:\n              app.kubernetes.io/name: alloy-logs\n              app.kubernetes.io/instance: k8s-monitoring-alloy-logs",
            "  ingress:\n    # Open 4317 to all sources\n    - {} #\n    - from:\n        - podSelector:\n            matchLabels:\n              app.kubernetes.io/name: alloy-logs\n              app.kubernetes.io/instance: k8s-monitoring-alloy-logs",
        ),
        (
            NETPOL_YAML,
            "    # 1. alloy-logs collector pods (monitoring) -> Tempo OTLP gRPC ingest (4317).\n    - from:\n        - podSelector:\n            matchLabels:\n              app.kubernetes.io/name: alloy-logs\n              app.kubernetes.io/instance: k8s-monitoring-alloy-logs\n      ports:\n        - protocol: TCP\n          port: 4317",
            "    - ports:\n        - protocol: TCP\n          port: 4317",
        ),
        (
            NETPOL_YAML,
            "          port: 4317\n        - protocol: TCP\n          port: 15008\n    # 2. Grafana",
            "          port: 4317\n        - protocol: TCP\n          port: 4318\n        - protocol: TCP\n          port: 15008\n    # 2. Grafana",
        ),
        (
            NETPOL_YAML,
            "              app.kubernetes.io/name: alloy-logs\n              app.kubernetes.io/instance: k8s-monitoring-alloy-logs",
            "              gateway.networking.k8s.io/gateway-name: waypoint",
        ),
        (
            NETPOL_YAML,
            "          port: 4317\n        - protocol: TCP\n          port: 15008",
            "          port: 4317",
        ),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        fixture = Path(tmp)
        for rel in sources:
            (fixture / rel).parent.mkdir(parents=True, exist_ok=True)
        for rel, old, new in mutations:
            for k, v in sources.items():
                (fixture / k).write_text(v, encoding="utf-8")
            assert old in sources[rel], f"mutation anchor missing in {rel}: {old!r}"
            if rel == NETPOL_YAML and "tempo-ingress-policy-disabled" in new:
                # Simulate removing tempo-ingress-policy completely.
                text = sources[rel].split("# D-TEMPO-INGRESS")[0]
                (fixture / rel).write_text(text, encoding="utf-8")
            elif rel == NETPOL_YAML and "# Open 4317 to all sources" in new:
                # Replace the alloy-logs rule's from with an open 4318 receiver rule.
                text = sources[rel] + "\n    - ports:\n        - protocol: TCP\n          port: 4318\n        - protocol: TCP\n          port: 15008\n"
                (fixture / rel).write_text(text, encoding="utf-8")
            else:
                (fixture / rel).write_text(sources[rel].replace(old, new, 1), encoding="utf-8")
            try:
                verify(fixture)
                if chart_tgz:
                    verify_helm_renders(fixture, chart_tgz=chart_tgz, tempo_tgz=tempo_tgz)
            except (AssertionError, subprocess.CalledProcessError):
                continue
            raise AssertionError(f"mutation survived: {rel}: {old!r}")
    return len(mutations)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mutation-verify", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[3]
    verify(root)
    rendered = verify_helm_renders(root)
    render_msg = "helm render ok" if rendered else "helm render skipped (no bundle)"
    if args.mutation_verify:
        caught = mutation_verify(root)
        print(f"PASS: R373 trustworthy OTLP k8s.namespace.name enrichment + {render_msg} ({caught} mutations rejected)")
    else:
        print(f"PASS: R373 trustworthy OTLP k8s.namespace.name enrichment + {render_msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
