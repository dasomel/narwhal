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
   - Authentication: Requires `X-API-KEY` matching credentials in Secret `apisix-admin-key`. The admin and viewer keys are generated randomly by `scripts/cluster/08-1-networking.sh` (`resolve_apisix_keys`); the public upstream default keys are never accepted or reused, and no key literal exists in git.

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

## Rotating the admin/viewer keys (live cluster)

Run it on master-1, whose kubeconfig is `/home/vagrant/.kube/config-local` (created by `02-init-cluster.sh`, the same path on Vagrant and Kakao; `stage-kakao-nodes.sh` reproduces the `/home/vagrant/{scripts,charts}` layout the script sources).

Kakao (re-stage first so the node has this script, `lib.sh` and the bundled charts; the SSH key, bastion proxy and `<ssh-user>` are the ones `set-config-kakao.sh` uses):

```bash
./scripts/cloud/stage-kakao-nodes.sh <master-1-ip>
ssh -i "$SSH_KEY" -o ProxyCommand="ssh -i $SSH_KEY -W %h:%p <ssh-user>@<bastion-ip>" <ssh-user>@<master-1-ip> \
  'sudo env KUBECONFIG=/home/vagrant/.kube/config-local bash /home/vagrant/scripts/cluster/rotate-apisix-admin-key.sh'
```

Vagrant (`scripts/` is a synced folder):

```bash
vagrant ssh master-1 -c 'sudo env KUBECONFIG=/home/vagrant/.kube/config-local bash /home/vagrant/scripts/cluster/rotate-apisix-admin-key.sh'
```

The script does, in this order:

1. Secret: new random keys replace `platform-system/apisix-admin-key` (`key`, `viewer`); the previous values are kept in a 0600 temp file for rollback.
2. APISIX config: a configmap that still holds literal keys (legacy bootstrap) is rewritten; a GitOps-rendered configmap holds only `APISIX_ADMIN_KEY`/`APISIX_VIEWER_KEY` env placeholders, so ArgoCD has nothing to revert.
3. APISIX restart (reads the Secret env at start).
4. Ingress controller: helm release pointed at the Secret (`config.apisix.existingSecret`), re-labelled `istio.io/dataplane-mode=none`, restarted.
5. Portal: `devtools/narwhal-portal-secrets` (`APISIX_API_KEY`, `APISIX_API_KEY_READONLY`) is patched and `deploy/narwhal-portal` restarted.
6. Verification through `kubectl port-forward svc/apisix-admin` (the pod loopback, which `allow_admin` permits; the APISIX image has no curl): old admin key 401, new admin key 200, new viewer key 200, no 401 in the controller log. Values never reach argv or the terminal.

If the run is interrupted (INT/TERM) or any step fails after step 1, a rollback is triggered (previous Secret, configmap and portal Secret restored, APISIX restarted, controller re-applied, old key re-verified), so APISIX and the controller never stay on different keys. The script prints `ROLLBACK COMPLETE`, or `ROLLBACK INCOMPLETE` with the path of the kept 0700 backup directory (previous keys and configmap; delete it once healthy) and the advice to re-run the script, which is idempotent and converges both consumers on one new key. A Secret that holds env placeholders (GitOps-converted) is backed up from the configmap literal instead.

Re-running `08-1-networking.sh` on a cluster that still holds the public default also replaces it, but it does not re-sync the portal; use the rotate script, or re-run `13-2-narwhal-portal-bindings.sh`.

"No key literal in git" covers tracked files (R256). The vendored ingress-controller chart in the untracked airgap bundle still ships the upstream default as an unused chart value; 08-1 overrides it with `existingSecret`.
