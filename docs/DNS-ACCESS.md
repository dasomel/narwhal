# DNS 접속 가이드

Master 노드의 dnsmasq(포트 53)가 `*.local.narwhal.io` 도메인을 `192.168.56.200` (MetalLB LoadBalancer IP)으로 해석합니다.

## 네트워크 구성

| 구성요소 | IP | 설명 |
|----------|-----|------|
| Master-1 (DNS) | 192.168.56.10 | dnsmasq DNS 서버 (primary) |
| Master-2 (DNS) | 192.168.56.11 | dnsmasq DNS 서버 (secondary) |
| MetalLB VIP | 192.168.56.200 | Traefik LoadBalancer IP |
| Control Plane VIP | 192.168.56.100 | kube-vip (API Server) |

## 서비스 URL

| 서비스 | URL | 설명 |
|--------|-----|------|
| ArgoCD | https://argocd.local.narwhal.io | GitOps CD |
| Grafana | https://grafana.local.narwhal.io | 모니터링 대시보드 |
| Gitea | https://gitea.local.narwhal.io | Git 서버 |
| Harbor | https://harbor.local.narwhal.io | 컨테이너 레지스트리 |
| Keycloak | https://keycloak.local.narwhal.io | IAM / SSO |
| Headlamp | https://headlamp.local.narwhal.io | Kubernetes UI |
| OpenBao | https://openbao.local.narwhal.io | 시크릿 관리 |
| Hubble | https://hubble.local.narwhal.io | Cilium 네트워크 관찰 |

## DNS 설정

### macOS

```bash
# local.narwhal.io 도메인만 Master DNS 사용 (HA: 양쪽 master)
sudo mkdir -p /etc/resolver
printf 'nameserver 192.168.56.10\nnameserver 192.168.56.11\n' | sudo tee /etc/resolver/local.narwhal.io

# 설정 확인
scutil --dns | grep -A3 "local.narwhal.io"

# 테스트
nslookup argocd.local.narwhal.io 192.168.56.10
```

### Linux (systemd-resolved)

```bash
# DNS 설정 파일 생성
sudo mkdir -p /etc/systemd/resolved.conf.d
sudo tee /etc/systemd/resolved.conf.d/narwhal.conf << 'EOF'
[Resolve]
DNS=192.168.56.10 192.168.56.11
Domains=~local.narwhal.io
EOF

# 서비스 재시작
sudo systemctl restart systemd-resolved

# 테스트
resolvectl query argocd.local.narwhal.io
```

### Linux (NetworkManager)

```bash
# nmcli로 DNS 추가
nmcli connection modify "유선 연결 1" +ipv4.dns "192.168.56.10"
nmcli connection modify "유선 연결 1" +ipv4.dns-search "local.narwhal.io"
nmcli connection up "유선 연결 1"
```

### Windows (PowerShell 관리자 권한)

```powershell
# VMware/VirtualBox 네트워크 어댑터 확인
Get-NetAdapter | Where-Object {$_.Name -like "*VMware*" -or $_.Name -like "*VirtualBox*"}

# DNS 서버 설정 (어댑터 이름에 맞게 수정)
Set-DnsClientServerAddress -InterfaceAlias "VMware Network Adapter VMnet8" -ServerAddresses 192.168.56.10

# 또는 hosts 파일 직접 수정 (C:\Windows\System32\drivers\etc\hosts)
# MetalLB IP로 직접 매핑
Add-Content -Path C:\Windows\System32\drivers\etc\hosts -Value "192.168.56.200 argocd.local.narwhal.io"
```

## DNS 테스트

```bash
# nslookup 테스트 (Master DNS 서버 지정)
nslookup argocd.local.narwhal.io 192.168.56.10

# dig 테스트
dig @192.168.56.10 grafana.local.narwhal.io

# curl 테스트 (DNS 설정 완료 후)
curl -k -I https://argocd.local.narwhal.io
```

## 브라우저 접속

DNS 설정 후 브라우저에서 직접 접속:

```bash
# macOS
open https://argocd.local.narwhal.io
open https://grafana.local.narwhal.io

# Linux
xdg-open https://argocd.local.narwhal.io

# Windows
start https://argocd.local.narwhal.io
```

## 기본 자격 증명

| 서비스 | 사용자 | 비밀번호 |
|--------|--------|----------|
| ArgoCD | admin | `kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" \| base64 -d` |
| Grafana | admin | admin (또는 Keycloak SSO) |
| Gitea | gitea-admin | gitea-admin |
| Harbor | admin | Harbor12345 |
| Keycloak | admin | admin |
| Headlamp | - | Keycloak SSO |
| OpenBao | - | `bao operator init` 후 root token |

## Traefik Gateway

Traefik이 Gateway API 컨트롤러로 동작하며 다음 기능을 제공합니다:

- **Rate Limiting**: 분당 100개 요청, 버스트 50
- **Body Size 제한**: 10MB (API), 100MB (Harbor)
- **TLS 종료**: self-signed 인증서 (개발용)

### Middleware 적용 예시

HTTPRoute에 Middleware를 적용하려면:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: my-route
  namespace: my-namespace
  annotations:
    # Traefik Middleware 참조
    traefik.io/middleware: "traefik-rate-limit@kubernetescrd"
spec:
  parentRefs:
    - name: traefik-gateway
      namespace: traefik
  hostnames:
    - "my-app.local.narwhal.io"
  rules:
    - backendRefs:
        - name: my-service
          port: 80
```

## 문제 해결

### DNS 해석 실패

```bash
# dnsmasq 상태 확인 (Master 노드에서)
vagrant ssh master-1 -c "sudo systemctl status dnsmasq"

# dnsmasq 설정 확인
vagrant ssh master-1 -c "cat /etc/dnsmasq.d/local.conf"

# dnsmasq 재시작
vagrant ssh master-1 -c "sudo systemctl restart dnsmasq"
```

### 연결 거부

```bash
# Traefik Pod 상태 확인
vagrant ssh master-1 -c "kubectl get pods -n traefik"

# Traefik 서비스 확인 (LoadBalancer IP: 192.168.56.200)
vagrant ssh master-1 -c "kubectl get svc traefik -n traefik"

# MetalLB 상태 확인
vagrant ssh master-1 -c "kubectl get ipaddresspool -n metallb-system"
vagrant ssh master-1 -c "kubectl get pods -n metallb-system"

# HTTPRoute 상태 확인
vagrant ssh master-1 -c "kubectl get httproute -A"

# Gateway 상태 확인
vagrant ssh master-1 -c "kubectl get gateway -n traefik"
```

### TLS 인증서 오류

개발 환경에서는 self-signed 인증서를 사용하므로 브라우저 경고가 표시됩니다.

**Self-signed 인증서 신뢰 설정**:

1. **macOS**:
   - VM에서 CA 인증서 추출: `kubectl get secret narwhal-ca-cert -n cert-manager -o jsonpath='{.data.ca\.crt}' | base64 -d > narwhal-ca.crt`
   - Keychain Access 앱 열기 → 인증서 가져오기 → 신뢰 설정 "항상 신뢰"로 변경

2. **브라우저 경고 수락**: `https://argocd.local.narwhal.io` 접속 시 "위험 감수 및 계속" 클릭

3. **curl 테스트**: `-k` 플래그로 인증서 검증 스킵
   ```bash
   curl -k https://argocd.local.narwhal.io
   ```
