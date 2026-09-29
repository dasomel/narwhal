# GPU partitioning and consumable-capacity policy

> **Status:** policy proposal; this repository does not currently deploy a GPU DRA driver,
> GPU device class, partition profile, or GPU quota. Values and names marked **PROPOSED**
> are contracts for a future implementation, not existing Kubernetes objects.
>
> **Scope:** vendor-neutral control-plane policy for dedicated and shared accelerator
> allocations. Driver installation, vendor-specific partition creation, firmware, and
> hardware operations remain the responsibility of a separately reviewed implementation.

## Repository baseline

`VERSIONS.md` pins Kubernetes v1.35.7 and describes this repository's current cluster;
the issue's Kubernetes v1.36 beta statement is not evidence that Narwhal runs 1.36.
The repository search for DRA device classes, GPU resource keys, MIG profiles, and
time-slicing configuration found no active implementation. The `resourceclaims` entries
in `scripts/ops/velero-rbac-api-resources.snapshot.txt` and the Velero permissions in
`gitops/charts/narwhal-apps/templates/velero.yaml` describe backup API permissions only;
they do not establish DRA support.

Tenant ownership is represented by `narwhal.io/team` and tenant namespaces in
`gitops/resources/tenants/README.md`. `gitops/resources/argocd-projects.yaml` makes
tenant Applications namespace-scoped and prevents tenants from editing their own
`ResourceQuota` and `LimitRange`. `gitops/resources/dev-namespace.yaml` is the current
quota example. This policy extends those ownership boundaries; it does not claim those
files currently enforce accelerator quotas.

## Resource and inventory contract

The following names and fields are **PROPOSED**. A backend adapter maps them to DRA
device attributes and consumable capacity, or to a documented backend-native resource
interface when DRA is unavailable. A vendor resource name such as `nvidia.com/gpu` must
not be presented as the portable policy API.

| Contract field | Meaning | Required rule |
|---|---|---|
| `narwhal.io/accelerator` | Logical accelerator device claim class | Claim one or more whole devices; never use this field to imply fractional compute. |
| `narwhal.io/accelerator-share` | Shared logical slice claim class | Allowed only when inventory explicitly advertises share mode and capacity units. |
| `narwhal.io/backend` | Adapter identity | Informational/capability selector; workload policy must not require a vendor value. |
| `narwhal.io/profile` | Stable profile identifier, e.g. `gpu-small` | Inventory value, not a request to reconfigure a device. **PROPOSED** examples are illustrative. |
| `narwhal.io/share-mode` | `dedicated`, `time-sliced`, or `multiplexed` | Exactly one advertised mode per allocatable unit. MPS-class execution is represented as a backend capability, not as guaranteed hardware partitioning. |
| `narwhal.io/capacity-unit` | Unit for schedulable capacity | Must be declared as integer units with an adapter-defined mapping; units are not assumed to equal GPU memory or a fixed percentage. |
| `narwhal.io/health` | `healthy`, `degraded`, `unhealthy`, or `unknown` | Only `healthy` is allocatable by default. `degraded` requires an explicit policy exception; `unhealthy` and `unknown` are excluded. |

An inventory record **PROPOSED** for each allocatable unit contains: stable device ID,
node reference, backend and driver versions, parent-device identity, profile, share
mode, capacity-unit definition and total, current allocatable/allocated capacity,
health, and supported constraints. Parent identity is needed to prevent a scheduler
from treating sibling partitions as independent fault domains. Do not expose serial
numbers or other hardware identifiers to tenants; use an opaque stable ID in operator
telemetry.

Discovery reports capability, not entitlement. A profile is schedulable only after the
adapter reports it and the operator has approved its profile-to-capacity mapping. MIG
profile inventory, time-slice count, and MPS behavior remain backend-specific facts;
the portable policy stores those as advertised capabilities and does not manufacture
equivalence between vendors.

## Allocation, quota, and scheduling rules

The tenant is the namespace's `narwhal.io/team` owner. Platform-managed tenant
manifests, rather than tenant-owned Applications, set accelerator quota, following the
`ResourceQuota`/`LimitRange` ownership rule in `gitops/resources/argocd-projects.yaml`.
The quota key and DRA quota integration are **PROPOSED** and must be validated against
the selected Kubernetes/DRA release before enforcement.

| Policy | Rule |
|---|---|
| Dedicated claim | Requests one whole allocatable unit. Admission rejects fractional quantities and sharing selectors. |
| Shared claim | Requests positive integer capacity units from an advertised shared profile. Admission rejects values below the profile minimum, above its per-claim maximum, or above tenant quota. |
| Tenant quota | **PROPOSED:** `narwhal.io/accelerator` counts whole devices and `narwhal.io/accelerator-share` counts share capacity units. Namespace quota is an upper bound on outstanding claims, not a reservation that guarantees immediate placement. |
| Fairness | The scheduler may choose among eligible claims, but may not exceed namespace quota or backend capacity. No preemption of another tenant's healthy running claim is implied. Fair-share ordering/borrowing is **PROPOSED** and must be specified by the quota controller before implementation. |
| Claim lifetime | Capacity is charged from successful allocation until the claim is released and the adapter confirms it is reusable. A deleted Pod alone does not prove capacity release. |
| Unschedulable | No compatible healthy capacity, unsupported profile/mode, or taint mismatch leaves the claim pending with a stable reason; it must not silently fall back from dedicated to shared or from one profile to another. |

