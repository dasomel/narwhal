#!/usr/bin/env python3
"""R379: Portal image publishes CycloneDX SBOM attestations and Kyverno matches https://cyclonedx.org/bom (narwhal#684).

Verifies:
1. .github/workflows/portal-docker-publish.yml `sign` job generates a CycloneDX SBOM from
   `${IMAGE}@${DIGEST}` via SHA-pinned `aquasecurity/trivy-action` for both normal releases
   and `sign_digest` recovery runs, fail-closed.
2. `sign` job attests `${IMAGE}@${DIGEST}` with `cosign attest --type cyclonedx` in both
   Sigstore bundle format and legacy `.att` format (`--new-bundle-format=false` +
   `--use-signing-config=false`) for Kyverno 1.18.x, fail-closed.
3. gitops/resources/kyverno-policies.yaml `verify-image-signatures` uses
   `predicateType: https://cyclonedx.org/bom` (`in_toto.PredicateCycloneDX`).
4. portal/docs/cosign-signing-setup.md documents `cosign verify-attestation` and
   `https://cyclonedx.org/bom`.

--mutation-verify proves broken copies fail without touching tracked files.
"""

from __future__ import annotations

import copy
import re
import shlex
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = ROOT / ".github/workflows/portal-docker-publish.yml"
POLICY_PATH = ROOT / "gitops/resources/kyverno-policies.yaml"
DOC_PATH = ROOT / "portal/docs/cosign-signing-setup.md"

TRIVY_PIN_RE = re.compile(r"^aquasecurity/trivy-action@[0-9a-f]{40}$")
EXPECTED_DIGEST_EXPR = "${{ inputs.sign_digest || needs.merge.outputs.digest }}"
EXPECTED_SCAN_REF = f"${{{{ env.IMAGE }}}}@{EXPECTED_DIGEST_EXPR}"
EXPECTED_PREDICATE_TYPE = "https://cyclonedx.org/bom"


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


def check(workflow: dict, policies: list[dict], doc_text: str) -> list[str]:
    errors: list[str] = []

    sign_job = ((workflow.get("jobs") or {}).get("sign")) or {}
    steps = sign_job.get("steps") or []
    if not isinstance(steps, list) or not steps:
        return ["sign job has no steps"]

    trivy_idx = None
    trivy_step = None
    attest_idx = None
    attest_step = None

    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        uses = str(step.get("uses") or "")
        if uses.startswith("aquasecurity/trivy-action@"):
            trivy_idx = idx
            trivy_step = step
        run_text = str(step.get("run") or "")
        if "cosign attest" in run_text:
            attest_idx = idx
            attest_step = step

    if trivy_step is None:
        errors.append("sign job missing aquasecurity/trivy-action CycloneDX SBOM generation step")
    else:
        uses = str(trivy_step.get("uses") or "")
        if not TRIVY_PIN_RE.match(uses):
            errors.append(f"trivy-action must be pinned by 40-hex commit SHA, got {uses!r}")
        if str(trivy_step.get("if") or "").strip() != "steps.secrets-check.outputs.present == 'true'":
            errors.append("trivy SBOM step must be gated on steps.secrets-check.outputs.present == 'true'")
        if trivy_step.get("continue-on-error") is True:
            errors.append("trivy SBOM step must fail closed (continue-on-error must not be true)")
        with_cfg = trivy_step.get("with") or {}
        if with_cfg.get("scan-type") != "image":
            errors.append(f"trivy SBOM step scan-type must be 'image', got {with_cfg.get('scan-type')!r}")
        if with_cfg.get("format") != "cyclonedx":
            errors.append(f"trivy SBOM step format must be 'cyclonedx', got {with_cfg.get('format')!r}")
        scan_ref = str(with_cfg.get("scan-ref") or with_cfg.get("image-ref") or "").strip()
        if scan_ref != EXPECTED_SCAN_REF:
            errors.append(
                f"trivy SBOM step scan-ref must target {EXPECTED_SCAN_REF!r} (both release and sign_digest), got {scan_ref!r}"
            )
        sbom_out = str(with_cfg.get("output") or "").strip()
        if not sbom_out:
            errors.append("trivy SBOM step must set with.output")

    if attest_step is None:
        errors.append("sign job missing cosign attest step")
    else:
        if trivy_idx is not None and attest_idx is not None and attest_idx <= trivy_idx:
            errors.append("cosign attest step must run after trivy SBOM generation step")
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
        if trivy_step is not None:
            trivy_out = str((trivy_step.get("with") or {}).get("output") or "").strip()
            if str(env_cfg.get("SBOM_PATH") or "").strip() != trivy_out:
                errors.append("cosign attest step SBOM_PATH must match trivy step with.output")

        run_text = str(attest_step.get("run") or "")
        cmds = _logical_commands(run_text)
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

    for needle in ("cosign verify-attestation", "--type cyclonedx", "--new-bundle-format=false", EXPECTED_PREDICATE_TYPE):
        if needle not in doc_text:
            errors.append(f"portal/docs/cosign-signing-setup.md missing {needle!r}")

    return errors


