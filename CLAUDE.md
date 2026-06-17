# Narwhal - Claude Code Guide

> Vagrant-based Kubernetes Internal Developer Platform (IDP) cluster automated provisioning project

## Quick Overview

An infrastructure project that automatically provisions a complete Kubernetes IDP stack (GitOps, SSO, Monitoring, Storage, Backup) using Vagrant VMs in a local environment.

---

## Plan Mode Guide (Shift+Tab x2)

When Plan mode is needed in Narwhal:
- Adding a new component
- Major modifications to existing scripts
- Changing GitOps app structure
- Version upgrades

---

## Mistakes Log (Compounding Engineering)

> Add entries here whenever Claude makes a mistake. The same mistake will not be repeated.
> Request CLAUDE.md updates with the `@.claude` tag during code reviews.

### Shell Script Mistakes
| Date | Mistake | Fix |
|------|---------|-----|
| 2025-01-27 | Script written without `set -e` | Always add `set -euo pipefail` as the first line |
| - | Variable expansion error in heredoc | Use `<<'EOF'` (quoted) to prevent variable expansion, `<<EOF` to allow it |
| - | Missing `-y` flag in apt commands | Always use `apt-get install -y` |

### Kubernetes/Helm Mistakes
| Date | Mistake | Fix |
|------|---------|-----|
| - | Missing namespace creation | Use `--create-namespace` or `CreateNamespace=true` syncOption |
| - | Ignoring CRD dependencies | Create CR after Operator installation, use ArgoCD sync-wave |
| - | Attempted to resize PVC | PVC can only be expanded, not shrunk |
| 2026-01-30 | OAuth2 Proxy cookie-secret empty value | `cookieSecret: ""` causes error, generate exactly 32 bytes with `openssl rand -hex 16` |
| 2026-01-30 | OAuth2 Proxy service.port deprecated | Use `service.portNumber` (v7.x chart) |
| 2026-01-30 | Keycloak issuer URL mismatch | `insecure_oidc_skip_issuer_verification = true` or match Keycloak hostname setting |
| 2026-01-30 | Loki chunks/results cache failure | `chunksCache.enabled: false`, `resultsCache.enabled: false` for dev environment |
| 2026-01-30 | Headlamp Helm repo URL changed | Use `https://kubernetes-sigs.github.io/headlamp/` |
| 2026-01-30 | Keycloak service name typo | `keycloak-service` (not `keycloak`), specify port 8080 |
| 2026-02-04 | YAML parsing error when modifying with sed | Use `yq` for safe YAML modification (e.g., API server manifest OIDC config) |
| 2026-02-05 | Helm `--set` nodeSelector boolean error | Use `--set-string nodeSelector.key=true` (force string) |
| 2026-02-14 | kube-vip `vip_subnet` value contains `/` | Use `"32"` (NOT `"/32"`), internally combines `address + "/" + vip_subnet` |
| 2026-02-14 | kube-vip kubeconfig path not recognized | Mount at `/.kube/config` (distroless image HOME=/) |
| 2026-02-14 | kube-vip admin.conf VIP circular dependency | Create separate `kube-vip.conf`, change server address to local IP |
| 2026-02-14 | kube-vip static pod created before init (chicken-and-egg) | master-1 creates manifest after init, bootstrap with manual VIP binding |
| 2026-02-14 | kubeadm join detects VMware NAT interface | `--apiserver-advertise-address=192.168.56.x` must be specified |
| 2026-02-14 | VMware Vagrant private_network IP not assigned | Create netplan directly in `01-prerequisites.sh`, `chmod 600` required |
| 2026-02-14 | API server OOM restart on Master 4GB RAM (during platform apps installation) | Current topology: Master has NoSchedule taint (control-plane only, 4GB OK), Worker 6GB runs platform apps |
| 2026-06-07 | Master 4GB still too tight at steady state: apiserver ~2GB (no mem limit) + mandatory DaemonSets ~0.8GB → ~567Mi headroom on master-3 → apiserver SIGKILL (exit 137 via /livez timeout) + intermittent NodeNotReady flapping (also broke metrics-server → portal CPU/mem blank) | **Fixed**: bumped `MASTER_MEMORY` 4096→6144 in Vagrantfile. Apply: rolling `vagrant reload` one master at a time. Partial: exclude falco DaemonSet from masters |
| 2026-02-14 | ArgoCD v3.x applicationsets CRD exceeds 262KB | Use `kubectl apply --server-side --force-conflicts` |
| 2026-02-14 | Helm `--wait` causes release rollback on timeout | Remove `--wait` for non-critical apps, use `--timeout` only |
| 2026-02-15 | Velero CRD hook musl/glibc incompatibility (ARM64) | Set `upgradeCRDs: false`, alpine/k8s musl cannot execute in velero glibc container |
| 2026-02-15 | registry.k8s.io/kubectl is distroless (no shell) | Use `docker.io/alpine/k8s:1.31.4` (includes shell+kubectl) |
| 2026-02-15 | Traefik routes applied before GatewayClass created | Wait loop for Traefik deployment+GatewayClass before applying routes |
| 2026-02-15 | etcd container cannot execute `sh -c` | etcd is distroless, call `kubectl exec -- etcdctl ...` directly |
| 2026-02-15 | alpine/k8s image tag has no `v` prefix | Use `"1.31.4"` (NOT `v1.31.4`), verify Docker Hub tag format |
| 2026-02-15 | ArgoCD v3.x `server.insecure` wrong location | Must set in `argocd-cmd-params-cm` (NOT `argocd-cm`). `argocd-cm` is legacy |
| 2026-02-15 | worker/master-2 DNS resolves to public IP | Set `Domains=~local.narwhal.io` in `systemd-resolved` + use master dnsmasq as DNS |
| 2026-02-15 | Keycloak `groups` scope missing -> `invalid_scope` error | Create realm-level `groups` client scope + add mapper + assign as default scope to all clients |
| 2026-02-15 | ArgoCD SSO `x509: certificate signed by unknown authority` | Add `oidc.tls.insecure.skip.verify: "true"` to `argocd-cm` (self-signed cert) |
| 2026-02-15 | Headlamp `extraArgs` ignored at root level | Must place in `config.extraArgs` for container args injection |
| 2026-02-15 | Harbor `configureUserSettings` change rejected by API | Injected via env vars so cannot modify via API, change Helm values + redeploy |
| 2026-02-18 | Cilium + Istio CNI conflict (cni.exclusive defaults to true) | `cni.exclusive=false` required, prevents Cilium from deleting other CNI configs |
| 2026-02-18 | Cilium socket LB bypasses ztunnel traffic | `socketLB.hostNamespaceOnly=true` required, prevents mesh traffic from bypassing ztunnel |
| 2026-02-18 | Istio CRD annotation exceeds 262KB (ArgoCD) | Add `ServerSideApply=true` to ArgoCD syncOptions |
| 2026-02-18 | Istio 1.28 does not support K8s 1.35 | Use Istio 1.29.x (supports K8s 1.31~1.35) |
| 2026-02-18 | Istio images docker.io only (no alternative) | Docker OSS rate limit exempt, `docker.io/istio/*` usage unavoidable |
| 2026-02-19 | CiliumClusterwideNetworkPolicy `endpointSelector: {}` blocks all traffic | Empty endpointSelector selects all pods -> deny-by-default activated. Never use empty selector in CCNP |
| 2026-02-19 | CoreDNS `forward . /etc/resolv.conf` dnsmasq loop | Master's resolv.conf is 127.0.0.1 (dnsmasq) -> CoreDNS internal loop. Use `forward . 8.8.8.8 8.8.4.4` explicitly |
| 2026-02-19 | Istio ambient HBONE port 15008 blocked by NetworkPolicy | Ambient mesh uses HBONE (15008) between pods. Must add 15008/TCP to NetworkPolicy ingress |
| 2026-02-19 | OAuth2-Proxy `insecure_oidc_skip_issuer_verification` does not skip TLS verification | Only skips issuer, TLS verification is separate. Need to add `ssl_insecure_skip_verify = true` |
| 2026-02-19 | Traefik Gateway API CRD field manager conflict | Extract chart CRDs -> apply with `--server-side --force-conflicts` -> `helm install --skip-crds` |
| 2026-02-24 | Keycloak Operator auto-generated NetworkPolicy missing HBONE 15008 | Operator manages `keycloak-network-policy` so cannot modify directly. Create separate `keycloak-allow-hbone` NetworkPolicy for 15008/TCP (see `11-keycloak.sh` pattern) |
| 2026-02-24 | Keycloak hostname v1/v2 option conflict | Do not use `hostname-url` (v1 deprecated) in `additionalOptions`. v2: `hostname.hostname` + `hostname.strict: true` + `proxy.headers: xforwarded` |
| 2026-02-24 | yq inserts shell quotes into URL -> API server crash | Use `yq -i ".spec... += [\"--flag=value\"]"` (NOT `'...'`). Use double quotes for outer quoting when shell variable expansion needed |
| 2026-02-24 | HTTPRoute does not exist when adding API server OIDC flags | Must create Keycloak HTTPRoute before OIDC verification in `11-keycloak.sh`. Runs before GitOps bootstrap (14) |
| 2026-02-25 | Keycloak OIDC token `aud` claim only contains `account` (K8s API server Unauthorized) | Must add audience mapper to Keycloak `kubernetes` client: `oidc-audience-mapper`, `included.client.audience=kubernetes`. Without it: `oidc: expected audience "kubernetes" got ["account"]` error |
| 2026-02-25 | API server `--oidc-ca-file` missing -> JWKS verification fails with self-signed cert | `--oidc-ca-file=/etc/kubernetes/pki/oidc-ca.crt` required when Keycloak uses self-signed cert. Extract cert: `openssl s_client -connect ... \| openssl x509 -outform PEM` |
| 2026-02-25 | `kcadm.sh --format csv --noquotes \| tail -1` returns wrong ID | CSV format has unstable result order/fields. Use `kcadm.sh get ... 2>/dev/null \| jq -r '.[] \| select(.name=="X") \| .id'` instead |
| 2026-02-25 | `kubectl --token=X` does not override kubeconfig client-cert | X.509 auth takes priority over token. For OIDC testing use `KUBECONFIG=/dev/null kubectl --server=... --certificate-authority=... --token=...` |
| 2026-02-25 | Istio ambient mesh corrupts SSO web server cookies -> `http: named cookie not present` | Web servers in ambient namespace (ArgoCD, Grafana, Harbor, Gitea, OAuth2-Proxy, Headlamp) must opt-out with `istio.io/dataplane-mode: none` pod label. ztunnel HBONE corrupts Set-Cookie/Cookie headers |
| 2026-02-25 | Gitea OAuth2 source name case mismatch -> `/user/oauth2/Keycloak` 500 error | URL path source name is case-sensitive. Register with `gitea admin auth add-oauth --name "keycloak"` (lowercase) to access via `/user/oauth2/keycloak` |
| 2026-02-25 | `microprofile-jwt` scope has duplicate `groups` claim mapper | Default Keycloak `microprofile-jwt` scope already maps realm-role to `groups`. After creating custom `groups` scope, delete the `groups` mapper from other scopes |
| 2026-02-25 | All Keycloak clients `aud` claim only contains `["account"]` | Must add audience mapper to **all OIDC clients**, not just `kubernetes`. ArgoCD and other apps that verify tokens directly will get `expected audience "argocd" got ["account"]` error |
| 2026-02-26 | Keycloak user `emailVerified=false` -> OAuth2-Proxy 500 error | Add `-s emailVerified=true` to `kcadm.sh create users`. Or add `insecure_oidc_allow_unverified_email = true` to OAuth2-Proxy |
| 2026-02-26 | Traefik Errors middleware preserves 401 status code -> browser auto-redirect fails | Use nginx+JS redirect page instead of ExternalName->OAuth2-Proxy. Force redirect with `window.location.href` |
| 2026-02-26 | Traefik blocks ExternalName services by default | `providers.kubernetesCRD.allowExternalNameServices: true` required. Without it: 404 `externalName services not allowed` |
| 2026-02-26 | OAuth2-Proxy PKCE conflict (concurrent multiple OAuth flows) | Multiple protected apps redirect simultaneously -> code_verifier conflict. Add sessionStorage 5-second debounce in JS |
| 2026-02-26 | Traefik LB annotation `io.cilium/lb-ipam-ips` ignored by MetalLB | For MetalLB use: `metallb.universe.tf/loadBalancerIPs: "IP"` |
| 2026-06-11 | Keycloak Operator (26.6.1) **IGNORES resources set under `unsupported.podTemplate`** — the running pod had no effective CPU request (operator defaults: mem 2Gi/1700Mi, no cpu) regardless of the 200m in podTemplate. On a CPU-saturated node Keycloak got starved → its `:9000` health endpoint missed the operator's **1s** liveness `timeoutSeconds` → SIGKILL (exit 137) crash loop → `keycloak-service` endpoints emptied → APISIX `502` on ALL `keycloak.local.narwhal.io` → cluster-wide SSO outage. (Contrary to the older note: in 26.6.1 podTemplate **probes ARE honored**; it's **resources** that are dropped.) | Put CPU/memory in the **first-class `Keycloak.spec.resources`** (operator honors it — verified pod got `cpu req 600m`). Relax probes via `unsupported.podTemplate` containers[0] `livenessProbe/readinessProbe/startupProbe` (`timeoutSeconds: 5`, higher `failureThreshold`) — honored. Immediate recovery: `kubectl cordon <hot-node> && kubectl delete pod keycloak-0 -n iam` to reschedule onto a node with CPU headroom, then uncordon. |
| 2026-06-12 | velero-ui native Keycloak OAuth: browser-side Keycloak login SUCCEEDED but `/api/auth/oauth` returned 500 → bounced to `/login?state=error&reason=sso`. Cause: velero-api (Node/passport-oauth2) makes a **server-to-server** call to the Keycloak token endpoint and rejected the private-CA TLS cert (`UNABLE_TO_VERIFY_LEAF_SIGNATURE`). Node trusts only the public bundle — mounting the CA for the *browser* path is irrelevant. Also: velero-ui SPA only completes the OAuth code exchange for a `state` matching `localStorage['auth.oauth.state']` → a cross-origin Keycloak authorize deep link can't work; zero-click requires a SAME-ORIGIN bootstrap page that seeds that key first (APISIX `serverless-pre-function` `/sso` route). Same pattern for OpenBao: `/sso` + `/sso/callback` pages drive the OIDC API flow and write the token as `localStorage['vault-token☃1']` in the UI's format (needs the extra redirect URI in BOTH the oidc role and the Keycloak client) | Mount `narwhal-ca-cert` secret + set `NODE_EXTRA_CA_CERTS=/etc/narwhal-ca/ca.crt` in chart values (`gitops/apps/velero-ui.yaml`). Verify in-pod: `node -e 'fetch(tokenUrl,{method:"POST"})'` → HTTP 400 (TLS ok) not TLS error. Deploy via Gitea push (kubectl apply gets reverted by app-of-apps selfHeal). Zero-click bootstrap routes: `apisix-routes.yaml` `openbao-sso`/`velero-ui-sso`; APISIX IC may need a restart to translate new ApisixRoute rules |
| 2026-06-12 | Harbor OIDC auto-onboard fails for Keycloak user `admin`: "failed to create user record: user admin or email ... already exists" — Harbor reserves the built-in local `admin`, so a same-named OIDC user can never onboard. ALSO: Keycloak 24+ **silently drops** user attributes set via admin API unless the realm user-profile allows them (`unmanagedAttributePolicy` unset = blocked) — a protocol mapper for the attribute then emits nothing and Harbor falls back to the `name` claim (onboards as `admin_narwhal` from full name "admin narwhal") | Dedicated username claim: realm user-profile `unmanagedAttributePolicy=ENABLED` (11-2), per-user attribute `harbor_username` (admin→`narwhal-admin`, others→own username), `oidc-usermodel-attribute-mapper` on the harbor client, Harbor `oidc_user_claim=harbor_username` (11-3). Harbor admin rights come from `oidc_admin_group=cluster-admin`, not the username. Delete any half-onboarded user via `DELETE /api/v2.0/users/<id>` before re-login |
| 2026-06-11 | trivy-operator (chart 0.27.0) operator/scanner config is read from **ConfigMaps, not the Deployment env** — `OPERATOR_CONCURRENT_SCAN_JOBS_LIMIT` lives in cm `trivy-operator-config`, scan-job resources (`trivy.resources.*`) in cm `trivy-operator-trivy-config`. Checking `deploy/trivy-operator` env (only has 5 `OPERATOR_*` basics) or the `trivy-operator` cm (scanJob template only) makes a correctly-applied value look missing. | Verify trivy runtime settings via `kubectl get cm trivy-operator-config` / `trivy-operator-trivy-config -n security-system`, not the deploy env. |

