# Multi-Cluster Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each running Narwhal cluster a safe, self-identifying credential-export path that can be registered by narwhal-portal without committing credentials.

**Architecture:** Add an operator-invoked exporter beside the existing portal binding script. It reuses the already-declared `devtools/narwhal-portal` ServiceAccount and `narwhal-portal` ClusterRoleBinding, validates that identity, and writes a `0600` bundle containing the endpoint, CA, token, kubeconfig, and portal registration metadata. Keep fleet control-plane parameterization as documented follow-up work rather than claiming a static implementation can make the destroyed cluster multi-cluster-ready.

**Tech Stack:** Bash, kubectl, Python 3 JSON serialization, Kubernetes TokenRequest API, repository static regression harness.

**Spec:** `docs/common/multicluster-control-plane.md`; GitHub `dasomel/narwhal#6`; portal reference `/Users/m/Documents/IdeaProjects/20.dasomel/idp/narwhal-portal/src/types/cluster.ts`.

## Global Constraints

- Never commit, log, or put a sample token, password, kubeconfig credential, or CA bundle in Git.
- Reuse `narwhal-portal` RBAC; do not create broader or parallel reader RBAC.
- Shell scripts use `set -euo pipefail`, two-space indentation, `umask 077`, and explicit input validation.
- The current Kakao cluster is destroyed; static checks prove repository contracts only, not live registration, rotation, or fleet health.

---

### Task 1: Export a per-cluster Portal credential bundle

**Files:**

- Create: `scripts/cluster/13-3-export-narwhal-portal-cluster-credentials.sh`

**Interfaces:**

- Consumes: active `kubectl` context, `--cluster-id`, `--cluster-name`, `--environment`, `--provider`, and `--output-dir`.
- Produces: `registration.json` (non-secret), `credentials.env` and `kubeconfig` (mode `0600`) under the requested output directory.

- [x] Validate the portal-compatible RFC 1123 cluster ID and declared provider/environment.
- [x] Verify the existing `devtools/narwhal-portal` ServiceAccount and `narwhal-portal` ClusterRoleBinding before minting a TokenRequest token.
- [x] Extract the active API server and CA data; fail closed if either is missing.
- [x] Emit portal registration metadata containing only credential environment-variable names, then emit secret-bearing files with restrictive permissions and no secret output.

### Task 2: Record the lifecycle and readiness boundary

**Files:**

- Create: `docs/common/multicluster-control-plane.md`
- Modify: `docs/common/lessons-log.md`

**Interfaces:**

- Consumes: portal `ClusterRegistrationInput` and the existing GitOps RBAC declaration.
- Produces: cluster-ID assignment, secure delivery/rotation procedure, and a file-level single-cluster-assumption audit.

- [x] Specify the stable `CLUSTER_ID` and provider-neutral registration fields.
- [x] Distinguish the portal registry's credential references from secret material and describe the secure deployment-secret handoff.
- [x] List bootstrap assumptions by file and state which are intentionally deferred.
- [x] Add the credential-export discriminator to the Shell lessons log.

### Task 3: Make static contract regressions visible

**Files:**

- Modify: `scripts/test/regression-check-kakao.sh`

**Interfaces:**

- Consumes: exporter source and design document.
- Produces: static check IDs that require the exporter, `umask 077`, RBAC reuse, and portal-only credential references.

- [x] Add positive static checks for the exporter and the design contract.
- [x] Run ShellCheck at warning severity on the modified script and the complete static regression suite.
