#!/usr/bin/env python3
"""Assert Kyverno has a valid verify-image-signatures policy configured for supply chain verification (narwhal#35).

Validates that gitops/resources/kyverno-policies.yaml contains a ClusterPolicy named
'verify-image-signatures' that configures `verifyImages` with Cosign/x509 public key attestors,
SBOM attestation predicate validation, and enabled digest verification.
"""
import sys
from pathlib import Path
import yaml

DEFAULT_PATH = Path("gitops/resources/kyverno-policies.yaml")


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
    if mode not in ("Audit", "Enforce"):
        print(f"VIOLATION: validationFailureAction must be 'Audit' or 'Enforce', got {mode!r}", file=sys.stderr)
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

    cfg = configs[0]
    if cfg.get("verifyDigest") is not True:
        print(f"VIOLATION: verifyDigest must be true in verifyImages configuration", file=sys.stderr)
        return 1

    if not cfg.get("attestors"):
        print(f"VIOLATION: attestors (signature verification keys) missing in verifyImages configuration", file=sys.stderr)
        return 1

    if not cfg.get("attestations"):
        print(f"VIOLATION: attestations (SBOM predicate verification) missing in verifyImages configuration", file=sys.stderr)
        return 1

    attestors = [
        *cfg["attestors"],
        *(attestor for attestation in cfg["attestations"] for attestor in attestation.get("attestors", [])),
    ]
    entries = [entry for attestor in attestors for entry in attestor.get("entries", [])]
    if not entries or any(not entry.get("keys", {}).get("publicKeys") for entry in entries):
        print("VIOLATION: every signature and attestation attestor must use keys.publicKeys", file=sys.stderr)
        return 1

    print(f"OK: verify-image-signatures policy in {path} is valid (mode: {mode})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