### GitOps/ArgoCD Mistakes
| Date | Mistake | Fix |
|------|---------|-----|
| - | values file path typo | `valueFiles` paths are relative to repoURL |
| - | targetRevision format error | Chart version is `"1.0.0"` (string), Git ref is `HEAD` |
| 2026-02-14 | app-of-apps repoURL uses `https://` -> Gitea is HTTP only | Use `http://gitea-http.gitea.svc.cluster.local:3000/...` |
| 2026-02-14 | Harbor gitops YAML missing ARM64 image overrides | Must specify all components: `ghcr.io/dasomel/goharbor/*:latest` |
| 2026-02-14 | SeaweedFS chart version 4.0.410 -> Docker Hub image 4.10 doesn't exist | Verify appVersion=image tag exists before using (4.0.407->4.07) |
| 2026-02-14 | ArgoCD repo-server GitHub Pages IPv6 connection failure | VM does not support IPv6, ArgoCD fails when attempting IPv6 |
| 2026-02-14 | ArgoCD managed resource Helm upgrade conflict | Patch directly with `kubectl set image`, use kubectl instead of Helm |
| 2026-02-15 | Headlamp v0.40.0 has no `-oidc-skip-issuer-tls-verify` flag | Mount CA cert to `/etc/ssl/certs/` with subPath, do not use `SSL_CERT_FILE` |
| 2026-02-15 | Grafana `assertNoLeakedSecrets` chart validation failure | `grafana.assertNoLeakedSecrets: false` required |
| 2026-02-15 | Prometheus Helm release name mismatch (`prometheus` vs `prometheus-stack`) | Must match ArgoCD app name for HTTPRoute/ConfigMap to work correctly |
| 2026-02-15 | Gitea OIDC source add fails with self-signed cert verification | Mount CA cert in Gitea container before running `add-oauth` |
| 2026-02-24 | ArgoCD installed in non-default namespace (devtools) ClusterRoleBinding Subject mismatch | Upstream install.yaml always sets Subject namespace to `argocd`. After installation, `kubectl patch clusterrolebinding argocd-application-controller argocd-applicationset-controller argocd-server` to update namespace to actual installation namespace |
| 2026-02-24 | Istio ambient namespace ArgoCD health check ports (8082/8084/9001) blocked by ztunnel | ztunnel intercepts all inbound traffic in ambient namespace. kubelet probe (plain HTTP) conflicts with ztunnel expecting mTLS -> CrashLoopBackOff. Add `istio.io/dataplane-mode: none` label to pod template to exclude from ambient. `traffic.sidecar.istio.io/excludeInboundPorts` annotation does not work in ambient mode |
| 2026-02-26 | ArgoCD selfHeal reverts kubectl apply changes | ArgoCD managed resources revert to Gitea state even when modified with kubectl. Must push to Gitea repo for persistence |
| 2026-02-26 | Gitea headless service (ClusterIP: None) -> git clone fails | DNS resolution fails with headless Gitea service. Use pod IP directly: `kubectl get pod -l app.kubernetes.io/name=gitea -o jsonpath='{.items[0].status.podIP}'` |
| 2026-06-11 | APISIX 3.16 has NO `kubernetes` secret manager (only vault/aws/gcp) — `$secret://kubernetes/k8s-1/...` ApisixRoute refs can never resolve and `secret_providers` in config.yaml is not a real APISIX option. IC meanwhile DID sync ExternalName-backed routes, creating broken duplicates that shadowed the working manual admin-API routes → velero-ui (and 5 others) `unauthorized_client` → callback 500 | Inject OIDC secrets via apisix chart `extraEnvVars` (secretKeyRef) + reference `$env://VAR` in ApisixRoute; delete legacy manual duplicate routes from etcd via admin API |

