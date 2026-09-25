# smolvm tool sandbox evaluation

Status: research-only decision for narwhal#212

Date: 2026-09-25

## Decision

**D1 — Keep smolvm outside Narwhal core for now.** The proof of concept works for a one-shot, host-side validation task on this Apple Silicon macOS host. There is no current Narwhal service that accepts generated commands and executes them at a management boundary, so adding a runtime integration now would create a new execution surface without an owner or caller. Revisit this as an optional adapter if an agent/tool runner is introduced and its host platform is defined.

**D2 — Fetch and verify OCI inputs before starting the sandbox; keep the VM network disabled while the command runs.** A registry image reference could not be pulled with VM networking disabled. Passing a digest-pinned local OCI archive worked and preserved the no-network execution boundary. The adapter must accept only pre-fetched image artifacts with verified digests; it must not turn on VM networking just to pull an image.

**D3 — Treat VM isolation as preparation/validation only.** A sandbox result never grants Kubernetes permission. Any apply, sync, or other cluster mutation must pass through the existing Narwhal identity, RBAC, approval, and policy path after the result is checked.

## Phase A: threat and use-case boundary

Current host-side execution includes provisioning and cluster scripts under `scripts/cluster/`, the `scripts/up.sh` orchestration path, and local validation under `scripts/test/`. The checked-in workflows invoke validation tools against repository content. This audit did not find a generic service that executes user or AI supplied shell commands. Future generated shell/Python helpers, rendered manifests, Helm values, and policy inputs are untrusted inputs if an agent is allowed to produce them.

Process/container isolation alone shares the host kernel and depends on the host process's filesystem and credential boundaries. The sandbox must therefore receive no kubeconfig, service-account token, cloud/provider credential, SSH agent, home directory, Docker socket, or writable checkout. Hardware virtualization adds a guest-kernel boundary, but does not remove risk in the host hypervisor, VM agent, OCI parser, or explicitly shared mounts.

Never place credentials in the sandbox input or environment. Treat stdout, stderr, generated files, and exit diagnostics as untrusted data: cap their size, redact before persistence, and do not place them in logs, caches, or domain responses without the existing secret-scrubbing path.

## Phase B: local proof of concept

The host was Apple Silicon macOS. `smolvm` v1.18.2 was downloaded to a temporary directory, and the release archive SHA-256 was checked against the GitHub Releases API asset digest before extraction:

| Input | Immutable identity used |
|---|---|
| smolvm binary archive | `smol-machines/smolvm` v1.18.2, `darwin-arm64`; SHA-256 `ba4f6d95f5245063e99ec388dc59eda24514464d6894211d0d163e8447bedf71` |
| Helm validator image | `docker.io/alpine/helm@sha256:57b8041193d7b278878eec07a21565c159f4daaa713db75e9bdfa8c0d83a34e7` (Linux/arm64 manifest) |
| Kubeconform validator image | `ghcr.io/yannh/kubeconform@sha256:cb8ca5a6e9bfb60b1858d65cba57c27f8080befcd5282b21518b860192b91e31` (Linux/arm64 manifest) |
| Chart input | Copy of `gitops/charts/narwhal-apps`, mounted at `/workspace` read-only |
| Custom-resource input | 24 checked-in CR instances selected from `gitops/resources/*.yaml`; vendored schemas mounted read-only from `schemas/crds/` |

The tagged Helm image index digest was `e7ecbf4a200dea73d64bfb8cb0936829164945f2b4d02a0274093073ee8d264f`; the Kubeconform image index digest was `85dbef6b4b312b99133decc9c6fc9495e9fc5f92293d4ff3b7e1b30f5611823c`. The image manifests above pin the selected platform variant. No image tag was used as the execution identity.

The first direct pull failed because image pulling used the network-disabled VM path. The two images were then copied by digest into local Docker-archive files with `skopeo`; smolvm consumed those files without `--net`. This is a required staging step for offline execution, not a reason to grant network to the workload.

Observed run evidence:

- `smolvm machine run --timeout 60s --image <local Helm archive> --volume <workspace>:/workspace:ro -- <validation command>` returned exit code 0. `helm lint` reported `1 chart(s) linted, 0 chart(s) failed`; `helm template` rendered 101,815 bytes.
- From that guest, a request to `https://example.com` failed (`NETWORK_BLOCKED`), writing to `/workspace` failed (`MOUNT_WRITE_BLOCKED`), and kubeconfig, service-account token, and `KUBECONFIG` checks reported absent. Guest identity was uid/gid `0:0`; guest kernel was Linux `6.12.95`. Wall time was 6.08 seconds.
- The digest-pinned Kubeconform image validated the 24 selected CR instances against the mounted vendored CRD schemas: `Valid: 24, Invalid: 0, Errors: 0, Skipped: 0`; exit code 0, wall time 0.34 seconds.
- `smolvm machine ls --json` returned `[]` after both runs. The command was ephemeral and its VM was removed on exit.
- There are no `kustomization.yaml`, `kustomization.yml`, or `Kustomization` files in this checkout, so a Kustomize validation target does not exist.

