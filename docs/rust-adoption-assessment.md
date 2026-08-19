# Rust Adoption Assessment

## Principle

Narwhal should not be rewritten wholesale in Rust. Rust is considered only where it provides a material benefit in one or more of:

- memory safety for security-sensitive parsers/privileged helpers
- deterministic single-binary/offline distribution
- high-concurrency or low-latency data-paths
- robust artifact/checksum/signature processing
- typed state machines that benefit from compile-time invariants

Existing Go/TypeScript/shell implementations remain the default where their ecosystem and maintainability are better.

## Candidate areas

### High
- offline artifact manifest/resolution verifier
- image/chart digest, checksum, signature and SBOM evidence processor
- sandbox/security policy validator

### Medium
- high-volume event/evidence normalization helper
- isolated parser helpers for upgrade/RCA evidence

### Keep in existing language
- Kubernetes control-plane/orchestration integrations that already align with the Go ecosystem
- Helm/Kustomize/YAML orchestration glue where Rust does not provide sufficient benefit

## Migration rule

Prefer a Rust sidecar/helper with a stable CLI or JSON contract before considering replacement of a larger component.

Every Rust candidate requires:

1. benchmark or threat-model evidence
2. test parity with the existing implementation
3. offline build reproducibility
4. SBOM/license/provenance output
5. rollback path to the existing implementation