### Vagrant/Infrastructure Mistakes
| Date | Mistake | Fix |
|------|---------|-----|
| 2026-01-29 | Vagrant Cloud 401 error | Use `VAGRANT_CLOUD_TOKEN=$(hcp auth print-access-token)` when HCP token required |
| 2026-01-29 | charts.keycloak.org URL 404 | Use official Keycloak Operator (`keycloak-k8s-resources` GitHub) |
| 2026-01-29 | Bitnami image not found | Use official images or Operators (CloudNative-PG, etc.) due to Bitnami commercialization |
| 2026-01-29 | kubeadm init fails on vagrant provision re-run | Add logic to check if cluster already initialized, or run individual scripts directly |
| 2026-01-29 | Keycloak DB connection fails with FQDN | Use short service name in same namespace (`keycloak-db-rw` not `keycloak-db-rw.keycloak.svc.cluster.local`) |
| 2026-01-29 | Gitea Helm chart valkey FQDN issue | `gitea-init` secret script uses FQDN, needs patching. Set `valkey.enabled=true`, `valkey-cluster.enabled=false` in script |
| 2026-01-29 | Keycloak 2 replicas resource shortage | Use `instances: 1` in test environment, HA only for production |
| 2026-01-30 | Cluster DNS not accessible from API server | Expose OIDC issuer URL via NodePort (`http://NODE_IP:30080/realms/kubernetes`) |
| 2026-01-31 | OpenBao v2.4.4 no ARM64 image | No ARM64 at quay.io/openbao/openbao, use tag `2.2.0` (default registry) |
| 2026-01-31 | Velero bitnami/kubectl image not found | Bitnami banned, use `docker.io/alpine/k8s` (includes shell, ARM64 support) |
| 2026-01-31 | Pod eviction due to disk pressure | Add `node.kubernetes.io/disk-pressure:NoSchedule` to `tolerations` |
| 2026-01-31 | GitHub Actions get-changed-files doesn't support workflow_dispatch | Add `if: github.event_name != 'workflow_dispatch'` condition |
| 2026-01-31 | CNPG cluster replica WAL archiving failure | Delete problematic replica PVC and wait for auto-recreation, reduce instance count to stabilize |
| 2026-02-10 | MetalLB Helm upgrade CRD field manager conflict | Initial install is fine, re-upgrade has CRD caBundle conflict. Pods function normally |
| 2026-02-10 | Harbor ARM64 `exec format error` | Use `ghcr.io/dasomel/goharbor/*:latest`, official images are AMD64 only |
| 2026-02-10 | OAuth2 Proxy cookie_secret 34-byte error | `openssl rand -base64 32` produces 44 bytes, use `openssl rand -hex 16` for 32 bytes |
| 2026-02-10 | Velero CRD job kubectl missing `/bin/sh` | `registry.k8s.io/kubectl` is distroless, use `docker.io/alpine/k8s` |
| 2026-02-10 | VERSIONS.md out of sync with actual deployed versions | Must sync VERSIONS.md based on chart versions pinned in scripts |
| 2026-02-11 | CNPG DB password hardcoding mismatch | Must query actual password from CNPG Secret (`harbor-db-credentials`) |
| 2026-02-11 | Loki 6.52.0 bucketNames required | Must specify `loki.storage.bucketNames.{chunks,ruler,admin}` |
| 2026-02-11 | Traefik 39.0.0 HTTPS certificateRefs required | TLS certificate reference required for Gateway HTTPS listener |
| 2026-02-11 | Traefik Helm --set port type error | Use values file instead of `--set` (float64 vs int64) |
| 2026-02-11 | ArgoCD managed resource Helm reinstall conflict | Delete existing resources (SA, IngressClass, GatewayClass) before reinstall |
| 2026-02-28 | Keycloak HTTPRoute + Operator Ingress both active -> 502 | Keycloak Operator manages Ingress automatically, do not create HTTPRoute. HTTPRoute + Ingress coexisting on same Host causes Traefik WRR null backend selection |
| 2026-02-28 | Keycloak pod restart loses `istio.io/dataplane-mode: none` label | Must set permanently in Keycloak CR `spec.unsupported.podTemplate.metadata.labels`. Direct pod label addition is lost on restart |
| 2026-06-17 | `up.sh` Phase 2 call used `|| true` + final `echo`-only exit, so clean-install failures (SSH key race, mid-provision disconnect) silently exited 0 | Capture Phase 2 provision exit code; on non-zero, recover master-1 SSH and retry once; exit 1 on failure so the caller sees a real error |
| 2026-06-17 | `vagrant provision` over VMware SSH key race kills only the 06-phase2 orchestrator via SIGHUP; sub-scripts (08-1 onward) become orphans and Phase 2 stalls mid-way; yet master-1 is k8s-Ready so the main retry loop skips it | `up.sh` now checks `master1_ssh_ok` before Phase 2 and calls `recover_master1_ssh` (`vagrant reload master-1` + 40×15s poll) on failure; 3-master HA keeps the control plane quorate during the single-node reboot; phase2-platform is idempotent so the retry completes safely |

