#!/usr/bin/env python3
"""R235: admission self-lockout safety — Fail webhooks must exclude own namespace and kube-system.

Every repo-owned webhook whose failurePolicy is Fail must exclude its own
namespace and kube-system via namespaceSelector (or objectSelector), or carry
an explicit documented waiver in the WAIVERS table with reason.

Reports matchPolicy, sideEffects, and admissionReviewVersions presence for
each entry.  Helm template values ({{ }}) are WARN, not silent skips.
"""

import argparse
import re
import sys
import tempfile
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
WEBHOOK_KINDS = {"MutatingWebhookConfiguration", "ValidatingWebhookConfiguration"}
WEBHOOK_KIND_RE = re.compile(
    r"(?m)^kind:\s*(MutatingWebhookConfiguration|ValidatingWebhookConfiguration)\s*$"
)

# Fields whose presence we report for each webhook entry.
PRESENCE_FIELDS = ("matchPolicy", "sideEffects", "admissionReviewVersions")
PRESENCE_RE = {
    field: re.compile(rf"(?m)^[ \t]+{field}:[ \t]*(.*?)[ \t]*(?:#.*)?$")
    for field in PRESENCE_FIELDS
}
FAILURE_POLICY_RE = re.compile(r"(?m)^[ \t]+failurePolicy:[ \t]*(.*?)[ \t]*(?:#.*)?$")
NS_SELECTOR_RE = re.compile(r"(?m)^[ \t]+namespaceSelector:")
OBJ_SELECTOR_RE = re.compile(r"(?m)^[ \t]+objectSelector:")
NON_PRODUCTION = {
    "gitops/charts/kubernetes-dashboard/charts/kong/": "Kong dashboard subchart is disabled; docs/common/admission-webhook-continuity.md §1",
    "gitops/charts/kubernetes-dashboard/charts/ingress-nginx/": "ingress-nginx dashboard subchart is disabled; docs/common/admission-webhook-continuity.md §1",
}

# Application YAML → (component-name, deployed namespace).
# These files are ArgoCD Application manifests that do not directly contain
# WebhookConfiguration, but their charts generate them at runtime.
# D1: We cannot determine rendered webhook namespace selectors from Application
# manifests alone.  They are WARN undecidable, never silently skipped.
APP_NAMESPACE_MAP = {
    "kyverno.yaml": ("kyverno", "platform-system"),
    "cert-manager.yaml": ("cert-manager", "platform-system"),
    "istiod.yaml": ("istiod", "istio-system"),
    "istio-base.yaml": ("istio-base", "istio-system"),
    "chaos-mesh.yaml": ("chaos-mesh", "chaos-testing"),
    "openbao.yaml": ("openbao", "storage"),
}

# Waivers: (source-file-basename, webhook-entry-name-pattern) → reason.
# Self-lockout exclusion is waived for webhooks whose rules target only their
# own API groups and therefore cannot lock out control-plane components.
# D2: cert-manager webhooks only intercept cert-manager.io and acme.cert-manager.io
# resources; kube-system and the webhook's own namespace never submit those kinds
# organically, so namespace exclusion is unnecessary.  The waiver is documented
# here rather than silently assumed.
WAIVERS: dict[tuple[str, str], str] = {
    ("webhook-validating-webhook.yaml", "webhook.cert-manager.io"):
        "only intercepts cert-manager.io/acme.cert-manager.io resources; "
        "kube-system/own-namespace never submit those kinds",
    ("webhook-mutating-webhook.yaml", "webhook.cert-manager.io"):
        "only intercepts cert-manager.io CertificateRequests; "
        "kube-system/own-namespace never submit those kinds",
}


def _extract_selector_block(block: str, selector_re: re.Pattern) -> str:
    """Extract the YAML block under a selector key from a webhook entry."""
    m = selector_re.search(block)
    if not m:
        return ""
    lines = block[m.end():].split("\n")
    result = [block[m.start():m.end()]]
    indent = None
    for line in lines:
        stripped = line.lstrip()
        if not stripped:
            continue
        current_indent = len(line) - len(stripped)
        if indent is None:
            indent = current_indent
        if current_indent < indent:
            break
        result.append(line)
    return "\n".join(result)


