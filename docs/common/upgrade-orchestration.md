# Upgrade Orchestration Model

This is the canonical design reference for narwhal#46. It defines the dependency
order for the platform and implements one complete, statically verifiable pilot:
cert-manager. It is not a claim that the platform has an automatic upgrade
orchestrator or that any live-cluster upgrade has been exercised.

## Scope and operating boundary

The Kakao Cloud cluster is destroyed. The pilot therefore validates GitOps desired
state only. A later live operation must first push the approved GitOps commit with
[`scripts/gitops/push-to-gitea.sh`](gitops-push.md), then perform the live gates
listed below. `kubectl apply` is not a durable path because Argo CD self-heal
reverts it.

## Platform dependency graph

An edge means the row on the left must be healthy before starting the row on the
right. Versions remain pinned in [`VERSIONS.md`](../../VERSIONS.md); an upgrade
proposal must state the source and target pins for every changed component.

| Wave | Component | Depends on | Reason |
|---|---|---|---|
| 0 | Kubernetes control plane | approved etcd backup and node capacity | The API, scheduler, and CRD machinery are the substrate for every other rollout. |
| 1 | Cilium/CNI | Kubernetes control plane | Pod networking, Service routing, and kube-proxy replacement must settle before scheduling dependent workloads. |
| 2 | CSI/storage and SeaweedFS | Cilium | Stateful platform services need networked volumes and object storage before their controllers reconcile. |
| 3 | cert-manager | Cilium | Admission webhooks and certificate issuers must be ready before services request or rotate TLS material. |
| 4 | APISIX ingress and Istio | Cilium, cert-manager | Gateway routes and mesh workloads consume network reachability and certificates. |
| 5 | Argo CD/GitOps | Cilium, cert-manager, storage | It deploys the remaining desired state and needs repository/network/TLS dependencies available. |
| 6 | Keycloak and OpenBao | Argo CD, cert-manager, storage | Identity and secret services need GitOps reconciliation, TLS, and persistent data. |
| 7 | Harbor | Argo CD, cert-manager, storage, Keycloak | Registry storage, ingress TLS, and optional identity integration must be stable. |
| 8 | Prometheus/Grafana/Loki/Tempo | Argo CD, storage, Istio | Observability needs persistent backends and should not be changed before it can observe earlier waves. |
| 9 | Velero | storage, SeaweedFS, cert-manager | Backup locations and TLS must be healthy before relying on a backup or restore. |
| 10 | Portal and add-ons | APISIX, Istio, Argo CD, Keycloak, observability | User-facing services depend on routing, GitOps, identity, and their operational signals. |

The table is an ordering model, not an implementation of the other components'
upgrade strategies. Upgrading Kubernetes itself also requires its own provider and
node-drain plan; it is not performed by this pilot.

## Pilot: cert-manager

The source of truth is
[`gitops/charts/narwhal-apps/templates/cert-manager.yaml`](../../gitops/charts/narwhal-apps/templates/cert-manager.yaml).
It deploys chart `cert-manager` at `targetRevision: v1.20.2` into
`platform-system`, with CRDs enabled and Argo CD automated sync/self-heal. The
chart values now declare the following topology for controller, webhook, and
cainjector:

| Property | Declared value | Why |
|---|---|---|
| Replicas | 2 each | Each workload has a replacement candidate; controller and cainjector use leader election. |
| Strategy | `RollingUpdate`, `maxSurge: 1`, `maxUnavailable: 1` | Replace at most one replica at a time while allowing a temporary extra replica. |
| PDB | enabled, `minAvailable: 1` | A voluntary disruption cannot evict both replicas. |
| Spread | hostname, `maxSkew: 1`, `DoNotSchedule` | A two-pod workload does not deliberately co-locate when the cluster has suitable nodes. |

This is a rolling HA strategy, not recreate or HA failover. The webhook is the
availability-critical admission endpoint; the controller and cainjector preserve
reconciliation/injection continuity during the chart rollout. A PDB constrains
voluntary evictions only; it neither proves replica readiness nor protects a node
failure.

### Static preflight

Run before changing the chart pin:

```bash
scripts/cluster/preflight-cert-manager-upgrade.sh
```

It rejects a missing cert-manager chart pin, fewer than two replicas, missing
rolling-update limits, missing PDB declaration, or missing hostname topology spread
for each of the three deployments. It reads the GitOps Application only and does
not contact Kubernetes.

Before an actual upgrade, the live preflight must additionally establish all of
the following against the intended cluster:

- Each deployment has `status.readyReplicas == spec.replicas`.
- Each PDB reports `status.disruptionsAllowed >= 1` before a voluntary disruption.
- Ready pods are placed on distinct nodes as intended by the topology constraint.
- The `cert-manager-webhook` Service is reachable from the API server and its
  validating/mutating webhook configurations are present.
- Existing `Certificate` and `ClusterIssuer` resources are `Ready=True`.

The static script cannot establish any of these runtime facts.

### Checkpoint and rollback policy

Before creating the upgrade commit, capture a repository checkpoint:

```bash
scripts/cluster/capture-cert-manager-upgrade-checkpoint.sh \
  --output /safe/operator-controlled/cert-manager-<timestamp>
```

The checkpoint contains the Git commit, whether the Application manifest was
dirty, chart version pin, exact Application manifest, values snapshot, and SHA-256
hashes. It deliberately records `live_state_captured: false`; Kubernetes object
state, Helm release history, and certificate/Secret material are not available
from the repository and must be captured by a live runbook.

On a post-upgrade health-gate failure, the policy is: stop subsequent waves, retain
the failed revision and logs for audit, restore the checkpoint's chart pin and
values in a new Git commit, push it through `push-to-gitea.sh`, and observe the
same live gates. This is a rollback *model*, not an automatic rollback executor.
It must not delete CRDs, Certificates, Issuers, or Secrets as a generic rollback
step, because doing so can cascade-delete platform certificate resources.

### Post-upgrade health gates

The following live checks define success for this pilot, after Argo CD reports the
Application `Synced` and `Healthy`:

1. `cert-manager`, `cert-manager-webhook`, and `cert-manager-cainjector` each have
   `readyReplicas == spec.replicas` and `updatedReplicas == spec.replicas`.
2. No pod in `platform-system` selected by those components has `CrashLoopBackOff`
   during the five-minute observation window.
3. `cert-manager-webhook` serves admission requests from the API server, and both
   webhook configurations remain registered with a nonempty CA bundle.
4. All `ClusterIssuer` resources and every existing `Certificate` are `Ready=True`;
   no certificate has entered a new `Issuing=True`/`Ready=False` state during the
   observation window.
5. Every PDB permits at least one disruption only when its workload has the two
   declared Ready replicas; no availability alert for certificate expiry or webhook
   failure is firing.

## Explicitly not implemented in this pilot

- Component-specific upgrade execution for Kubernetes, Cilium, CSI/storage, APISIX,
  Istio, Argo CD, Keycloak, OpenBao, Harbor, observability, SeaweedFS, Velero,
  Portal, or other add-ons.
- Maintenance-window and approval workflow, which needs a live scheduling system.
- Multi-cluster staggered upgrades, blocked on unstarted narwhal#6.
- Actual automatic rollback execution, live health-gate execution, disruption or
  availability measurement, and post-upgrade smoke/regression automation, all of
  which require a live cluster.
- Direct integration with the air-gap bundle; an operator must separately confirm
  the proposed target chart/images are present and admissible before a live upgrade.