The repeatable operator-run harness is `scripts/test/smolvm-validation-poc.sh`. It requires smolvm v1.18.2, Skopeo 1.24.1, and yq v4.53.6; fetches the two images by their platform manifest digests with an empty registry auth file; verifies the resulting archive SHA-256 values; and runs both guests without `--net` or inherited host environment. Each VM is capped at 2 vCPUs, 2048 MiB, 20 GiB storage, and 60 seconds. The script writes stdout, stderr, VM identity, runtime version, timestamps, duration, exit status, and capability probes to `SMOLVM_EVIDENCE_DIR` (or a new temporary evidence directory). It deletes its staging workspace and verifies the temporary smolvm profile has no remaining machines.

Run it with the verified smolvm wrapper on `PATH`, or pass `SMOLVM_BIN=/path/to/smolvm`; optionally set `SMOLVM_EVIDENCE_DIR` to an empty directory to retain the run evidence at a chosen location. The test was rerun after adding the harness: Helm lint/render passed with 101,815 rendered bytes, all network/write/credential probes were denied, and Kubeconform reported 24 valid resources with no invalid, error, or skipped resources.

On macOS, stage the VM inputs under `/tmp`: the default `$TMPDIR` resolves under `/var/folders`, which caused libkrun to fail starting the VM with `EINVAL` in this environment. The validation guest still sees only explicit read-only mounts. The script is intentionally manual and macOS/Apple Silicon only; CI runners do not have the host prerequisites verified for this PoC.

Reproduction shape (use local archives already verified by digest, and mount only a temporary workspace copy):

```sh
smolvm machine run --timeout 60s --image ./helm-validator.tar \
  --volume ./workspace:/workspace:ro -- \
  sh -c 'helm lint /workspace/narwhal-apps && helm template narwhal-apps /workspace/narwhal-apps'

smolvm machine run --timeout 60s --image ./kubeconform-validator.tar \
  --volume ./custom-resources:/workspace:ro --volume ./schemas/crds:/schemas:ro -- \
  /kubeconform -strict -summary \
  -schema-location '/schemas/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  /workspace/custom-resources.yaml
```

These runs prove the local VM and validation path on this host. They do not prove macOS Intel, Linux KVM, Windows, CI-hosted execution, escape resistance, or a production evidence pipeline.

## Phase C: proposed execution contract

Any future adapter should validate a versioned `SandboxExecutionSpec` before starting a VM:

| Field | Required contract |
|---|---|
| `image` | OCI platform manifest digest; image bytes fetched and verified before VM start |
| `command` | Argument array, never concatenated into a host shell string |
| `input` | Read-only artifact/workspace reference plus content digest |
| `resources` | Explicit CPU, memory, disk, and wall-clock limits with conservative defaults |
| `mounts` | Empty by default; each mount explicitly granted, read-only unless a named output directory is required; reject home, credential, socket, and device paths |
| `network` | Disabled by default; any exception is an explicit capability grant with exact destinations, not a boolean convenience flag |
| `credentials` | Empty; no kubeconfig, service-account token, provider token, SSH agent, or inherited secret environment |
| `result` | Requested and effective capabilities, image/input digests, runtime version, command identity, start/end time, exit code, bounded stdout/stderr digests, and cleanup result |

The caller must compare requested and effective capabilities and reject widening. A timeout, denied mount/network probe, credential-presence probe, non-zero exit, or cleanup failure must produce a failed result. Persist only bounded, redacted evidence. Never interpret sandbox success as approval to perform a cluster action.

## Phase D: integration choice

| Option | Best fit | Cost or boundary |
|---|---|---|
| smolvm | One-shot local preparation or validation on a supported host, outside Kubernetes | New host-side runtime and artifact-staging lifecycle; host/architecture coverage must be proven per runner |
| Kubernetes `RuntimeClass` with gVisor | Cluster-scheduled workloads where the nodes and CRI are configured for the handler | Requires node runtime configuration and scheduling policy; remains a Kubernetes workload boundary |
| Kubernetes `RuntimeClass` with Kata Containers | Cluster workloads that need a hardware-virtualized Pod boundary | Requires compatible node hypervisor/runtime and cluster integration; remains governed by Kubernetes scheduling and RBAC |

Kubernetes documents `RuntimeClass` as selecting a configured CRI runtime handler for Pods. gVisor is an OCI runtime with a userspace kernel boundary; Kata represents a Kubernetes Pod as a VM. Those cluster-native options are the right comparison for workloads that belong in the cluster. smolvm is a candidate only for a future host-side tool runner; it is not a replacement for RuntimeClass, Kata/gVisor, namespaces, RBAC, or admission policy.

Before an optional adapter is accepted, define supported runner hosts, prefetch/verification ownership, concurrency and resource limits, evidence retention, cancellation behavior, and negative tests for network, mounts, credentials, and cross-boundary cluster actions. Until then this stays research-only.

## References

- [smolvm README](https://github.com/smol-machines/smolvm) and [agent reference](https://github.com/smol-machines/smolvm/blob/main/AGENTS.md)
- [smolvm v1.18.2 release](https://github.com/smol-machines/smolvm/releases/tag/v1.18.2)
- [Kubernetes RuntimeClass](https://kubernetes.io/docs/concepts/containers/runtime-class/)
- [gVisor documentation](https://gvisor.dev/docs/)
- [Kata Containers Kubernetes support](https://github.com/kata-containers/kata-containers/blob/main/docs/design/architecture/kubernetes.md) and [virtualization architecture](https://github.com/kata-containers/kata-containers/blob/main/docs/design/virtualization.md)
