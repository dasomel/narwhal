# DNS 접속 가이드

Master 노드의 dnsmasq(포트 53)가 `*.local.narwhal.internal` 도메인을 `192.168.56.200` (MetalLB LoadBalancer IP)으로 해석합니다.

> **이 문서는 Vagrant 기준이다.** Kakao Cloud에는 해당하지 않는다 — `PROVIDER=kakao`는 dnsmasq를
> 건너뛰고, 노드는 프라이빗 서브넷에 있어 리졸버로 지정할 수도 없다. 이름은 worker LB의 공인 IP가
> 서빙하고 공개 DNS가 없으므로 `/etc/hosts`가 유일한 경로다:
> `scripts/cloud/setup-hosts-kakao.sh --apply` — 자세한 내용은 [`cloud-deployment.md`](../kakao/cloud-deployment.md).

## 네트워크 구성

| 구성요소 | IP | 설명 |
|----------|-----|------|
| Master-1 (DNS) | 192.168.56.10 | dnsmasq DNS 서버 (primary) |
| Master-2 (DNS) | 192.168.56.11 | dnsmasq DNS 서버 (secondary) |
| Master-3 (DNS) | 192.168.56.12 | dnsmasq DNS 서버 (tertiary) |
| MetalLB VIP | 192.168.56.200 | APISIX LoadBalancer IP |
| Control Plane VIP | 192.168.56.100 | kube-vip (API Server) |

## 서비스 URL

| 서비스 | URL | 설명 |
|--------|-----|------|
| ArgoCD | https://argocd.local.narwhal.internal | GitOps CD |
| Grafana | https://grafana.local.narwhal.internal | 모니터링 대시보드 |
| Gitea | https://gitea.local.narwhal.internal | Git 서버 |
| Harbor | https://harbor.local.narwhal.internal | 컨테이너 레지스트리 |
| Keycloak | https://keycloak.local.narwhal.internal | IAM / SSO |
| Headlamp | https://headlamp.local.narwhal.internal | Kubernetes UI |
| OpenBao | https://openbao.local.narwhal.internal | 시크릿 관리 |
| Hubble | https://hubble.local.narwhal.internal | Cilium 네트워크 관찰 |

## DNS 설정

> **왜 `.internal`인가?** `local.narwhal.internal`은 ICANN이 사설 전용으로 예약한 TLD로, 공개 DNS에서 절대 해석되지 않습니다. 이전 도메인(`local.narwhal.io`)은 실제 공개 도메인이어서 외부 DNS 와일드카드가 클러스터 내부 dnsmasq보다 우선 적용될 수 있었습니다.

### macOS

**권장: `/etc/resolver` (분리 DNS, HA 지원)**

```bash
# local.narwhal.internal 도메인만 Master DNS 사용 (HA: 3개 master)
sudo mkdir -p /etc/resolver
printf 'nameserver 192.168.56.10\nnameserver 192.168.56.11\nnameserver 192.168.56.12\n' | sudo tee /etc/resolver/local.narwhal.internal

# 설정 확인
scutil --dns | grep -A3 "local.narwhal.internal"

# 테스트
nslookup argocd.local.narwhal.internal 192.168.56.10
```

**대안: `/etc/hosts` 직접 항목 (단일 IP, DNS 없이 동작)**

```bash
# MetalLB VIP(192.168.56.200)로 자주 쓰는 서비스만 직접 매핑
sudo tee -a /etc/hosts << 'EOF'
192.168.56.200 argocd.local.narwhal.internal
192.168.56.200 grafana.local.narwhal.internal
192.168.56.200 gitea.local.narwhal.internal
192.168.56.200 harbor.local.narwhal.internal
192.168.56.200 keycloak.local.narwhal.internal
192.168.56.200 headlamp.local.narwhal.internal
192.168.56.200 openbao.local.narwhal.internal
192.168.56.200 hubble.local.narwhal.internal
EOF
```

### Linux (systemd-resolved)

