#!/usr/bin/env python3
"""R379: Portal image SBOM generation is isolated from cosign signing secrets and Kyverno matches https://cyclonedx.org/bom (narwhal#684 / #750).

Verifies:
1. .github/workflows/portal-docker-publish.yml `sbom` job is enabled for normal and `sign_digest`
   recovery runs, depends on `merge`, holds NO secrets, declares minimal read-only `permissions`,
   validates the digest fail-closed, runs SHA-pinned `aquasecurity/trivy-action` (cyclonedx),
   exports `sbom_sha256` as a job output, and uploads the SBOM + `.sha256` via SHA-pinned
   `actions/upload-artifact`.
2. `sign` job is enabled (never `if: false`), depends on `[merge, sbom]`, does not silently skip
   when `sbom` fails or is skipped (step 1 fails closed on `needs.sbom.result != 'success'`),
   contains ONLY allowlisted SHA-pinned actions (no trivy or third-party SBOM action), downloads
   the artifact, and behaviorally verifies before `cosign sign`/`cosign attest`:
   (a) SHA-256 against `needs.sbom.outputs.sbom_sha256` (never trusting an artifact `.sha256` file),
   (b) regular non-symlink file below the size ceiling,
   (c) single-document CycloneDX JSON describing `${IMAGE}@${DIGEST}` (`metadata.component.name`
       and `aquasecurity:trivy:RepoDigest`).
3. `sign` job attests `${IMAGE}@${DIGEST}` with `cosign attest --type cyclonedx` in both
   Sigstore bundle format and legacy `.att` format (`--new-bundle-format=false` +
   `--use-signing-config=false`) for Kyverno 1.18.x, fail-closed.
4. gitops/resources/kyverno-policies.yaml `verify-image-signatures` uses
   `predicateType: https://cyclonedx.org/bom` (`in_toto.PredicateCycloneDX`).
5. portal/docs/cosign-signing-setup.md documents `cosign verify-attestation`,
   `https://cyclonedx.org/bom`, isolated `sbom` -> `sign`, and accurately states that
   `10-verify-image-signatures.sh` only checks file existence.

--mutation-verify proves broken copies fail without touching tracked files.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = ROOT / ".github/workflows/portal-docker-publish.yml"
POLICY_PATH = ROOT / "gitops/resources/kyverno-policies.yaml"
DOC_PATH = ROOT / "portal/docs/cosign-signing-setup.md"

PINNED_ACTION_RE = re.compile(r"^([^@\s]+)@([0-9a-f]{40})$")
SECRETS_REF_RE = re.compile(r"\bsecrets\.|COSIGN_PRIVATE_KEY|COSIGN_PASSWORD", re.IGNORECASE)
FORBIDDEN_SIGN_SBOM_CMD_RE = re.compile(r"(?:^|[;&|(`]\s*)(?:sudo\s+)?(trivy|syft|cyclonedx-bom|cyclonedx-cli)\b")
EXPECTED_DIGEST_EXPR = "${{ inputs.sign_digest || needs.merge.outputs.digest }}"
EXPECTED_SCAN_REF = f"${{{{ env.IMAGE }}}}@{EXPECTED_DIGEST_EXPR}"
EXPECTED_PREDICATE_TYPE = "https://cyclonedx.org/bom"
ALLOWED_SIGN_ACTIONS = frozenset({
    "actions/checkout",
    "actions/download-artifact",
    "docker/login-action",
    "sigstore/cosign-installer",
})
ALLOWED_SBOM_PERM_SCOPES = frozenset({"contents", "packages"})
ALLOWED_SBOM_PERM_LEVELS = frozenset({"read", "none"})


def _logical_commands(script: str) -> list[str]:
    """Join backslash-continued shell lines and drop blank/comment lines."""
    cmds: list[str] = []
    buf = ""
    for raw in script.splitlines():
        line = raw.strip()
        if not buf and (not line or line.startswith("#")):
            continue
        if line.endswith("\\"):
            buf += line[:-1].rstrip() + " "
            continue
        buf += line
        cmds.append(buf.strip())
        buf = ""
    if buf.strip():
        cmds.append(buf.strip())
    return cmds


def _has_flag(tokens: list[str], flag: str, value: str | None = None) -> bool:
    if value is None:
        return flag in tokens
    if f"{flag}={value}" in tokens:
        return True
    for i, tok in enumerate(tokens[:-1]):
        if tok == flag and tokens[i + 1] == value:
            return True
    return False


def _needs_set(job: dict) -> set[str]:
    needs = job.get("needs")
    if isinstance(needs, str):
        return {needs}
    if isinstance(needs, list):
        return {str(x) for x in needs}
    return set()


def _is_disabled_if(val: Any) -> bool:
    if val is False or val == 0:
        return True
    if not isinstance(val, str):
        return False
    s = val.strip()
    if s.startswith("${{") and s.endswith("}}"):
        s = s[3:-2].strip()
    return s.lower() in {"false", "0", "''", '""'}


def _walk_strings(obj: Any) -> list[str]:
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.append(str(k))
            out.extend(_walk_strings(v))
    elif isinstance(obj, list):
        for item in obj:
            out.extend(_walk_strings(item))
    elif obj is not None:
        out.append(str(obj))
    return out


def _check_gating_if(job_name: str, job: dict, errors: list[str]) -> str:
    raw_if = job.get("if")
    if _is_disabled_if(raw_if):
        errors.append(f"{job_name} job is disabled (if={raw_if!r})")
        return ""
    if_str = str(raw_if or "").strip()
    for token in ("!cancelled()", "needs.merge.result == 'success'", "inputs.sign_digest != ''", "needs.merge.result == 'skipped'"):
        if token not in if_str:
            errors.append(f"{job_name} job `if` missing required gating token {token!r}")
    return if_str


def _make_sbom(ref: str, *, bom_format: str = "CycloneDX", spec_version: str = "1.6",
               comp_type: str = "container", comp_name: str | None = None,
               repo_digest: str | None = None) -> bytes:
    doc = {
        "bomFormat": bom_format,
        "specVersion": spec_version,
        "metadata": {
            "component": {
                "type": comp_type,
                "name": ref if comp_name is None else comp_name,
                "properties": [
                    {"name": "aquasecurity:trivy:RepoDigest", "value": ref if repo_digest is None else repo_digest},
                ],
            }
        },
        "components": [],
    }
    return json.dumps(doc).encode("utf-8")


def _run_behavioral_verify(verify_script: str, gate_script: str, max_bytes_str: str, errors: list[str]) -> None:
    image = "ghcr.io/dasomel/narwhal-portal"
    digest = "sha256:" + "a" * 64
    other_digest = "sha256:" + "b" * 64
    ref = f"{image}@{digest}"

    for res, expect_ok in (("success", True), ("failure", False), ("skipped", False), ("cancelled", False)):
        proc = subprocess.run(
            ["bash", "-c", gate_script],
            env={**os.environ, "SBOM_RESULT": res},
            capture_output=True,
            text=True,
            check=False,
        )
        if (proc.returncode == 0) != expect_ok:
            errors.append(f"sign sbom-result gate returned rc={proc.returncode} for SBOM_RESULT={res!r} (expected ok={expect_ok})")

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        sbom_path = td_path / "sbom-cyclonedx.json"
        sha_file = td_path / "sbom-cyclonedx.json.sha256"

        def run_verify(payload: bytes, *, expected_sha: str | None = None,
                       target_digest: str = digest, max_bytes: str = max_bytes_str,
                       path_override: Path | None = None, write_matching_sha_file: bool = True) -> int:
            sbom_path.write_bytes(payload)
            actual = hashlib.sha256(payload).hexdigest()
            if write_matching_sha_file:
                sha_file.write_text(f"{actual}  sbom-cyclonedx.json\n", encoding="utf-8")
            env = {
                **os.environ,
                "IMAGE": image,
                "DIGEST": target_digest,
                "EXPECTED_SHA256": actual if expected_sha is None else expected_sha,
                "SBOM_PATH": str(sbom_path if path_override is None else path_override),
                "MAX_SBOM_BYTES": max_bytes,
            }
            return subprocess.run(["bash", "-c", verify_script], env=env, capture_output=True, text=True, check=False).returncode

        good = _make_sbom(ref)
        if run_verify(good) != 0:
            errors.append("verify SBOM script rejected a valid CycloneDX SBOM matching target digest")

        # Tampered job output sha256 even though artifact .sha256 file matches bytes
        if run_verify(good, expected_sha="0" * 64, write_matching_sha_file=True) == 0:
            errors.append("verify SBOM script accepted mismatched job-output EXPECTED_SHA256 (must not trust artifact .sha256)")

        # Malformed job output sha256
        if run_verify(good, expected_sha="not-a-sha") == 0:
            errors.append("verify SBOM script accepted malformed EXPECTED_SHA256")

        # Wrong target digest in both name and RepoDigest
        wrong_ref_sbom = _make_sbom(f"{image}@{other_digest}")
        if run_verify(wrong_ref_sbom) == 0:
            errors.append("verify SBOM script accepted SBOM describing a different image digest")

        # Wrong RepoDigest even if metadata.component.name matches
        wrong_repodigest = _make_sbom(ref, repo_digest=f"{image}@{other_digest}")
        if run_verify(wrong_repodigest) == 0:
            errors.append("verify SBOM script accepted SBOM with mismatched aquasecurity:trivy:RepoDigest")

        # Wrong metadata.component.name even if RepoDigest matches
        wrong_name = _make_sbom(ref, comp_name=f"{image}@{other_digest}")
        if run_verify(wrong_name) == 0:
            errors.append("verify SBOM script accepted SBOM with mismatched metadata.component.name")

        # Wrong bomFormat
        if run_verify(_make_sbom(ref, bom_format="SPDX")) == 0:
            errors.append("verify SBOM script accepted non-CycloneDX bomFormat")

        # Concatenated JSON stream
        if run_verify(good + b"\n" + good) == 0:
            errors.append("verify SBOM script accepted concatenated multi-document JSON")

        # Oversized payload
        if run_verify(good, max_bytes="10") == 0:
            errors.append("verify SBOM script failed to enforce MAX_SBOM_BYTES ceiling")

        # Symlink rejected
        link = td_path / "symlink.json"
        link.symlink_to(sbom_path)
        if run_verify(good, path_override=link) == 0:
            errors.append("verify SBOM script accepted symlinked SBOM path")


def check(workflow: dict, policies: list[dict], doc_text: str) -> list[str]:
    errors: list[str] = []
    jobs = workflow.get("jobs") or {}

    # ---- 1. Isolated `sbom` job ----
    sbom_job = jobs.get("sbom")
    if not isinstance(sbom_job, dict):
        return ["workflow missing isolated `sbom` job"]

    if "merge" not in _needs_set(sbom_job):
        errors.append("sbom job must declare `needs: merge`")
    _check_gating_if("sbom", sbom_job, errors)

    # No secrets anywhere in sbom job
    if "secrets" in sbom_job:
        errors.append("sbom job must not declare job-level `secrets`")
    for text in _walk_strings(sbom_job):
        if SECRETS_REF_RE.search(text):
            errors.append(f"sbom job must not reference secrets or cosign keys (found in {text[:80]!r})")
            break

    # Minimal read-only permissions on sbom job
    perms = sbom_job.get("permissions")
    if not isinstance(perms, dict) or not perms:
        errors.append("sbom job must declare explicit minimal read-only `permissions` map")
    else:
        for scope, level in perms.items():
            if str(level) not in ALLOWED_SBOM_PERM_LEVELS:
                errors.append(f"sbom job permission {scope}: {level!r} must be read or none (no write)")
            elif str(level) == "read" and scope not in ALLOWED_SBOM_PERM_SCOPES:
                errors.append(f"sbom job permission scope {scope!r} exceeds minimal allowlist {sorted(ALLOWED_SBOM_PERM_SCOPES)}")

    sbom_steps = sbom_job.get("steps") or []
    if not isinstance(sbom_steps, list) or not sbom_steps:
        errors.append("sbom job has no steps")
        sbom_steps = []

    sbom_validate_idx = None
    trivy_idx = None
    trivy_step = None
    hash_idx = None
    hash_step = None
    upload_idx = None
    upload_step = None

    for idx, step in enumerate(sbom_steps):
        if not isinstance(step, dict):
            continue
        uses = str(step.get("uses") or "").strip()
        if uses:
            m = PINNED_ACTION_RE.match(uses)
            if not m:
                errors.append(f"sbom job action must be pinned by 40-hex commit SHA, got {uses!r}")
            else:
                repo = m.group(1)
                if repo == "aquasecurity/trivy-action":
                    trivy_idx = idx
                    trivy_step = step
                elif repo == "actions/upload-artifact":
                    upload_idx = idx
                    upload_step = step
        run_text = str(step.get("run") or "")
        if "sha256:[0-9a-f]{64}" in run_text:
            sbom_validate_idx = idx
            env_cfg = step.get("env") or {}
            if env_cfg.get("DIGEST") != EXPECTED_DIGEST_EXPR:
                errors.append(f"sbom digest validation step DIGEST must be {EXPECTED_DIGEST_EXPR!r}")
            if "inputs.sign_digest" in run_text or "needs.merge.outputs" in run_text:
                errors.append("sbom digest validation step must pass digest via env, not inline in run")
        if "sha256sum" in run_text and "GITHUB_OUTPUT" in run_text:
            hash_idx = idx
            hash_step = step

    if sbom_validate_idx is None:
        errors.append("sbom job missing fail-closed digest validation step (sha256:[0-9a-f]{64})")
    if trivy_step is None:
        errors.append("sbom job missing SHA-pinned aquasecurity/trivy-action CycloneDX SBOM step")
    else:
        if sbom_validate_idx is not None and trivy_idx is not None and sbom_validate_idx >= trivy_idx:
            errors.append("sbom digest validation must run before trivy-action")
        if trivy_step.get("continue-on-error") is True:
            errors.append("sbom trivy step must fail closed (continue-on-error must not be true)")
        with_cfg = trivy_step.get("with") or {}
        if with_cfg.get("scan-type") != "image":
            errors.append(f"sbom trivy scan-type must be 'image', got {with_cfg.get('scan-type')!r}")
        if with_cfg.get("format") != "cyclonedx":
            errors.append(f"sbom trivy format must be 'cyclonedx', got {with_cfg.get('format')!r}")
        scan_ref = str(with_cfg.get("scan-ref") or with_cfg.get("image-ref") or "").strip()
        if scan_ref != EXPECTED_SCAN_REF:
            errors.append(f"sbom trivy scan-ref must target {EXPECTED_SCAN_REF!r}, got {scan_ref!r}")
        if not str(with_cfg.get("output") or "").strip():
            errors.append("sbom trivy step must set with.output")

    sbom_outputs = sbom_job.get("outputs") or {}
    sha_out_expr = str(sbom_outputs.get("sbom_sha256") or "").strip()
    out_m = re.fullmatch(r"\$\{\{\s*steps\.([\w-]+)\.outputs\.(\w+)\s*\}\}", sha_out_expr)
    if hash_step is None or not out_m or hash_step.get("id") != out_m.group(1):
        errors.append("sbom job must compute sha256sum and export outputs.sbom_sha256 from that step")
    else:
        if trivy_idx is not None and hash_idx is not None and hash_idx <= trivy_idx:
            errors.append("sbom sha256 step must run after trivy-action")
        if f'echo "{out_m.group(2)}=' not in str(hash_step.get("run") or ""):
            errors.append("sbom sha256 step must write its output to GITHUB_OUTPUT")
        if ".sha256" not in str(hash_step.get("run") or ""):
            errors.append("sbom sha256 step must write a .sha256 file alongside the SBOM")

    artifact_name = ""
    if upload_step is None:
        errors.append("sbom job missing SHA-pinned actions/upload-artifact step")
    else:
        if hash_idx is not None and upload_idx is not None and upload_idx <= hash_idx:
            errors.append("sbom upload-artifact must run after sha256 computation")
        if upload_step.get("continue-on-error") is True:
            errors.append("sbom upload-artifact must fail closed")
        up_with = upload_step.get("with") or {}
        artifact_name = str(up_with.get("name") or "").strip()
        if not artifact_name:
            errors.append("sbom upload-artifact must set with.name")
        if up_with.get("if-no-files-found") != "error":
            errors.append("sbom upload-artifact must set if-no-files-found: error")
        up_path = str(up_with.get("path") or "")
        if "sbom-cyclonedx.json" not in up_path or ".sha256" not in up_path:
            errors.append("sbom upload-artifact must upload both sbom-cyclonedx.json and its .sha256 file")

    # ---- 2. Hardened `sign` job ----
    sign_job = jobs.get("sign")
    if not isinstance(sign_job, dict):
        errors.append("workflow missing `sign` job")
        return errors

    sign_needs = _needs_set(sign_job)
    if not {"merge", "sbom"}.issubset(sign_needs):
        errors.append(f"sign job must declare `needs: [merge, sbom]`, got {sorted(sign_needs)!r}")
    sign_if = _check_gating_if("sign", sign_job, errors)
    if "needs.sbom.result" in sign_if:
        errors.append("sign job `if` must not gate on needs.sbom.result (that makes sign skipped/green if sbom is skipped); fail closed in step 1 instead")

    sign_steps = sign_job.get("steps") or []
    if not isinstance(sign_steps, list) or not sign_steps:
        errors.append("sign job has no steps")
        return errors

    gate_idx = None
    gate_step = None
    dl_idx = None
    dl_step = None
    verify_idx = None
    verify_step = None
    cosign_sign_idx = None
    attest_idx = None
    attest_step = None

    for idx, step in enumerate(sign_steps):
        if not isinstance(step, dict):
            continue
        uses = str(step.get("uses") or "").strip()
        if uses:
            m = PINNED_ACTION_RE.match(uses)
            if not m:
                errors.append(f"sign job action must be pinned by 40-hex commit SHA, got {uses!r}")
            else:
                repo = m.group(1)
                if repo not in ALLOWED_SIGN_ACTIONS:
                    errors.append(f"sign job must not use third-party/SBOM action {uses!r} (allowlist: {sorted(ALLOWED_SIGN_ACTIONS)})")
                if repo == "actions/download-artifact":
                    dl_idx = idx
                    dl_step = step
        run_text = str(step.get("run") or "")
        for cmd in _logical_commands(run_text):
            if FORBIDDEN_SIGN_SBOM_CMD_RE.search(cmd):
                errors.append(f"sign job step {step.get('name')!r} must not invoke SBOM generator tools in run")
                break
        env_cfg = step.get("env") or {}
        if env_cfg.get("SBOM_RESULT") == "${{ needs.sbom.result }}":
            gate_idx = idx
            gate_step = step
        if env_cfg.get("EXPECTED_SHA256") == "${{ needs.sbom.outputs.sbom_sha256 }}":
            verify_idx = idx
            verify_step = step
        if "cosign sign " in run_text:
            cosign_sign_idx = idx
        if "cosign attest" in run_text:
            attest_idx = idx
            attest_step = step

    if gate_step is None:
        errors.append("sign job missing fail-closed step checking needs.sbom.result == 'success'")
    else:
        if gate_idx != 0:
            errors.append("sign job SBOM-result gate must be step 1 (before download or cosign)")
        if gate_step.get("continue-on-error") is True:
            errors.append("sign job SBOM-result gate must not set continue-on-error: true")

    if dl_step is None:
        errors.append("sign job missing SHA-pinned actions/download-artifact step")
    else:
        if dl_step.get("continue-on-error") is True:
            errors.append("sign download-artifact step must fail closed")
        dl_name = str((dl_step.get("with") or {}).get("name") or "").strip()
        if artifact_name and dl_name != artifact_name:
            errors.append(f"sign download-artifact name {dl_name!r} does not match sbom upload name {artifact_name!r}")

    if verify_step is None:
        errors.append("sign job missing SBOM verification step reading EXPECTED_SHA256 from needs.sbom.outputs.sbom_sha256")
    else:
        if verify_step.get("continue-on-error") is True:
            errors.append("sign verify SBOM step must fail closed")
        if dl_idx is not None and verify_idx is not None and verify_idx <= dl_idx:
            errors.append("sign verify SBOM step must run after download-artifact")
        if cosign_sign_idx is not None and verify_idx is not None and verify_idx >= cosign_sign_idx:
            errors.append("sign verify SBOM step must run before cosign sign")
        v_env = verify_step.get("env") or {}
        if v_env.get("DIGEST") != EXPECTED_DIGEST_EXPR:
            errors.append(f"sign verify SBOM step DIGEST must be {EXPECTED_DIGEST_EXPR!r}")
        max_bytes_str = str(v_env.get("MAX_SBOM_BYTES") or "").strip()
        if not max_bytes_str.isdigit() or not (1024 <= int(max_bytes_str) <= 50 * 1024 * 1024):
            errors.append(f"sign verify SBOM step must set sane MAX_SBOM_BYTES (1 KiB..50 MiB), got {max_bytes_str!r}")
        v_run = str(verify_step.get("run") or "")
        if not any(c == "set -euo pipefail" for c in _logical_commands(v_run)):
            errors.append("sign verify SBOM step must enable set -euo pipefail")
        if ".sha256" in v_run:
            errors.append("sign verify SBOM step must verify against job output EXPECTED_SHA256, not an artifact .sha256 file")
        if not errors and gate_step is not None and max_bytes_str.isdigit():
            _run_behavioral_verify(v_run, str(gate_step.get("run") or ""), max_bytes_str, errors)

    # ---- 3. Dual-format cosign attest ----
    if attest_step is None:
        errors.append("sign job missing cosign attest step")
    else:
        if verify_idx is not None and attest_idx is not None and attest_idx <= verify_idx:
            errors.append("cosign attest step must run after SBOM verification step")
        if str(attest_step.get("if") or "").strip() != "steps.secrets-check.outputs.present == 'true'":
            errors.append("cosign attest step must be gated on steps.secrets-check.outputs.present == 'true'")
        if attest_step.get("continue-on-error") is True:
            errors.append("cosign attest step must fail closed (continue-on-error must not be true)")
        env_cfg = attest_step.get("env") or {}
        if env_cfg.get("COSIGN_PRIVATE_KEY") != "${{ secrets.COSIGN_PRIVATE_KEY }}":
            errors.append("cosign attest step must pass COSIGN_PRIVATE_KEY secret")
        if env_cfg.get("COSIGN_PASSWORD") != "${{ secrets.COSIGN_PASSWORD }}":
            errors.append("cosign attest step must pass COSIGN_PASSWORD secret")
        if env_cfg.get("DIGEST") != EXPECTED_DIGEST_EXPR:
            errors.append(f"cosign attest step DIGEST must be {EXPECTED_DIGEST_EXPR!r}")
        if verify_step is not None:
            v_sbom = str((verify_step.get("env") or {}).get("SBOM_PATH") or "").strip()
            if str(env_cfg.get("SBOM_PATH") or "").strip() != v_sbom:
                errors.append("cosign attest step SBOM_PATH must match verified SBOM_PATH")

        cmds = _logical_commands(str(attest_step.get("run") or ""))
        if not any(c == "set -euo pipefail" for c in cmds):
            errors.append("cosign attest step must enable set -euo pipefail")

        attest_cmds = [shlex.split(c) for c in cmds if c.startswith("cosign attest ")]
        bundle_ok = False
        legacy_ok = False
        for toks in attest_cmds:
            base_ok = (
                _has_flag(toks, "--yes")
                and _has_flag(toks, "--key", "env://COSIGN_PRIVATE_KEY")
                and _has_flag(toks, "--type", "cyclonedx")
                and _has_flag(toks, "--predicate", "${SBOM_PATH}")
                and toks[-1] == "${IMAGE}@${DIGEST}"
            )
            if not base_ok:
                continue
            has_legacy_bundle = _has_flag(toks, "--new-bundle-format", "false")
            has_no_sc = _has_flag(toks, "--use-signing-config", "false")
            if not has_legacy_bundle and not has_no_sc:
                bundle_ok = True
            elif has_legacy_bundle and has_no_sc:
                legacy_ok = True

        if not bundle_ok:
            errors.append("cosign attest step must publish Sigstore bundle attestation for ${IMAGE}@${DIGEST}")
        if not legacy_ok:
            errors.append(
                "cosign attest step must publish legacy .att attestation with --new-bundle-format=false --use-signing-config=false"
            )

    # ---- 4. Kyverno policy predicateType ----
    policy = next(
        (d for d in policies if d.get("kind") == "ClusterPolicy" and (d.get("metadata") or {}).get("name") == "verify-image-signatures"),
        None,
    )
    if policy is None:
        errors.append("ClusterPolicy verify-image-signatures missing")
    else:
        rules = (policy.get("spec") or {}).get("rules") or []
        verify_images = (rules[0].get("verifyImages") or []) if rules else []
        atts = (verify_images[0].get("attestations") or []) if verify_images else []
        ptypes = [a.get("predicateType") for a in atts if isinstance(a, dict)]
        if ptypes != [EXPECTED_PREDICATE_TYPE]:
            errors.append(
                f"verify-image-signatures attestations predicateType must be {[EXPECTED_PREDICATE_TYPE]!r}, got {ptypes!r}"
            )

    # ---- 5. Docs accuracy ----
    for needle in ("cosign verify-attestation", "--type cyclonedx", "--new-bundle-format=false", EXPECTED_PREDICATE_TYPE, "`sbom` job"):
        if needle not in doc_text:
            errors.append(f"portal/docs/cosign-signing-setup.md missing {needle!r}")
    if re.search(r"10-verify-image-signatures\.sh\)\s+can verify", doc_text):
        errors.append("portal/docs/cosign-signing-setup.md must not claim 10-verify-image-signatures.sh verifies signatures/SBOMs")
    if "only\nchecks local file existence" not in doc_text and "only checks local file existence" not in doc_text:
        errors.append("portal/docs/cosign-signing-setup.md must state 10-verify-image-signatures.sh only checks local file existence")

    return errors


def _find_step(job: dict, pred) -> dict:
    for s in job["steps"]:
        if pred(s):
            return s
    raise AssertionError("expected step not found for mutation")


def run_mutations(workflow: dict, policies: list[dict], doc_text: str) -> tuple[bool, int]:
    mutations: list[tuple[str, dict, list[dict], str]] = []

    # 1. Disable sign job (if: false)
    w = copy.deepcopy(workflow)
    w["jobs"]["sign"]["if"] = False
    mutations.append(("disable-sign-job-bool", w, policies, doc_text))

    # 2. Disable sign job (if: 'false')
    w = copy.deepcopy(workflow)
    w["jobs"]["sign"]["if"] = "${{ false }}"
    mutations.append(("disable-sign-job-expr", w, policies, doc_text))

    # 3. Disable sbom job
    w = copy.deepcopy(workflow)
    w["jobs"]["sbom"]["if"] = False
    mutations.append(("disable-sbom-job", w, policies, doc_text))

    # 4. Drop `sbom` from sign `needs`
    w = copy.deepcopy(workflow)
    w["jobs"]["sign"]["needs"] = ["merge"]
    mutations.append(("drop-sign-needs-sbom", w, policies, doc_text))

    # 5. Drop `merge` from sbom `needs`
    w = copy.deepcopy(workflow)
    w["jobs"]["sbom"].pop("needs", None)
    mutations.append(("drop-sbom-needs-merge", w, policies, doc_text))

    # 6. Move sbom result check into sign job `if` (would leave sign skipped/green if sbom skipped)
    w = copy.deepcopy(workflow)
    w["jobs"]["sign"]["if"] = "${{ !cancelled() && needs.sbom.result == 'success' && (needs.merge.result == 'success' || (inputs.sign_digest != '' && needs.merge.result == 'skipped')) }}"
    mutations.append(("sign-if-skips-on-sbom", w, policies, doc_text))

    # 7. Drop step-1 SBOM result gate in sign
    w = copy.deepcopy(workflow)
    w["jobs"]["sign"]["steps"] = [
        s for s in w["jobs"]["sign"]["steps"]
        if (s.get("env") or {}).get("SBOM_RESULT") != "${{ needs.sbom.result }}"
    ]
    mutations.append(("drop-sign-sbom-result-gate", w, policies, doc_text))

    # 8. Leak cosign secret into sbom job
    w = copy.deepcopy(workflow)
    w["jobs"]["sbom"]["env"] = {"COSIGN_PRIVATE_KEY": "${{ secrets.COSIGN_PRIVATE_KEY }}"}
    mutations.append(("sbom-gains-cosign-secret", w, policies, doc_text))

    # 9. Add GITHUB_TOKEN secret to sbom job
    w = copy.deepcopy(workflow)
    w["jobs"]["sbom"]["steps"][0].setdefault("env", {})["TOKEN"] = "${{ secrets.GITHUB_TOKEN }}"
    mutations.append(("sbom-gains-github-token-secret", w, policies, doc_text))

    # 10. Grant write permission to sbom job
    w = copy.deepcopy(workflow)
    w["jobs"]["sbom"]["permissions"]["packages"] = "write"
    mutations.append(("sbom-gains-write-permission", w, policies, doc_text))

    # 11. Remove permissions block from sbom job
    w = copy.deepcopy(workflow)
    w["jobs"]["sbom"].pop("permissions", None)
    mutations.append(("sbom-drops-permissions", w, policies, doc_text))

    # 12. Re-add trivy-action to sign job
    w = copy.deepcopy(workflow)
    w["jobs"]["sign"]["steps"].append({
        "name": "Sneaky trivy",
        "uses": "aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25",
    })
    mutations.append(("sign-gains-trivy-action", w, policies, doc_text))

    # 13. Add third-party SBOM action to sign job
    w = copy.deepcopy(workflow)
    w["jobs"]["sign"]["steps"].append({
        "name": "Third-party SBOM",
        "uses": "anchore/sbom-action@" + "1" * 40,
    })
    mutations.append(("sign-gains-third-party-sbom-action", w, policies, doc_text))

    # 13b. Invoke trivy CLI directly in sign run step
    w = copy.deepcopy(workflow)
    w["jobs"]["sign"]["steps"].append({
        "name": "Direct trivy CLI",
        "run": "trivy image --format cyclonedx ghcr.io/dasomel/narwhal-portal@sha256:00",
    })
    mutations.append(("sign-runs-trivy-cli", w, policies, doc_text))

    # 14. Unpin trivy-action in sbom job
    w = copy.deepcopy(workflow)
    _find_step(w["jobs"]["sbom"], lambda s: str(s.get("uses") or "").startswith("aquasecurity/trivy-action@"))["uses"] = "aquasecurity/trivy-action@v0.36.0"
    mutations.append(("unpin-trivy-action", w, policies, doc_text))

    # 15. Wrong Trivy format (spdx-json) in sbom job
    w = copy.deepcopy(workflow)
    _find_step(w["jobs"]["sbom"], lambda s: str(s.get("uses") or "").startswith("aquasecurity/trivy-action@"))["with"]["format"] = "spdx-json"
    mutations.append(("wrong-trivy-format", w, policies, doc_text))

    # 16. Drop sign_digest from Trivy scan-ref in sbom job
    w = copy.deepcopy(workflow)
    _find_step(w["jobs"]["sbom"], lambda s: str(s.get("uses") or "").startswith("aquasecurity/trivy-action@"))["with"]["scan-ref"] = "${{ env.IMAGE }}@${{ needs.merge.outputs.digest }}"
    mutations.append(("drop-recovery-digest-from-trivy", w, policies, doc_text))

    # 17. continue-on-error on Trivy step in sbom job
    w = copy.deepcopy(workflow)
    _find_step(w["jobs"]["sbom"], lambda s: str(s.get("uses") or "").startswith("aquasecurity/trivy-action@"))["continue-on-error"] = True
    mutations.append(("trivy-continue-on-error", w, policies, doc_text))

    # 18. Remove sha256 verification from sign verify step
    w = copy.deepcopy(workflow)
    v = _find_step(w["jobs"]["sign"], lambda s: (s.get("env") or {}).get("EXPECTED_SHA256") == "${{ needs.sbom.outputs.sbom_sha256 }}")
    v["run"] = re.sub(r'if \[ "\$actual_sha" != "\$EXPECTED_SHA256" \]; then.*?fi', "true", v["run"], flags=re.S)
    mutations.append(("remove-sha256-verification", w, policies, doc_text))

    # 19. Verify sha256 from artifact .sha256 file instead of job output
    w = copy.deepcopy(workflow)
    v = _find_step(w["jobs"]["sign"], lambda s: (s.get("env") or {}).get("EXPECTED_SHA256") == "${{ needs.sbom.outputs.sbom_sha256 }}")
    v["run"] = v["run"].replace('"$EXPECTED_SHA256"', '"$(awk \'{print $1}\' "${SBOM_PATH}.sha256")"')
    mutations.append(("verify-sha256-from-artifact-file", w, policies, doc_text))

    # 20. Remove SBOM target-digest verification (RepoDigest / name check)
    w = copy.deepcopy(workflow)
    v = _find_step(w["jobs"]["sign"], lambda s: (s.get("env") or {}).get("EXPECTED_SHA256") == "${{ needs.sbom.outputs.sbom_sha256 }}")
    v["run"] = v["run"].replace("and .metadata.component.name == $ref", "").replace('and .value == $ref', "")
    mutations.append(("remove-sbom-digest-verification", w, policies, doc_text))

    # 21. Remove SBOM size bound check
    w = copy.deepcopy(workflow)
    v = _find_step(w["jobs"]["sign"], lambda s: (s.get("env") or {}).get("EXPECTED_SHA256") == "${{ needs.sbom.outputs.sbom_sha256 }}")
    v["run"] = re.sub(r'if \[ "\$size" -le 0 \].*?fi', "true", v["run"], flags=re.S)
    mutations.append(("remove-sbom-size-check", w, policies, doc_text))

    # 22. Remove cosign attest step
    w = copy.deepcopy(workflow)
    w["jobs"]["sign"]["steps"] = [
        s for s in w["jobs"]["sign"]["steps"] if "cosign attest" not in str(s.get("run") or "")
    ]
    mutations.append(("remove-cosign-attest-step", w, policies, doc_text))

    # 23. Drop --use-signing-config=false from legacy cosign attest (#745/#746 lesson)
    w = copy.deepcopy(workflow)
    a = _find_step(w["jobs"]["sign"], lambda s: "cosign attest" in str(s.get("run") or ""))
    a["run"] = a["run"].replace("--use-signing-config=false", "")
    mutations.append(("drop-use-signing-config-false", w, policies, doc_text))

    # 24. Drop legacy --new-bundle-format=false invocation
    w = copy.deepcopy(workflow)
    a = _find_step(w["jobs"]["sign"], lambda s: "cosign attest" in str(s.get("run") or ""))
    a["run"] = a["run"].replace("--new-bundle-format=false", "")
    mutations.append(("drop-legacy-att-format", w, policies, doc_text))

    # 25. Revert Kyverno predicateType to broken schema/1.4
    p = copy.deepcopy(policies)
    for doc in p:
        if doc.get("kind") == "ClusterPolicy" and (doc.get("metadata") or {}).get("name") == "verify-image-signatures":
            doc["spec"]["rules"][0]["verifyImages"][0]["attestations"][0]["predicateType"] = "https://cyclonedx.org/schema/1.4"
    mutations.append(("revert-kyverno-predicate-schema-1-4", workflow, p, doc_text))

    # 26. Re-introduce inaccurate preflight claim in docs
    bad_doc = doc_text.replace(
        "only\nchecks local file existence",
        "and its air-gap preflight gate (`scripts/airgap/10-verify-image-signatures.sh`) can verify them",
    ).replace(
        "only checks local file existence",
        "and its air-gap preflight gate (`scripts/airgap/10-verify-image-signatures.sh`) can verify them",
    )
    mutations.append(("revert-doc-preflight-claim", workflow, policies, bad_doc))

    uncaught: list[str] = []
    for name, mw, mp, md in mutations:
        if not check(mw, mp, md):
            uncaught.append(name)

    if uncaught:
        print(f"FAIL: mutations not caught: {uncaught}", file=sys.stderr)
        return False, 0
    return True, len(mutations)


def main() -> int:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    policies = [d for d in yaml.safe_load_all(POLICY_PATH.read_text(encoding="utf-8")) if isinstance(d, dict)]
    doc_text = DOC_PATH.read_text(encoding="utf-8")

    errors = check(workflow, policies, doc_text)
    if errors:
        for err in errors:
            print(f"VIOLATION: {err}", file=sys.stderr)
        return 1

    if "--mutation-verify" in sys.argv[1:]:
        ok, caught = run_mutations(workflow, policies, doc_text)
        if not ok:
            return 1
        print(f"PASS: R379 isolated portal CycloneDX SBOM + attestation + Kyverno predicateType ({caught} mutations rejected)")
    else:
        print("PASS: R379 isolated portal CycloneDX SBOM + attestation + Kyverno predicateType")
    return 0


if __name__ == "__main__":
    sys.exit(main())
