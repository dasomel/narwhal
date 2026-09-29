#!/usr/bin/env python3
"""Check explicit webhook settings in YAML and Helm templates."""

import argparse
import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
WEBHOOK_KINDS = {"MutatingWebhookConfiguration", "ValidatingWebhookConfiguration"}
WEBHOOK_KIND_RE = re.compile(r"(?m)^kind:\s*(MutatingWebhookConfiguration|ValidatingWebhookConfiguration)\s*$")
FIELD_RE = {
    field: re.compile(rf"(?m)^[ \t]+{field}:[ \t]*(.*?)[ \t]*(?:#.*)?$")
    for field in ("failurePolicy", "timeoutSeconds")
}
APP_TEMPLATES = {
    "istiod.yaml": "Istio chart values do not declare generated webhook settings",
    "istio-base.yaml": "Istio chart values do not declare generated webhook settings",
    "chaos-mesh.yaml": "Application does not declare webhook policy or timeout values",
    "openbao.yaml": "Application does not declare webhook policy or timeout values",
    "cert-manager.yaml": "Application pins chart version but has no local production chart render",
}


def value_status(block: str, field: str, source: str, label: str) -> tuple[list[str], list[str]]:
    matches = FIELD_RE[field].findall(block)
    prefix = f"{source}: webhook {label}"
    if not matches:
        return [f"{prefix} has no {field}"], []
    value = matches[0].strip()
    if not value:
        return [f"{prefix} has empty {field}"], []
    if "{{" in value or "}}" in value:
        return [], [f"{prefix} {field} is templated ({value}); rendered value is undecidable"]
    if field == "failurePolicy" and value.strip('"\'') not in ("Fail", "Ignore"):
        return [f"{prefix} has invalid failurePolicy={value}"], []
    if field == "timeoutSeconds":
        try:
            timeout = int(value)
        except ValueError:
            return [f"{prefix} has non-integer timeoutSeconds={value}"], []
        if not 1 <= timeout <= 30:
            return [f"{prefix} timeoutSeconds={timeout} is outside 1..30"], []
    return [], []


def check_template(text: str, source: str) -> tuple[list[str], list[str], int, int]:
    errors: list[str] = []
    warnings: list[str] = []
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
            errors.append(f"{source}: {match.group(1)}/{config_name} has no webhooks list")
            continue
        section = resource[list_match.end():]
        first_item = re.search(r"(?m)^([ \t]*)-[ \t]+", section)
        item_matches = []
        if first_item:
            item_indent = len(first_item.group(1))
            item_matches = list(re.finditer(rf"(?m)^ {{{item_indent}}}-\s+", section))
        if not item_matches:
            errors.append(f"{source}: {match.group(1)}/{config_name} has no webhook entries")
            continue
        for index, item in enumerate(item_matches):
            entries += 1
            item_end = item_matches[index + 1].start() if index + 1 < len(item_matches) else len(section)
            block = section[item.start():item_end]
            item_name = re.search(r"(?m)^[ \t]+name:[ \t]*(.+)$", section[item.start():item_end])
            label = item_name.group(1).strip() if item_name else f"index {index}"
            for field in FIELD_RE:
                field_errors, field_warnings = value_status(block, field, source, label)
                errors.extend(field_errors)
                warnings.extend(field_warnings)
            # Cert-manager's vendored chart template is repo-controlled and the
            # continuity document classifies it Fail-closed. Other components
            # retain their per-webhook classification; presence is the contract.
            if "cert-manager" in source and "failurePolicy: Fail" not in block:
                errors.append(f"{source}: cert-manager webhook {label} must remain failurePolicy=Fail")
    return errors, warnings, configs, entries


def check_application(text: str, source: str) -> list[str]:
    reason = APP_TEMPLATES.get(Path(source).name)
    if not reason:
        return []
    return [f"WARN {source}: generated webhook values are undecidable: {reason}"]


def check_paths(paths: list[Path]) -> tuple[list[str], list[str], int, int]:
    errors: list[str] = []
    warnings: list[str] = []
    configs = 0
    entries = 0
    for path in paths:
        source = str(path)
        try:
            text = path.read_text()
        except OSError as exc:
            errors.append(f"{source}: cannot read input: {exc}")
            continue
        if WEBHOOK_KIND_RE.search(text) and "{{" in text and "/templates/" in source:
            found_errors, found_warnings, found_configs, found_entries = check_template(text, source)
            errors.extend(found_errors)
            warnings.extend(found_warnings)
            configs += found_configs
            entries += found_entries
            continue
        if "/narwhal-apps/templates/" in source and Path(source).name in APP_TEMPLATES:
            warnings.extend(check_application(text, source))
            continue
        if "WebhookConfiguration" not in text or "kind: WebhookConfiguration" in text:
            continue
        if not WEBHOOK_KIND_RE.search(text):
            continue
        try:
            for document in yaml.safe_load_all(text):
                if not isinstance(document, dict) or document.get("kind") not in WEBHOOK_KINDS:
                    continue
                configs += 1
                webhooks = document.get("webhooks")
                if not isinstance(webhooks, list) or not webhooks:
                    errors.append(f"{source}: {document['kind']} has no webhook entries")
                    continue
                for index, webhook in enumerate(webhooks):
                    entries += 1
                    if not isinstance(webhook, dict):
                        errors.append(f"{source}: webhook index {index} is not a mapping")
                        continue
                    name = webhook.get("name", f"index {index}")
                    block = "\n".join(f"  {key}: {value}" for key, value in webhook.items())
                    for field in FIELD_RE:
                        found_errors, found_warnings = value_status(block, field, source, str(name))
                        errors.extend(found_errors)
                        warnings.extend(found_warnings)
        except yaml.YAMLError as exc:
            errors.append(f"{source}: YAML parse error (not skipped): {exc}")
    if configs == 0 or entries == 0:
        errors.append(f"evaluated zero webhook configurations or entries (configurations={configs}, entries={entries})")
    return errors, warnings, configs, entries


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args()
    paths = args.paths or sorted((ROOT / "gitops").rglob("*.yaml")) + sorted((ROOT / "gitops").rglob("*.yml"))
    errors, warnings, configs, entries = check_paths(paths)
    for warning in warnings:
        print(warning)
    if errors:
        print("\n".join(errors))
        print(f"FAIL evaluated webhook configurations={configs}, entries={entries}")
        return 1
    print(f"PASS evaluated webhook configurations={configs}, entries={entries}; scanned YAML files={len(paths)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
