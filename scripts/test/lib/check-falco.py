#!/usr/bin/env python3
"""R291: Falco 0.45.1 (chart 9.2.1) is enabled, airgap-safe and consistent with the portal reader.

2026-10-28 (D-FALCO-045): Falco was re-enabled on every provider after the 0.39.2 -> 0.45.1 migration.
The failure modes this pins are the ones found while migrating, each of which is silent:

  A1  the Application renders (no Helm `if false` guard anywhere in the file: 03-save-helm-charts.sh and
      check-chart-upstream-sources.py match the guard text even inside a comment) with a known chart pin
  A2  image tags agree across falco.yaml, images.txt and image-digests.tsv (one row per image, well-formed
      index digest) and with the tag the pinned chart expects
  A3  the driver is modern_ebpf (legacy eBPF and gVisor were dropped by chart 9.0.0 / Falco 0.44)
  A4  nothing is pulled at runtime: falcoctl install/follow off, no k8smeta, no web UI, every image the
      values declare is in images.txt, only the bundled container plugin is loaded
  A5  container metadata the 0.45 way: collectors.containerEngine with only the CRI engine on the containerd
      socket; the removed collectors.containerd/cri/docker keys are silently ignored by chart 9.x and the
      chart then falls back to six hostPath mounts, so they must stay absent
  A6  falcosidekick contract: Loki lines are JSON (the portal JSON.parse()s every line), Alertmanager keeps
      source:falco and is posted to /api/v2/alerts (chart 0.14.1 sends /api/v1/alerts, which Alertmanager
      >= 0.27 answers with 410 Gone, so every Critical alert would be dropped), suggested_output stays on and
      metrics.outputRule stays off (the snapshot event would appear in the portal feed once per node per hour)
  A7  portal seam: the Loki selector only uses labels falcosidekick always writes (rule, priority), never
      matches priority with a lowercase equality (falcosidekick writes "Critical") and never selects a
      literal source=falco (Loki's `source` label is the Falco event source, "syscall"); every
      output_fields key the portal reads is one the container plugin's suggested output provides; the
      security page shows the Runtime Events tab on every node OS (it used to hide it on Ubuntu 26.04)
  A8  the privileged DaemonSet stays admissible: security-system is excluded from the Enforce
      disallow-privileged-containers policy
  A9  14-gitops-bootstrap.sh no longer removes falco.yaml on Ubuntu 26.04
  A10 the airgap chart source map and VERSIONS.md carry the same pins
  A11 2026-10-28 (D-FALCO-NOISE): the one exception appended to the bundled rule "Contact K8S API Server From
      Container" (47k Notice events in 20 h from 8 platform API clients). It must APPEND (a replace would drop
      exceptions appended elsewhere; Falco 0.45.1 rejects a bare redefinition), keep the bundled rule name
      exactly (case-sensitive), use fields [k8s.ns.name, container.image.repository] with comps [=, =], list
      exactly the 8 fully qualified (namespace, repository) pairs with no glob and no empty value, and be the
      ONLY custom rule item (a macro override could widen it). falco.priority stays notice and rules.d stays
      loaded after the bundled rules. The exception was validated against one Falco image: an image bump fails
      here until the rule name, fields and falco -V result are re-checked, because a rules file Falco cannot
      load stops it on every node.

Usage: check-falco.py [--mutation-verify]
"""
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
FILES = {
    "app": ROOT / "gitops/charts/narwhal-apps/templates/falco.yaml",
    "images": ROOT / "scripts/airgap/images.txt",
    "digests": ROOT / "scripts/airgap/lib/image-digests.tsv",
    "sources": ROOT / "scripts/airgap/lib/chart-upstream-sources.tsv",
    "versions": ROOT / "VERSIONS.md",
    "portal": ROOT / "portal/src/lib/falco.ts",
    "page": ROOT / "portal/src/app/(dashboard)/security/page.tsx",
    "kyverno": ROOT / "gitops/resources/kyverno-policies.yaml",
    "bootstrap": ROOT / "scripts/cluster/14-gitops-bootstrap.sh",
}