| 2026-03-09 | 14-gitops-bootstrap.sh `GITEA_ADMIN_PASSWORD="gitea-admin"` hardcoded -> mismatch with actual generated password | Dynamically query with `kubectl get secret gitea-admin -n devtools -o jsonpath='{.data.admin-password}' \| base64 -d` |
| 2026-03-09 | oauth2-proxy-secrets missing `client-id` key -> `CreateContainerConfigError` | Add `--from-literal=client-id=oauth2-proxy` when creating secret in 11-3-keycloak-clients.sh |
| 2026-06-17 | 11-3이 apisix용 `platform-system/gitea-oidc-secret`(키 client_secret/session_secret)과 storage용 `velero-ui-oauth`(키 client_secret)을 생성하지 않아 apisix·velero-ui가 `CreateContainerConfigError` → 11-3 Group B에 gitea(platform-system) 추가 + storage/velero-ui-oauth 생성 추가로 해소 |

| 2026-03-23 | `set -o pipefail` causes exit 1 when `grep "${NODE_IP}"` has no match -> script terminates immediately | Use `grep ... \| head -1 || true` pattern (grep pipelines always need `|| true` in pipefail environment) |
| 2026-03-29 | `kubeadm join` without `--node-name` registers worker as `vagrant-ubuntu` (OS default hostname) instead of `narwhal-worker-X` | Always pass `--node-name $(hostname)` to `kubeadm join`. Even if `01-prerequisites.sh` sets hostname, kubeadm may use the OS default. Fix: `02-join-worker.sh` now includes `--node-name`. Recovery: `kubectl drain + delete node`, then `kubeadm reset -f && kubeadm join --node-name narwhal-worker-X` |
| 2026-04-06 | Keycloak StatefulSet 인데 `kubectl exec -n iam deploy/keycloak` 사용 | Keycloak Operator는 StatefulSet 생성. `kubectl exec -n iam keycloak-0 -c keycloak` 사용 |
| 2026-04-06 | `kcadm.sh` protocol mapper config 형식 오류 `{"config":{...}}` | 올바른 형식: `-s 'config={"key":"value"}'` (NOT `-s '{"config":{...}}'`) |
| 2026-04-06 | bash 함수 내 `echo` stdout 캡처 문제 → secret 변수에 로그 메시지 혼입 | 함수 내 모든 로그용 echo에 `>&2` 추가. `$(func)` 캡처 시 stdout만 반환됨 |
| 2026-04-06 | `KC_HOSTNAME_PORT` 은 Keycloak v1 옵션 (v26에서 무시됨) | Keycloak v2(v26+): `hostname.hostname` + `proxy.headers: xforwarded` 사용. 포트 제거는 X-Forwarded-Port 헤더로 처리 |
| 2026-04-06 | APISIX IC + ExternalName 서비스: `conflict headless service and backend resolve granularity` | ExternalName 서비스는 IC가 headless로 간주하여 `resolveGranularity: service`와 비호환. IC가 동기화 실패 시 APISIX admin API에 직접 패치로 우회 |
| 2026-04-06 | APISIX IC route 동기화 실패 시 admin API의 기존 route는 유지됨 | IC 실패 중에는 수동 admin API 패치가 overwrite되지 않음. `curl PATCH /apisix/admin/routes/{id}` 로 plugin 직접 추가 가능 |
| 2026-04-06 | Keycloak issuer URL에 `:9443` 포트 포함 → OpenBao OIDC discovery 실패 | APISIX `proxy-rewrite` plugin으로 `X-Forwarded-Port: 443` 설정 필요. IC 실패 시 admin API 직접 패치 |
| 2026-04-06 | API server manifest OIDC 플래그 중복 추가 (yq append 반복 실행 시) | 스크립트 실행 전 `grep -c 'oidc-issuer-url'` 으로 중복 확인. Python으로 중복 제거 후 재실행 |
| 2026-04-06 | Keycloak Operator-managed NetworkPolicy `keycloak-allow-hbone` blocks service port 8080 | Operator creates NetworkPolicy allowing only HBONE port 15008. When Keycloak is opted-out (`istio.io/dataplane-mode: none`), clients must connect via plain TCP 8080, not HBONE. Must create separate `keycloak-allow-plain` or add 8080 ingress rule to the NetworkPolicy |
| 2026-04-06 | StatefulSet pod stuck in old revision (ztunnel blocking probe) causes RollingUpdate deadlock | If pod is not Ready (startup probe fails), StatefulSet RollingUpdate cannot proceed even after CR/StatefulSet update. Old revision pod keeps running with wrong config. Fix: `kubectl delete pod keycloak-0 -n iam` to force recreation with new spec |
| 2026-04-06 | APISIX IC sets `nodes: []` for ExternalName service with `resolveGranularity: service` | IC cannot resolve ClusterIP for ExternalName (no ClusterIP). Remove `resolveGranularity: service` from ApisixRoute backend — IC then follows CNAME and sets FQDN as upstream node (same as ArgoCD pattern) |
| 2026-04-06 | Keycloak container image has no `curl` or `wget` (UBI9 minimal) | Cannot use `curl`-based exec probes. Use `bash /dev/tcp` or rely on Operator-managed httpGet probes. Operator ignores container probe overrides in `unsupported.podTemplate` |
| 2026-04-07 | APISIX container has no `curl` — `kubectl exec deploy/apisix -- curl` fails | Use master-1's curl directly: `curl http://$(kubectl get svc apisix-admin -n platform-system -o jsonpath='{.spec.clusterIP}'):9180/apisix/admin/...` |
| 2026-04-07 | nfs-quota-agent v0.2.1 HTML embeds Go raw-string-literal syntax `` ` + "`" + ` `` instead of actual backtick chars → JS SyntaxError → dashboard shows "Loading..." forever | Apply APISIX `serverless-post-function` body_filter with Lua `ngx.re.gsub` to replace all 22 occurrences at the gateway layer |
| 2026-04-07 | APISIX IC stores `$secret://kubernetes/k8s-1/...` refs as plaintext in etcd when secret manager is not configured → openid-connect plugin sends literal string to Keycloak → `unauthorized_client` | Configure APISIX k8s-1 secret provider via admin API, OR patch the route directly via admin API with the plaintext secret (APISIX auto-encrypts on write) |
| 2026-04-07 | APISIX IC `ResourceSyncAborted` for ExternalName backends (no endpoints) — IC cannot sync route but doesn't re-create after admin API DELETE | Maintain these routes via admin API in provisioning scripts (11-3-keycloak-clients.sh). ApisixRoute YAML is documentation-only for these cases |
| 2026-04-07 | Grafana `grafana.ini` in Helm values generates `GF_AUTH_*` env vars; additional `kubectl patch deployment` adding same env vars → duplicate entries → ArgoCD `ComparisonError: duplicate entries for key` | Remove kubectl patch; manage all Grafana OAuth config solely via `prometheus-stack.yaml` grafana.ini. If live deployment has duplicates: Python dedup + `kubectl replace` |
| 2026-04-07 | `vault.hashicorp.com/agent-inject: "true"` annotation without `agent-inject-secret-*` path → OpenBao Agent Injector still injects init container → 403 `permission denied` on Kubernetes auth → pod stuck `Init:0/1` | Remove vault annotations when app uses `envFrom.secretRef` directly. Only add vault annotations when actual `agent-inject-secret-*` paths are configured |

