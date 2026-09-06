# APISIX Admin API Trust Boundary & Access Guide

## Architecture & Security Boundary

The Apache APISIX Admin API (`http://apisix-admin.platform-system.svc.cluster.local:9180/apisix/admin`) is a control-plane interface that manages route definitions, upstreams, SSL certificates, and plugins. It is protected by dual-layer defense-in-depth:

1. **Network Layer (L3/L4 NetworkPolicy)**:
   - Resource: `gitops/resources/apisix-admin-ingress-policy.yaml` (`apisix-admin-ingress-policy` in `platform-system`).
   - Default-deny ingress behavior: Only explicit match rules can reach TCP port 9180.
   - Authorized principals:
     - APISIX Ingress Controller: Pods in `platform-system` with label `app.kubernetes.io/name: apisix-ingress-controller`.
     - Narwhal Portal: Pods in `devtools` with label `app: narwhal-portal`.
   - All unrelated workloads and namespaces (e.g. `monitoring`, `storage`, `database`, `default`) are dropped by CNI policy enforcement.

2. **Application Layer (L7 IP Allowlist & Authentication)**:
   - Config: `allow_admin` and `admin.allow.ipList` in `gitops/charts/narwhal-apps/templates/apisix.yaml`.
   - Allowed sources: `127.0.0.1/32` (localhost loopback) and `10.244.0.0/16` (Kubernetes pod network CIDR).
   - Broad RFC1918 supernets (`10.0.0.0/8`, `172.16.0.0/12`) and wildcard allowlists (`0.0.0.0/0`) are strictly forbidden.
   - Authentication: Requires `X-API-KEY` matching credentials in Secret `apisix-admin-key`.

3. **Public Exposure Restrictions**:
   - The service is strictly `type: ClusterIP`.
   - It is never exposed via `ApisixRoute`, `Ingress`, `NodePort`, `LoadBalancer`, or `hostNetwork`.

## Normal Controller & Application Access

- **APISIX Ingress Controller**:
  - Connects internally to `http://apisix-admin.platform-system.svc.cluster.local:9180`.
  - Authenticates via `APISIX_ADMIN_KEY` mounted from `secretKeyRef: apisix-admin-key:key`.
- **Narwhal Portal**:
  - Connects to `http://apisix-admin.platform-system.svc.cluster.local:9180`.
  - Authenticates via `APISIX_API_KEY` (admin) or `APISIX_API_KEY_READONLY` (viewer) from `apisix-admin-key`.

## Emergency / Break-Glass Procedures

In incident scenarios (e.g., etcd desynchronization or controller bootstrap deadlock described in `docs/common/apisix-etcd-recovery.md`), operators may require manual access to inspect or seed routes.

### Method 1: Local Port-Forwarding (Recommended)

Port-forwarding tunnels directly to localhost within the pod, matching `127.0.0.1/32`:

```bash
# 1. Fetch admin key
ADMIN_KEY=$(kubectl -n platform-system get secret apisix-admin-key -o jsonpath='{.data.key}' | base64 -d)

# 2. Port-forward the Admin API to localhost:9180
kubectl -n platform-system port-forward svc/apisix-admin 9180:9180

# 3. In another terminal, query or mutate Admin API
curl -i -H "X-API-KEY: ${ADMIN_KEY}" http://127.0.0.1:9180/apisix/admin/routes
```

### Method 2: Labeled In-Cluster Diagnostic Pod

When executing from within the cluster, diagnostic pods must carry the controller label to pass `apisix-admin-ingress-policy`:

```bash
# 1. Fetch admin key
ADMIN_KEY=$(kubectl -n platform-system get secret apisix-admin-key -o jsonpath='{.data.key}' | base64 -d)

# 2. Launch ephemeral probe pod with authorized label
kubectl -n platform-system run apisix-admin-debug --image=curlimages/curl:8.11.0 --restart=Never \
  --labels=app.kubernetes.io/name=apisix-ingress-controller \
  --command -- sleep 300

kubectl -n platform-system wait --for=condition=Ready pod/apisix-admin-debug --timeout=30s

# 3. Execute command against the Admin API
kubectl -n platform-system exec apisix-admin-debug -- \
  curl -s -H "X-API-KEY: ${ADMIN_KEY}" http://apisix-admin.platform-system.svc.cluster.local:9180/apisix/admin/routes

# 4. Clean up probe pod
kubectl -n platform-system delete pod apisix-admin-debug --wait=false
```

Any pod launched without `app.kubernetes.io/name: apisix-ingress-controller` (or outside authorized namespaces) will experience connection timeouts due to NetworkPolicy enforcement.
