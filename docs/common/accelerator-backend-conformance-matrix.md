# Accelerator backend capability / conformance matrix (proposal)

> **Status:** contract proposal. Narwhal currently pins Kubernetes v1.35.7
> (`VERSIONS.md`) and does not deploy an accelerator DRA driver, device class,
> partition profile, or GPU quota (`docs/common/gpu-partitioning-policy.md`). No
> backend below is certified by this repository. Values marked **PROPOSED** are
> contract fields, not existing APIs, resources, labels, metrics, or endpoints.
>
> **Scope:** a focused capability vocabulary, backend profile matrix, and test
> boundary for NVIDIA-, AMD-, Intel-class Linux backends and Apple Metal/MPS as a
> reference profile. This document complements
> `docs/common/gpu-partitioning-policy.md`: that policy governs allocation,
> tenant quota, health gating, and release; this document describes what each
> adapter may claim and what evidence is needed to claim conformance.

## Repository evidence and issue corrections

- `VERSIONS.md` records Kubernetes v1.35.7. The repository policy says its current
  cluster does not establish DRA support; the issue's Kubernetes v1.36 statement
  therefore does not describe this repository's deployed baseline.
- `docs/common/gpu-partitioning-policy.md` defines proposed portable fields
  (`narwhal.io/accelerator`, `narwhal.io/accelerator-share`, `narwhal.io/backend`,
  `narwhal.io/profile`, `narwhal.io/share-mode`, `narwhal.io/capacity-unit`, and
  `narwhal.io/health`) and requires unsupported profiles to stay unallocatable.
- `docs/common/ai-llmops-extension-contract.md` says there is no GPU device plugin
  or training controller in `gitops/` or `scripts/`; GPU allocation is a platform
  boundary and an extension cannot assume a resource class exists.
- `gitops/resources/` and `scripts/` contain no Narwhal accelerator adapter,
  DRA DeviceClass, GPU Operator, Kueue configuration, or accelerator metrics
  contract. Accordingly, all backend and scheduler combinations below are
  `UNVERIFIED`, not claims about upstream product support.
- **ASSUMPTION:** KubeMetal remains the source of the Apple Silicon Metal/MPS
  reference implementation, while this repository owns only the portable
  contract. This repository does not contain KubeMetal issue #11 or Narwhal #81/#94
  acceptance artifacts, so their integration status cannot be asserted here.

## Status vocabulary and discovery record

Each capability is reported with exactly one status. Omission is invalid: adapters
must state `unavailable` or `not-applicable` where a feature cannot be used.

| Status | Meaning | Scheduling / fallback effect |
|---|---|---|
| `supported` | The adapter implements the capability and passes its required conformance checks for the declared backend/version/profile. | Eligible only if workload constraints match. |
| `partial` | A named subset or constraint is implemented; `limitations[]` must enumerate it. | Eligible only when the request fits the declared subset; otherwise reject/pending with a stable reason. |
| `unavailable` | Relevant capability is absent, not measured, not configured, or not yet verified. | Must not be inferred or silently emulated; reject/pending if required. |
| `not-applicable` | The capability has no meaningful interpretation for this backend/profile. `reason` is required. | A request requiring it is incompatible. |

The following record shape is **PROPOSED** and illustrative, not a registered API:

```yaml
apiVersion: narwhal.io/accelerator-capability/v1alpha1 # PROPOSED
backend: <stable-adapter-id>
backendVersion: <immutable-version>
platform: <os-architecture>
kubernetesVersion: <exact-version>
driverOrRuntime: <name-and-version-or-not-applicable>
capabilities:
  <capability-id>:
    status: supported|partial|unavailable|not-applicable
    profile: <profile-id-or-null>
    limitations: []
    evidenceRefs: []
```

`backend`, API version, and field names are **PROPOSED**. Record exact Kubernetes,
OS/architecture, adapter, driver/operator, and runtime versions; `latest` or a
version range alone is not reproducible evidence. An absent/stale discovery record
is `unavailable`. Capability discovery is descriptive and grants no tenant
entitlement; allocation still follows
`docs/common/gpu-partitioning-policy.md`.

