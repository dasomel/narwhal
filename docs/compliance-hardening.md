# Compliance Hardening & Risk-Accepted Exceptions

Status of the portal `/compliance` page (trivy-operator ClusterComplianceReport:
CIS 1.23, NSA 1.0, PSS baseline/restricted). Records what was hardened, and which
failing controls are **accepted risk** (fixing them would break the platform or
undo a deliberate design choice) vs. **deferred** (safe but not yet done).

## Applied hardening (2026-07-08)

| Area | Change | Controls fixed |
|------|--------|----------------|
| Control-plane profiling | `--profiling=false` on apiserver / controller-manager / scheduler (kubeadm `extraArgs` in `scripts/cluster/02-init-cluster.sh` + live static-pod patch on all 3 masters) | CIS 1.2.18, 1.4.1 (verified PASS) |
| Portal / Valkey workloads | `securityContext`: `runAsNonRoot`, drop ALL caps, `allowPrivilegeEscalation:false`, `seccompProfile:RuntimeDefault` (`gitops/charts/narwhal-platform/templates/narwhal-portal-k8s.yaml`) | Removed all critical/high config-audit findings for these two workloads (PSS 5.2.x / 5.7.2 contributions) |

Result: CIS 72% → 84% (33 → 19 failing controls), NSA 44% → 59%.

## Risk-accepted exceptions (do NOT "fix")

These failing controls are expected for this IDP platform. Changing them breaks
functionality or reverses an intentional trade-off.

1. **`--bind-address` = 0.0.0.0 on controller-manager / scheduler** (CIS 1.3.7, 1.4.2 —
   1.4.2 is CRITICAL). Deliberately set so Prometheus (off the control-plane node) can
   scrape `/metrics`. On the private `192.168.56.0/24` lab network this is not an
   exposure. Reverting to 127.0.0.1 blinds monitoring. See `02-init-cluster.sh`.
2. **Privileged / hostPath / hostNetwork / NET_RAW / root workloads** (CIS 5.2.2/5.2.3/
   5.2.5/5.2.6/5.2.7/5.2.8/5.2.10/5.2.12/5.2.13, 5.7.2/5.7.3; NSA 1.0/1.1/1.2/1.4/1.5/
   1.7/1.9/1.10; PSS baseline/restricted 3/4/5). The large counts are dominated by
   **platform components that require these privileges to function**: Cilium (NET_ADMIN,
   privileged, hostNetwork), Istio ambient (ztunnel/istio-cni), trivy node-collector
   (hostPath to read node files), CSI/NFS, kube-system system pods. These cannot be
   hardened without breaking CNI / mesh / storage / scanning.
3. **`kube-system` used by platform** (NSA 1.12) — the platform legitimately runs there.
4. **Broad RBAC / secret access / wildcards** (CIS 5.1.2, 5.1.3, 5.1.6) — required by
   operators (ArgoCD, Keycloak Operator, cert-manager, etc.).

## Deferred (safe, not yet done)

Worthwhile but out of the "quick safe win" scope; each needs care and control-plane
restarts on all 3 masters:

- **Node file permissions/ownership** (CIS 1.1.9 CNI, 1.1.12 etcd dir, 4.1.1 kubelet
  service, 4.1.7 CA files, 4.1.9 kubelet config) — `chmod`/`chown` on nodes via a
  provisioning step.
- **Secrets encryption at rest** (`--encryption-provider-config`) — real security win;
  needs an encryption config + key management + apiserver restart.
- **API audit logging** (`--audit-log-path` + policy file) — needs an audit policy
  mounted into the apiserver static pod.
- **`--anonymous-auth=false`**, `EventRateLimit`, `--kubelet-certificate-authority` —
  apiserver hardening (test probes first).
- Portal/Valkey `readOnlyRootFilesystem` (CIS 5.7.3 / KSV014) — Next.js writes cache/tmp
  at runtime; needs writable `emptyDir` mounts before enabling.

## How the page is fed

trivy-operator scanners must be enabled (`gitops/charts/narwhal-apps/templates/trivy-operator.yaml`):
`configAuditScannerEnabled`, `rbacAssessmentScannerEnabled`, `infraAssessmentScannerEnabled`,
`clusterComplianceEnabled` = true, and `compliance.reportType: all` (summary yields no
per-control detail). Reports regenerate on the `0 */6 * * *` cron; to force one, briefly
patch a report's `spec.cron` to `* * * * *`.