# falco chart version -> image tags pinned for it. falco: the chart's `appVersion: 0.45.1` (Chart.yaml of
# falco 9.2.1). falcosidekick: NOT the subchart default (0.14.1 values.yaml says image.tag 2.32.0 and its
# Chart.yaml still says appVersion 2.31.1) but the D-FALCO-SIDEKICK override 2.35.0, which carries
# falcosecurity/falcosidekick#1366 (vulnerable Go modules) and falcosecurity/falcosidekick#1390
# (multi-output race).
CHART_PINS = {"9.2.1": {"falco": "0.45.1", "falcosidekick": "2.35.0"}}
FALCO_IMAGE = ("docker.io", "falcosecurity/falco")
SIDEKICK_IMAGE = ("docker.io", "falcosecurity/falcosidekick")
CHART_SOURCE = "falco\thelm-repo\thttps://falcosecurity.github.io/charts"
LOKI_HOSTPORT = "http://loki.monitoring.svc.cluster.local:3100"
ALERTMANAGER_ENDPOINT = "/api/v2/alerts"
ALERTMANAGER_HOSTPORT = "http://prometheus-stack-kube-prom-alertmanager.monitoring.svc.cluster.local:9093"
CRI_SOCKET = "/run/containerd/containerd.sock"
# D-FALCO-NOISE. NOISE_RULE is the rule name in /etc/falco/falco_rules.yaml of docker.io/falcosecurity/falco:0.45.1
# (line 840); Falco matches it case-sensitively. NOISE_VERIFIED_FALCO is the image the exception was validated
# against with `falco -V /etc/falco/falco_rules.yaml -V <rules.d file>`. The pairs are the exact
# (k8s.ns.name, container.image.repository) values seen in live Loki on Kakao (CRI reports docker.io images
# WITH the docker.io prefix).
NOISE_RULE = "Contact K8S API Server From Container"
NOISE_FILE = "narwhal-api-noise.yaml"
NOISE_EXCEPTION = "narwhal_platform_api_clients"
NOISE_FIELDS = ["k8s.ns.name", "container.image.repository"]
NOISE_COMPS = ["=", "="]
NOISE_VERIFIED_FALCO = "0.45.1"
NOISE_PAIRS = {
    ("storage", "docker.io/otwld/velero-ui"),
    ("devtools", "ghcr.io/dasomel/narwhal-portal"),
    ("iam", "quay.io/keycloak/keycloak-operator"),
    ("platform-system", "reg.kyverno.io/kyverno/kyverno"),
    ("devtools", "ghcr.io/headlamp-k8s/headlamp"),
    ("devtools", "docker.io/kubernetesui/dashboard-auth"),
    ("devtools", "docker.io/kubernetesui/dashboard-web"),
    ("platform-system", "ghcr.io/cloudnative-pg/cloudnative-pg"),
}
NOISE_NS_RE = re.compile(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?")
# registry host (must contain a dot: `otwld/velero-ui` is not fully qualified), optional port, path components
NOISE_REPO_RE = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*\.[a-z]{2,}(?::[0-9]+)?(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+")
# Observed on 2026-10-27 (Falco 0.45.1 + container plugin 0.7.5, kernel 7.0.14): output_fields of a
# syscall event carry exactly these container/k8s keys through append_output.suggested_output.
SUGGESTED_FIELDS = {
    "container.id", "container.name", "container.image.repository", "container.image.tag",
    "k8s.ns.name", "k8s.pod.name",
}
# Labels falcosidekick 2.31-2.35 (outputs/loki.go) writes on EVERY Loki stream.
SIDEKICK_LABELS = {"rule", "source", "priority"}
PRIORITY_RANK = {"emergency": 0, "alert": 1, "critical": 2, "error": 3, "warning": 4, "notice": 5,
                 "informational": 6, "debug": 7}

HELM_COMMENT_RE = re.compile(r"\A\{\{-\s*/\*.*?\*/\s*-?\}\}\s*", re.S)
GUARD_RE = re.compile(r"\{\{-?\s*if\s+false\s*-?\}\}")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
STAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
SEMVER_RE = re.compile(r"\d+\.\d+\.\d+")


def strip_ts_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?:^|(?<=\s))//.*$", "", text, flags=re.M)


class UniqueLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys (a duplicate silently resolves last-wins)."""


def _unique_mapping(loader, node, deep=False):
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(None, None, f"duplicate key {key!r}", key_node.start_mark)
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


class RuleLoader(UniqueLoader):
    """UniqueLoader for Falco rule files. PyYAML resolves a bare `=` to the YAML 1.1 `value` type, which SafeLoader
    cannot construct, but Falco's yaml-cpp reads `comps: [=, =]` as plain strings, so `=` must stay a string."""


RuleLoader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:value"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def load_app(text):
    """Returns (valuesObject dict or None, source dict, destination dict, problems)."""
    out = []
    if GUARD_RE.search(text):
        out.append("[A1] falco.yaml contains a Helm `if false` guard: the Application is disabled")
    body = HELM_COMMENT_RE.sub("", text, count=1)
    if "{{" in body:
        out.append("[A1] falco.yaml has template expressions outside the leading comment; "
                   "this checker only parses a static Application")
        return None, {}, {}, out
    try:
        doc = yaml.load(body, Loader=UniqueLoader)
    except yaml.YAMLError as exc:
        return None, {}, {}, out + [f"[A1] falco.yaml does not parse: {exc}"]
    spec = (doc or {}).get("spec") or {}
    if (doc or {}).get("kind") != "Application":
        out.append(f"[A1] falco.yaml kind must be Application, got {(doc or {}).get('kind')!r}")
    source = spec.get("source") or {}
    values = (source.get("helm") or {}).get("valuesObject")
    if not isinstance(values, dict):
        out.append("[A1] falco.yaml has no spec.source.helm.valuesObject")
        values = None
    return values, source, spec.get("destination") or {}, out


def image_ref(image, default_repo):
    if not isinstance(image, dict):
        return None, f"image block missing for {default_repo}"
    registry, repo, tag = image.get("registry"), image.get("repository"), image.get("tag")
    return (registry, repo, str(tag) if tag is not None else None), None


def declared_images(node, path=""):
    """Every `image:` mapping that names a repository, anywhere in the values (path, registry, repo, tag)."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            if key == "image" and isinstance(value, dict) and "repository" in value:
                found.append((here, value.get("registry"), value.get("repository"), value.get("tag")))
            found.extend(declared_images(value, here))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found.extend(declared_images(value, f"{path}[{i}]"))
    return found


def lines_of(text):
    return [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def problems(texts):
    out = []
    values, source, destination, errs = load_app(texts["app"])
    out.extend(errs)

    chart_version = str(source.get("targetRevision", ""))
    if source and source.get("chart") != "falco":
        out.append(f"[A1] spec.source.chart must be falco, got {source.get('chart')!r}")
    pins = CHART_PINS.get(chart_version)
    if source and pins is None:
        out.append(f"[A1] falco chart {chart_version!r} is not in CHART_PINS {sorted(CHART_PINS)}; "
                   "read appVersion and the falcosidekick image.tag from the chart, then add the row")
    if source and destination.get("namespace") != "security-system":
        out.append(f"[A1] destination namespace must be security-system, got {destination.get('namespace')!r}")

    if values is None:
        return out

    # ---- A2 image pins
    refs = {}
    for name, node, default in (("falco", values.get("image"), FALCO_IMAGE),
                                ("falcosidekick", (values.get("falcosidekick") or {}).get("image"), SIDEKICK_IMAGE)):
        ref, err = image_ref(node, name)
        if err:
            out.append(f"[A2] {name}: {err}")
            continue
        registry, repo, tag = ref
        if (registry, repo) != default:
            out.append(f"[A2] {name} image must be {default[0]}/{default[1]}, got {registry}/{repo}")
        if not tag or not SEMVER_RE.fullmatch(tag):
            out.append(f"[A2] {name} image tag must be an explicit x.y.z (never latest or empty), got {tag!r}")
        elif pins and tag != pins[name]:
            out.append(f"[A2] {name} image tag {tag} differs from the {pins[name]} pinned for chart {chart_version} (CHART_PINS)")
        if registry and repo and tag:
            refs[name] = f"{registry}/{repo}:{tag}"
    image_lines = lines_of(texts["images"])
    for name, ref in refs.items():
        if image_lines.count(ref) != 1:
            out.append(f"[A2] images.txt must list {ref} exactly once, found {image_lines.count(ref)}")
    stale = sorted(l for l in image_lines if "falcosecurity/" in l and l not in refs.values())
    if stale:
        out.append(f"[A2] images.txt lists falcosecurity images the Application does not pin: {stale}")
    rows = [line.split("\t") for line in texts["digests"].splitlines() if line.strip() and not line.startswith("#")]
    fal_rows = [r for r in rows if "falcosecurity/" in r[0]]
    for name, ref in refs.items():
        match = [r for r in fal_rows if r[0] == ref]
        if len(match) != 1:
            out.append(f"[A2] image-digests.tsv must have exactly one row for {ref}, found {len(match)}")
            continue
        row = match[0]
        if len(row) != 4 or not DIGEST_RE.fullmatch(row[1]) or not STAMP_RE.fullmatch(row[2]) or row[3] != "crane digest":
            out.append(f"[A2] image-digests.tsv row for {ref} is malformed (need ref, sha256 index digest, UTC stamp, 'crane digest'): {row}")
    stale_rows = sorted(r[0] for r in fal_rows if r[0] not in refs.values())
    if stale_rows:
        out.append(f"[A2] image-digests.tsv has falcosecurity rows the Application does not pin: {stale_rows}")

    # ---- A3 driver
    driver = values.get("driver") or {}
    if driver.get("enabled") is not True or driver.get("kind") != "modern_ebpf":
        out.append(f"[A3] driver must be enabled with kind modern_ebpf, got enabled={driver.get('enabled')!r} kind={driver.get('kind')!r}")
    if (values.get("falco") or {}).get("engine"):
        out.append("[A3] falco.engine is chart-managed from driver.*; do not set it in values")

    # ---- A4 nothing pulled at runtime
    artifact = (values.get("falcoctl") or {}).get("artifact") or {}
    for mode in ("install", "follow"):
        if (artifact.get(mode) or {}).get("enabled") is not False:
            out.append(f"[A4] falcoctl.artifact.{mode}.enabled must be false: falcoctl pulls rules and plugins from ghcr.io at runtime")
    collectors = values.get("collectors") or {}
    if (collectors.get("kubernetes") or {}).get("enabled") not in (None, False):
        out.append("[A4] collectors.kubernetes.enabled pulls the k8smeta plugin through falcoctl and needs k8s-metacollector")
    if ((values.get("falcosidekick") or {}).get("webui") or {}).get("enabled") is not False:
        out.append("[A4] falcosidekick.webui.enabled must be false: the web UI adds an unmirrored redis image")
    if (values.get("responseActions") or {}).get("enabled") not in (None, False) or values.get("falco-talon"):
        out.append("[A4] falco-talon (responseActions) adds an unmirrored image and a cluster-wide actor")
    mirrored = set(image_lines)
    for path, registry, repo, tag in declared_images(values):
        ref = f"{registry}/{repo}:{tag}" if registry else f"{repo}:{tag}"
        if ref not in mirrored:
            out.append(f"[A4] values declare image {ref} at {path} but images.txt does not list it: it would be pulled from the internet")
    falco_cfg = values.get("falco") or {}
    plugins = falco_cfg.get("plugins") or []
    loaded = falco_cfg.get("load_plugins") or []
    if loaded or any(p.get("library_path") != "libcontainer.so" for p in plugins if isinstance(p, dict)):
        out.append("[A4] falco.load_plugins/plugins may only come from the chart's collectors; only the bundled libcontainer.so exists in the image")

    # ---- A5 container metadata
    if collectors.get("enabled") is not True:
        out.append("[A5] collectors.enabled must be true (container metadata)")
    for legacy in ("containerd", "cri", "docker"):
        if legacy in collectors:
            out.append(f"[A5] collectors.{legacy} was removed in chart 7.0.0 and is silently ignored; use collectors.containerEngine")
    engine = collectors.get("containerEngine") or {}
    if engine.get("enabled") is not True:
        out.append("[A5] collectors.containerEngine.enabled must be true")
    engines = engine.get("engines") or {}
    enabled_engines = sorted(name for name, cfg in engines.items() if isinstance(cfg, dict) and cfg.get("enabled"))
    if enabled_engines != ["cri"]:
        out.append(f"[A5] only the cri container engine may be enabled (each one adds a hostPath mount), got {enabled_engines}")
    if (engines.get("cri") or {}).get("sockets") != [CRI_SOCKET]:
        out.append(f"[A5] cri engine sockets must be exactly [{CRI_SOCKET}], got {(engines.get('cri') or {}).get('sockets')!r}")

    # ---- A6 falcosidekick contract
    sidekick = values.get("falcosidekick") or {}
    config = sidekick.get("config") or {}
    loki = config.get("loki") or {}
    alertmanager = config.get("alertmanager") or {}
    if sidekick.get("enabled") is not True:
        out.append("[A6] falcosidekick.enabled must be true")
    if loki.get("hostport") != LOKI_HOSTPORT:
        out.append(f"[A6] loki.hostport must be {LOKI_HOSTPORT}, got {loki.get('hostport')!r}")
    if loki.get("format") != "json":
        out.append(f"[A6] loki.format must be json (the portal JSON.parse()s every line; text lines are dropped), got {loki.get('format')!r}")
    minimum = str(loki.get("minimumpriority", "")).lower()
    notice = PRIORITY_RANK["notice"]
    if minimum and (minimum not in PRIORITY_RANK or PRIORITY_RANK[minimum] < notice):
        out.append(f"[A6] loki.minimumpriority {minimum!r} would drop events Falco emits at its priority: notice")
    if alertmanager.get("hostport") != ALERTMANAGER_HOSTPORT:
        out.append(f"[A6] alertmanager.hostport must be {ALERTMANAGER_HOSTPORT}, got {alertmanager.get('hostport')!r}")
    if alertmanager.get("endpoint") != ALERTMANAGER_ENDPOINT:
        out.append(f"[A6] alertmanager.endpoint must be {ALERTMANAGER_ENDPOINT} (Alertmanager >= 0.27 answers the chart default /api/v1/alerts with 410 Gone and falcosidekick drops every Critical alert), got {alertmanager.get('endpoint')!r}")
    if "source:falco" not in [p.strip() for p in str(alertmanager.get("extralabels", "")).split(",")]:
        out.append(f"[A6] alertmanager.extralabels must carry source:falco, got {alertmanager.get('extralabels')!r}")
    if falco_cfg.get("json_output") is not True:
        out.append("[A6] falco.json_output must be true (falcosidekick and the portal need JSON)")
    append = falco_cfg.get("append_output") or []
    if {"suggested_output": True} not in [a for a in append if isinstance(a, dict)]:
        out.append("[A6] falco.append_output must keep `suggested_output: true`: it is the only source of container.*/k8s.* output_fields")
    if (values.get("metrics") or {}).get("outputRule") is not False:
        out.append("[A6] metrics.outputRule must be false: the 'Falco internal: metrics snapshot' event would reach Loki and the portal feed hourly per node")

    # ---- A7 portal seam (comments stripped: falco.ts documents the old, wrong selectors in prose)
    portal = strip_ts_comments(texts["portal"])
    labels = set(re.findall(r'(?<![\w.])([A-Za-z_][A-Za-z0-9_]*)(?:=~|!~|!=|=)"', portal))
    if "rule" not in labels:
        out.append("[A7] portal falco.ts: no LogQL selector with a rule label found; the parser is stale")
    extra = labels - {"rule", "priority"}
    if extra:
        out.append(f"[A7] portal selector uses labels {sorted(extra)} that falcosidekick does not always write (always: {sorted(SIDEKICK_LABELS)}; the selector may use rule and priority)")
    if re.search(r"""\bsource\s*=\s*"falco"|\bsource\s*=\s*\\?"falco""", portal):
        out.append('[A7] portal selects source="falco": Loki\'s source label is the Falco event source ("syscall"); the literal exists only on Alertmanager alerts')
    if re.search(r'priority\s*=\s*"', portal):
        out.append('[A7] portal matches priority with an exact equality: falcosidekick writes the Capitalized name ("Critical"); match both spellings with =~')
    elif not re.search(r"priority=~", portal):
        out.append("[A7] portal no longer filters Loki by priority label")
    extra_fields = set()
    for entry in append:
        if isinstance(entry, dict):
            for item in entry.get("extra_fields") or []:
                extra_fields.add(next(iter(item)) if isinstance(item, dict) else item)
    read_keys = set(re.findall(r'output_fields\?\.\["([^"]+)"\]', portal))
    if len(read_keys) < 4:
        out.append(f"[A7] portal falco.ts: parsed only {len(read_keys)} output_fields keys ({sorted(read_keys)}); parser is stale")
    for key in sorted(read_keys - SUGGESTED_FIELDS - extra_fields):
        out.append(f"[A7] portal reads output_fields[{key!r}] but neither the container plugin's suggested output nor falco.append_output.extra_fields provides it")

    page = strip_ts_comments(texts["page"])
    if re.search(r"26\.04|osImage|is2604", page):
        out.append("[A7] portal security page gates on the node OS image (26.04/osImage): Falco runs on every provider since D-FALCO-045, so the Runtime Events tab must not be hidden")
    if not re.search(r'<TabsTrigger value="runtime">', page) or not re.search(r'<TabsContent value="runtime"[^>]*>\s*<RuntimeEventsFeed', page):
        out.append("[A7] portal security page must render the Runtime Events tab trigger and its RuntimeEventsFeed content")

    # ---- A8 privileged DaemonSet stays admissible
    namespace = destination.get("namespace")
    try:
        policies = [d for d in yaml.load_all(texts["kyverno"], Loader=UniqueLoader) if isinstance(d, dict)]
    except yaml.YAMLError as exc:
        policies = []
        out.append(f"[A8] kyverno-policies.yaml does not parse: {exc}")
    privileged = [p for p in policies if (p.get("metadata") or {}).get("name") == "disallow-privileged-containers"]
    if len(privileged) != 1:
        out.append(f"[A8] expected exactly one disallow-privileged-containers policy, found {len(privileged)}")
    else:
        policy = privileged[0]
        excluded = set()
        for rule in (policy.get("spec") or {}).get("rules") or []:
            for clause in ((rule.get("exclude") or {}).get("any") or []):
                excluded.update((clause.get("resources") or {}).get("namespaces") or [])
        if (policy.get("spec") or {}).get("validationFailureAction") == "Enforce" and namespace not in excluded:
            out.append(f"[A8] disallow-privileged-containers is Enforce and does not exclude {namespace}: the privileged Falco DaemonSet would be denied on its next recreation")

    # ---- A9 bootstrap
    for number, line in enumerate(texts["bootstrap"].splitlines(), 1):
        code = line.split("#", 1)[0]
        if "templates/falco.yaml" in code and re.search(r"\brm\b", code):
            out.append(f"[A9] 14-gitops-bootstrap.sh:{number} removes falco.yaml from the staged tree")

    # ---- A10 chart source map and VERSIONS.md
    if CHART_SOURCE not in [l.rstrip("\n") for l in texts["sources"].splitlines()]:
        out.append(f"[A10] chart-upstream-sources.tsv has no `{CHART_SOURCE}` row: 03-save-helm-charts.sh would fail on the falco chart")
    falco_row = re.search(r"^\|\s*Falco\s*\|\s*v(\d+\.\d+\.\d+)\s*\(chart\s+(\d+\.\d+\.\d+)\)\s*\|", texts["versions"], re.M)
    sidekick_row = re.search(r"^\|\s*Falcosidekick\s*\|\s*(\d+\.\d+\.\d+)\s*\(subchart\s+(\d+\.\d+\.\d+)", texts["versions"], re.M)
    if not falco_row or not sidekick_row:
        out.append("[A10] VERSIONS.md needs '| Falco | v<app> (chart <chart>) |' and '| Falcosidekick | <app> (subchart <ver> ...' rows")
    elif pins:
        if (falco_row.group(1), falco_row.group(2)) != (pins["falco"], chart_version):
            out.append(f"[A10] VERSIONS.md Falco row {falco_row.groups()} differs from app {pins['falco']} / chart {chart_version}")
        if sidekick_row.group(1) != pins["falcosidekick"]:
            out.append(f"[A10] VERSIONS.md Falcosidekick row {sidekick_row.group(1)} differs from {pins['falcosidekick']}")

    # ---- A11 API-server noise exception (D-FALCO-NOISE)
    if falco_cfg.get("priority") != "notice":
        out.append(f"[A11] falco.priority must stay notice: a higher floor would hide every NOTICE rule, not only {NOISE_RULE!r}, got {falco_cfg.get('priority')!r}")
    rules_files = falco_cfg.get("rules_files") or []
    bundled_rules, rules_dir = "/etc/falco/falco_rules.yaml", "/etc/falco/rules.d"
    if bundled_rules not in rules_files or rules_dir not in rules_files or rules_files.index(bundled_rules) > rules_files.index(rules_dir):
        out.append(f"[A11] falco.rules_files must load {bundled_rules} before {rules_dir} (customRules land there, and an override needs the bundled rule loaded first), got {rules_files}")
    falco_tag = str((values.get("image") or {}).get("tag"))
    if falco_tag != NOISE_VERIFIED_FALCO:
        out.append(f"[A11] the exception was validated against falco {NOISE_VERIFIED_FALCO}, the image is {falco_tag}: a rule name or field the new bundle lacks makes Falco refuse to start on every node; re-run falco -V with the bundled rules and the rules.d file, then update NOISE_VERIFIED_FALCO")
    custom = values.get("customRules")
    items = []
    if not isinstance(custom, dict) or sorted(custom) != [NOISE_FILE]:
        out.append(f"[A11] customRules must hold exactly the file {NOISE_FILE!r} (the chart mounts it on /etc/falco/rules.d), got {sorted(custom) if isinstance(custom, dict) else custom!r}")
    if isinstance(custom, dict):
        for fname, content in custom.items():
            try:
                parsed = yaml.load(content, Loader=RuleLoader)
            except yaml.YAMLError as exc:
                out.append(f"[A11] customRules[{fname!r}] does not parse: {exc}")
                continue
            if not isinstance(parsed, list):
                out.append(f"[A11] customRules[{fname!r}] must be a YAML list of rule objects")
                continue
            items.extend(parsed)
    if len(items) != 1 or not isinstance(items[0], dict):
        out.append(f"[A11] customRules must hold exactly one item, the exception override of {NOISE_RULE!r}; a second rule, macro or list (for example an override of user_known_contact_k8s_api_server_activities) could widen it, got {len(items)} item(s)")
        return out
    item = items[0]
    if set(item) != {"rule", "exceptions", "override"}:
        out.append(f"[A11] the exception item must have exactly the keys rule, exceptions and override (condition, priority, enabled or append would redefine or weaken the bundled rule), got {sorted(item)}")
    if item.get("rule") != NOISE_RULE:
        out.append(f"[A11] rule must be exactly {NOISE_RULE!r}, the bundled rule's name (case-sensitive: Falco rejects any other name with 'no rule by that name already exists'), got {item.get('rule')!r}")
    if item.get("override") != {"exceptions": "append"}:
        out.append(f"[A11] override must be exactly {{exceptions: append}} (replace would drop exceptions appended elsewhere; a bare redefinition is rejected by Falco 0.45.1), got {item.get('override')!r}")
    exceptions = item.get("exceptions")
    if not isinstance(exceptions, list) or len(exceptions) != 1 or not isinstance(exceptions[0], dict):
        out.append(f"[A11] exceptions must hold exactly one exception, got {exceptions!r}")
        return out
    exception = exceptions[0]
    if set(exception) != {"name", "fields", "comps", "values"}:
        out.append(f"[A11] the exception must have exactly the keys name, fields, comps and values, got {sorted(exception)}")
    if exception.get("name") != NOISE_EXCEPTION:
        out.append(f"[A11] exception name must be {NOISE_EXCEPTION!r}, got {exception.get('name')!r}")
    if exception.get("fields") != NOISE_FIELDS:
        out.append(f"[A11] exception fields must be exactly {NOISE_FIELDS} (a pair, so a namespace or an image alone never matches), got {exception.get('fields')!r}")
    if exception.get("comps") != NOISE_COMPS:
        out.append(f"[A11] exception comps must be exactly {NOISE_COMPS} (exact match, no startswith/contains/glob), got {exception.get('comps')!r}")
    listed = exception.get("values")
    pairs = []
    if not isinstance(listed, list) or not listed:
        out.append(f"[A11] exception values must be a non-empty list of [namespace, repository] pairs, got {listed!r}")
    else:
        for value in listed:
            if not isinstance(value, list) or len(value) != 2 or not all(isinstance(v, str) for v in value):
                out.append(f"[A11] every value must be a [namespace, repository] pair of two strings, got {value!r}")
                continue
            namespace_name, repository = value
            if not namespace_name.strip() or not repository.strip():
                out.append(f"[A11] value {value!r} has an empty namespace or repository: Falco 0.45.1 rejects an empty exception value ('Value must be non-empty') and stops on every node")
            elif re.search(r"[*?\[\]]", namespace_name + repository):
                out.append(f"[A11] value {value!r} contains a glob character: values must be exact")
            else:
                if not NOISE_NS_RE.fullmatch(namespace_name):
                    out.append(f"[A11] value {value!r}: {namespace_name!r} is not a Kubernetes namespace name")
                if not NOISE_REPO_RE.fullmatch(repository):
                    out.append(f"[A11] value {value!r}: {repository!r} is not a fully qualified image repository (registry host with a dot, then path)")
            pairs.append((namespace_name, repository))
        if len(pairs) != len(set(pairs)):
            out.append(f"[A11] exception values list a pair twice: {sorted(p for p in set(pairs) if pairs.count(p) > 1)}")
        if set(pairs) != NOISE_PAIRS:
            out.append(f"[A11] exception values differ from the 8 reviewed pairs (NOISE_PAIRS): added {sorted(set(pairs) - NOISE_PAIRS)}, missing {sorted(NOISE_PAIRS - set(pairs))}")
    return out