### Adding New Mistakes
```markdown
| YYYY-MM-DD | Mistake description | Fix |
```

---

## Core Flows

### 1. Cluster Provisioning Flow

| Step | File | Description |
|------|------|-------------|
| Prerequisites | `scripts/common/01-prerequisites.sh` | Hostname, /etc/hosts, netplan setup |
| Container Runtime | `scripts/common/02-containerd.sh` | containerd installation |
| K8s Installation | `scripts/common/03-k8s-install.sh` | kubeadm, kubelet, kubectl |
| Config | `scripts/common/set-config.sh` | Shared configuration variables |
| Library | `scripts/common/lib.sh` | Shared utility functions |

### 2. Master Node Setup Flow (2-Phase Structure)

**Phase 1: Cluster Infrastructure** (runs during master-1 provisioning)

| Step | File | Description |
|------|------|-------------|
| kube-vip | `scripts/cluster/00-kube-vip.sh` | Control Plane VIP |
| NFS Server | `scripts/cluster/01-nfs-server.sh` | NFS server setup |
| Cluster Init | `scripts/cluster/02-init-cluster.sh` | kubeadm init |
| CNI Install | `scripts/cluster/03-cni-install.sh` | Cilium + Hubble |
| Addons | `scripts/cluster/04-addons.sh` | metrics-server, csi-driver-nfs |
| NFS Quota | `scripts/cluster/05-nfs-quota-agent.sh` | NFS project quota |