## Capability taxonomy and acceptance rules

Every advertised capability uses one of the following IDs. Adapter-native names may
appear as `mapping` evidence but do not replace these portable meanings.

| Capability ID | What `supported` must mean | Offline-checkable assertions | Hardware/runtime-only assertions |
|---|---|---|---|
| `inventory.whole-device` | Stable opaque device ID, node, parent ID when applicable, profile, health, and allocatable integer units are discoverable. | Fixture/schema validation; IDs are unique; required fields and units are present; serial numbers are absent from tenant-visible records. | Enumerated inventory matches devices visible to the driver; hot add/remove and parent-child identity are accurate. |
| `allocation.dedicated` | One whole allocatable device can be claimed, bound, released, and accounted exactly once. | Admission/scheduler fixtures cover whole unit, duplicate claim, quota, profile mismatch, pending and release-pending outcomes. | Real claim binds to intended device; concurrent claims do not double allocate; release is confirmed reusable. |
| `partitioning.hardware` | Backend creates or discovers hardware-enforced partitions with declared parent and capacity mapping. | Partition profile fixtures and parent-child consistency; no claim that fixture proves physical isolation. | Create/use/release each supported partition profile; verify limits and sibling fault-domain mapping. |
| `sharing.time-slicing` | Multiple workloads share execution by temporal multiplexing; no memory/compute isolation is implied. | Sharing mode is explicit; integer capacity and per-claim limits validate; no conversion to a hardware partition. | Run concurrent workloads and observe contention/fairness semantics and cleanup. |
| `sharing.multiprocess` | Backend-supported process-level sharing (for example, an MPS-class mode) is configured and its constraints are published. | Configuration/profile contract and incompatible request fixtures. | Processes execute under the intended sharing mode; termination and failure behavior are exercised. |
| `monitoring.utilization` | Utilization is a measured ratio with documented source, scope, and sampling interval. | Units, scope, timestamps, stale threshold, and missing-value representation validate. | Telemetry corresponds to an exercised device/workload and has expected freshness. |
| `monitoring.memory` | Used/total device memory is measured in bytes and device/partition scope is explicit. | Unit and scope mapping; unavailable partition-level values remain unavailable. | Compare measurements to workload allocation/use; validate partition scope and freshness. |
| `monitoring.thermal` | Temperature is measured with sensor/source and unit documented. | Unit, sensor scope, and missing/stale behavior validate. | Read actual sensor and verify sensible change under workload/thermal load. |
| `monitoring.power` | Power is measured with sensor/source and unit documented. | Unit and missing/stale behavior validate. | Validate measurement against a real active device and supported sensor. |
| `health.device` | Adapter reports `healthy`, `degraded`, `unhealthy`, or `unknown`; only healthy is allocatable by default. | State transitions and fail-closed allocation fixtures; unknown/stale excludes new claims. | Inject or observe device fault; verify state and allocation exclusion; test recovery gate. |
| `isolation.compute-memory` | Declared isolation boundary and guarantees between allocations are explicit. | Policy rejects stronger guarantees than the profile advertises. | Co-tenant stress/fault test validates the stated boundary; software time slicing must not claim hardware isolation. |
| `isolation.security-boundary` | Device access, privileged components, tenant visibility, and operator ownership are described and tested. | RBAC/admission fixtures ensure tenants cannot mutate device inventory, taints, or platform quota; opaque IDs only. | Verify device nodes and host interfaces are inaccessible outside authorized workloads; validate driver privilege boundary. |
| `storage.fast-path.gds` | GPU-direct storage path is configured and the eligible storage/device paths are declared. | If requested but absent, compatibility rejects it; configuration metadata can be validated. | Verify actual I/O uses the fast path and compare evidence to an explicitly defined baseline. |
| `network.fast-path.collective` | Collective communication path such as NCCL is available for the declared topology. | Required topology and incompatible placement fixtures validate. | Run collective workload across allocated devices and verify successful data exchange and path. |
| `network.fast-path.rdma` | RDMA/RoCE path is available for a declared fabric and workload placement. | Required fabric/profile selectors are explicit; absent flag rejects request. | Verify RDMA path/counters, end-to-end workload, and failure behavior on the target fabric. |
| `benchmark.contract` | Adapter runs the same workload contract and emits comparable metadata, not necessarily comparable raw performance. | Bundle contains workload ID/version, input digest, parameters, units, warmup/repetition policy, backend profile, and evidence references. | Execute workload; preserve raw results, environment, and repeated-run variance. |
| `offline.bundle` | Required adapter artifacts and evidence can be validated without network access. | Digest/signature presence, dependency closure, and offline fixture replay. | A clean isolated host installs/loads the bundle and completes backend-specific checks; hardware capabilities remain hardware-only. |