def block_bounds(text, header):
    """Character span of the YAML block whose first line is exactly `header` (indentation included)."""
    lines = text.splitlines(keepends=True)
    hits = [i for i, line in enumerate(lines) if line.rstrip("\n") == header]
    if len(hits) != 1:
        return None
    start = hits[0]
    indent = len(header) - len(header.lstrip())
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if line.strip() and (len(line) - len(line.lstrip())) <= indent:
            break
        end += 1
    offset = sum(len(l) for l in lines[:start])
    return offset, offset + sum(len(l) for l in lines[start:end])


def mutate_block(text, header, old, new):
    """Replace `old` with `new` once, only inside the block under `header`; text is returned unchanged if the
    anchor is absent so the caller can report an unapplied mutation."""
    bounds = block_bounds(text, header)
    if bounds is None:
        return text
    lo, hi = bounds
    block = text[lo:hi]
    if old not in block:
        return text
    return text[:lo] + block.replace(old, new, 1) + text[hi:]


def delete_block(text, header):
    """Remove the YAML block whose first line is exactly `header` (header line included)."""
    bounds = block_bounds(text, header)
    if bounds is None:
        return text
    lo, hi = bounds
    return text[:lo] + text[hi:]


def mutate_doc(text, name, old, new):
    """Replace `old` once inside the one `---`-separated document that contains `name: <name>`."""
    docs = text.split("\n---\n")
    hits = [i for i, doc in enumerate(docs) if re.search(rf"^  name: {re.escape(name)}$", doc, re.M)]
    if len(hits) != 1 or old not in docs[hits[0]]:
        return text
    docs[hits[0]] = docs[hits[0]].replace(old, new, 1)
    return "\n---\n".join(docs)


