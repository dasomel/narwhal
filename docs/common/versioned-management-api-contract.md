# Versioned Management API / CLI / Webhook / Event Contract

narwhal#42 defines the control-plane boundary that future Portal, Terraform, CI/CD and
ITSM callers share. This is a **static contract baseline**, not a live API or webhook
service: the Kakao cluster is destroyed, so no delivery, authentication or cluster query
has been runtime-verified.

The canonical machine-readable artifact is
[`schemas/event-envelope-1.0.schema.json`](../../schemas/event-envelope-1.0.schema.json).
It deliberately mirrors `narwhal-portal/src/types/event-envelope.ts` (portal#11), so a
producer or consumer can move between the portal pipeline and Narwhal control plane without
field translation.

## `/api/v1` policy

`/api/v1` is the first externally supported control-plane URL namespace. A future server
MUST expose only contract-stable resources beneath it; unversioned management endpoints are
not a public interface. The first narrow read operation is reserved as `GET /api/v1/status`;
it may be implemented only when it can report a real control-plane state.

- Additive optional response fields and new event types are compatible within `/api/v1`.
- Removing or changing the meaning/type of a published field requires `/api/v2`.
- `schema_version` versions the envelope shape and is fixed to `"1.0"` in this baseline;
  `event_version` versions the meaning of `data` for one `event_type`.
- A CLI or webhook consumer MUST reject an unsupported `schema_version`, preserve unknown
  top-level fields, and ignore unknown `data` members unless its event-type contract says
  otherwise. The schema therefore permits extension fields while requiring every canonical
  field.

The offline CLI is `python3 scripts/narwhalctl.py events emit --file <event.json>`. Despite
the verb, it makes no network call: it validates the candidate event and prints one stable
success or error line. This intentionally avoids implying a live control-plane endpoint.

## Canonical event envelope

All canonical keys are required, including nullable linkage keys. `null` means that the
relationship is unavailable or does not apply; omitting a key means the producer did not
construct the canonical envelope and is invalid. Timestamp strings are RFC 3339/JSON Schema
`date-time` values. Identifier fields remain strings rather than UUID-only values because
portal-compatible upstream systems can use their own stable IDs.

| Field | Type | Canonical meaning |
| --- | --- | --- |
| `event_id` | string | Unique Narwhal envelope record identifier. |
| `event_type` | string | Dotted control-plane lifecycle type, such as `operation.started` or `incident.created`; it is not a UI category. |
| `event_version` | string | Version of this event type's `data` semantics. |
| `schema_version` | literal `"1.0"` | Version of this envelope's common shape. |
| `occurred_at` / `received_at` | RFC 3339 strings | Time at source / time accepted by Narwhal's boundary; do not substitute one for the other. |
| `correlation_id` | string | Business-lifecycle ID propagated alert → incident → RCA → runbook → change → postmortem/CAPA → evidence. |
| `causation_id` | string or null | Parent event or correlation-context identifier, enabling reconstruction of the event chain. |
| `request_id` | string or null | One HTTP/API request identifier. |
| `operation_id` | string or null | A long-running control-plane operation identifier. |
| `incident_id` / `evidence_id` | string or null | Incident lifecycle / evidence bundle identifiers. |
| `trace_id` / `span_id` | string or null | OpenTelemetry tracing IDs. They diagnose a technical request path and never replace business `correlation_id`. |
| `source` / `source_version` | string / string or null | Producer name and producer version retained for audit. |
| `actor` | object | `{id, type, displayName?}`; `type` is exactly `user`, `system`, or `service`; `id` is non-empty. |
| `resource` | object or null | Optional `{cluster, namespace, kind, name, workload}` strings, matching the portal shape. |
| `idempotency_key` / `source_event_id` | string or null | Producer deduplication key / original producer event ID. Ingest callers SHOULD provide at least one. |
| `data` | JSON value | Event-type-specific payload. Envelope consumers must not infer business state from a title or description outside `data`. |

`source_event_id` is retained because portal#11 added it after the initial issue text: it is
the portal-compatible original producer-event identity and enables provider retry deduplication.

## Async operation model

Mutating `/api/v1` operations will be asynchronous. A server accepting one MUST create an
`operation_id`, return `202 Accepted` with that ID plus `correlation_id` and `request_id`, and
emit `operation.started`. It later emits exactly one terminal `operation.completed` or
`operation.failed` carrying the same `operation_id` and `correlation_id`. Future endpoints
are reserved as `GET /api/v1/operations/{operation_id}` and
`POST /api/v1/operations/{operation_id}:cancel`; cancellation is a requested state transition,
not proof that the underlying action stopped.

The portal-compatible creation rule is: adopt a non-empty inbound `X-Correlation-Id` as both
the operation's `correlation_id` and its `causation_id`; that `causation_id` carries causal
context and is not necessarily an `event_id`. Otherwise mint `operation_id` and use it as a new
`correlation_id` with null causation. Record a non-empty `X-Request-Id` unchanged. This preserves
the portal operation-context behavior while separating it from OpenTelemetry IDs.

## Webhook ingest and delivery policy

No webhook endpoint exists in this repository yet. When one is introduced, `/api/v1/events/ingest`
will accept this JSON Schema envelope and apply these required boundary rules:

- Verify `X-Narwhal-Timestamp` and `X-Narwhal-Signature: v1=<hex>` before parsing business
  payloads. `v1` is HMAC-SHA-256 over `<timestamp>.<raw request body>` using a per-webhook
  secret kept outside Git; reject missing, malformed, stale (over five minutes), and
  constant-time unequal signatures.
- Deduplicate atomically by `idempotency_key`, falling back to `source_event_id`, for a
  24-hour retention window. A duplicate returns the original accepted `event_id` and must not
  create another notification or lifecycle event. No key is a client error for external ingest;
  local producers may retain null keys in the shared schema.
- Delivery clients retry only retryable failures (network, `408`, `429`, and `5xx`) using
  exponential backoff at 1, 5, 30, 120 and 600 seconds, preserving the exact body and
  idempotency identity. A non-retryable `4xx` is recorded as a delivery failure.
- Redact tenant/security-policy-defined sensitive fields before persistence or fan-out, but
  retain `actor`, `received_at`, `source`, `source_version`, `event_version`,
  `correlation_id`, and the redaction decision in audit evidence.

## Static verification and open work

Run `scripts/test/check-event-envelope-contract.sh` to validate the committed sample and prove
that a temporary unsupported-version mutation fails. `scripts/test/regression-check-kakao.sh
--static` includes the same positive/negative contract checks as R74/R75.

Met by this baseline: `/api/v1` policy; common envelope JSON Schema; audit/correlation/
idempotency semantics; async operation and webhook policy; a no-network CLI validator; and
static positive/negative validation.

Open: OpenAPI generation, live API server, cluster/fleet/resource/policy endpoint prototypes,
operation persistence/cancellation, signature secret storage, replay store, real webhook
delivery, Portal consuming a live Narwhal API, and all cluster-backed tests. Those require
separate implementation and a rebuilt reachable cluster.