def _selector_excludes(selector_text: str, namespace: str) -> bool:
    """Check whether a selector block plausibly excludes the given namespace.

    Only a namespace-name NotIn expression listing the namespace is accepted.
    Templated values are undecidable — return False so caller can WARN.
    """
    if not selector_text:
        return False
    if "{{" in selector_text:
        return False  # Helm template — undecidable
    # Parse each expression independently so key/operator/values cannot bleed
    # across adjacent expressions. Only namespace-name NotIn proves exclusion.
    expressions = re.findall(
        r"(?ms)^\s*-\s*key:\s*(.*?)\s*\n(.*?)(?=^\s*-\s*key:|\Z)",
        selector_text,
    )
    for key, body in expressions:
        operator = re.search(r"(?m)^\s*operator:\s*(\S+)", body)
        values = re.findall(r"(?m)^\s*-\s*[\"']?([^\s\"']+)", body.split("values:", 1)[-1]) if "values:" in body else []
        if (key.strip(" \t\"'") == "kubernetes.io/metadata.name"
                and operator and operator.group(1) == "NotIn"
                and namespace in values):
            return True
    return False


def _infer_own_namespace(source: str, block: str) -> str | None:
    """Infer which namespace the webhook backend lives in.

    For Helm chart templates, look for clientConfig.service.namespace.
    For narwhal-apps Application templates, use APP_NAMESPACE_MAP.
    """
    basename = Path(source).name
    if basename in APP_NAMESPACE_MAP:
        return APP_NAMESPACE_MAP[basename][1]
    # Try to find clientConfig.service.namespace in the block
    m = re.search(r"(?m)^\s+namespace:\s*(.+)$", block)
    if m:
        value = m.group(1).strip()
        if "{{" not in value:
            return value
    return None


def _is_waiver_candidate(source: str, entry_name: str) -> str | None:
    """Return the documented reason for a cert-manager waiver candidate."""
    basename = Path(source).name
    for (waiver_file, waiver_name_pattern), reason in WAIVERS.items():
        if basename == waiver_file and waiver_name_pattern in entry_name:
            return reason
    return None


def _waiver_for_block(source: str, entry_name: str, block: str) -> str | None:
    reason = _is_waiver_candidate(source, entry_name)
    if not reason:
        return None
    lines = block.splitlines()
    rules_start = next((i for i, line in enumerate(lines) if re.match(r"^[ \t]*rules:\s*$", line)), None)
    if rules_start is None:
        return None
    rules_indent = len(lines[rules_start]) - len(lines[rules_start].lstrip())
    rules_lines = [lines[rules_start]]
    for line in lines[rules_start + 1:]:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if stripped and indent <= rules_indent and re.match(r"^[^\-\s][^:]*:", stripped):
            break
        rules_lines.append(line)
    rules_text = "\n".join(rules_lines)
    if "{{" in rules_text:
        return None
    rule_blocks = re.split(r"(?m)^\s*-\s+apiGroups:", "\n".join(rules_lines[1:]))
    if len(rule_blocks) < 2:
        return None
    api_group_indent = re.search(r"(?m)^(\s*)-\s+apiGroups:", "\n".join(rules_lines[1:]))
    if not api_group_indent:
        return None
    rule_indent = len(api_group_indent.group(1))
    rule_items = re.findall(rf"(?m)^ {{{rule_indent}}}-\s+", "\n".join(rules_lines[1:]))
    if len(rule_items) != len(rule_blocks) - 1:
        return None
    allowed = {"cert-manager.io", "acme.cert-manager.io"}
    for rule in rule_blocks[1:]:
        group_section = rule.split("apiVersions:", 1)[0]
        groups = re.findall(r"(?m)^\s*-\s*[\"']?([^\s\"']+)", group_section)
        if not groups or any(group not in allowed for group in groups):
            return None
    return reason


def _check_presence(block: str, source: str, label: str) -> list[str]:
    """Report presence/absence of matchPolicy, sideEffects, admissionReviewVersions."""
    info: list[str] = []
    for field, regex in PRESENCE_RE.items():
        matches = regex.findall(block)
        if matches:
            value = matches[0].strip()
            if "{{" in value:
                info.append(f"  {source}: {label} {field} is templated ({value})")
            else:
                info.append(f"  {source}: {label} {field}={value}")
        else:
            info.append(f"  {source}: {label} {field} MISSING")
    return info