Scheduling eligibility is the intersection of: requested profile and mode; node/device
advertised capacity; health; workload node selectors/affinity; device taint tolerations;
tenant quota; and backend constraints. A claim binds only after all predicates pass.
For a shared unit, allocation must be atomic against its remaining consumable capacity;
two concurrent claims cannot both consume the same final units.

### Dedicated versus shared gate

Workloads must declare **PROPOSED** `narwhal.io/accelerator-class: dedicated|shared` in
their claim template. Missing or unknown values are rejected. `dedicated` can select
only a whole-device/dedicated profile. `shared` can select only an explicitly approved
shared profile and must set a positive capacity request. Tenant opt-in is not sufficient
to enable sharing: the operator must enable the profile, quota, adapter capability, and
telemetry mapping together. The policy does not promise isolation equivalent to a
hardware partition for time-slicing or MPS-class sharing.

### Taints and tolerations

Device/node taints are **PROPOSED** scheduling gates, not permission grants. A
workload toleration is accepted only if its tenant policy allows the key/effect and its
claim still satisfies health/profile/quota rules. A `NoSchedule` device taint without
matching toleration makes that unit ineligible. `NoExecute` prevents or evicts use as
defined by the adapter's Kubernetes integration. Tenant workloads may not set or remove
device taints; only the platform operator/adapter owns them. Tolerating a taint never
overrides `unhealthy`/`unknown` exclusion.

### Error and health behavior

| Condition | Required behavior |
|---|---|
| Capability/profile absent or adapter version unsupported | Do not publish the profile as allocatable; new claims remain pending/rejected with `UnsupportedCapability`. |
| Insufficient free consumable capacity | Keep claim pending with `InsufficientCapacity`; do not oversubscribe quota or capacity. |
| Profile or share mode mismatch | Reject admission where statically knowable; otherwise leave pending with `ProfileMismatch`. Never fallback implicitly. |
| Claim release not confirmed | Keep capacity charged; mark it unavailable for reuse and surface `ReleasePending`. |
| Unit health becomes `unhealthy` | Exclude it from new allocation, preserve allocation/Pod evidence, and isolate action to the affected unit. Eviction/restart behavior follows the backend contract and must not be assumed safe. |
| Health is `unknown` or telemetry is stale | Fail closed for new allocation. Existing workload handling requires a backend-specific validated policy; do not assert it is unaffected. |
| Parent physical device fails | Mark all child units unhealthy unless the backend proves finer isolation. |

Health recovery does not automatically restore allocatable status until the adapter
reports healthy and the operator-defined recovery check succeeds. Existing claims are
not silently rebound to a different physical device.

## Usage and cost evidence

Each allocation/release evidence record **PROPOSED** correlates claim UID, Pod UID,
namespace, `narwhal.io/team`, opaque device/unit ID, parent ID, profile, mode, requested
and granted capacity units, allocation/release timestamps, health transitions, and
adapter version. Usage telemetry must distinguish *allocated capacity* from measured
utilization; a time-slice share count is not utilization. Missing claim/team correlation
is an accounting error and must be reported as unattributed rather than assigned to a
default tenant.

Cost export integration with issues #67/#69 is a follow-on: this repository evidence
does not identify their schemas or an existing GPU cost pipeline. **ASSUMPTION:** those
issues define the target cost/usage record interfaces. Until confirmed, emit no invented
cost endpoint or billing unit; retain allocation and utilization evidence for a future
mapping.

## Compatibility and offline verification

`VERSIONS.md` is the current component version source. It records Kubernetes v1.35.7;
this repository contains no supported DRA driver/backend/version combination, so the
matrix below is intentionally `UNVERIFIED`, not a compatibility claim.

| Kubernetes | DRA API/feature state | Driver/backend | GPU hardware | Status |
|---|---|---|---|---|
| v1.35.7 (repo pin) | Must be discovered from the actual cluster feature gates and API discovery | None configured in this repository | Any | DRA/GPU policy runtime unsupported here; contract-only |
| Other Kubernetes versions | Verify API availability, feature maturity/gates, and consumable-capacity semantics against that release | Each adapter/version pair requires its own evidence | Matching backend hardware | `UNVERIFIED` until tested |

The offline profile can verify policy structure without GPU hardware:

1. Validate inventory and policy documents against a versioned schema once one exists;
   until then, review required fields in this document manually. Schema is **PROPOSED**.
2. Check tenant manifests preserve `narwhal.io/team`, platform-owned quota, and the
   `dev-*`/`tenants` boundaries in `gitops/resources/tenants/README.md` and
   `gitops/resources/argocd-projects.yaml`.
3. Exercise admission/scheduler contract fixtures for: dedicated whole-unit request;
   approved shared positive units; missing class; min/max violations; quota exhaustion;
   incompatible profile; missing toleration; unhealthy/unknown unit; concurrent final
   capacity requests; and release-pending capacity.
4. Assert error reasons and that invalid/pending claims do not produce allocation or
   cost usage records. These are future contract checks, not existing repository tests.
5. Run the repository's current static suite (`make test`, documented by `Makefile` and
   `docs/common/test-strategy.md`) for regressions in existing behavior. It cannot prove
   DRA API compatibility, driver allocation, partition creation/release, GPU health
   isolation, utilization accuracy, or billing correctness.

Hardware/runtime acceptance still requires, for each supported matrix row: discover
capability and inventory, allocate and release distinct profiles, verify dedicated and
shared policy gates and quota under concurrent claims, inject a partition and parent
health failure, and correlate allocation to measured usage. Offline success must never
be reported as this runtime evidence.