Metric normalization is semantic, not a promise that every backend exposes every
sensor. **PROPOSED** normalized names: `utilization.ratio` in `[0,1]`,
`memory.used.bytes`, `memory.total.bytes`, `temperature.celsius`, and
`power.watts`. Each sample also carries `observedAt`, `sampleWindow`, `scope`
(`device` or `partition`), and `source`. Missing, stale, unsupported, or
partition-unavailable values are null with an explicit reason; they are never
reported as zero. A backend may report `not-applicable` only where the sensor or
concept genuinely has no meaning; unimplemented collection is `unavailable`.

## Backend profile matrix

The profile column describes evidence to collect, not assumed support. Every cell
starts at `unverified`; promotion to a capability status requires evidence matching
the criteria above.

| Backend class | Adapter/runtime profile to identify | Allocation and partitioning | Monitoring and health | Isolation | Storage/network fast paths |
|---|---|---|---|---|---|
| Apple Silicon / KubeMetal reference | Metal/MPS implementation version, macOS version, architecture, and workload runtime. **ASSUMPTION:** KubeMetal #11 supplies this adapter. | Whole-device inventory/allocation: `unverified`; hardware partitioning: `unavailable` unless implementation evidence says otherwise; time/process sharing: separately measured. | Utilization, memory, and thermal values are candidates from issue text, but each metric remains `unverified` until source, scope, units, freshness, and evidence pass. GPU time must not be treated as utilization without a defined measurement. | Publish process/device boundary and host access limits; hardware isolation is `unavailable` absent explicit evidence. | GDS, CUDA/NCCL, and RDMA are `not-applicable` to the Metal/MPS profile. Generic local storage benchmark is possible only through `benchmark.contract`, not a GDS claim. |
| NVIDIA Linux | Exact Kubernetes, DRA driver, GPU Operator, CUDA/runtime, and device firmware/driver versions. | DRA DeviceClass/ResourceClaim/ResourceSlice mapping and whole-device allocation are `unverified` here. MIG hardware partitioning, time-slicing, and MPS are distinct capabilities; report each separately. | Metric names/scope and health source must be adapter-specific and proven; DRA inventory alone does not prove monitoring or health. | Dedicated, MIG, time-slicing, and MPS have distinct isolation semantics; never collapse them into one `isolated` boolean. | GDS is optional; NCCL and RDMA/RoCE are optional and topology-dependent. Each requires its own capability and runtime evidence. |
| AMD Linux class | Exact adapter/driver/operator/runtime versions, device model/profile, Kubernetes and architecture. | Allocation, partitioning, and sharing remain `unverified` until a concrete adapter mapping and tests exist. Do not infer MIG, CUDA, DRA, or NVIDIA semantics. | Declare each normalized metric and health signal independently; absent collection is `unavailable`. | State tested boundary per profile; do not inherit another vendor's guarantees. | Storage/network fast paths each independently `unverified` until selected stack and workload prove them. |
| Intel Linux class | Exact adapter/driver/runtime versions, device model/profile, Kubernetes and architecture. | Allocation, partitioning, and sharing remain `unverified` until a concrete adapter mapping and tests exist. Do not infer NVIDIA/AMD partition or sharing behavior. | Declare each normalized metric and health signal independently; absent collection is `unavailable`. | State tested boundary per profile; do not inherit another vendor's guarantees. | Storage/network fast paths each independently `unverified` until selected stack and workload prove them. |
| Other accelerator | Backend-specific adapter/runtime and exact versions. | No default capability. Each capability requires explicit discovery and conformance evidence. | No default metric mapping. | No default isolation claim. | No default fast-path claim. |