def check_template(text: str, source: str) -> tuple[list[str], list[str], list[str], int, int]:
    """Check Helm template files at text level (same approach as R226)."""
    errors: list[str] = []
    warnings: list[str] = []
    info: list[str] = []
    configs = 0
    entries = 0
    matches = list(WEBHOOK_KIND_RE.finditer(text))
    for resource_index, match in enumerate(matches):
        configs += 1
        end = matches[resource_index + 1].start() if resource_index + 1 < len(matches) else len(text)
        resource = text[match.start():end]
        header = resource.split("webhooks:", 1)[0]
        metadata_name = re.search(r"(?m)^[ \t]+name:[ \t]*(.+)$", header)
        config_name = metadata_name.group(1).strip() if metadata_name else "<templated>"
        list_match = re.search(r"(?m)^([ \t]*)webhooks:[ \t]*$", resource)
        if not list_match:
            continue
        section = resource[list_match.end():]
        first_item = re.search(r"(?m)^([ \t]*)-[ \t]+", section)
        item_matches = []
        if first_item:
            item_indent = len(first_item.group(1))
            item_matches = list(re.finditer(rf"(?m)^ {{{item_indent}}}-\s+", section))
        if not item_matches:
            continue
        for index, item in enumerate(item_matches):
            entries += 1
            item_end = item_matches[index + 1].start() if index + 1 < len(item_matches) else len(section)
            block = section[item.start():item_end]
            # Match both "  - name: X" (list item start) and "    name: X" (nested field),
            # but prefer the first match which is the webhook entry name.
            item_name_m = re.search(r"(?m)^[ \t]*-?\s*name:[ \t]*(.+)$", block)
            label = item_name_m.group(1).strip() if item_name_m else f"index {index}"
            info.extend(_check_presence(block, source, label))
            # Only check self-lockout for failurePolicy: Fail entries
            fp_matches = FAILURE_POLICY_RE.findall(block)
            if not fp_matches:
                continue
            fp_value = fp_matches[0].strip().strip("\"'")
            if "{{" in fp_value:
                warnings.append(
                    f"{source}: {label} failurePolicy is templated ({fp_value}); "
                    f"self-lockout safety undecidable"
                )
                continue
            if fp_value != "Fail":
                continue
            # failurePolicy: Fail — check namespace exclusions
            waiver = _waiver_for_block(source, label, block)
            if waiver:
                info.append(f"  {source}: {label} WAIVED: {waiver}")
                continue
            own_ns = _infer_own_namespace(source, resource)
            ns_selector = _extract_selector_block(block, NS_SELECTOR_RE)
            obj_selector = _extract_selector_block(block, OBJ_SELECTOR_RE)
            if not ns_selector and not obj_selector:
                if "{{" in block and ("namespaceSelector" in block or "objectSelector" in block):
                    warnings.append(
                        f"{source}: {label} selector block is entirely templated; "
                        f"self-lockout safety undecidable"
                    )
                else:
                    errors.append(
                        f"{source}: {label} failurePolicy=Fail but has no "
                        f"namespaceSelector or objectSelector to prevent self-lockout"
                    )
                continue
            # Check kube-system exclusion
            excludes_kube_system = _selector_excludes(ns_selector, "kube-system")
            if not excludes_kube_system:
                if "{{" in (ns_selector + obj_selector):
                    warnings.append(
                        f"{source}: {label} selector contains Helm template; "
                        f"kube-system exclusion undecidable"
                    )
                else:
                    errors.append(
                        f"{source}: {label} failurePolicy=Fail but kube-system "
                        f"is not excluded by namespaceSelector"
                    )
            # Check own-namespace exclusion
            if own_ns:
                excludes_own = _selector_excludes(ns_selector, own_ns)
                if not excludes_own:
                    if "{{" in (ns_selector + obj_selector):
                        warnings.append(
                            f"{source}: {label} selector contains Helm template; "
                            f"own-namespace ({own_ns}) exclusion undecidable"
                        )
                    else:
                        errors.append(
                            f"{source}: {label} failurePolicy=Fail but own namespace "
                            f"({own_ns}) is not excluded by namespaceSelector"
                        )
            else:
                warnings.append(
                    f"{source}: {label} own namespace cannot be determined; "
                    f"self-lockout safety partially undecidable"
                )
    return errors, warnings, info, configs, entries


