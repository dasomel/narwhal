# VM 리부트 생존성 아키텍처

> Narwhal IDP 클러스터의 VM 리부트 시 자동 복구를 보장하는 아키텍처 설계 문서

---

## 목차

1. [문제 정의](#1-문제-정의)
2. [근본 원인 분석](#2-근본-원인-분석)
3. [해결 아키텍처](#3-해결-아키텍처)
4. [변경 사항 상세](#4-변경-사항-상세)
5. [복구 시퀀스](#5-복구-시퀀스)
6. [모니터링](#6-모니터링)
7. [검증 절차](#7-검증-절차)
8. [트러블슈팅](#8-트러블슈팅)

---

## 1. 문제 정의

### 카스케이드 장애 패턴

`vagrant halt && vagrant up` 또는 VM 비정상 종료 후 재부팅 시 전체 플랫폼이
CrashLoopBackOff에 빠지는 카스케이드 장애가 발생했습니다.

```
t+0s    VM 부팅
t+30s   containerd 시작
t+60s   kubelet 시작, Cilium agent 초기화 시작
        → node.cilium.io/agent-not-ready taint 적용
        ╳ ztunnel Pending (toleration 없음) ← ROOT CAUSE
        ╳ istio-cni Pending (toleration 없음)
t+120s  Cilium 완료 → taint 제거
        → ztunnel/istio-cni 이제서야 스케줄링 시작
t+180s  ztunnel Ready, HBONE(15008) 복원 시작
        → 하지만 이미 DB 연결 타임아웃, 앱들 CrashLoopBackOff 진입
t+300s+ backoff 간격 증가로 복구 지연 (10s → 20s → 40s → ...)
t+600s+ 일부 앱 여전히 CrashLoop (최악 시 수동 개입 필요)
```

### 영향 범위

| 컴포넌트 | 증상 |
|----------|------|
| ztunnel | Pending → HBONE 15008 트래픽 전면 단절 |
| istio-cni | Pending → 새 Pod의 ambient mesh 참여 불가 |
| DB 의존 앱 (Keycloak, Harbor, Gitea) | HBONE 단절 → CNPG 연결 실패 → CrashLoop |
| SSO 체인 | Keycloak 다운 → OAuth2-Proxy 실패 → 전체 앱 인증 불가 |
| 모니터링 | Prometheus/Grafana 데이터 수집 중단 |

---

## 2. 근본 원인 분석

### Cilium Taint 메커니즘

Cilium CNI는 노드 부팅 시 `node.cilium.io/agent-not-ready:NoSchedule` taint를 적용합니다.
이는 Cilium이 완전히 초기화되기 전에 Pod가 스케줄링되어 네트워크 없이 시작하는 것을 방지하는
정상적인 안전장치입니다.

```
노드 부팅
  → kubelet 시작
    → Cilium DaemonSet Pod 스케줄링
      → Cilium agent 초기화 시작
        → node.cilium.io/agent-not-ready taint 적용
        → (초기화 완료 시 taint 자동 제거)
```

### 누락된 Toleration

Cilium 자체 DaemonSet은 이 taint에 대한 toleration을 가지고 있지만,
**Istio ambient mesh 컴포넌트(ztunnel, istio-cni)에는 해당 toleration이 없었습니다.**

```yaml
# ztunnel/istio-cni에 있던 toleration (부족)
tolerations:
  - key: node-role.kubernetes.io/control-plane    # control-plane 노드 허용
  - key: node.kubernetes.io/disk-pressure          # 디스크 압력 허용
  # ╳ node.cilium.io/agent-not-ready 누락!
```

### 왜 초기 설치 시에는 문제가 없었나?

초기 프로비저닝은 다음 순서로 실행됩니다:

1. `03-cni-install.sh`: Cilium 설치 및 완료 대기
2. `09-istio-ambient.sh`: Istio 설치

Cilium이 이미 Ready이므로 taint가 없는 상태에서 Istio가 설치됩니다.
**리부트 시에는 모든 컴포넌트가 동시에 시작**되므로 경쟁 조건이 발생합니다.

---

## 3. 해결 아키텍처

### 설계 원칙

1. **Toleration 추가** (근본 해결): Istio DaemonSet이 Cilium taint를 무시하고 즉시 스케줄링
2. **systemd 재시작 정책**: 인프라 서비스(containerd, NFS, dnsmasq) 자동 복구
3. **Graceful Shutdown**: 정상 종료로 스테일 상태 최소화
4. **모니터링**: 복구 지연 시 알림으로 조기 감지

### 변경 매트릭스

| 우선순위 | 파일 | 변경 |
|---------|------|------|
| **P1** | `scripts/common/02-containerd.sh` | `Restart=always`, `RestartSec=5` |
| **P1** | `scripts/cluster/09-istio-ambient.sh` | 3곳 toleration 추가 |
| **P1** | `gitops/apps/istiod.yaml` | toleration 추가 (ArgoCD 영속) |
| **P1** | `gitops/apps/istio-cni.yaml` | toleration 추가 |
| **P1** | `gitops/apps/ztunnel.yaml` | toleration 추가 |
| **P2** | `scripts/cluster/01-nfs-server.sh` | systemd drop-in |
| **P2** | `scripts/cluster/10-dnsmasq.sh` | systemd drop-in |
| **P3** | `gitops/resources/prometheus-alerts.yaml` | `reboot-recovery` 알림 그룹 |
| **P4** | `Vagrantfile` | `trigger.before :halt` |

---

## 4. 변경 사항 상세

### P1: Root Cause 제거

#### containerd 자동 재시작

containerd가 비정상 종료 시 5초 후 자동 재시작합니다.

```ini
# /etc/systemd/system/containerd.service.d/limits.conf
[Service]
LimitNOFILE=1048576
LimitNPROC=infinity
LimitCORE=infinity
TasksMax=infinity
Restart=always
RestartSec=5
```

**파일**: `scripts/common/02-containerd.sh`

#### Istio Toleration 추가

istiod, istio-cni, ztunnel 모두에 동일한 toleration을 추가합니다:

```yaml
tolerations:
  - key: node-role.kubernetes.io/control-plane
    operator: Exists
    effect: NoSchedule
  - key: node.kubernetes.io/disk-pressure
    operator: Exists
    effect: NoSchedule
  - key: node.cilium.io/agent-not-ready    # 신규 추가
    operator: Exists
    effect: NoSchedule
```

이 toleration 덕분에 Cilium이 초기화되는 동안에도 ztunnel/istio-cni가 즉시 스케줄링됩니다.
ztunnel은 Cilium이 준비되는 즉시 HBONE 트래픽을 처리할 수 있게 됩니다.

**파일 (스크립트)**: `scripts/cluster/09-istio-ambient.sh` - istiod, istio-cni, ztunnel values
**파일 (GitOps)**: `gitops/apps/istiod.yaml`, `gitops/apps/istio-cni.yaml`, `gitops/apps/ztunnel.yaml`

> **중요**: 스크립트와 GitOps 양쪽 모두 수정해야 합니다.
> ArgoCD `selfHeal: true`가 활성화되어 있으므로 스크립트만 수정하면
> ArgoCD가 GitOps 상태로 되돌려 toleration이 제거됩니다.

### P2: 인프라 서비스 자동 복구

#### NFS 서버

네트워크 의존성 명시 + 실패 시 자동 재시작:

```ini
# /etc/systemd/system/nfs-kernel-server.service.d/restart.conf
[Unit]
After=network-online.target
Wants=network-online.target

[Service]
Restart=on-failure
RestartSec=10
```

**파일**: `scripts/cluster/01-nfs-server.sh`

NFS가 네트워크보다 먼저 시작되어 바인드 실패하는 문제를 방지합니다.
`Restart=on-failure`로 비정상 종료 시 10초 후 자동 재시작합니다.

#### dnsmasq

```ini
# /etc/systemd/system/dnsmasq.service.d/restart.conf
[Unit]
After=network-online.target
Wants=network-online.target

[Service]
Restart=always
RestartSec=5
```

**파일**: `scripts/cluster/10-dnsmasq.sh`

모든 master 노드의 dnsmasq가 `Restart=always`로 항상 복구됩니다.
CoreDNS의 `*.local.narwhal.internal` 포워딩이 dnsmasq에 의존하므로 DNS 해석 연속성을 보장합니다.

### P3: 리부트 복구 모니터링

Prometheus 알림 그룹 `reboot-recovery`를 추가하여 복구 지연 시 조기 감지합니다:

| 알림 | 조건 | 심각도 |
|------|------|--------|
| `CiliumAgentNotReady` | `node.cilium.io/agent-not-ready` taint 3분 이상 지속 | warning |
| `ZtunnelNotReady` | ztunnel DaemonSet Ready < Desired, 5분 이상 | critical |
| `IstioCNINotReady` | istio-cni-node DaemonSet Ready < Desired, 5분 이상 | critical |

**파일**: `gitops/resources/prometheus-alerts.yaml`

### P4: Graceful Shutdown

`vagrant halt` 실행 시 kubelet을 먼저 정상 종료하여 컨테이너가 graceful shutdown할
시간을 확보합니다.

```ruby
# Vagrantfile
config.trigger.before :halt do |trigger|
  trigger.name = "Graceful Kubernetes Shutdown"
  trigger.info = "Stopping kubelet gracefully before VM halt..."
  trigger.run_remote = {inline: "systemctl stop kubelet && sleep 3 || true"}
end
```

**파일**: `Vagrantfile`

kubelet이 먼저 종료되면:
- 실행 중인 Pod에 SIGTERM 전달
- containerd가 컨테이너를 정상 종료
- 리부트 시 스테일 containerd 참조 감소

---

## 5. 복구 시퀀스

### 변경 전 (장애 패턴)

```
t+0s    VM 부팅
t+30s   containerd 시작
t+60s   kubelet → Cilium agent-not-ready taint
        ╳ ztunnel/istio-cni Pending (toleration 없음)
t+120s  Cilium Ready → taint 제거 → ztunnel 스케줄링 시작
t+180s  ztunnel Ready, HBONE 복원
        ╳ 하지만 앱들이 이미 CrashLoopBackOff 진입
t+600s+ backoff 간격 증가로 복구 장기화
```

### 변경 후 (정상 복구)

```
t+0s    VM 부팅
t+30s   containerd 시작 (Restart=always 보장)
t+60s   kubelet 재시작, Cilium agent 초기화 시작
        → agent-not-ready taint 적용
        → ztunnel/istio-cni 즉시 스케줄링 (toleration으로 taint 무시)
t+90s   ztunnel/istio-cni 초기화 시작
t+120s  Cilium 완료 → taint 제거
        → ztunnel/istio-cni Ready
        → HBONE(15008) 트래픽 복원
t+180s  DB 연결 정상화, 앱 자동 복구
t+300s  전체 플랫폼 정상 운영
```

**핵심 차이**: ztunnel이 Cilium과 병렬로 초기화되므로 HBONE 복원이 ~60초 빨라지고,
앱들이 CrashLoopBackOff에 빠지기 전에 네트워크가 정상화됩니다.

---

## 6. 모니터링

### Prometheus 알림

리부트 후 정상 복구가 되지 않으면 다음 알림이 발생합니다:

```
3분 후   CiliumAgentNotReady (warning)
         → Cilium 초기화 지연, 노드 네트워킹 문제 가능성
5분 후   ZtunnelNotReady (critical)
         → HBONE 트래픽 단절, 전체 서비스 메시 영향
5분 후   IstioCNINotReady (critical)
         → 새 Pod의 ambient mesh 참여 차단
```

### Grafana 대시보드 권장 패널

| 패널 | PromQL |
|------|--------|
| Cilium taint 상태 | `kube_node_spec_taint{key="node.cilium.io/agent-not-ready"}` |
| ztunnel Ready 비율 | `kube_daemonset_status_number_ready{daemonset="ztunnel"} / kube_daemonset_status_desired_number_scheduled{daemonset="ztunnel"}` |
| 노드 부팅 시간 | `node_boot_time_seconds` |
| containerd 재시작 횟수 | `node_systemd_unit_state{name="containerd.service",state="active"}` |

---

## 7. 검증 절차

### 로컬 파일 검증

```bash
# 스크립트 문법
shellcheck scripts/common/02-containerd.sh \
  scripts/cluster/09-istio-ambient.sh \
  scripts/cluster/01-nfs-server.sh \
  scripts/cluster/10-dnsmasq.sh

# YAML 문법
yq eval '.' gitops/apps/ztunnel.yaml \
  gitops/apps/istio-cni.yaml \
  gitops/apps/istiod.yaml \
  gitops/resources/prometheus-alerts.yaml > /dev/null

# Vagrantfile 문법
ruby -c Vagrantfile
```

### 클러스터 적용 후 검증

```bash
# ztunnel toleration 확인
kubectl get ds ztunnel -n istio-system -o yaml | grep -A15 tolerations
# → node.cilium.io/agent-not-ready 포함 확인

# istio-cni toleration 확인
kubectl get ds istio-cni-node -n istio-system -o yaml | grep -A15 tolerations

# istiod toleration 확인
kubectl get deploy istiod -n istio-system -o yaml | grep -A15 tolerations

# containerd systemd 설정 확인 (모든 노드)
for node in master-1 master-2 master-3 worker-1 worker-2 worker-3; do
  echo "=== ${node} ==="
  vagrant ssh ${node} -c "systemctl show containerd | grep -E 'Restart=|RestartUSec='"
done

# NFS systemd 설정 확인 (master-1만)
vagrant ssh master-1 -c "systemctl show nfs-kernel-server | grep -E 'Restart=|RestartUSec='"

# dnsmasq systemd 설정 확인 (master 노드들)
for node in master-1 master-2 master-3; do
  echo "=== ${node} ==="
  vagrant ssh ${node} -c "systemctl show dnsmasq | grep -E 'Restart=|RestartUSec='"
done
```

### 리부트 테스트

```bash
# 1. 단일 워커 노드 리부트
vagrant halt worker-1
vagrant up worker-1

# 2분 후 확인
vagrant ssh master-1 -c "kubectl get nodes"
vagrant ssh master-1 -c "kubectl get pods -A | grep -v Running | grep -v Completed"

# 2. 전체 클러스터 리부트
vagrant halt
vagrant up

# 5분 후 확인
vagrant ssh master-1 -c "kubectl get nodes"
vagrant ssh master-1 -c "kubectl get pods -n istio-system"
vagrant ssh master-1 -c "kubectl get pods -A | grep -v Running | grep -v Completed"
```

### 성공 기준

| 항목 | 기준 |
|------|------|
| 노드 Ready | 전체 6개 노드 Ready (3분 이내) |
| ztunnel | 모든 노드에서 Running (2분 이내) |
| istio-cni | 모든 노드에서 Running (2분 이내) |
| HBONE 트래픽 | 복원 (3분 이내) |
| 플랫폼 앱 | 전체 Running (5분 이내) |
| DNS 해석 | `*.local.narwhal.internal` 정상 (3분 이내) |

---

## 8. 트러블슈팅

### ztunnel이 여전히 Pending인 경우

```bash
# taint 목록 확인
kubectl describe node <node-name> | grep Taints

# ztunnel Pod 이벤트 확인
kubectl describe pod -n istio-system -l app=ztunnel | grep -A 10 Events

# toleration이 적용되었는지 확인
kubectl get ds ztunnel -n istio-system -o jsonpath='{.spec.template.spec.tolerations}'
```

**원인 1: ArgoCD가 toleration을 되돌림**

ArgoCD `selfHeal: true`가 GitOps 상태를 강제합니다.
GitOps 파일(`gitops/apps/ztunnel.yaml`)에 toleration이 없으면 스크립트 변경이 무효화됩니다.

```bash
# GitOps 파일 확인
cat gitops/apps/ztunnel.yaml | grep -A 10 tolerations

# ArgoCD 동기화 상태 확인
kubectl get application ztunnel -n devtools -o jsonpath='{.status.sync.status}'
```

**원인 2: Helm release와 ArgoCD 충돌**

```bash
# Helm 릴리스 values 확인
helm get values ztunnel -n istio-system | grep -A 10 tolerations
```

### containerd가 재시작되지 않는 경우

```bash
# systemd 설정 확인
vagrant ssh <node> -c "systemctl cat containerd"
vagrant ssh <node> -c "cat /etc/systemd/system/containerd.service.d/limits.conf"

# 재시작 이력 확인
vagrant ssh <node> -c "systemctl show containerd --property=NRestarts"
vagrant ssh <node> -c "journalctl -u containerd --since '-10min'"
```

### NFS 마운트 실패

```bash
# NFS 서버 상태
vagrant ssh master-1 -c "systemctl status nfs-kernel-server"
vagrant ssh master-1 -c "exportfs -v"

# 워커에서 NFS 접근 테스트
vagrant ssh worker-1 -c "showmount -e 192.168.56.10"
```

### Prometheus 알림이 발생하지 않는 경우

```bash
# PrometheusRule 적용 확인
kubectl get prometheusrule narwhal-alerts -n monitoring

# Prometheus가 규칙을 로드했는지 확인
kubectl port-forward -n monitoring svc/prometheus-stack-kube-prom-prometheus 9090:9090
# 브라우저에서 http://localhost:9090/rules → reboot-recovery 그룹 확인
```

---

## 관련 문서

- [disaster-recovery.md](disaster-recovery.md) - 장애 복구 런북
- [troubleshooting.md](troubleshooting.md) - 일반 트러블슈팅
- [architecture.md](architecture.md) - 아키텍처 개요
- [operations.md](operations.md) - 일상 운영 가이드