def insert_after_line(text, anchor_line, new_text):
    lines = text.split("\n")
    hits = [i for i, line in enumerate(lines) if line == anchor_line]
    if len(hits) != 1:
        return text
    lines.insert(hits[0] + 1, new_text)
    return "\n".join(lines)


def mutate_line(text, old_line, new_line):
    lines = text.split("\n")
    hits = [i for i, line in enumerate(lines) if line == old_line]
    if len(hits) != 1:
        return text
    lines[hits[0]] = new_line
    return "\n".join(lines)


def mutants(texts):
    app, portal = texts["app"], texts["portal"]
    ref_falco = "docker.io/falcosecurity/falco:0.45.1"
    ref_sidekick = "docker.io/falcosecurity/falcosidekick:2.35.0"
    digest_lines = texts["digests"].split("\n")
    sidekick_digest = next(l for l in digest_lines if l.startswith(ref_sidekick + "\t"))
    falco_digest = next(l for l in digest_lines if l.startswith(ref_falco + "\t"))
    kyverno_excl = "                - security-system\n"
    noise = "        customRules:"
    noise_override = "              override:\n                exceptions: append\n"
    noise_pair_indent = "                    "
    noise_last_pair = noise_pair_indent + "- [platform-system, ghcr.io/cloudnative-pg/cloudnative-pg]\n"
    return [
        # A1
        ("A1", "falco.yaml wrapped in an if-false guard again", "app",
         "{{- if false }}\n" + app + "\n{{- end }}\n"),
        ("A1", "chart back to 4.16.0", "app",
         mutate_block(app, "  source:", "targetRevision: 9.2.1", "targetRevision: 4.16.0")),
        ("A1", "destination namespace changed", "app",
         mutate_block(app, "  destination:", "namespace: security-system", "namespace: monitoring")),
        # A2
        ("A2", "images.txt falco tag back to 0.39.2", "images",
         mutate_line(texts["images"], ref_falco, "docker.io/falcosecurity/falco:0.39.2")),
        ("A2", "images.txt drops the falcosidekick line", "images",
         mutate_line(texts["images"], ref_sidekick, "")),
        ("A2", "images.txt keeps a stale falcoctl line", "images",
         mutate_line(texts["images"], ref_sidekick, ref_sidekick + "\ndocker.io/falcosecurity/falcoctl:0.14.2")),
        ("A2", "image-digests.tsv drops the falcosidekick row", "digests",
         mutate_line(texts["digests"], sidekick_digest, "")),
        ("A2", "image-digests.tsv digest truncated", "digests",
         mutate_line(texts["digests"], falco_digest, falco_digest.replace(falco_digest.split("\t")[1], falco_digest.split("\t")[1][:-4]))),
        ("A2", "image-digests.tsv stale falco row", "digests",
         mutate_line(texts["digests"], falco_digest, falco_digest.replace(":0.45.1", ":0.39.2"))),
        ("A2", "falco image tag differs from the chart's appVersion", "app",
         mutate_block(app, "        image:", 'tag: "0.45.1"', 'tag: "0.45.0"')),
        ("A2", "falcosidekick tag back to the subchart default 2.32.0", "app",
         mutate_block(app, "          image:", 'tag: "2.35.0"', 'tag: "2.32.0"')),
        ("A2", "falcosidekick tag back to the Chart.yaml appVersion", "app",
         mutate_block(app, "          image:", 'tag: "2.35.0"', 'tag: "2.31.1"')),
        ("A2", "falco image tag becomes latest", "app",
         mutate_block(app, "        image:", 'tag: "0.45.1"', 'tag: "latest"')),
        # A3
        ("A3", "driver kind ebpf", "app",
         mutate_block(app, "        driver:", "kind: modern_ebpf", "kind: ebpf")),
        ("A3", "driver kind auto", "app",
         mutate_block(app, "        driver:", "kind: modern_ebpf", "kind: auto")),
        ("A3", "driver disabled", "app",
         mutate_block(app, "        driver:", "enabled: true", "enabled: false")),
        # A4
        ("A4", "falcoctl install enabled", "app",
         mutate_block(app, "            install:", "enabled: false", "enabled: true")),
        ("A4", "falcoctl follow enabled", "app",
         mutate_block(app, "            follow:", "enabled: false", "enabled: true")),
        ("A4", "k8smeta collector enabled", "app",
         mutate_block(app, "        collectors:", "          enabled: true\n          containerEngine:",
                      "          enabled: true\n          kubernetes:\n            enabled: true\n          containerEngine:")),
        ("A4", "web UI enabled", "app",
         mutate_block(app, "          webui:", "enabled: false", "enabled: true")),
        ("A4", "falcoctl image declared but not mirrored", "app",
         mutate_block(app, "        falcoctl:", "        falcoctl:\n",
                      '        falcoctl:\n          image:\n            registry: docker.io\n            repository: falcosecurity/falcoctl\n            tag: "0.14.2"\n')),
        ("A4", "response actions enabled", "app",
         insert_after_line(app, "        tty: true", "        responseActions:\n          enabled: true")),
        ("A4", "an unbundled plugin loaded", "app",
         mutate_block(app, "        falco:", "          json_output: true\n",
                      "          json_output: true\n          load_plugins: [k8saudit]\n")),
        # A5
        ("A5", "docker engine enabled", "app",
         mutate_block(app, "              docker:", "enabled: false", "enabled: true")),
        ("A5", "cri engine disabled", "app",
         mutate_block(app, "              cri:", "enabled: true", "enabled: false")),
        ("A5", "cri socket changed", "app",
         mutate_block(app, "              cri:", CRI_SOCKET, "/run/crio/crio.sock")),
        ("A5", "removed collectors.containerd key re-added", "app",
         mutate_block(app, "        collectors:", "          enabled: true\n          containerEngine:",
                      "          enabled: true\n          containerd:\n            enabled: true\n            socket: /run/containerd/containerd.sock\n          containerEngine:")),
        ("A5", "containerEngine disabled", "app",
         mutate_block(app, "          containerEngine:", "enabled: true", "enabled: false")),
        # A6
        ("A6", "loki format back to text", "app",
         mutate_block(app, "            loki:", "format: json", "format: text")),
        ("A6", "loki host wrong", "app",
         mutate_block(app, "            loki:", "loki.monitoring.svc", "loki-gateway.monitoring.svc")),
        ("A6", "loki minimumpriority above notice", "app",
         mutate_block(app, "            loki:", 'minimumpriority: "informational"', 'minimumpriority: "critical"')),
        ("A6", "alertmanager endpoint back to the removed v1 API", "app",
         mutate_block(app, "            alertmanager:", 'endpoint: "/api/v2/alerts"', 'endpoint: "/api/v1/alerts"')),
        ("A6", "alertmanager endpoint left to the chart default", "app",
         mutate_block(app, "            alertmanager:", '              endpoint: "/api/v2/alerts"\n', "")),
        ("A6", "alertmanager source label changed", "app",
         mutate_block(app, "            alertmanager:", 'extralabels: "source:falco"', 'extralabels: "source:other"')),
        ("A6", "alertmanager host wrong", "app",
         mutate_block(app, "            alertmanager:", "prometheus-stack-kube-prom-alertmanager", "alertmanager")),
        ("A6", "json_output off", "app",
         mutate_block(app, "        falco:", "json_output: true", "json_output: false")),
        ("A6", "suggested_output off", "app",
         mutate_block(app, "          append_output:", "suggested_output: true", "suggested_output: false")),
        ("A6", "metrics outputRule on", "app",
         mutate_block(app, "        metrics:", "outputRule: false", "outputRule: true")),
        ("A6", "falcosidekick disabled", "app",
         mutate_block(app, "        falcosidekick:", "enabled: true", "enabled: false")),
        # A7
        ("A7", "portal reads a key falco does not provide", "portal",
         portal.replace('output_fields?.["container.name"]', 'output_fields?.["k8s.deployment.name"]', 1)),
        ("A7", "portal priority matched by lowercase equality again", "portal",
         portal.replace('priority=~"${capitalized}|${capitalized.toLowerCase()}"', 'priority="${capitalized.toLowerCase()}"', 1)),
        ("A7", "portal selects a literal source=falco", "portal",
         portal.replace('`{rule=~".+"}`', '`{rule=~".+", source="falco"}`', 1)),
        ("A7", "portal selects on a label falcosidekick does not always write", "portal",
         portal.replace('`{rule=~".+"}`', '`{rule=~".+", k8s_ns_name=~".+"}`', 1)),
        ("A7", "portal security page hides the runtime tab on 26.04 again", "page",
         texts["page"].replace("export default async function SecurityPage",
                               'const is2604 = (n: { osImage: string }) => n.osImage.includes("26.04")\n\nexport default async function SecurityPage', 1)),
        ("A7", "portal security page loses the runtime tab trigger", "page",
         texts["page"].replace('<TabsTrigger value="runtime">', '<TabsTrigger value="other">', 1)),
        # A8
        ("A8", "security-system no longer excluded from disallow-privileged-containers", "kyverno",
         mutate_doc(texts["kyverno"], "disallow-privileged-containers", kyverno_excl, "")),
        # A9
        ("A9", "bootstrap removes falco.yaml on 26.04 again", "bootstrap",
         texts["bootstrap"].replace("# The chart the root Application points at.",
                                    '[ "${VERSION_ID:-}" = "26.04" ] && rm -f charts/narwhal-apps/templates/falco.yaml\n\n# The chart the root Application points at.', 1)),
        # A10
        ("A10", "chart source row removed", "sources",
         mutate_line(texts["sources"], CHART_SOURCE, "")),
        ("A10", "VERSIONS.md Falco chart drifts", "versions",
         texts["versions"].replace("| Falco | v0.45.1 (chart 9.2.1) |", "| Falco | v0.45.1 (chart 9.2.0) |", 1)),
        ("A10", "VERSIONS.md Falcosidekick drifts", "versions",
         texts["versions"].replace("| Falcosidekick | 2.35.0 (subchart", "| Falcosidekick | 2.32.0 (subchart", 1)),
        # A11
        ("A11", "exception override replaces instead of appends", "app",
         mutate_block(app, noise, "exceptions: append", "exceptions: replace")),
        ("A11", "exception loses its override key", "app",
         mutate_block(app, noise, noise_override, "")),
        ("A11", "rule name case drifts from the bundled rule", "app",
         mutate_block(app, noise, "Contact K8S API Server From Container", "Contact K8s API Server From Container")),
        ("A11", "exception fields lose the namespace", "app",
         mutate_block(app, noise, "fields: [k8s.ns.name, container.image.repository]", "fields: [container.image.repository]")),
        ("A11", "exception comps become a prefix match", "app",
         mutate_block(app, noise, "comps: [=, =]", "comps: [=, startswith]")),
        ("A11", "an image-wide glob value", "app",
         mutate_block(app, noise, "[storage, docker.io/otwld/velero-ui]", "[storage, docker.io/otwld/*]")),
        ("A11", "a namespace-wide glob value", "app",
         mutate_block(app, noise, "[iam, quay.io/keycloak/keycloak-operator]", '["*", quay.io/keycloak/keycloak-operator]')),
        ("A11", "an empty repository value", "app",
         mutate_block(app, noise, "[platform-system, reg.kyverno.io/kyverno/kyverno]", '[platform-system, ""]')),
        ("A11", "a repository without its registry", "app",
         mutate_block(app, noise, "[devtools, ghcr.io/headlamp-k8s/headlamp]", "[devtools, headlamp-k8s/headlamp]")),
        ("A11", "an extra pair widens the exception", "app",
         mutate_block(app, noise, noise_last_pair, noise_last_pair + noise_pair_indent + "- [default, docker.io/library/nginx]\n")),
        ("A11", "a reviewed pair is dropped", "app",
         mutate_block(app, noise, noise_pair_indent + noise_last_pair.lstrip(" "), "")),
        ("A11", "a pair is listed twice", "app",
         mutate_block(app, noise, noise_last_pair, noise_last_pair + noise_last_pair)),
        ("A11", "a second item overrides the bundled macro", "app",
         mutate_block(app, noise, noise_override,
                      noise_override + "            - macro: user_known_contact_k8s_api_server_activities\n"
                      '              condition: (k8s.ns.name startswith "")\n'
                      "              override:\n                condition: replace\n")),
        ("A11", "exception file renamed", "app",
         mutate_block(app, noise, "narwhal-api-noise.yaml: |-", "other-rules.yaml: |-")),
        ("A11", "customRules removed", "app", delete_block(app, noise)),
        ("A11", "falco.priority raised to warning", "app",
         mutate_block(app, "        falco:", "priority: notice", "priority: warning")),
        ("A11", "rules.d dropped from rules_files", "app",
         mutate_block(app, "          rules_files:", "            - /etc/falco/rules.d\n", "")),
        ("A11", "rules.d loaded before the bundled rules", "app",
         mutate_block(app, "          rules_files:",
                      "            - /etc/falco/falco_rules.yaml\n            - /etc/falco/falco_rules.local.yaml\n            - /etc/falco/rules.d\n",
                      "            - /etc/falco/rules.d\n            - /etc/falco/falco_rules.yaml\n            - /etc/falco/falco_rules.local.yaml\n")),
        ("A11", "falco image bumped without re-validating the exception", "app",
         mutate_block(app, "        image:", 'tag: "0.45.1"', 'tag: "0.45.2"')),
    ]


