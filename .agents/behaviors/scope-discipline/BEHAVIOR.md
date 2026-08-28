---
name: scope-discipline
description: Keep changes within the smallest coherent scope and preserve Kubernetes, GitOps, permission, and source-of-truth boundaries.
---

# Scope Discipline

## Intent
Solve the requested problem without unrelated cleanup or architecture drift.

## Decision
Treat exported APIs, RBAC/permission widening, destructive operations, cluster topology changes, and GitOps source-of-truth changes as design-level scope expansion.

## Failure modes
- drive-by refactoring
- unreviewed RBAC or permission widening
- bypassing GitOps ownership
- changing cluster topology without explicit design scope