| Control Plane Join | `scripts/cluster/02-join-control-plane.sh` | master-2, master-3 join |
| Worker Join | `scripts/cluster/02-join-worker.sh` | worker-1, worker-2, worker-3 join |

**Phase 2: Platform Apps** (auto-triggered after last worker join)

| Step | File | Description |
|------|------|-------------|
| Phase 2 Wrapper | `scripts/cluster/06-phase2-start.sh` | Runs Phase 2 scripts |
| PostgreSQL | `scripts/cluster/07-cnpg.sh` | CloudNative-PG Operator |
| Networking | `scripts/cluster/08-1-networking.sh` | MetalLB, APISIX, cert-manager |
| Monitoring | `scripts/cluster/08-2-monitoring.sh` | Prometheus, Loki, Promtail, Tempo |
| Security | `scripts/cluster/08-3-security.sh` | Kyverno, Headlamp, OAuth2-Proxy |
| Storage | `scripts/cluster/08-4-storage.sh` | SeaweedFS, OpenBao, Velero |
| Registry | `scripts/cluster/08-5-registry.sh` | Harbor |
| TLS/Routes | `scripts/cluster/08-6-tls-routes.sh` | CA cert distribution, APISIX routes |
| Istio | `scripts/cluster/09-istio-ambient.sh` | Service Mesh (ambient mode) |
| dnsmasq | `scripts/cluster/10-dnsmasq.sh` | Local DNS + CoreDNS forward |
| Authentik | `scripts/cluster/11-authentik.sh` | Authentik SSO + PostgreSQL |
| Authentik Config | `scripts/cluster/11-2-authentik-config.sh` | Flows, providers, applications, groups |
| Authentik API Server | `scripts/cluster/11-4-authentik-apiserver.sh` | K8s OIDC config + RBAC |
| Gitea | `scripts/cluster/12-gitea.sh` | Git server |
| ArgoCD | `scripts/cluster/13-argocd.sh` | GitOps CD |
| Bootstrap | `scripts/cluster/14-gitops-bootstrap.sh` | App-of-Apps deployment |
| IDP Portal | `scripts/cluster/15-idp-portal.sh` | Developer portal |

