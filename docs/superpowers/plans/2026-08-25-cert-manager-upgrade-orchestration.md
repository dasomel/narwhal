# Cert-manager Upgrade Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a static, GitOps-backed cert-manager upgrade-orchestration pilot that is safe to validate without the destroyed Kakao Cloud cluster.

**Architecture:** Keep the dependency graph and live-only operational policy in one canonical reference document. Make the cert-manager Application declare HA topology, PDB, and rolling-update intent, then validate that contract with a standalone preflight script. A repository checkpoint script captures the exact Git revision and Application values needed to prepare a later rollback; it never executes a rollback.

**Tech Stack:** Bash, yq, Git, Argo CD Application YAML, the existing static regression harness.

**Spec:** `docs/common/upgrade-orchestration.md`

## Global Constraints

- Pilot scope is cert-manager only; the other eleven component execution slices remain unimplemented.
- No live cluster is available, so every automated check must be static and clearly separate live-state gates.
- YAML mutations use `yq`; shell scripts use `set -euo pipefail` and 2-space indentation.
- GitOps changes are committed to this repository; a later cluster operation must use `scripts/gitops/push-to-gitea.sh`.

---

### Task 1: Define the upgrade contract

**Files:**
- Create: `docs/common/upgrade-orchestration.md`
- Modify: `docs/common/lessons-log.md`

- [x] **Step 1: Define the platform dependency graph and the cert-manager pilot strategy.**
- [x] **Step 2: Specify static versus live preflight, checkpoint, health-gate, rollback, and out-of-scope boundaries.**
- [x] **Step 3: Record the static-preflight regression discriminator in the lessons log.**

### Task 2: Encode and check cert-manager HA intent

**Files:**
- Modify: `gitops/charts/narwhal-apps/templates/cert-manager.yaml`
- Create: `scripts/cluster/preflight-cert-manager-upgrade.sh`

- [x] **Step 1: Set two replicas, RollingUpdate limits, PDBs, and hostname spread constraints for controller, webhook, and cainjector.**
- [x] **Step 2: Implement a manifest-only preflight that rejects missing version, HA, PDB, topology, or rolling-update declarations.**
- [x] **Step 3: Run the preflight against the real Application manifest.**

### Task 3: Capture rollback inputs and prevent regression

**Files:**
- Create: `scripts/cluster/capture-cert-manager-upgrade-checkpoint.sh`
- Modify: `scripts/test/regression-check-kakao.sh`

- [x] **Step 1: Capture the Git commit, dirty-state marker, chart revision, values snapshot, and checksums into a caller-selected checkpoint directory.**
- [x] **Step 2: Add a real-file regression PASS case and a mutated temporary-manifest negative case for the preflight.**
- [x] **Step 3: Run shellcheck and the static regression suite.**
