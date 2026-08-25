# Versioned Management API Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a portal-compatible, statically verifiable first contract for Narwhal's versioned management API, event envelope, webhook policy, and offline CLI validation.

**Architecture:** Put the canonical reusable event shape in a JSON Schema Draft 2020-12 artifact, with a prose mapping document describing future `/api/v1`, async operation, and webhook boundaries. Keep `narwhalctl` offline: it validates a supplied envelope rather than pretending a destroyed cluster has a live API. A shell guard and the repository static regression suite exercise the real sample and a temporary invalid mutation.

**Tech Stack:** JSON Schema Draft 2020-12, Python 3 `jsonschema`, Bash, repository static regression harness.

**Spec:** `docs/common/versioned-management-api-contract.md`; GitHub `dasomel/narwhal#42`; portal reference `/Users/m/Documents/IdeaProjects/20.dasomel/idp/narwhal-portal/src/types/event-envelope.ts`.

## Global Constraints

- Preserve portal field names/types/semantics exactly, including nullable link fields and `source_event_id`.
- Fix `schema_version` to `"1.0"`; do not build a live endpoint, webhook delivery path, or fake cluster result.
- Shell scripts use `set -euo pipefail`, two-space indentation, and a `mktemp` cleanup trap.
- Validate locally with shellcheck, Python syntax checking, the CLI sample run, and `regression-check-kakao.sh --static`.

---

### Task 1: Define the reusable envelope artifact and mapping contract

**Files:**

- Create: `schemas/event-envelope-1.0.schema.json`
- Create: `examples/event-envelope.operation.started.json`
- Create: `docs/common/versioned-management-api-contract.md`

**Interfaces:**

- Consumes: portal `EventEnvelope`, `EventActor`, and `EventResource` shapes.
- Produces: Draft 2020-12 schema and a valid representative `operation.started` event for CLI and shell guards.

- [ ] **Step 1: Add a failing fixture with a non-`1.0` schema version**

```json
{ "schema_version": "9.9" }
```

- [ ] **Step 2: Write the Draft 2020-12 schema**

Require every canonical envelope key, enforce `schema_version: {"const":"1.0"}`, define
`actor` as `{id, type, displayName?}` with `user|system|service`, and define nullable portal
resource/linkage fields without UUID-only restrictions.

- [ ] **Step 3: Write the valid operation sample and mapping table**

Use `operation.started`, a business `correlation_id`, a distinct `request_id`, and an actor/resource
matching portal types. Document `/api/v1` compatibility, async operation terminal events, webhook
HMAC/retry/idempotency rules, and explicitly list live-cluster work as open.

- [ ] **Step 4: Validate the real sample against the schema**

Run: `python3 scripts/narwhalctl.py events emit --file examples/event-envelope.operation.started.json`

Expected: `VALID: ... conforms to canonical event envelope schema 1.0`.

### Task 2: Add the offline `narwhalctl` validation command

**Files:**

- Create: `scripts/narwhalctl.py`

**Interfaces:**

- Consumes: `narwhalctl events emit --file <Path> [--schema <Path>]`.
- Produces: exit `0` with a stable `VALID:` line, exit `1` for schema violations, and exit `2` for invalid JSON/missing dependency/input.

- [ ] **Step 1: Implement JSON loading and Draft 2020-12 validation**

```python
validator = Draft202012Validator(schema, format_checker=FormatChecker())
errors = sorted(validator.iter_errors(event), key=lambda error: list(error.absolute_path))
```

- [ ] **Step 2: Return a location-aware error for the fixture**

```text
ERROR: event validation failed at $.schema_version: '1.0' was expected
```

- [ ] **Step 3: Parse the CLI module**

Run: `python3 -c "import ast; ast.parse(open('scripts/narwhalctl.py').read())"`

Expected: zero exit status.

### Task 3: Make the contract executable in local and regression checks

**Files:**

- Create: `scripts/test/check-event-envelope-contract.sh`
- Modify: `scripts/test/regression-check-kakao.sh`
- Modify: `docs/common/lessons-log.md`

**Interfaces:**

- Consumes: CLI success/failure exit status and the committed sample event.
- Produces: R74 real-sample pass and R75 temporary-mutation rejection in `--static` output.

- [ ] **Step 1: Implement a guarded temporary-mutation shell check**

```bash
tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT
cp "${sample}" "${invalid_event}"
```

Set only `schema_version` to `9.9` in the copy and fail the guard if the CLI accepts it.

- [ ] **Step 2: Add R74/R75 with the existing real-file plus temporary-copy pattern**

Run the real guard for R74. For R75 mutate only the temporary JSON copy, then use `check_not`
against the CLI so a schema regression cannot pass just because the positive sample still does.

- [ ] **Step 3: Record the mutation-testing discriminator in the lessons log**

Sharpen the existing 2026-08-03 check lesson: a successful sample says nothing about rejection
behavior until an invalid temporary copy demonstrably fails.

- [ ] **Step 4: Run the full static suite**

Run: `bash scripts/test/regression-check-kakao.sh --static`

Expected: R74 and R75 PASS; any environment-dependent warning is reported rather than hidden.

## Self-Review

All issue #42 static-baseline requirements map to Tasks 1–3. This plan intentionally excludes
OpenAPI, live endpoints, persistent operation state, webhook secrets/delivery, and cluster tests;
the contract document names those as follow-up work. The interface names, field spellings, and
schema version match the portal reference. No placeholder scan terms are used as implementation
instructions.