## Scheduling compatibility matrix

These are Narwhal contract gates, not upstream compatibility claims. Until the
repository pins and exercises the relevant versions, every combination is
`UNVERIFIED`. A capability status does not imply scheduler integration.

| Combination / request | Required decision before admission | Current repository status |
|---|---|---|
| Kubernetes × adapter/driver/operator/runtime | Require exact version tuple and a reviewed compatibility record; missing tuple or record is `unavailable`. | Kubernetes v1.35.7 is pinned in `VERSIONS.md`; no accelerator tuple is recorded. |
| DRA allocation | Require API discovery, enabled feature state, driver resources, and claim allocation/release evidence for that exact tuple. | No active DRA driver or GPU resource class per `docs/common/gpu-partitioning-policy.md`. |
| DRA × Kueue quota | Require Kueue version and explicit device quota accounting test for the declared claim/resource model. | No Kueue deployment/configuration or conformance evidence found in repository. |
| DRA × TAS (Topology Aware Scheduling) | Require a test proving claim allocation and topology placement predicates compose for the exact versions. If not tested, reject topology-constrained request as unsupported. | `UNVERIFIED`; do not repeat issue assertions about upstream support as Narwhal fact. |
| DRA × device taints / prioritized lists | Require separate tests for taint eligibility and ordering semantics, including denied/missing toleration. | `UNVERIFIED`; proposed taint behavior is specified in `gpu-partitioning-policy.md`. |
| DRA × MPS or time-slicing | Require explicit sharing capability and Kueue/scheduler quota semantics; DRA allocation does not itself prove sharing. | `UNVERIFIED`; no sharing mode is deployed. |
| DRA × MultiKueue | Require a cross-cluster test preserving claim identity, capacity/quota accounting, and supported backend profile on target cluster. | `UNVERIFIED`; no MultiKueue configuration found. |
| DRA × ElasticWorkload | Require versioned integration test for resize, claim lifecycle, quota update, and rollback. | `UNVERIFIED`; no ElasticWorkload integration found. |
| Partitionable device × sharing mode | Require one declared capacity model per allocatable unit and atomic concurrent allocation tests. Partition and time-slicing capacities cannot be conflated. | Contract behavior is described by `gpu-partitioning-policy.md`; implementation absent. |

Decision rule: if any requested capability, compatibility tuple, or scheduler
combination is `unavailable`, `not-applicable`, or `UNVERIFIED`, admission must
return an explicit incompatibility/unsupported result before workload placement.
If placement-time information is required, the claim remains pending with a stable
reason. No silent fallback from a partition to a whole device, from dedicated to
shared, or from a requested fast path to a conventional path is allowed. A user may
retry only after changing the request or an operator publishes new evidence.

## Conformance profiles and evidence bundle

### Offline profile (no accelerator hardware)

Offline checks are contract evidence only and cannot mark hardware behavior
`supported`:

1. Validate capability-record schema, enum completeness, exact version tuple,
   evidence references, units, and status reason/limitations.
2. Replay fixtures for missing capability, stale discovery, each non-supported
   status, profile mismatch, unavailable metric, unhealthy/unknown health,
   quota exhaustion, absent taint toleration, sharing mismatch, and claim release
   pending. Assert reject/pending reason and no fabricated allocation/usage record.
3. Validate benchmark manifest and dependency bundle digests; block network access
   and verify the offline fixture can resolve every declared artifact.