```bash
# DNS 설정 파일 생성
sudo mkdir -p /etc/systemd/resolved.conf.d
sudo tee /etc/systemd/resolved.conf.d/narwhal.conf << 'EOF'
[Resolve]
DNS=192.168.56.10 192.168.56.11 192.168.56.12
Domains=~local.narwhal.internal
EOF

# 서비스 재시작
sudo systemctl restart systemd-resolved

# 테스트
resolvectl query argocd.local.narwhal.internal
```

### Linux (NetworkManager)

```bash
# nmcli로 DNS 추가
nmcli connection modify "유선 연결 1" +ipv4.dns "192.168.56.10"
nmcli connection modify "유선 연결 1" +ipv4.dns-search "local.narwhal.internal"
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
Add-Content -Path C:\Windows\System32\drivers\etc\hosts -Value "192.168.56.200 argocd.local.narwhal.internal"
```

## DNS 테스트

```bash
# nslookup 테스트 (Master DNS 서버 지정)
nslookup argocd.local.narwhal.internal 192.168.56.10

# dig 테스트
dig @192.168.56.10 grafana.local.narwhal.internal

# curl 테스트 (DNS 설정 완료 후)
curl -k -I https://argocd.local.narwhal.internal
```

## 브라우저 접속

DNS 설정 후 브라우저에서 직접 접속:

```bash
# macOS
open https://argocd.local.narwhal.internal
open https://grafana.local.narwhal.internal

# Linux
xdg-open https://argocd.local.narwhal.internal

# Windows
start https://argocd.local.narwhal.internal
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

## APISIX API Gateway

APISIX가 API 게이트웨이로 동작하며 다음 기능을 제공합니다:

- **TLS 종료**: self-signed 인증서 (개발용, ApisixTls CRD)
- **OIDC 인증**: Keycloak `openid-connect` 플러그인 (ApisixRoute에 설정)
- **라우팅**: ApisixRoute CRD로 hostname → backend 매핑
- **Namespace**: `platform-system`

### ApisixRoute 예시

```yaml
apiVersion: apisix.apache.org/v2
kind: ApisixRoute
metadata:
  name: my-route
  namespace: my-namespace
spec:
  http:
    - name: my-route
      match:
        hosts:
          - "my-app.local.narwhal.internal"
        paths:
          - "/*"
      backends:
        - serviceName: my-service
          servicePort: 80
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
# APISIX Pod 상태 확인
vagrant ssh master-1 -c "kubectl get pods -n platform-system -l app.kubernetes.io/name=apisix"

# APISIX 서비스 확인 (LoadBalancer IP: 192.168.56.200)
vagrant ssh master-1 -c "kubectl get svc -n platform-system | grep apisix"

# MetalLB 상태 확인
vagrant ssh master-1 -c "kubectl get ipaddresspool -n metallb-system"
vagrant ssh master-1 -c "kubectl get pods -n metallb-system"

# ApisixRoute 상태 확인
vagrant ssh master-1 -c "kubectl get apisixroute -A"

# ApisixTls 상태 확인
vagrant ssh master-1 -c "kubectl get apisixtls -A"
```

### TLS 인증서 오류

개발 환경에서는 self-signed 인증서를 사용하므로 브라우저 경고가 표시됩니다.

**Self-signed 인증서 신뢰 설정**:

1. **macOS**:
   - VM에서 CA 인증서 추출: `kubectl get secret narwhal-ca-cert -n cert-manager -o jsonpath='{.data.ca\.crt}' | base64 -d > narwhal-ca.crt`
   - Keychain Access 앱 열기 → 인증서 가져오기 → 신뢰 설정 "항상 신뢰"로 변경

2. **브라우저 경고 수락**: `https://argocd.local.narwhal.internal` 접속 시 "위험 감수 및 계속" 클릭

3. **curl 테스트**: `-k` 플래그로 인증서 검증 스킵
   ```bash
   curl -k https://argocd.local.narwhal.internal
   ```
