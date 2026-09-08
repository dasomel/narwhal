---
name: evidence-before-claim
description: Require scoped executable evidence before completion claims, including runtime evidence for Kubernetes-dependent behavior.
---

# Evidence Before Claim

## Intent
Completion claims must be backed by explicit checks appropriate to the affected layer.

## Evidence
Prefer documented tests, build/static checks, and real cluster/runtime verification when behavior depends on Kubernetes, networking, storage, identity, GitOps, or external services.

## Failure modes
- declaring done after edits only
- treating unit or mocked evidence as proof of cluster/runtime behavior
- omitting failed or unrun checks