### 3. GitOps App Management

| App | File | Description |
|-----|------|-------------|
| App-of-Apps | `gitops/apps/app-of-apps.yaml` | Manages all apps |
| **Networking** | | |
| MetalLB | `gitops/apps/metallb.yaml` | Load balancer |
| APISIX | `gitops/apps/apisix.yaml` | API gateway |
| APISIX Infra | `gitops/apps/apisix-infra.yaml` | APISIX infrastructure resources |
| APISIX Routes | `gitops/apps/apisix-routes.yaml` | APISIX route definitions |
| APISIX Dashboard | `gitops/apps/apisix-dashboard.yaml` | APISIX management UI |
| cert-manager | `gitops/apps/cert-manager.yaml` | TLS automation |
| **Service Mesh** | | |
| Istio Base | `gitops/apps/istio-base.yaml` | Istio CRDs |
| Istiod | `gitops/apps/istiod.yaml` | Istio control plane |
| Istio CNI | `gitops/apps/istio-cni.yaml` | Istio CNI plugin |
| ztunnel | `gitops/apps/ztunnel.yaml` | Istio ambient ztunnel |
| **Monitoring** | | |
| Prometheus | `gitops/apps/prometheus-stack.yaml` | Monitoring + Grafana |
| Loki | `gitops/apps/loki.yaml` | Log collection |
| Promtail | `gitops/apps/promtail.yaml` | Log shipping |
| Tempo | `gitops/apps/tempo.yaml` | Distributed tracing |
| **Storage/Security** | | |
| Harbor | `gitops/apps/harbor.yaml` | Container registry |
| OpenBao | `gitops/apps/openbao.yaml` | Secret management |
| SeaweedFS | `gitops/apps/seaweedfs.yaml` | Object storage |
| Velero | `gitops/apps/velero.yaml` | Backup |
| Velero UI | `gitops/apps/velero-ui.yaml` | Backup management UI |
| Kyverno | `gitops/apps/kyverno.yaml` | Policy management |
| **IAM/UI** | | |
| Authentik | `gitops/apps/authentik.yaml` | SSO/OIDC (IAM) |
| Headlamp | `gitops/apps/headlamp.yaml` | K8s UI |
| IDP Portal | `gitops/apps/idp-portal.yaml` | Developer portal |

## Development Commands

```bash
# Create cluster
vagrant up --provider=vmware_desktop

# Create specific node only
vagrant up master-1
vagrant up worker-1

# SSH access
vagrant ssh master-1

# kubectl check
vagrant ssh master-1 -c "kubectl get nodes"

# Run Phase 2 only manually (after cluster setup)
vagrant provision master-1 --provision-with phase2-platform

# Re-provisioning
vagrant provision master-1

# Stop cluster
vagrant halt

# Destroy cluster
vagrant destroy -f

# Script validation (shellcheck)
shellcheck scripts/**/*.sh
```

## Key Configuration

