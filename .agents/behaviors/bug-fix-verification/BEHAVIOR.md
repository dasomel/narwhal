---
name: bug-fix-verification
description: Require reproduction, minimal fixes, and regression verification with runtime evidence when the defect is cluster-dependent.
---

# Bug Fix Verification

## Intent
Prefer reproduce → failing evidence → minimal fix → same evidence passes → relevant regression suite.

## Evidence
For Kubernetes/network/storage/identity defects, distinguish mocked or unit evidence from real cluster verification.

## Failure modes
- guessing without reproduction
- symptom-only patches
- tests that mirror the implementation
- claiming runtime safety from mocks