def main(argv):
    texts = {key: path.read_text() for key, path in FILES.items()}
    found = problems(texts)
    for line in found:
        print(f"FAIL {line}")
    if found:
        return 1
    print("OK Falco 0.45.1 + falcosidekick 2.35.0 / chart 9.2.1: rendered Application, pins agree across "
          "falco.yaml, images.txt and image-digests.tsv, modern_ebpf, no runtime pull, CRI-only container "
          "metadata, JSON Loki + source:falco Alertmanager on the v2 API, portal seam (priority casing, runtime "
          "tab not gated on the node OS), Kyverno exclusion and the appended API-server exception (8 exact "
          "pairs, priority floor unchanged) hold")
    if "--mutation-verify" not in argv:
        return 0
    cases = mutants(texts)
    for expected, name, key, mutated in cases:
        if mutated == texts[key]:
            print(f"FAIL mutation '{name}' did not apply (anchor drifted)")
            return 1
        caught = problems({**texts, key: mutated})
        if not any(p.startswith(f"[{expected}]") for p in caught):
            print(f"FAIL mutation '{name}' was not caught by [{expected}] (caught: {caught or 'nothing'})")
            return 1
        print(f"OK mutation caught by {expected}: {name}")
    print(f"OK {len(cases)} mutations caught")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