| Setting | File | Variable |
|---------|------|----------|
| K8s version | `Vagrantfile` | `K8S_VERSION` |
| Worker count | `Vagrantfile` | `WORKER_COUNT` |
| Memory/CPU | `Vagrantfile` | `MASTER_MEMORY`, `WORKER_CPUS` |
| VIP address | `Vagrantfile` | `VIP_ADDRESS` |
| Component versions | `VERSIONS.md` | All version management |

## Permissions

### Allowed Operations
- Modify shell scripts in scripts/ folder
- Modify YAML files in gitops/apps/, gitops/resources/
- Change Vagrantfile settings
- Update documentation (README.md, docs/)

### Forbidden Operations
- Do not directly modify .vagrant/ folder
- Do not hardcode sensitive information (passwords, tokens)
- Do not remove `set -euo pipefail` from shell scripts
- **Bitnami images/charts are banned** (exception only when absolutely no alternative exists)
  - Risk of image deletion/inaccessibility due to Bitnami commercialization
  - DB: Use official images or Operators (CloudNative-PG, etc.)
  - Other: Prefer upstream official images, Alpine-based community images as fallback
- **Minimize docker.io (Docker Hub) usage** (allow only when no alternative exists)
  - Rate limit issues (anonymous 100pulls/6h, authenticated 200pulls/6h)
  - Registry priority: `ghcr.io` > `registry.k8s.io` > `quay.io` > `docker.io`
  - Use `ghcr.io/dasomel/` for custom images
  - When docker.io usage is unavoidable, add a comment explaining the reason

## Code Style

- **Shell Script**: `set -euo pipefail` required, 2 spaces indentation
- **YAML**: 2 spaces indentation
- **Variable names**: ENV_VAR (environment), local_var (local)
- **Filenames**: Numeric prefix for execution order (00-, 01-, ...)

## Network Info

| Item | Value |
|------|-------|
| Master IPs | 192.168.56.10-12 (master-1, master-2, master-3) |
| Worker IPs | 192.168.56.21-23 (worker-1, worker-2, worker-3) |
| VIP | 192.168.56.100 |
| Pod CIDR | 10.244.0.0/16 |
| Service CIDR | 10.96.0.0/12 |

---

## Infrastructure Resource Safety

- **Limit parallel cluster modifications to 2-3 max** -- concurrent operations cause OOM in Master 4GB / Worker 6GB environment
- Do not restart/modify multiple pods simultaneously -- modify one -> verify stability -> next modification
- Check resource availability with `kubectl top nodes` between cluster modification tasks before proceeding
- After applying infrastructure/cluster changes, **verify from actual user perspective** (e.g., curl endpoint, kubectl exec test, DNS resolve)

---

## Verification Loop

> Providing Claude with ways to verify its own work increases quality 2-3x.

### Verification Methods for This Project

1. **Script Validation**
   ```bash
   shellcheck scripts/**/*.sh
   ruby -c Vagrantfile
   ```

2. **YAML Validation**
   ```bash
   yq eval '.' gitops/apps/*.yaml > /dev/null
   ```

3. **Live Testing** (when VM is running)
   ```bash
   vagrant ssh master-1 -c "kubectl get nodes"
   vagrant ssh master-1 -c "kubectl get pods -A"
   ```

4. **ArgoCD Sync Verification**
   ```bash
   vagrant ssh master-1 -c "kubectl get applications -n devtools"
   ```

### Verification Commands
- `/verify` - Run full verification loop
- `/check` - Quick syntax check

---

## Slash Commands (Repetitive Task Automation)

| Command | Description |
|---------|-------------|
| `/check` | Quick type/syntax check |
| `/verify` | Run full verification loop |
| `/commit-push-pr` | Commit -> Push -> PR in one step |
| `/sync-versions` | VERSIONS.md sync check |
| `/add-mistake` | Record mistake pattern |
| `/compact` | Clean session context and save summary |

---

## Team Contribution Guide

### How to Update CLAUDE.md

1. **When a mistake is found**: Add to Mistakes Log section
2. **When a new pattern is discovered**: Add to Code Style or Permissions
3. **During code review**: Request update with `@.claude` tag

### Weekly Review
- Team members contribute to CLAUDE.md weekly
- Share newly discovered mistake patterns
- Discuss verification loop improvements

---

## Leverage the Team for Debugging (Required)

> During debugging, do not try to solve alone -- actively leverage the team (subagents + Gemini).

| Scenario | Team Utilization |
|----------|-----------------|
| **Pod failure debugging** | Collect logs/events/describe in parallel with Task agents, analyze error messages with Gemini |
| **Helm install failure** | Verify chart version compatibility/breaking changes with Gemini, validate values with subagents |
| **Network issues** | Investigate DNS/services/endpoints simultaneously with subagents |
| **Image issues** | Check ARM64 support/tags with Gemini, test registry access with subagents |
| **Vagrant provisioning failure** | Analyze VM logs/script output with subagents, search for alternatives with Gemini |

---

## Ralph Technique

### Using Ralph in This Project

```bash
# 1. Write PROMPT.md
cat > PROMPT.md << 'EOF'
# Task: Add GitOps App

## Goal
Add Velero backup application to GitOps

## Task List
1. Create gitops/apps/velero.yaml
2. Add Helm values inline to gitops/apps/velero.yaml
3. Update VERSIONS.md
4. Add reference to app-of-apps.yaml

## Completion Criteria
- All YAML is syntactically valid
- Follows ArgoCD Application spec
- Versions match VERSIONS.md

## Verification
After completion, run `yq eval '.' gitops/apps/velero.yaml`
EOF

# 2. Run Ralph
.claude/scripts/ralph.sh
```

### Ralph PROMPT.md Template

See `.claude/templates/PROMPT.md`
