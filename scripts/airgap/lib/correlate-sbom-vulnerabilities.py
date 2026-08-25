#!/usr/bin/env python3
"""Correlate CycloneDX SBOM components with Trivy vulnerability findings (narwhal#53).

Links artifact digest -> SBOM component identity -> source commit SHA -> CI workflow run ID -> CVE findings.
Validates output against schemas/sbom-correlation-1.0.schema.json.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import sys
from typing import Any

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
DEFAULT_SCHEMA = REPOSITORY_ROOT / "schemas" / "sbom-correlation-1.0.schema.json"


def load_json(path: pathlib.Path, label: str) -> Any:
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"ERROR: {label} file not found: {path}", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as exc:
        print(f"ERROR: {label} is invalid JSON ({path}): {exc.msg}", file=sys.stderr)
        sys.exit(2)


def get_property(obj: dict, prop_name: str) -> str | None:
    for prop in obj.get("properties", []):
        if isinstance(prop, dict) and prop.get("name") == prop_name:
            return prop.get("value")
    return None


def extract_digest_from_component(comp: dict) -> str | None:
    for h in comp.get("hashes", []):
        if h.get("alg") in ("SHA-256", "sha256") and h.get("content"):
            content = h["content"]
            if not content.startswith("sha256:"):
                return f"sha256:{content}"
            return content
    purl = comp.get("purl", "")
    if "digest=sha256:" in purl:
        digest_part = purl.split("digest=sha256:")[-1].split("&")[0]
        return f"sha256:{digest_part}"
    return None


def extract_trivy_vulnerabilities(trivy_doc: Any) -> list[dict[str, Any]]:
    """Parse Trivy v2 JSON output report(s) into flat vulnerability records."""
    findings = []
    reports = trivy_doc if isinstance(trivy_doc, list) else [trivy_doc]

    for report in reports:
        if not isinstance(report, dict):
            continue

        artifact_name = report.get("ArtifactName", "")
        metadata = report.get("Metadata") or {}
        repo_digests = metadata.get("RepoDigests") or []
        image_id = metadata.get("ImageID", "")

        results = report.get("Results") or []
        for res in results:
            target = res.get("Target", "")
            vulns = res.get("Vulnerabilities") or []
            for v in vulns:
                findings.append({
                    "artifact_name": artifact_name,
                    "target": target,
                    "image_id": image_id,
                    "repo_digests": repo_digests,
                    "vulnerability_id": v.get("VulnerabilityID", "UNKNOWN"),
                    "severity": v.get("Severity", "UNKNOWN").upper(),
                    "pkg_name": v.get("PkgName") or v.get("PkgID", "unknown"),
                    "installed_version": v.get("InstalledVersion", ""),
                    "fixed_version": v.get("FixedVersion"),
                    "primary_url": v.get("PrimaryURL"),
                    "title": v.get("Title") or v.get("Description"),
                })
    return findings


def match_vuln_to_component(vuln: dict, comp: dict) -> bool:
    name = comp.get("name", "")
    version = comp.get("version", "")
    purl = comp.get("purl", "")
    comp_digest = extract_digest_from_component(comp)

    art_name = vuln.get("artifact_name", "")
    target = vuln.get("target", "")
    pkg_name = vuln.get("pkg_name", "")

    # Direct match on container image name or purl
    if art_name and (art_name == name or art_name == f"{name}:{version}" or name in art_name):
        return True
    if target and (name in target or art_name in target):
        return True

    # Match by repo digest
    if comp_digest:
        for rd in vuln.get("repo_digests", []):
            if comp_digest in rd:
                return True
        if vuln.get("image_id") and comp_digest in vuln["image_id"]:
            return True

    # Match by OS / library package name
    if pkg_name and (pkg_name == name or name == f"pkg:deb/ubuntu/{pkg_name}"):
        return True

    return False


def validate_correlation_schema(doc: dict, schema: dict) -> list[str]:
    errors = []
    try:
        from jsonschema import Draft202012Validator, FormatChecker
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        for err in validator.iter_errors(doc):
            path_str = "$" + "".join(f"[{item}]" if isinstance(item, int) else f".{item}" for item in err.absolute_path)
            errors.append(f"Schema violation at {path_str}: {err.message}")
    except ImportError:
        # Fallback structural validation
        for req in schema.get("required", []):
            if req not in doc:
                errors.append(f"Missing required top-level field: '{req}'")
        if doc.get("schema_version") != "1.0":
            errors.append(f"Invalid schema_version '{doc.get('schema_version')}', expected '1.0'")
        if not isinstance(doc.get("components"), list):
            errors.append("Field 'components' must be a list")
    return errors


def format_summary_report(corr_doc: dict) -> str:
    lines = []
    lines.append("=====================================================================")
    lines.append("        NARWHAL SBOM & VULNERABILITY CORRELATION REPORT")
    lines.append("=====================================================================")
    lines.append(f"Correlation ID : {corr_doc.get('correlation_id')}")
    lines.append(f"Generated At   : {corr_doc.get('generated_at')}")
    lines.append(f"Source Commit  : {corr_doc.get('source_commit_sha')}")
    lines.append(f"Workflow Run ID: {corr_doc.get('workflow_run_id') or '<not set (CI build-time field)>'}")
    lines.append(f"Bundle Arch    : {corr_doc.get('bundle_arch') or 'unknown'}")
    lines.append("---------------------------------------------------------------------")

    components = corr_doc.get("components", [])
    total_comps = len(components)
    comps_with_vulns = [c for c in components if c.get("vulnerabilities")]
    total_vulns = sum(len(c.get("vulnerabilities", [])) for c in components)

    lines.append(f"Summary: {total_comps} components evaluated, {len(comps_with_vulns)} linked to {total_vulns} CVE findings")
    lines.append("---------------------------------------------------------------------")

    if comps_with_vulns:
        lines.append("\n[LINKED VULNERABILITY FINDINGS BY SBOM COMPONENT]")
        for c in comps_with_vulns:
            digest_str = c.get("artifact_digest") or "no-digest"
            lines.append(f"\n* Component: {c['name']}:{c['version']} ({c.get('component_type', 'unknown')})")
            lines.append(f"  PURL:   {c['purl']}")
            lines.append(f"  Digest: {digest_str}")
            lines.append("  Vulnerabilities:")
            for v in c.get("vulnerabilities", []):
                fix_str = f" -> fix: {v['fixed_version']}" if v.get("fixed_version") else " (no fix)"
                lines.append(f"    - [{v['severity']}] {v['vulnerability_id']} in {v['pkg_name']} {v.get('installed_version', '')}{fix_str}")
    else:
        lines.append("\nNo vulnerability findings linked to the evaluated SBOM components.")

    lines.append("\n=====================================================================")
    return "\n".join(lines)


def correlate(
    sbom_path: pathlib.Path,
    trivy_path: pathlib.Path,
    schema_path: pathlib.Path,
    commit_flag: str | None = None,
    workflow_flag: str | None = None,
) -> dict[str, Any]:
    sbom_doc = load_json(sbom_path, "CycloneDX SBOM")
    trivy_doc = load_json(trivy_path, "Trivy report")
    schema_doc = load_json(schema_path, "Correlation schema")

    meta = sbom_doc.get("metadata") or {}
    meta_comp = meta.get("component") or {}
    arch = get_property(meta, "narwhal:architecture") or "amd64"

    commit_sha = (
        commit_flag
        or get_property(meta, "narwhal:commit_sha")
        or os.environ.get("SOURCE_COMMIT")
        or os.environ.get("GITHUB_SHA")
        or "unknown"
    )

    workflow_run_id = (
        workflow_flag
        or get_property(meta, "narwhal:workflow_run_id")
        or os.environ.get("WORKFLOW_RUN_ID")
        or os.environ.get("GITHUB_RUN_ID")
        or None
    )

    raw_findings = extract_trivy_vulnerabilities(trivy_doc)
    components_in = sbom_doc.get("components") or []
    correlated_components = []

    for comp in components_in:
        comp_name = comp.get("name", "")
        comp_version = comp.get("version", "")
        comp_type = comp.get("type")
        purl = comp.get("purl", "")
        digest = extract_digest_from_component(comp)
        comp_commit = get_property(comp, "narwhal:commit_sha") or commit_sha
        comp_run = get_property(comp, "narwhal:workflow_run_id") or workflow_run_id

        lic_str = None
        if comp.get("licenses"):
            lic_entry = comp["licenses"][0]
            lic_str = lic_entry.get("expression") or lic_entry.get("license", {}).get("id")

        matched_vulns = [v for v in raw_findings if match_vuln_to_component(v, comp)]
        formatted_vulns = []
        for v in matched_vulns:
            formatted_vulns.append({
                "vulnerability_id": v["vulnerability_id"],
                "severity": v["severity"],
                "pkg_name": v["pkg_name"],
                "installed_version": v["installed_version"],
                "fixed_version": v["fixed_version"],
                "primary_url": v["primary_url"],
                "title": v["title"],
            })

        correlated_components.append({
            "name": comp_name,
            "version": comp_version,
            "component_type": comp_type,
            "purl": purl,
            "artifact_digest": digest,
            "source_commit_sha": comp_commit,
            "workflow_run_id": comp_run,
            "license": lic_str,
            "vulnerabilities": formatted_vulns,
        })

    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    corr_id = f"corr-{commit_sha[:8] if commit_sha != 'unknown' else 'local'}-{arch}"

    correlation_doc = {
        "schema_version": "1.0",
        "correlation_id": corr_id,
        "generated_at": now_iso,
        "source_commit_sha": commit_sha,
        "workflow_run_id": workflow_run_id,
        "bundle_arch": arch,
        "sbom_ref": str(sbom_path),
        "components": correlated_components,
    }

    schema_errs = validate_correlation_schema(correlation_doc, schema_doc)
    if schema_errs:
        for err in schema_errs:
            print(f"ERROR: {err}", file=sys.stderr)
        print(f"FAIL: Correlation manifest failed schema validation", file=sys.stderr)
        sys.exit(1)

    return correlation_doc


def main() -> int:
    ap = argparse.ArgumentParser(description="Correlate SBOM components with Trivy vulnerability findings")
    ap.add_argument("--sbom", type=pathlib.Path, required=True, help="Path to CycloneDX SBOM JSON file")
    ap.add_argument("--trivy-report", type=pathlib.Path, required=True, help="Path to Trivy JSON report file")
    ap.add_argument("--schema", type=pathlib.Path, default=DEFAULT_SCHEMA, help="Path to sbom-correlation schema file")
    ap.add_argument("--output", type=pathlib.Path, help="Path to write correlation JSON manifest")
    ap.add_argument("--commit", help="Override source commit SHA")
    ap.add_argument("--workflow-run-id", help="Override CI workflow run ID")
    ap.add_argument("--json", action="store_true", help="Print correlation result as JSON to stdout")
    ap.add_argument("--strict", action="store_true", help="Fail with exit 1 if CRITICAL/HIGH vulnerabilities exist")

    args = ap.parse_args()

    corr_doc = correlate(
        sbom_path=args.sbom,
        trivy_path=args.trivy_report,
        schema_path=args.schema,
        commit_flag=args.commit,
        workflow_flag=args.workflow_run_id,
    )

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8") as f:
            json.dump(corr_doc, f, indent=2)
            f.write("\n")
        print(f"Wrote correlation manifest to {args.output}")

    if args.json:
        print(json.dumps(corr_doc, indent=2))
    else:
        print(format_summary_report(corr_doc))

    if args.strict:
        crit_or_high = [
            v
            for c in corr_doc.get("components", [])
            for v in c.get("vulnerabilities", [])
            if v.get("severity") in ("CRITICAL", "HIGH")
        ]
        if crit_or_high:
            print(f"FAIL: --strict enabled and {len(crit_or_high)} CRITICAL/HIGH vulnerability finding(s) detected", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