def check_yaml(text: str, source: str) -> tuple[list[str], list[str], list[str], int, int]:
    """Check plain YAML files (no Helm templates)."""
    errors: list[str] = []
    warnings: list[str] = []
    info: list[str] = []
    configs = 0
    entries = 0
    try:
        for document in yaml.safe_load_all(text):
            if not isinstance(document, dict) or document.get("kind") not in WEBHOOK_KINDS:
                continue
            configs += 1
            webhooks = document.get("webhooks")
            if not isinstance(webhooks, list) or not webhooks:
                continue
            for index, webhook in enumerate(webhooks):
                entries += 1
                if not isinstance(webhook, dict):
                    continue
                name = str(webhook.get("name", f"index {index}"))
                # Presence report
                for field in PRESENCE_FIELDS:
                    value = webhook.get(field)
                    if value is not None:
                        info.append(f"  {source}: {name} {field}={value}")
                    else:
                        info.append(f"  {source}: {name} {field} MISSING")
                fp = webhook.get("failurePolicy")
                if fp != "Fail":
                    continue
                waiver = _waiver_for_block(source, name, yaml.safe_dump(webhook, sort_keys=False))
                if waiver:
                    info.append(f"  {source}: {name} WAIVED: {waiver}")
                    continue
                ns_selector = webhook.get("namespaceSelector")
                obj_selector = webhook.get("objectSelector")
                if not ns_selector and not obj_selector:
                    errors.append(
                        f"{source}: {name} failurePolicy=Fail but has no "
                        f"namespaceSelector or objectSelector to prevent self-lockout"
                    )
                    continue
                # Check exclusions in structured selectors
                def _structured_excludes(selector: dict | None, ns: str) -> bool:
                    if not selector or not isinstance(selector, dict):
                        return False
                    for expr in selector.get("matchExpressions", []):
                        if (
                            expr.get("key") == "kubernetes.io/metadata.name"
                            and expr.get("operator") == "NotIn"
                            and ns in expr.get("values", [])
                        ):
                            return True
                    return False

                if not _structured_excludes(ns_selector, "kube-system"):
                    errors.append(
                        f"{source}: {name} failurePolicy=Fail but kube-system "
                        f"is not excluded by namespaceSelector"
                    )
                # Own namespace — try clientConfig.service.namespace
                own_ns = None
                cc = webhook.get("clientConfig", {})
                svc = cc.get("service", {}) if isinstance(cc, dict) else {}
                if isinstance(svc, dict) and svc.get("namespace"):
                    own_ns = svc["namespace"]
                if own_ns:
                    if not _structured_excludes(ns_selector, own_ns):
                        errors.append(
                            f"{source}: {name} failurePolicy=Fail but own namespace "
                            f"({own_ns}) is not excluded by namespaceSelector"
                        )
    except yaml.YAMLError as exc:
        errors.append(f"{source}: YAML parse error (not skipped): {exc}")
    return errors, warnings, info, configs, entries


def check_application(source: str) -> list[str]:
    """Produce WARN for Application manifests whose webhook selectors are undecidable."""
    basename = Path(source).name
    if basename not in APP_NAMESPACE_MAP:
        return []
    component, ns = APP_NAMESPACE_MAP[basename]
    return [
        f"{source}: {component} webhook namespaceSelector is chart-generated; "
        f"self-lockout safety for namespace {ns} undecidable from Application manifest alone"
    ]


