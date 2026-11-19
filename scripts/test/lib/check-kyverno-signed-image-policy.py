#!/usr/bin/env python3
"""Assert Kyverno has a valid verify-image-signatures policy configured for supply chain verification (narwhal#35).

Validates that gitops/resources/kyverno-policies.yaml contains a ClusterPolicy named
'verify-image-signatures' that configures `verifyImages` with Cosign/x509 public key attestors,
SBOM attestation predicate validation, and tag-compatible Audit scope (narwhal#683).
"""
import sys
from pathlib import Path
import yaml

DEFAULT_PATH = Path("gitops/resources/kyverno-policies.yaml")
KEY_PATH = Path(__file__).resolve().parents[3] / "scripts/airgap/keys/cosign.pub"


def load_policies(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [doc for doc in yaml.safe_load_all(f) if isinstance(doc, dict)]


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PATH
    if not path.exists():
        print(f"ERROR: policy file does not exist: {path}", file=sys.stderr)
        return 1

    docs = load_policies(path)
    policy = next((d for d in docs if d.get("kind") == "ClusterPolicy" and d.get("metadata", {}).get("name") == "verify-image-signatures"), None)

    if not policy:
        print(f"VIOLATION: ClusterPolicy 'verify-image-signatures' not found in {path}", file=sys.stderr)
        return 1

    spec = policy.get("spec", {})
    mode = spec.get("validationFailureAction")
    if mode != "Audit":
        print(f"VIOLATION: validationFailureAction must remain 'Audit', got {mode!r}", file=sys.stderr)
        return 1

    rules = spec.get("rules", [])
    if not rules:
        print(f"VIOLATION: ClusterPolicy 'verify-image-signatures' has no rules", file=sys.stderr)
        return 1

    verify_images_rule = next((r for r in rules if "verifyImages" in r), None)
    if not verify_images_rule:
        print(f"VIOLATION: no rule containing 'verifyImages' found in verify-image-signatures policy", file=sys.stderr)
        return 1

    configs = verify_images_rule.get("verifyImages", [])
    if not configs:
        print(f"VIOLATION: 'verifyImages' list is empty in rule {verify_images_rule.get('name')!r}", file=sys.stderr)
        return 1

    if len(rules) != 1 or len(configs) != 1:
        print("VIOLATION: expected one signature rule and one verifyImages configuration", file=sys.stderr)
        return 1

    # D-KYVERNO-VERIFY-SCOPE: pin the signed repository and namespace coverage; widening
    # creates unsigned-image noise. Cost: new signed images require an explicit update.
    # Escape hatch: extend this contract alongside a reviewed signing workflow.
    if verify_images_rule.get("match") != {"any": [{"resources": {"kinds": ["Pod"]}}]}:
        print("VIOLATION: signature rule must match Pods without additional restrictions", file=sys.stderr)
        return 1
    expected_exclude = {"any": [{"resources": {"namespaces": [
        "kube-system", "istio-system", "platform-system", "security-system",
    ]}}]}
    if verify_images_rule.get("exclude") != expected_exclude:
        print("VIOLATION: signature namespace exclusions must preserve devtools coverage", file=sys.stderr)
        return 1

    cfg = configs[0]
    if cfg.get("imageReferences") != ["ghcr.io/dasomel/narwhal-portal:*", "ghcr.io/dasomel/narwhal-portal@sha256:*"]:
        print("VIOLATION: imageReferences must cover only narwhal-portal tags and digests", file=sys.stderr)
        return 1
    if any(cfg.get(field) is not False for field in ("mutateDigest", "verifyDigest", "required")):
        print("VIOLATION: tag-compatible Audit requires mutateDigest/verifyDigest/required false", file=sys.stderr)
        return 1

    if not cfg.get("attestors"):
        print(f"VIOLATION: attestors (signature verification keys) missing in verifyImages configuration", file=sys.stderr)
        return 1

    if not cfg.get("attestations"):
        print(f"VIOLATION: attestations (SBOM predicate verification) missing in verifyImages configuration", file=sys.stderr)
        return 1

    if [a.get("predicateType") for a in cfg["attestations"]] != ["https://cyclonedx.org/bom"]:
        print("VIOLATION: CycloneDX attestation predicate must be https://cyclonedx.org/bom (D-CYCLONEDX-PREDICATE)", file=sys.stderr)
        return 1

    attestors = [
        *cfg["attestors"],
        *(attestor for attestation in cfg["attestations"] for attestor in attestation.get("attestors", [])),
    ]
    entries = [entry for attestor in attestors for entry in attestor.get("entries", [])]
    if not entries or any(not entry.get("keys", {}).get("publicKeys") for entry in entries):
        print("VIOLATION: every signature and attestation attestor must use keys.publicKeys", file=sys.stderr)
        return 1

    # D-REKOR: the cluster is air-gapped and cannot reach Rekor, so every key attestor must skip
    # the tlog/SCT lookup (CI still uploads to public Rekor for online consumers).
    for entry in entries:
        keys = entry["keys"]
        if (keys.get("rekor") or {}).get("ignoreTlog") is not True or (keys.get("ctlog") or {}).get("ignoreSCT") is not True:
            print("VIOLATION: every key attestor must set keys.rekor.ignoreTlog and keys.ctlog.ignoreSCT to true (air-gap, D-REKOR)", file=sys.stderr)
            return 1

    # Fail closed: a missing committed key must not silently skip the drift check.
    if not KEY_PATH.is_file():
        print(f"VIOLATION: committed cosign public key {KEY_PATH} is missing", file=sys.stderr)
        return 1
    want = KEY_PATH.read_text(encoding="utf-8").strip()
    for entry in entries:
        if entry["keys"]["publicKeys"].strip() != want:
            print(f"VIOLATION: publicKeys differs from {KEY_PATH} (inline key must match the committed key)", file=sys.stderr)
            return 1

    print(f"OK: verify-image-signatures policy in {path} is valid (mode: {mode})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
