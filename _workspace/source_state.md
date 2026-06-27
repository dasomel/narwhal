# Narwhal IDP Source State Map (2026-04-20)

## 1. Cluster Scripts (scripts/cluster/) — 30 Scripts

**00-15 Main Scripts:**
- 00-kube-vip.sh — Control plane VIP (ARP, leader election)
- 01-nfs-server.sh — NFS server setup on master-1
- 02-init-cluster.sh — kubeadm cluster bootstrap
- 02-join-control-plane.sh — Join master nodes
- 02-join-worker.sh — Join worker nodes
- 03-cni-install.sh — Cilium CNI installation
- 04-addons.sh — metrics-server, Helm (default addons)
- 05-nfs-quota-agent.sh — NFS quota DaemonSet
- 06-phase2-start.sh — Phase 2 orchestrator (platform apps trigger)
- 07-cnpg.sh — CloudNative-PG operator + narwhal-db StatefulSet
- 08-1-networking.sh — Cilium post-config, Hubble UI
- 08-2-monitoring.sh — Prometheus stack (kube-prometheus-stack)
- 08-3-security.sh — APISIX gateway, cert-manager, Kyverno, OpenBao
- 08-4-storage.sh — SeaweedFS S3 storage
- 08-5-registry.sh — Harbor registry (ARM64: ghcr.io/dasomel/goharbor)
- 08-6-tls-routes.sh — APISIX TLS certificate termination
- 09-istio-ambient.sh — Istio v1.29.0 ambient mode (ztunnel + istio-cni)
- 10-dnsmasq.sh — DNS HA, hairpin zone for local.narwhal.internal
- 11-keycloak.sh — Keycloak Operator + StatefulSet (Keycloak v26.1.4)
- 11-2-keycloak-config.sh — Bootstrap Keycloak users, create applications/clients
- 11-3-keycloak-clients.sh — Configure OAuth/OIDC clients (kubernetes, apisix, idp-portal, etc.)
- 11-4-keycloak-apiserver.sh — API server OIDC integration
- 12-gitea.sh — Gitea v1.25.4 (Git server)
- 13-argocd.sh — ArgoCD v3.3.0 manifest install
- 14-gitops-bootstrap.sh — ArgoCD app-of-apps, sync gitops/apps/
- 15-idp-portal.sh — IDP Portal (Next.js 16.2.1 with NextAuth Keycloak)

**11-x Authentik Stale Scripts (DEPRECATED):**
- 11-authentik.sh — DEPRECATED (replaced by 11-keycloak.sh)
- 11-2-authentik-config.sh — DEPRECATED (replaced by 11-2-keycloak-config.sh)
- 11-3-authentik-clients.sh — DEPRECATED (replaced by 11-3-keycloak-clients.sh)
- 11-4-authentik-apiserver.sh — DEPRECATED (replaced by 11-4-keycloak-apiserver.sh)

**Stale Files:**
- bak/08-platform-apps.sh — Old platform apps provisioner (replaced by 06-phase2-start.sh)

**⚠️ Flag: Authentik References in 11-2-keycloak-config.sh + 08-3-security.sh**
- Scripts still reference `authentik.local.narwhal.internal` DNS + issuer URLs
- Migration incomplete: comment shows old authentik OIDC paths
- Updated 2026-04-07 per git log (commit 86a4953), but DNS aliases + comments not cleaned

---

## 2. GitOps Applications (gitops/apps/) — 23 App YAMLs

| App | Chart | targetRevision | Status |
|-----|-------|-----------------|--------|
| apisix | apisix | 2.9.0 | ✓ matches VERSIONS.md |
| apisix-infra | — | HEAD | Custom resource (no chart) |
| apisix-routes | — | HEAD | Custom resource (no chart) |
| cert-manager | cert-manager | v1.19.3 | ✓ matches VERSIONS.md |
| harbor | harbor | 1.18.2 | ✓ matches VERSIONS.md |
| headlamp | headlamp | 0.40.0 | ✓ matches VERSIONS.md |
| idp-portal | — | HEAD | Custom resource (no chart) |
| istio-base | base | 1.29.0 | ✓ matches VERSIONS.md |
| istio-cni | cni | 1.29.0 | ✓ matches VERSIONS.md |
| istiod | istiod | 1.29.0 | ✓ matches VERSIONS.md |
| keycloak | — | HEAD | Custom resource (Operator CR) |
| kyverno | kyverno | 3.7.0 | ✓ matches VERSIONS.md |
| loki | loki | 6.52.0 | ✓ matches VERSIONS.md |
| metallb | metallb | 0.15.3 | ✓ matches VERSIONS.md |
| openbao | openbao | 0.25.0 | ✓ matches VERSIONS.md |
| prometheus-stack | kube-prometheus-stack | 81.5.1 | ✓ matches VERSIONS.md |
| promtail | promtail | 6.17.1 | ✓ matches VERSIONS.md |
| seaweedfs | seaweedfs | 4.0.407 | ✓ matches VERSIONS.md |
| tempo | tempo | 1.24.4 | ✓ matches VERSIONS.md |
| velero | velero | 11.3.2 | ✓ matches VERSIONS.md |
| velero-ui | — | v0.10.1 | ✓ matches VERSIONS.md |
| ztunnel | ztunnel | 1.29.0 | ✓ matches VERSIONS.md |

**All chart versions align with VERSIONS.md.** ✓

---

## 3. GitOps Resources (gitops/resources/) — 19 Resource YAMLs