def check_paths(paths: list[Path]) -> tuple[list[str], list[str], list[str], int, int]:
    errors: list[str] = []
    warnings: list[str] = []
    info: list[str] = []
    configs = 0
    entries = 0
    nonprod_configs = 0
    nonprod_entries = 0
    for path in paths:
        source = str(path)
        try:
            text = path.read_text()
        except OSError as exc:
            errors.append(f"{source}: cannot read input: {exc}")
            continue
        if WEBHOOK_KIND_RE.search(text) and "{{" in text and "/templates/" in source:
            e, w, i, c, n = check_template(text, source)
            errors.extend(e)
            is_nonprod = any(marker in source for marker in NON_PRODUCTION)
            if is_nonprod:
                nonprod_configs += c
                nonprod_entries += n
            else:
                warnings.extend(w)
            info.extend(i)
            configs += c
            entries += n
            continue
        if "/narwhal-apps/templates/" in source and Path(source).name in APP_NAMESPACE_MAP:
            warnings.extend(check_application(source))
            continue
        if "WebhookConfiguration" not in text or "kind: WebhookConfiguration" in text:
            continue
        if not WEBHOOK_KIND_RE.search(text):
            continue
        e, w, i, c, n = check_yaml(text, source)
        errors.extend(e)
        warnings.extend(w)
        info.extend(i)
        configs += c
        entries += n
    if configs == 0 or entries == 0:
        errors.append(
            f"evaluated zero webhook configurations or entries "
            f"(configurations={configs}, entries={entries})"
        )
    if nonprod_configs or nonprod_entries:
        info.append(
            "  NON_PRODUCTION dashboard subcharts ("
            + "; ".join(dict.fromkeys(NON_PRODUCTION.values()))
            + f"): evaluated configurations={nonprod_configs}, entries={nonprod_entries}; warnings summarized"
        )
    return errors, warnings, info, configs, entries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--mutation-verify", action="store_true")
    args = parser.parse_args()
    paths = args.paths or sorted((ROOT / "gitops").rglob("*.yaml")) + sorted(
        (ROOT / "gitops").rglob("*.yml")
    )
    errors, warnings, info, configs, entries = check_paths(paths)
    for line in info:
        print(line)
    for warning in warnings:
        print(f"WARN: {warning}")
    if errors:
        print("\n".join(errors))
        print(f"FAIL evaluated webhook configurations={configs}, entries={entries}")
        return 1
    if args.mutation_verify:
        result = _mutation_verify()
        if result != 0:
            return result
    print(f"PASS evaluated webhook configurations={configs}, entries={entries}; scanned YAML files={len(paths)}")
    return 0