4. Assert scheduler combinations without a compatibility record are reported
   `UNVERIFIED` and cannot pass admission.

This repository currently has no accelerator schema, adapter fixture suite, or
scheduler integration test. These criteria are **PROPOSED** acceptance tests, not
claims that `make test` exercises accelerators. The existing partitioning policy
also states that its contract checks are future tests.

### Hardware-only profile

For each claimed `supported` or `partial` hardware capability, execute against the
declared exact backend/version tuple:

1. Discover device and profile inventory; compare with driver-visible hardware.
2. Allocate concurrently to capacity boundary, prove no over-allocation, then
   release and prove capacity becomes reusable only after backend confirmation.
3. Exercise partition/share behavior and verify the documented isolation limits.
4. Run a workload to correlate normalized metrics and health with observed device
   state; inject or simulate supported fault/recovery paths.
5. For GDS, NCCL, or RDMA, prove the workload used the named fast path, rather than
   merely completing successfully.
6. Run the benchmark contract and retain raw output plus exact environment and
   parameters. Compare results only under matching workload/schema/conditions.
7. For scheduler rows claimed supported, run the named Kueue/TAS/MultiKueue/
   ElasticWorkload/taint/sharing scenario end to end.

### Required evidence bundle

The bundle schema is **PROPOSED**. At minimum it contains:

| Field | Requirement |
|---|---|
| `bundleVersion` | Immutable schema version. |
| `backend` and versions | Adapter, OS/architecture, Kubernetes, driver/operator, runtime, and firmware where relevant. |
| `capabilityResults[]` | Capability ID, status, profile, test ID, result, limitations, and reason. |
| `environment` | Hardware model/class, topology, enabled feature gates, scheduler versions/config, and relevant storage/network path; redact serials and credentials. |
| `benchmark` | Workload ID/version, input digest, parameters, warmup/repetitions, units, raw results, and timestamp. |
| `artifacts[]` | Name, immutable version, digest, provenance/signature reference where available. |
| `logs[]` | Sanitized command/result logs with stable relative paths and timestamps. |
| `summary` | Pass/fail by capability, skipped hardware-only checks, and explicit unsupported/unverified items. |

Offline reproducibility requires all required artifacts to be digest-pinned and
present in the bundle's dependency closure. A bundle that omits a hardware-only
check is valid offline evidence only if it labels that result `not-run`; it cannot
upgrade the capability status.

## Decision record

- **D1 —** Use the capability policy's four values (`supported`, `partial`,
  `unavailable`, `not-applicable`) and record `UNVERIFIED` in matrices as an
  evidence state, not as a fifth capability status. Cost: consumers must distinguish
  missing verification from a runtime report. Escape hatch: after a schema exists,
  encode verification separately from capability status.
- **D2 —** Keep whole-device, hardware partitioning, time-slicing, and process
  sharing as separate capabilities. Cost: more discovery fields and tests. Escape
  hatch: a backend may bundle fields in its adapter but must still publish separate
  results.
- **D3 —** Mark all vendor and scheduler combinations unverified until this repo
  has exact version tuples and evidence. Cost: no compatibility is implied by this
  draft. Escape hatch: promote individual cells only with attached conformance
  evidence.
- **D4 —** Require explicit admission failure/pending for absent capabilities and
  never emulate missing fast paths or isolation. Cost: some workloads cannot
  schedule on reduced-capability backends. Escape hatch: user changes requirements
  explicitly or operator publishes verified capability.
- **D5 —** Treat KubeMetal's Metal/MPS role and issue links as assumptions because
  their implementation is outside this checkout. Cost: profile completeness awaits
  KubeMetal evidence. Escape hatch: replace assumptions with implementation-linked
  evidence when available.

## Out of scope

This document does not install drivers/operators, implement an adapter or schema,
choose backend versions, certify upstream DRA/Kueue combinations, create actual
benchmarks/evidence bundles, or establish KubeMetal #11 / Narwhal #81/#94 integration.
