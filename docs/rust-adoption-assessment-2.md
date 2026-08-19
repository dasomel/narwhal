# Rust Adoption Assessment

Do not rewrite Narwhal wholesale. Evaluate Rust at security-sensitive, artifact-processing boundaries.

High-value candidates: offline artifact resolver/manifest verifier; image/chart digest/checksum/signature/SBOM processor; sandbox/security policy validator.

Medium: high-volume evidence normalization; upgrade/RCA evidence parser.

Keep the existing Kubernetes orchestration and Helm/Kustomize integration where the current ecosystem is stronger.

Every Rust candidate requires benchmark/threat-model evidence, parity tests, reproducible offline build, SBOM/provenance, and rollback.