- alertmanager-config.yaml
- apisix-external-services.yaml
- apisix-infra.yaml
- apisix-routes.yaml
- cnpg-backup.yaml
- dev-namespace.yaml
- gitea-db.yaml
- grafana-dashboards.yaml
- grafana-datasources.yaml
- harbor-db.yaml
- idp-portal-k8s.yaml
- istio-ambient-policies.yaml
- keycloak-cr.yaml — Keycloak Operator CR (replaces authentik-cr.yaml)
- kyverno-policies.yaml
- metallb-config.yaml
- narwhal-db.yaml
- network-policies.yaml
- prometheus-alerts.yaml
- rbac-policies.yaml

---

## 4. VERSIONS.md — Component Matrix (86 entries)

**Key Versions:**
- K8s: v1.35.3 (kubeadm, kubelet, kubectl, containerd 1.7.28)
- CNI: Cilium v1.19.0 (kube-proxy replacement)
- Mesh: Istio v1.29.0 (ambient, ztunnel, istio-cni all v1.29.0)
- Gateway: APISIX 3.10.0 app / 2.9.0 chart
- IDP: **Keycloak v26.1.4** (Operator + StatefulSet) — **Authentik removed**
- Storage: SeaweedFS v4.07, CNPG v1.28.1, PostgreSQL v18.2
- Observability: Prometheus 81.5.1, Grafana (bundled), Loki 3.6.4, Tempo 2.9.0
- Backup: Velero v1.17.1
- Portal: IDP Portal 0.1.0 (Next.js 16.2.1, NextAuth 5.0.0-beta.30)
- Git: Gitea v1.25.4, ArgoCD v3.3.0

---

## 5. Vagrantfile — Configuration

| Variable | Value |
|----------|-------|
| K8S_VERSION | 1.35 |
| K8S_PATCH_VERSION | 1.35.1 |
| MASTER_COUNT | 3 |
| WORKER_COUNT | 3 |
| MASTER_MEMORY | 6144 MB (6GB) |
| WORKER_MEMORY | 6144 MB (6GB) |
| VIP_ADDRESS | 192.168.56.100 |
| DOMAIN | local.narwhal.internal (set in phase2) |
| METALLB_IP | 192.168.56.200 |
| POD_NETWORK_CIDR | 10.244.0.0/16 |
| SERVICE_CIDR | 10.96.0.0/12 |
| NFS_SERVER_IP | 192.168.56.100 (master-1) |
| Ubuntu Box | dasomel/ubuntu-24.04-xfs v0.2.2 |

---

## 6. Recent Git Commits (Last 5 Cluster/GitOps Changes)

1. **86a4953** (2026-04-07) — `feat: migrate from Authentik to Keycloak`
   - Replace Authentik with Keycloak across all OIDC configs
   - Fix Grafana OIDC, idp-portal Keycloak client, remove vault-agent annotations
   - Update keycloak ArgoCD app path, VERSIONS.md K8s v1.35.3

2. **c5341cd** (2026-04-06) — `feat: migrate IAM from Authentik to Keycloak`
   - Add Keycloak scripts (11-keycloak.sh, 11-2-keycloak-config.sh, 11-3-keycloak-clients.sh, 11-4-keycloak-apiserver.sh)
   - Add nfs-quota dashboard route

3. **175fd3a** (2026-04-06) — `fix: add nfs-quota redirect URI to hubble Keycloak client`
   - Update OIDC client configuration

4. **06d9169** (2026-02-10) — `feat: apisix: add apisix OIDC client to Keycloak`
   - Gateway OIDC integration

5. **bac90fe** (2026-03-30) — `chore: exclude embedded git repos from tracking`
   - .gitignore: idp-portal, idp, csp subdirectories

---

## 7. Stale Files & Compatibility Issues

**No `.bak`, `.old`, `.orig` files found.** ✓

**⚠️ Critical: Authentik → Keycloak Migration Incomplete**

Scripts with outdated Authentik references (11 files):
- 11-2-authentik-config.sh (active, UNUSED — replaced by 11-2-keycloak-config.sh)
- 11-3-authentik-clients.sh (active, UNUSED — replaced by 11-3-keycloak-clients.sh)
- 11-4-authentik-apiserver.sh (active, UNUSED — replaced by 11-4-keycloak-apiserver.sh)
- 11-authentik.sh (active, UNUSED — replaced by 11-keycloak.sh)
- 08-3-security.sh → contains comment: `issuerURL: https://authentik.local.narwhal.internal/application/o/apisix/` (outdated)
- 10-dnsmasq.sh → DNS zone: `${APISIX_IP} authentik.${DOMAIN}` (unused, should be removed)
- bak/08-platform-apps.sh (stale backup)

**Recommendation:** Clean up old 11-*-authentik-*.sh scripts and update DNS references in 10-dnsmasq.sh.

---

## Summary

- ✓ **All 23 Helm chart versions sync with VERSIONS.md**
- ✓ **19 gitops resources in place**
- ✓ **Vagrant configuration stable (3 master, 3 worker nodes)**
- ⚠️ **Authentik migration needs cleanup:** 4 unused scripts, stale DNS alias, outdated issuer URL comments
- ✓ **Last major change:** 2026-04-07 (Keycloak full migration)
- ✓ **No backup/stale files with extensions (.bak, .old, .orig)**

**Ready for cluster rebuild:** Confirm removal of deprecated Authentik scripts and update dnsmasq DNS zone before Phase 2.
