# Security Policy

## Principles

### 1. Secure by Default
All configurations must be secure by default. Insecure defaults should not be used even for testing.

### 2. SSH Key Management
- Use `insert_key = true` (Vagrant default)
- Insecure key is only for initial access
- Automatically replaced with secure key on VM creation

### 3. Network Configuration
- Use private network when possible
- Restrict access with firewall when using public network
- Expose only necessary ports

```ruby
config.vm.network "private_network", ip: "192.168.56.10"
```

### 4. Shared Folders
- Disable if not needed (attack vector to host)

```ruby
config.vm.synced_folder ".", "/vagrant", disabled: true
```

### 5. Sensitive Data
- Never hardcode secrets in Vagrantfile
- Use environment variables or encrypted files
- Add sensitive files to .gitignore

## Kubernetes Security

### API Server
```yaml
apiServer:
  extraArgs:
    anonymous-auth: "false"
    audit-log-path: "/var/log/kubernetes/audit.log"
```

### Pod Security Standards
```yaml
metadata:
  labels:
    pod-security.kubernetes.io/enforce: restricted
```

### Network Policy (Default Deny)
```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-all
spec:
  podSelector: {}
  policyTypes: [Ingress, Egress]
```

### Gateway Ingress & Machine Bypass Surface (Narwhal #139, #101, #42)

Machine clients (Git CLI, ArgoCD Helm chart fetchers, cluster verification probes) cannot complete interactive browser-based OIDC flows. APISIX defines a high-priority bypass route (`gitea-git-bypass`, priority 100) with least-privilege scoping:

1. **Allowed Bypass Paths**:
   - **Git Smart HTTP**: `\.git(/|$)`, `/info/refs`, `/git-upload-pack`, `/git-receive-pack`
   - **Package Registry**: `^/api/packages/` (ArgoCD Helm index/tarball reads and chart publishing)
   - **Verification Probes**: `^/api/v1/version$`, `^/api/v1/repos/gitea-admin/narwhal-gitops$` (`scripts/test/verify-cluster.sh` readiness checks)
   - **OAuth / Login**: `^/login/oauth/`, `^/user/login`

2. **Protected API Endpoints**:
   - All other `/api/v1/*` REST endpoints (such as `/api/v1/users`, `/api/v1/admin/*`, `/api/v1/orgs`, and arbitrary repo endpoints) fall through to the default route (`gitea`, priority 0) and MUST authenticate via Keycloak OIDC (302 redirect for unauthenticated browser clients).
   - Machine scripts performing administrative actions (`scripts/cluster/14-gitops-bootstrap.sh`, `scripts/gitops/push-to-gitea.sh`, `narwhal-portal`) connect directly via in-cluster service DNS (`http://gitea-http.devtools.svc.cluster.local:3000`) or localhost port-forwarding with explicit tokens, never traversing the external bypass route.

3. **Package Registry Authentication Policy**:
   - **Reads (Anonymous)**: ArgoCD fetches published Helm charts and index metadata anonymously over HTTPS without browser redirect.
   - **Writes (Authenticated)**: Chart publishing (`scripts/cluster/12-gitea.sh`) requires machine credentials (`gitea-admin` basic auth or personal access token).

4. **Header Stripping & Anti-Spoofing**:
   - Both bypass and OIDC routes strip incoming `X-WEBAUTH-USER` and `X-Userinfo` via `proxy-rewrite` headers removal to prevent reverse proxy authentication spoofing (#160).

## OS Kernel Hardening (CIS Benchmark)

Applied in dasomel/ubuntu-24.04 Box:

| Setting | Value | Purpose |
|---------|-------|---------|
| net.ipv4.conf.all.rp_filter | 1 | IP Spoofing prevention |
| net.ipv4.conf.all.accept_redirects | 0 | ICMP Redirect block |
| net.ipv4.tcp_syncookies | 1 | SYN Flood prevention |
| net.ipv4.icmp_echo_ignore_broadcasts | 1 | Smurf attack prevention |

## SSH Hardening

```
PermitRootLogin no
PasswordAuthentication no  # Production
PubkeyAuthentication yes
MaxAuthTries 3
```

## Local Dev Exceptions

| Item | Production | Local Dev |
|------|------------|-----------|
| PasswordAuthentication | no | yes |
| metrics-server TLS | strict | insecure |
| NetworkPolicy | required | optional |

## Checklist

### Pre-creation
- [ ] No hardcoded secrets in Vagrantfile
- [ ] Sensitive files in .gitignore
- [ ] Latest Base Box version

### Post-creation
- [ ] All nodes Ready
- [ ] System pods running
- [ ] SSH key replaced

### Pre-production
- [ ] RBAC policies reviewed
- [ ] NetworkPolicy applied
- [ ] Pod Security Standards applied
- [ ] Audit logging enabled

## References

- [K8s Security Best Practices](https://kubernetes.io/docs/concepts/security/)
- [CIS Kubernetes Benchmark](https://www.cisecurity.org/benchmark/kubernetes)
- [NSA/CISA K8s Hardening Guide](https://media.defense.gov/2022/Aug/29/2003066362/-1/-1/0/CTR_KUBERNETES_HARDENING_GUIDANCE_1.2_20220829.PDF)