def _mutation_verify() -> int:
    """In-memory mutation test: a Fail webhook without selectors must be caught."""
    with tempfile.TemporaryDirectory() as tmp:
        fixture = Path(tmp) / "gitops" / "resources"
        fixture.mkdir(parents=True)
        # Good fixture: Fail with kube-system + own-namespace excluded
        good = fixture / "selflock-good.yaml"
        good.write_text(
            "apiVersion: admissionregistration.k8s.io/v1\n"
            "kind: ValidatingWebhookConfiguration\n"
            "metadata:\n"
            "  name: good-webhook\n"
            "webhooks:\n"
            "  - name: good.example.test\n"
            "    failurePolicy: Fail\n"
            "    matchPolicy: Equivalent\n"
            "    sideEffects: None\n"
            "    admissionReviewVersions: [\"v1\"]\n"
            "    namespaceSelector:\n"
            "      matchExpressions:\n"
            "        - key: kubernetes.io/metadata.name\n"
            "          operator: NotIn\n"
            "          values:\n"
            "            - kube-system\n"
            "            - my-namespace\n"
            "    clientConfig:\n"
            "      service:\n"
            "        name: my-webhook\n"
            "        namespace: my-namespace\n"
        )
        # Should pass
        e, w, i, c, n = check_paths([good])
        if e:
            print(f"mutation check: good fixture unexpectedly failed: {e}", file=sys.stderr)
            return 1

        # Bad fixture: Fail with NO selectors
        bad = fixture / "selflock-bad.yaml"
        bad.write_text(
            "apiVersion: admissionregistration.k8s.io/v1\n"
            "kind: ValidatingWebhookConfiguration\n"
            "metadata:\n"
            "  name: bad-webhook\n"
            "webhooks:\n"
            "  - name: bad.example.test\n"
            "    failurePolicy: Fail\n"
            "    matchPolicy: Equivalent\n"
            "    sideEffects: None\n"
            "    admissionReviewVersions: [\"v1\"]\n"
        )
        e2, w2, i2, c2, n2 = check_paths([bad])
        if not e2:
            print(
                "mutation check: bad fixture (Fail without selectors) was not caught",
                file=sys.stderr,
            )
            return 1

        # Bad fixture 2: Fail with selector but kube-system NOT excluded
        bad2 = fixture / "selflock-bad2.yaml"
        bad2.write_text(
            "apiVersion: admissionregistration.k8s.io/v1\n"
            "kind: ValidatingWebhookConfiguration\n"
            "metadata:\n"
            "  name: bad-webhook-2\n"
            "webhooks:\n"
            "  - name: bad2.example.test\n"
            "    failurePolicy: Fail\n"
            "    matchPolicy: Equivalent\n"
            "    sideEffects: None\n"
            "    admissionReviewVersions: [\"v1\"]\n"
            "    namespaceSelector:\n"
            "      matchExpressions:\n"
            "        - key: kubernetes.io/metadata.name\n"
            "          operator: NotIn\n"
            "          values:\n"
            "            - some-other-ns\n"
            "    clientConfig:\n"
            "      service:\n"
            "        name: my-webhook\n"
            "        namespace: my-namespace\n"
        )
        e3, w3, i3, c3, n3 = check_paths([bad2])
        if not e3:
            print(
                "mutation check: bad fixture (kube-system not excluded) was not caught",
                file=sys.stderr,
            )
            return 1
        # Verify the errors mention the right things
        has_kube = any("kube-system" in err for err in e3)
        has_own = any("my-namespace" in err for err in e3)
        if not has_kube or not has_own:
            print(
                f"mutation check: expected errors about kube-system and my-namespace; got: {e3}",
                file=sys.stderr,
            )
            return 1

        # Selector mutations: wrong key, wrong operator, and missing kube-system.
        valid_selector = (
            "namespaceSelector:\n      matchExpressions:\n"
            "        - key: kubernetes.io/metadata.name\n"
            "          operator: NotIn\n          values:\n"
            "            - kube-system\n            - my-namespace\n"
        )
        for label, mutant in (
            ("wrong key", valid_selector.replace("kubernetes.io/metadata.name", "unrelated.example/key")),
            ("operator In", valid_selector.replace("operator: NotIn", "operator: In")),
            ("missing kube-system", valid_selector.replace("            - kube-system\n", "")),
        ):
            candidate = fixture / f"selflock-{label.replace(' ', '-')}.yaml"
            candidate.write_text(
                "apiVersion: admissionregistration.k8s.io/v1\n"
                "kind: ValidatingWebhookConfiguration\nmetadata:\n  name: mutant\n"
                "webhooks:\n  - name: mutant.example.test\n    failurePolicy: Fail\n"
                "    clientConfig:\n      service:\n        namespace: my-namespace\n"
                "    " + mutant.replace("\n", "\n    ")
            )
            em, _, _, _, _ = check_paths([candidate])
            if not em:
                print(f"mutation check: selector {label} was not caught", file=sys.stderr)
                return 1

        # Exercise the Helm-template text parser directly (the original F1 path).
        for label, key, operator, values in (
            ("wrong key template", "unrelated.example/key", "NotIn", "kube-system\n            - my-namespace"),
            ("wrong operator template", "kubernetes.io/metadata.name", "In", "kube-system\n            - my-namespace"),
            ("missing kube-system template", "kubernetes.io/metadata.name", "NotIn", "my-namespace"),
        ):
            template = (
                "kind: ValidatingWebhookConfiguration\nmetadata:\n  name: bad\n"
                "webhooks:\n  - name: bad.example.test\n    failurePolicy: Fail\n"
                "    namespaceSelector:\n      matchExpressions:\n"
                f"        - key: {key}\n          operator: {operator}\n"
                f"          values:\n            - {values}\n"
                "    clientConfig:\n      service:\n        namespace: my-namespace\n"
            )
            et, _, _, _, _ = check_template(template, "fixture/templates/webhook.yaml")
            if not et:
                print(f"mutation check: template selector {label} was not caught", file=sys.stderr)
                return 1

        # Widen cert-manager's statically confined rules with an unrelated group.
        cm = fixture / "webhook-validating-webhook.yaml"
        cm.write_text(
            "kind: ValidatingWebhookConfiguration\nmetadata:\n  name: cm\nwebhooks:\n"
            "  - name: webhook.cert-manager.io\n    failurePolicy: Fail\n"
            "    rules:\n      - apiGroups:\n          - cert-manager.io\n"
            "          - apps\n        apiVersions: [v1]\n        operations: [CREATE]\n"
            "        resources: [deployments]\n"
            "    clientConfig:\n      service:\n        namespace: cm\n"
        )
        ec, _, _, _, _ = check_paths([cm])
        if not ec:
            print("mutation check: widened cert-manager waiver was not rejected", file=sys.stderr)
            return 1

    print("PASS: mutation-verify detected missing selectors, key/operator/value mutations, and widened waiver")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
