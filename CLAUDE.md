# Narwhal - Claude Code Guide

> Vagrant-based Kubernetes Internal Developer Platform (IDP) cluster automated provisioning project

## Quick Overview

An infrastructure project that automatically provisions a complete Kubernetes IDP stack (GitOps, SSO, Monitoring, Storage, Backup) using Vagrant VMs in a local environment.

> **Working procedure:** follow the global `<procedural_completion>` doctrine (`~/.claude/CLAUDE.md`) on substantive tasks — goal → decompose → execute → verify → risk (five principles + completion gate + escalation). Trivial one-shots answer directly.

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
| 2026-02-15 | worker/master-2 DNS resolves to public IP | Set `Domains=~local.narwhal.internal` in `systemd-resolved` + use master dnsmasq as DNS |
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
| 2026-06-11 | Keycloak Operator (26.6.1) **IGNORES resources set under `unsupported.podTemplate`** — the running pod had no effective CPU request (operator defaults: mem 2Gi/1700Mi, no cpu) regardless of the 200m in podTemplate. On a CPU-saturated node Keycloak got starved → its `:9000` health endpoint missed the operator's **1s** liveness `timeoutSeconds` → SIGKILL (exit 137) crash loop → `keycloak-service` endpoints emptied → APISIX `502` on ALL `keycloak.local.narwhal.internal` → cluster-wide SSO outage. (Contrary to the older note: in 26.6.1 podTemplate **probes ARE honored**; it's **resources** that are dropped.) | Put CPU/memory in the **first-class `Keycloak.spec.resources`** (operator honors it — verified pod got `cpu req 600m`). Relax probes via `unsupported.podTemplate` containers[0] `livenessProbe/readinessProbe/startupProbe` (`timeoutSeconds: 5`, higher `failureThreshold`) — honored. Immediate recovery: `kubectl cordon <hot-node> && kubectl delete pod keycloak-0 -n iam` to reschedule onto a node with CPU headroom, then uncordon. |
| 2026-06-12 | velero-ui native Keycloak OAuth: browser-side Keycloak login SUCCEEDED but `/api/auth/oauth` returned 500 → bounced to `/login?state=error&reason=sso`. Cause: velero-api (Node/passport-oauth2) makes a **server-to-server** call to the Keycloak token endpoint and rejected the private-CA TLS cert (`UNABLE_TO_VERIFY_LEAF_SIGNATURE`). Node trusts only the public bundle — mounting the CA for the *browser* path is irrelevant. Also: velero-ui SPA only completes the OAuth code exchange for a `state` matching `localStorage['auth.oauth.state']` → a cross-origin Keycloak authorize deep link can't work; zero-click requires a SAME-ORIGIN bootstrap page that seeds that key first (APISIX `serverless-pre-function` `/sso` route). Same pattern for OpenBao: `/sso` + `/sso/callback` pages drive the OIDC API flow and write the token as `localStorage['vault-token☃1']` in the UI's format (needs the extra redirect URI in BOTH the oidc role and the Keycloak client) | Mount `narwhal-ca-cert` secret + set `NODE_EXTRA_CA_CERTS=/etc/narwhal-ca/ca.crt` in chart values (`gitops/apps/velero-ui.yaml`). Verify in-pod: `node -e 'fetch(tokenUrl,{method:"POST"})'` → HTTP 400 (TLS ok) not TLS error. Deploy via Gitea push (kubectl apply gets reverted by app-of-apps selfHeal). Zero-click bootstrap routes: `apisix-routes.yaml` `openbao-sso`/`velero-ui-sso`; APISIX IC may need a restart to translate new ApisixRoute rules |
| 2026-06-12 | Harbor OIDC auto-onboard fails for Keycloak user `admin`: "failed to create user record: user admin or email ... already exists" — Harbor reserves the built-in local `admin`, so a same-named OIDC user can never onboard. ALSO: Keycloak 24+ **silently drops** user attributes set via admin API unless the realm user-profile allows them (`unmanagedAttributePolicy` unset = blocked) — a protocol mapper for the attribute then emits nothing and Harbor falls back to the `name` claim (onboards as `admin_narwhal` from full name "admin narwhal") | Dedicated username claim: realm user-profile `unmanagedAttributePolicy=ENABLED` (11-2), per-user attribute `harbor_username` (admin→`narwhal-admin`, others→own username), `oidc-usermodel-attribute-mapper` on the harbor client, Harbor `oidc_user_claim=harbor_username` (11-3). Harbor admin rights come from `oidc_admin_group=cluster-admin`, not the username. Delete any half-onboarded user via `DELETE /api/v2.0/users/<id>` before re-login |
| 2026-06-11 | trivy-operator (chart 0.27.0) operator/scanner config is read from **ConfigMaps, not the Deployment env** — `OPERATOR_CONCURRENT_SCAN_JOBS_LIMIT` lives in cm `trivy-operator-config`, scan-job resources (`trivy.resources.*`) in cm `trivy-operator-trivy-config`. Checking `deploy/trivy-operator` env (only has 5 `OPERATOR_*` basics) or the `trivy-operator` cm (scanJob template only) makes a correctly-applied value look missing. | Verify trivy runtime settings via `kubectl get cm trivy-operator-config` / `trivy-operator-trivy-config -n security-system`, not the deploy env. |
| 2026-06-30 | APISIX (chart v2.13.0) shipped global TLS OFF — bootstrap helm values in `08-1-networking.sh` lacked `apisix.ssl.enabled` → rendered config.yaml had no ssl block, 443 never served → Harbor push EOF, OIDC endpoint unreachable, Kaniko fail, portal ImagePullBackOff (D2) | Add `apisix.ssl.enabled: true` + `containerPort: 9443` to the values heredoc; idempotently ensure 443→9443 on apisix-gateway svc (chart bug). GitOps narwhal-apps chart already had ssl.enabled. (5492c1c) |
| 2026-06-30 | cert-manager root CA (`narwhal-root-ca`) timed out: kube-apiserver (hostNetwork static pod) intermittently can't reach cert-manager-webhook ClusterIP:443 (Cilium socketLB hostNamespaceOnly) → every cert-manager admission webhook call fails (D5) | In `08-1`: roll out all 3 cert-manager deploys, temporarily delete the validating/mutating webhook configs, create issuer+root-CA+wildcard with explicit Ready-polls, restore webhooks; trap on EXIT guarantees restore. (7f04fd3) |
| 2026-06-30 | metrics-server applied then patched TWICE (insecure-tls, then probes) → two ReplicaSet rollouts; a Phase-2 `kubectl wait ... pod -l k8s-app=metrics-server` matched the transient new pod and timed out → recurring clean-install abort (D10) | Merge both patches into ONE `kubectl patch`, then `kubectl rollout status deployment/metrics-server` (non-fatal) so Phase 1 only finishes once it settles to a single Ready pod. (d54d3c8) |
| 2026-06-30 | keycloak-0 flaky crashloop "FATAL: password authentication failed for user keycloak": the keycloak DB role password was set by BOTH `07-cnpg` (`narwhal-db-credentials/keycloak-password`) and `11-keycloak` (its own generated value) → last-writer-wins divergence vs the secret Keycloak uses (D9) | Make `07-cnpg` the single source: `11-keycloak` no longer generates a password or CREATE/ALTERs the role/db; it reads `narwhal-db-credentials/keycloak-password`, syncs `keycloak-db-secret`, and gates on a real psql auth check (10 retries) before deploying. (442985a) |
| 2026-07-10 | Kyverno Enforce validate pattern WITHOUT `=()` optional anchors (`securityContext: privileged: "!true"`) requires the field to EXIST → every pod lacking an explicit `privileged` field (i.e. most pods) was denied at admission in non-excluded namespaces; surfaced as velero-ui ReplicaSet `FailedCreate`, and would have blocked ALL rollouts/reschedules (reboot = cluster-wide outage) | In Kyverno patterns, always wrap optional fields in `=()` anchors: `=(securityContext): =(privileged): "false"` (upstream-standard form, also cover `=(initContainers)`/`=(ephemeralContainers)`). Verify both directions with `kubectl run --dry-run=server`: plain pod admitted AND privileged pod denied. (e898f59) |
| 2026-07-13 | GitOps portal pin bumps silently NOT deploying while the app showed Synced: argocd-cm carried a GLOBAL `ignoreDifferences.apps_Deployment` ignoring the narwhal-portal container `.image`/`.resources` (added for skaffold live-dev so selfHeal wouldn't revert dev images). With immutable SemVer pins this made every image-only bump invisible — bumps only landed by piggybacking on unrelated changes in the same sync. Extra trap: argocd-cm has a self-ignore on its own `.data` customization keys, so fixing the rule in git alone never reaches the live cm — must ALSO `kubectl patch` argocd-cm | Make the ignore CONDITIONAL on a dev image actually running: `select(.name=="narwhal-portal") \| select(.image \| test(":dev-")) \| .image` (and same for `.resources`) in `argocd-config.yaml` + live-patch argocd-cm. Verified: 1.0.14 pin synced in 10s after the patch. When an app is Synced but live differs from git, grep argocd-cm `resource.customizations.ignoreDifferences` before blaming sync machinery |
| 2026-07-13 | Recurring "argocd-redis EOF wedge" root-caused: `13-argocd.sh` opted server/repo-server/controller/notifications out of ambient but NOT `argocd-redis` → redis stayed mesh-enrolled while all its clients are non-mesh → under STRICT mTLS ztunnel drops their plaintext connections → repo-server "failed to list refs: EOF" / "retrieve git references from cache: EOF", Gitea-sourced apps stuck Unknown, gitops pin bumps never sync. Restarting redis/repo-server only helped while ztunnel enforcement flapped — fresh pods failed identically | Add `istio.io/dataplane-mode: none` to the argocd-redis Deployment pod template (13-argocd.sh + live patch). Verified: repo-server EOF 68/2min → 0 immediately. Same non-mesh-client→enrolled-server pattern as the STRICT mTLS exceptions lesson — when opting an app's clients out of ambient, opt out its backing services too |
| 2026-07-13 | Portal login → 502 on `/api/auth/callback/keycloak`: portal 1.0.11 started storing Keycloak access+refresh tokens in the NextAuth JWE → session cookie chunked into 2+ ~4KB `Set-Cookie` headers → response headers exceeded nginx's default `proxy_buffer_size 4k` → APISIX "upstream sent too big header" → 502; the browser retry then hit `invalid_grant: Code not valid` (code already consumed). Initially misdiagnosed as a Keycloak-restart blip — the APISIX **error** log (not access log) had the real cause | Raise proxy buffers in apisix `fullCustomConfig` → `nginx_config.http_configuration_snippet`: `proxy_buffer_size 16k; proxy_buffers 8 16k; proxy_busy_buffers_size 32k;`. For any gateway 502 on an auth callback, check APISIX error log for "too big header" FIRST. Verify with a scripted curl login (csrf→signin→Keycloak form→callback) expecting 302 + chunked session cookies |
| 2026-07-10 | Added a SECOND `image:` block to an ArgoCD Application inline `values:` string (helm values YAML) — duplicate map key silently resolved last-wins → the original block's `tag: "0.10.1"` was dropped, image fell back to chart appVersion (0.10.0 rollback). Also: kyverno `add-default-securitycontext` mutate injects `runAsNonRoot: true`; images with a non-numeric USER (e.g. `node`) then fail kubelet verification (`CreateContainerConfigError`) — fix by setting numeric `runAsUser` in values | Before adding a key to a Helm values block, grep the block for an existing key and MERGE into it; validate rendered output (`helm template` + check the final image tag), not just YAML syntax. For mutate-injected `runAsNonRoot`, pair it with explicit `runAsUser: <uid>` when the image USER is non-numeric. (f1d7333) |

### GitOps/ArgoCD Mistakes
| Date | Mistake | Fix |
|------|---------|-----|
| 2026-07-07 | Portal deployed via a `:latest` image tag in the GitOps manifest — ArgoCD compares the rendered manifest string, so a re-published `:latest` is an IDENTICAL manifest → **no diff detected → no sync → stale image runs** until a manual `rollout restart`; selfHeal never fires either. (D-portal-ghcr) | Pin an immutable SemVer tag in GitOps (`ghcr.io/dasomel/narwhal-portal:1.0.0`). Upgrade = bump the tag → ArgoCD sees the diff → auto-syncs. Reserve `:latest` for non-GitOps flows. Portal now pulls the pinned public GHCR image; in-cluster Kaniko build demoted to optional dev tool (`docs/developer-kaniko-builds.md`) |
| 2026-07-05 | `scripts/gitops/push-to-gitea.sh` uses `cp -r` (mirror or per-path) into a fresh Gitea clone — this only adds/overwrites files, it never deletes a file that no longer exists in the local `gitops/` tree. Renaming/removing a gitops Application file (e.g. `promtail.yaml` -> `k8s-monitoring.yaml` for the Alloy migration) and running the script as-is would leave the stale old file committed in Gitea alongside the new one -> two conflicting ArgoCD Applications | For a rename/delete, do the Gitea clone/push manually with an explicit `git rm` instead of relying on the script's plain `cp -r` |
| 2026-07-10 | Ran `git push -f gitea main` from the narwhal repo DIRECTLY to the in-cluster `narwhal-gitops` Gitea repo — the two have DIFFERENT tree structures (gitea root = CONTENTS of `gitops/`), so the force-push replaced Gitea main with the entire narwhal repo tree (Vagrantfile, scripts/, docs/ …); a later `push-to-gitea.sh` mirror superimposed the correct root so ArgoCD kept working, masking the pollution. Also `git remote set-url` embedded the gitea-admin PASSWORD in plaintext in `.git/config` | NEVER push the narwhal repo directly to the gitea remote — always go through `scripts/gitops/push-to-gitea.sh` (it clones fresh and maps `gitops/` -> repo root). Cleanup was a clone + `git rm -r` of the non-gitops paths + normal push. Never embed credentials in a git remote URL; the script reads the password from the cluster secret per-run |
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
| 2026-07-06 | Assumed `ghcr.io/dasomel/goharbor/*:latest` tracked Harbor v2.15.2 (the source release that prompted a chart bump) solely because `docker manifest inspect` showed multi-arch (amd64+arm64) support — that only proves the tag has ARM64 builds, it says nothing about which app version is behind it. Live check via `curl .../api/v2.0/systeminfo` showed the actual running `harbor_version: v2.15.0-f585d00b` — the custom registry hadn't rebuilt from 2.15.2 source yet | For "is version X actually running" questions, query the app's own version-reporting endpoint/API (Harbor: `/api/v2.0/systeminfo`) — never infer app version from unrelated signals like manifest architecture support |
| 2026-07-05 | `08-4-storage.sh`'s SeaweedFS S3 bucket creation was fire-and-forget: `kubectl wait --for=condition=Ready pod -l ...=filer` only confirms the container passed its own readiness probe, not that the embedded S3 API is accepting requests yet. Observed live: the single bucket-create pass silently no-op'd for `tempo`/`velero`/`loki` (only `cnpg-backup` landed, apparently created later by CNPG's own first write) while the verify loop printed WARNs and the script still exited 0 (`\|\| true` everywhere) → Tempo crashlooped ("bucket does not exist"), Loki logged continuous `NoSuchBucket` errors on every index/ruler sync, Velero's backup-location went Unavailable | Retry each bucket's create+verify individually (5 attempts, 10s apart) and `exit 1` if one never lands instead of swallowing the failure — a stopped install here is far cheaper than three components silently degrading downstream |
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
| 2026-06-17 | Cluster nodes' systemd-resolved leaked `harbor.local.narwhal.internal` to the NAT interface (public DNS) → resolved to a public AWS IP → image pull failed with `tls: unrecognized name` | Add `192.168.56.200 harbor.local.narwhal.internal` (APISIX LB VIP) to each node's `/etc/hosts` in `01-prerequisites.sh`, and enable containerd `config_path` + a `certs.d/harbor.local.narwhal.internal/hosts.toml` with `skip_verify = true` in `02-containerd.sh` (internal registry behind APISIX — no node CA trust needed) |
| 2026-06-30 | containerd 2.2.2 (Ubuntu 26.04) emits `config_path = ''` (single-quoted) under plugin `io.containerd.cri.v1.images`; the old grep/sed in `02-containerd.sh` matched neither the quote style nor the 2.x namespace → config_path stayed empty on workers → certs.d ignored → Harbor (private CA) image pull TLS fail → portal ImagePullBackOff (D3) | Replace grep/sed with a `python3 re.sub` that handles both quote styles (`''`/`""`) and both plugin namespaces (`cri.v1.images` / `grpc.v1.cri`); exits 1 loudly if no registry section. (2e49e5e) |
| 2026-06-30 | cilium-operator hits transient `Unauthorized` from the apiserver during HA bring-up (SA token not yet issued) → "Failed to start hive" exits → containerd keeps the container name reserved → operator stuck CreateContainerError even after apiserver stabilises → Cilium degrades cluster-wide → metrics-server/pods can't get sandbox → `07-cnpg` times out (D6) | Add `cilium_health_gate` at start of `06-phase2-start.sh`: poll up to 300s for all cilium agents+operator Ready; at 150s purge Unknown/CrashLoop/CreateContainerError cilium pods so they reschedule (plain pod-delete clears the wedge); exit 1 only if it never converges. (273dcbc) |
| 2026-06-30 | Vagrantfile fired phase2-platform from a worker-3 `trigger.after :up`, but under `up.sh`'s per-VM resilient bring-up worker join order isn't guaranteed → trigger fired before all nodes stably Ready → nodes flapped (6→0→6) during Phase 2 → `08-1` kubectl ops failed (D7) | Remove the auto-trigger; make `scripts/up.sh` the sole Phase 2 driver (it polls all nodes Ready then runs phase2-platform once). Bare `vagrant up` no longer auto-runs Phase 2 by design. (5be1028) |
| 2026-06-30 | VMware SSH key-replacement race SIGHUPs the Phase 2 orchestrator mid-run (sub-scripts orphan, Phase 2 stalls partway — e.g. monitoring/storage ns never created); `up.sh`'s single retry was insufficient when the race recurred (D11) | `up.sh` Phase 2 is now a bounded retry loop (`PHASE2_MAX_ATTEMPTS=3`): recover_master1_ssh before each attempt, run phase2-platform (idempotent — resumes where it stalled), gate on phase2_complete, exit 1 only after all attempts. (a426b73) |
| 2026-06-30 | (narwhal-portal repo) in-cluster Kaniko build OOMKilled at 4Gi (Next.js standalone build) because `--compressed-caching=true` kept uncompressed+compressed layers in RAM; also 502 when pushing to a mid-restart Harbor (D8) | Set `--compressed-caching=false` (keep 4Gi limit); add a Harbor readiness gate (`kubectl rollout status` harbor-core+harbor-registry) before the Kaniko Job in `kaniko-build.sh`. narwhal-portal commit 8fe4159. |
| 2026-07-01 | A host reboot / `vagrant up` resume ran NO recovery (the Phase-2 `cilium_health_gate` only fires during provisioning), so a cold boot left a containerd stale-container wedge, Cilium/Istio `Unknown` ghosts, and platform CrashLoops — ingress dead until manual containerd restarts + pod cleanup | Two in-guest systemd oneshot units installed at provision, fired every boot: `narwhal-boot-heal.service` (all nodes — detect wedge, restart containerd+kubelet) + `narwhal-cluster-heal.service` (master-1 — restart not-Ready CNI, delete `Unknown` ghosts, kick CrashLoops, poll convergence). Validated via `vagrant reload`: converged to 0 non-running, ingress 307, no manual intervention. See `docs/reboot-recovery.md`. (b91091e) |
| 2026-07-01 | After reboot, static pods (etcd/apiserver/scheduler/controller-manager) showed `0/1 Running` with `<invalid>` restart ages: no working time sync (earlier "chrony not found") meant the kubelet started before chrony's `makestep` fixed the clock, so static-pod startTimes were recorded against a skewed clock → readiness never reconciled | Install chrony reliably; add `narwhal-clock-sync.service` (`chronyc waitsync`) + a `kubelet.service.d` drop-in so kubelet only starts once the clock is synced. To clear it on an already-skewed node: sync the clock then restart kubelet. (070149d) |
| 2026-07-01 | containerd 2.x `systemctl restart/stop containerd` leaves the **shim** alive; its child (the real container process) reparents to PID 1 and keeps holding ports/sockets/bolt-DB entries → `CreateContainerError` "name reserved" persists even after a containerd restart (bit deeper masters/openbao/etcd after reboot) | A plain containerd restart clears the common wedge (boot-heal), but a deep wedge needs: kill the reparented child PIDs, remove the CRI state dir `/var/lib/containerd/io.containerd.grpc.v1.cri/containers/`, AND `ctr -n k8s.io containers rm <id>` to drop the bolt-DB name reservation, then restart kubelet. |
| 2026-07-07 | Fresh clean install: ALL harbor pods CrashLoopBackOff with `exec /harbor/entrypoint.sh: exec format error` on arm64 nodes, even though `ghcr.io/dasomel/goharbor/*:latest` is a valid multi-arch image (the registry's arm64 variant is a genuine aarch64 binary, confirmed via `crane export`). Root cause: the `dasomel/ubuntu-26.04-xfs` Vagrant box bakes STALE amd64 `:latest` harbor layers into its containerd content store; a **mutable** tag is never re-fetched (a re-pull transfers only the ~16 KB manifest and reuses the baked amd64 layers — even `ctr pull --platform arm64` or pull-by-digest just fetch the manifest), so arm64 nodes execute amd64 binaries. Directly contradicts the older 2026-02-10 lesson ("use `:latest`") — for a baked box `:latest` is the trap. | **Pin harbor images to an IMMUTABLE version tag** in BOTH `08-5-registry.sh` (helm `--set`) and `harbor.yaml`: `:v2.15.1` for core/jobservice/registry-photon/registryctl/portal/nginx/redis-photon. A different tag has different layer digests absent from the stale bake → forces a real arm64 layer pull (verified `ctr run :v2.15.1 uname -m` → aarch64). **harbor-exporter EXCEPTION**: `ghcr.io/dasomel/goharbor/harbor-exporter` has NO clean `v2.15.x` multi-arch tag (only `:latest` + `:v2.15.0-build.NN`), so it can't follow v2.15.1; instead `metrics.enabled=false` (exporter Deployment never created, so the arch issue can't bite) and pin the unused override to `:v2.15.0-build.32`. Never trust `:latest` on a pre-baked box. (0e9a1fd) |
| 2026-07-08 | Falco 0.39.2 (chart 4.16.0) DaemonSet CrashLoopBackOff on every node: modern_ebpf probe fails `scap_init` ("An error occurred in an event source, forcing termination" → "Initialization issues during scap_init") on Ubuntu 26.04 kernel 7.0.0. NOT a config problem — BTF present (`/sys/kernel/btf/vmlinux`), pod privileged, driver already `modern_ebpf` (CO-RE). Falco 0.39.2's bundled libscap/libpman predates kernel 7.0. A live upgrade to 0.43.1 revealed it is a BREAKING migration, not a version bump: `--cri` CLI flag removed (`Option 'cri' does not exist`); container.* fields moved to a separate container plugin in Falco 0.41+, so the bundled rules fail to compile (`filter_check called with nonexistent field container.id`); falco.yaml/rules schema changed — it never reached scap_init, so even 0.43.x kernel-7.0 support is unconfirmed. | Disabled Falco in gitops (`falco.yaml` wrapped in `{{- if false }}` with rationale) to stop the crashloop; runtime coverage remains via trivy-operator (vuln scan), kyverno (policy), NetworkPolicy, and STRICT mTLS. A real fix is a dedicated Falco 0.43+ migration (args + falco.yaml + rules + container plugin via falcoctl) with `scap_init` verified on kernel 7.0, OR pin the box to a 6.x LTS kernel. |

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
| 2026-04-07 | nfs-quota-agent v0.2.1 HTML embeds Go raw-string-literal syntax `` ` + "`" + ` `` instead of actual backtick chars → JS SyntaxError → dashboard shows "Loading..." forever | Apply APISIX `serverless-post-function` body_filter with Lua `ngx.re.gsub` to replace all 22 occurrences at the gateway layer. **RESOLVED 2026-07-19 by upgrade to v0.3.0** (emits real backticks — verified 0 bug patterns / 17 real template literals through APISIX SSO); the gateway body_filter workaround was removed. |
| 2026-07-19 | Kyverno Enforce grandfather trap: nfs-quota-agent's running v0.2.1 pod (privileged + hostPID for XFS project quotas) was admitted BEFORE the `disallow-privileged-containers`/`disallow-host-namespaces` Enforce policies existed, so it kept running — but the v0.3.0 upgrade rollout hit `ReplicaFailure: FailedCreate` (webhook denied the NEW pod). A grandfathered privileged/hostPID/hostPort workload looks healthy until its next rollout/reschedule silently fails | When upgrading/rescheduling any privileged, hostPID/hostNetwork/hostIPC, or hostPort workload, FIRST confirm its namespace is in the relevant Kyverno policy `exclude` list — a running pod is NOT proof the policy allows it. Added `nfs-quota-agent` to both privileged + host-namespaces exclusions. Same class as the 2026-07-10 `=()` anchor lesson: Enforce policies bite at admission, i.e. on the NEXT create, not on already-running pods. |
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

- Provisioning order is encoded in filename prefixes: `scripts/common/0*.sh` then `scripts/cluster/00-15*.sh` (Phase 1 = cluster infra, Phase 2 = platform apps; `scripts/up.sh` is the SOLE Phase 2 driver — bare `vagrant up` does not auto-run Phase 2).
- GitOps apps: `gitops/charts/narwhal-apps/templates/` (+ `gitops/charts/narwhal-platform/` for domain-bearing resources); raw manifests in `gitops/resources/`; app-of-apps root at `gitops/apps/app-of-apps.yaml`.

## Development Commands

Standard `vagrant` workflow (`up/halt/destroy/ssh`); non-obvious bits only:

```bash
# Run Phase 2 (platform apps) manually — bare `vagrant up` does NOT auto-run it
vagrant provision master-1 --provision-with phase2-platform
```

Ralph loop: `/ralph` (OMC) with `.claude/templates/PROMPT.md`. Project slash commands live in `.claude/commands/`.

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

## Infrastructure Resource Safety

- **Limit parallel cluster modifications to 2-3 max** -- concurrent operations cause OOM in Master 4GB / Worker 6GB environment
- Do not restart/modify multiple pods simultaneously -- modify one -> verify stability -> next modification
- Check resource availability with `kubectl top nodes` between cluster modification tasks before proceeding
- After applying infrastructure/cluster changes, **verify from actual user perspective** (e.g., curl endpoint, kubectl exec test, DNS resolve)

---