def run_mutations(workflow: dict, policies: list[dict], doc_text: str) -> tuple[bool, int]:
    mutations: list[tuple[str, dict, list[dict], str]] = []

    # 1. Remove Trivy SBOM step
    w = copy.deepcopy(workflow)
    w["jobs"]["sign"]["steps"] = [
        s for s in w["jobs"]["sign"]["steps"] if not str(s.get("uses") or "").startswith("aquasecurity/trivy-action@")
    ]
    mutations.append(("remove-trivy-step", w, policies, doc_text))

    # 2. Unpin trivy-action to tag
    w = copy.deepcopy(workflow)
    for s in w["jobs"]["sign"]["steps"]:
        if str(s.get("uses") or "").startswith("aquasecurity/trivy-action@"):
            s["uses"] = "aquasecurity/trivy-action@v0.36.0"
    mutations.append(("unpin-trivy-action", w, policies, doc_text))

    # 3. Wrong Trivy format (spdx-json instead of cyclonedx)
    w = copy.deepcopy(workflow)
    for s in w["jobs"]["sign"]["steps"]:
        if str(s.get("uses") or "").startswith("aquasecurity/trivy-action@"):
            s["with"]["format"] = "spdx-json"
    mutations.append(("wrong-trivy-format", w, policies, doc_text))

    # 4. Drop sign_digest from Trivy scan-ref
    w = copy.deepcopy(workflow)
    for s in w["jobs"]["sign"]["steps"]:
        if str(s.get("uses") or "").startswith("aquasecurity/trivy-action@"):
            s["with"]["scan-ref"] = "${{ env.IMAGE }}@${{ needs.merge.outputs.digest }}"
    mutations.append(("drop-recovery-digest-from-trivy", w, policies, doc_text))

    # 5. continue-on-error on Trivy step
    w = copy.deepcopy(workflow)
    for s in w["jobs"]["sign"]["steps"]:
        if str(s.get("uses") or "").startswith("aquasecurity/trivy-action@"):
            s["continue-on-error"] = True
    mutations.append(("trivy-continue-on-error", w, policies, doc_text))

    # 6. Remove cosign attest step
    w = copy.deepcopy(workflow)
    w["jobs"]["sign"]["steps"] = [
        s for s in w["jobs"]["sign"]["steps"] if "cosign attest" not in str(s.get("run") or "")
    ]
    mutations.append(("remove-cosign-attest-step", w, policies, doc_text))

    # 7. Drop --use-signing-config=false from legacy cosign attest (#745/#746 lesson)
    w = copy.deepcopy(workflow)
    for s in w["jobs"]["sign"]["steps"]:
        if "cosign attest" in str(s.get("run") or ""):
            s["run"] = s["run"].replace("--use-signing-config=false", "")
    mutations.append(("drop-use-signing-config-false", w, policies, doc_text))

    # 8. Drop legacy --new-bundle-format=false invocation
    w = copy.deepcopy(workflow)
    for s in w["jobs"]["sign"]["steps"]:
        if "cosign attest" in str(s.get("run") or ""):
            s["run"] = s["run"].replace("--new-bundle-format=false", "")
    mutations.append(("drop-legacy-att-format", w, policies, doc_text))

    # 9. continue-on-error on cosign attest step
    w = copy.deepcopy(workflow)
    for s in w["jobs"]["sign"]["steps"]:
        if "cosign attest" in str(s.get("run") or ""):
            s["continue-on-error"] = True
    mutations.append(("attest-continue-on-error", w, policies, doc_text))

    # 10. Revert Kyverno predicateType to broken schema/1.4
    p = copy.deepcopy(policies)
    for doc in p:
        if doc.get("kind") == "ClusterPolicy" and (doc.get("metadata") or {}).get("name") == "verify-image-signatures":
            doc["spec"]["rules"][0]["verifyImages"][0]["attestations"][0]["predicateType"] = "https://cyclonedx.org/schema/1.4"
    mutations.append(("revert-kyverno-predicate-schema-1-4", workflow, p, doc_text))

    # 11. Remove verify-attestation docs
    mutations.append(("drop-doc-verify-attestation", workflow, policies, doc_text.replace("cosign verify-attestation", "")))

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
        print(f"PASS: R379 portal CycloneDX SBOM attestation + Kyverno predicateType ({caught} mutations rejected)")
    else:
        print("PASS: R379 portal CycloneDX SBOM attestation + Kyverno predicateType")
    return 0


if __name__ == "__main__":
    sys.exit(main())
