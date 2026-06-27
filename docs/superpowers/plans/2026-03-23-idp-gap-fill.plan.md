# Plan: IDP Gap Fill — idp/ 분석 기반 narwhal 갭 메우기

**Feature**: idp-gap-fill
**Date**: 2026-03-23
**Phase**: Plan
**Status**: Draft

---

## Executive Summary

| 항목 | 내용 |
|------|------|
| 문제 | narwhal이 idp/ 대비 모니터링 알림, DB UI, 검증 자동화 부재 |
| 솔루션 | idp 검증된 6개 패턴/컴포넌트를 narwhal에 이식 |
| 기능/UX 효과 | 서비스 장애 즉시 알림, DB 웹 관리, RBAC 자동 검증 |
| 핵심 가치 | 로컬 IDP가 프로덕션 idp 수준의 운영 성숙도 달성 |

| 관점 | 내용 |
|------|------|
| Problem | idp 운영 중 검증된 패턴이 narwhal에 미반영 → 운영 사각지대 존재 |
| Solution | Blackbox Exporter + Alertmanager Telegram + PgAdmin + RBAC 검증 + 교훈 동기화 |
| Function UX Effect | Telegram으로 서비스 다운 즉시 수신, pgadmin.local.narwhal.internal 접속, RBAC 자동 검증 |
| Core Value | 개발 환경이지만 프로덕션 수준의 가시성과 검증 체계 확보 |

---

## 1. User Intent Discovery (Phase 1 결과)

- **핵심 목적**: idp vs narwhal 전체 아키텍처 갭 분석 후 모두 메우기
- **대상 사용자**: narwhal 클러스터 운영자 / 개발자
- **성공 기준**:
  - 서비스 다운 시 Telegram 알림 수신
  - PostgreSQL 웹 UI (pgadmin.local.narwhal.internal) 접속 가능
  - K8s RBAC 검증 스크립트로 Authentik 그룹 권한 자동 확인
  - idp의 주요 교훈이 CLAUDE.md Mistakes Log에 반영

---

## 2. Gap Analysis: idp/ vs narwhal

### narwhal에 있는 것 (현재 완성)

| 컴포넌트 | narwhal | idp |
|---------|---------|-----|
| K8s | v1.35, Cilium, kube-vip | v1.35, Cilium, kube-vip |
| Gateway | APISIX | APISIX |
| IAM | Authentik | Keycloak |
| GitOps | Gitea + ArgoCD | GitLab + ArgoCD |
| Registry | Harbor (ARM64) | Harbor |
| DB | CNPG (unified narwhal-db) | CNPG |
| Storage | SeaweedFS + Velero | MinIO + Velero |
| Monitoring | Prometheus + Loki + Tempo + Grafana | 동일 |
| Service Mesh | Istio ambient | Istio ambient |
| Certs | cert-manager | cert-manager |
| DNS | dnsmasq + CoreDNS hairpin | PowerDNS + ExternalDNS |
| K8s UI | Headlamp | Headlamp |

### 갭 전체 목록

| # | 항목 | 우선순위 | 이 계획 |
|---|------|---------|--------|
| 1 | Blackbox Exporter | 높음 | **포함** |
| 2 | Alertmanager Telegram | 높음 | **포함** |
| 3 | RBAC 검증 스크립트 | 중간 | **포함** |
| 4 | PgAdmin | 중간 | **포함** |
| 5 | CLAUDE.md 교훈 동기화 | 중간 | **포함** |
| 6 | Velero UI 업그레이드 (0.10→0.14) | 낮음 | **포함** |
| 7 | External Secrets Operator | 높음 | 지연 (복잡도) |
| 8 | OpenBao Transit 자동 Unseal | 높음 | 지연 (수동 작업 필요) |
| 9 | Forecastle 포털 | 중간 | 지연 (Headlamp으로 대체) |
| 10 | 기능 기반 도메인 네이밍 변경 | 중간 | 지연 (파괴적 변경) |
| 11 | Chaos Mesh | 낮음 | 지연 (Worker 6GB 제약) |
| 12 | k6 부하 테스트 | 낮음 | 지연 |
| 13 | E2E 테스트 (Playwright) | 낮음 | 지연 |
| 14 | Termix 웹 터미널 | 낮음 | 지연 (무거움) |

---

## 3. Alternatives Explored (Phase 2 결과)

### 채택: Approach A — 우선순위 기반 단계적 추가

- **이유**: 운영 가치 높은 것부터 단계적으로 추가 → 빠른 가시적 효과
- 6개 항목, 난이도 낮음~중간, 기존 스크립트 패턴 재사용

### 검토된 대안

- **Approach B (운영 안정성만)**: ESO, OpenBao Transit 포함되어 복잡도 높음 → 지연
- **Approach C (문서/패턴만)**: 실질적 기능 개선 없음 → 최소 작업 CLAUDE.md 반영으로 흡수

---

## 4. YAGNI Review (Phase 3 결과)

### 1차 계획에서 제거

| 항목 | 제거 이유 |
|------|---------|
| External Secrets Operator | 현재 수동 시크릿 복사 패턴으로 충분, 추가 인프라 |
| OpenBao Transit 자동 Unseal | VM 재부팅 드문 환경, Shamir 수동 unseal 운영 가능 |
| Forecastle 포털 | Headlamp가 대시보드 역할 수행 중 |
| 도메인 네이밍 변경 | 기존 CLAUDE.md, docs 전체에 파급 → 이득 대비 비용 큼 |

### 최종 범위 (6개)

1. Blackbox Exporter
2. Alertmanager Telegram
3. K8s RBAC 검증 스크립트
4. PgAdmin
5. CLAUDE.md 교훈 반영
6. Velero UI 업그레이드

---

## 5. 구현 설계 (Phase 4)

### 5.1 Blackbox Exporter

**목적**: 8개 narwhal 서비스 엔드포인트 HTTP 가용성 모니터링

**파일**:
- `scripts/cluster/08-7-blackbox.sh` (신규)
- `gitops/apps/blackbox-exporter.yaml` (신규)

**Helm 차트**: `prometheus-community/prometheus-blackbox-exporter`

**모니터링 대상**:
```
https://authentik.local.narwhal.internal/-/health/ready/
https://argocd.local.narwhal.internal/healthz
https://grafana.local.narwhal.internal/api/health
https://gitea.local.narwhal.internal/api/swagger
https://harbor.local.narwhal.internal/api/v2.0/systeminfo
https://headlamp.local.narwhal.internal
https://openbao.local.narwhal.internal/v1/sys/health
https://hubble.local.narwhal.internal
```

**PrometheusRule**:
```yaml
- alert: EndpointDown
  expr: probe_success == 0
  for: 5m
  labels:
    severity: critical
```

**네임스페이스**: `monitoring`

---

### 5.2 Alertmanager Telegram

**목적**: 서비스 다운, 노드 이슈 등 Telegram 채널 알림

**파일**:
- `scripts/cluster/08-8-alertmanager-telegram.sh` (신규)
- `gitops/resources/alertmanager-telegram.yaml` (신규)

**사전 요건** (수동):
1. Telegram BotFather에서 봇 생성 → BOT_TOKEN 취득
2. 봇을 채널에 초대 → CHAT_ID 취득
3. `kubectl create secret generic alertmanager-telegram -n monitoring --from-literal=botToken=<TOKEN> --from-literal=chatID=<ID>`

**리소스**:
```yaml
# AlertmanagerConfig (narwhal-telegram)
# receiver: telegram-receiver
# route: severity=critical/warning → telegram
```

**PrometheusRule** (기본 3개):
- `NarwhalEndpointDown`: Blackbox probe 실패 5분
- `NarwhalNodePressure`: 노드 디스크/메모리 압력
- `NarwhalPodCrashLoop`: CrashLoopBackOff 15분

---

### 5.3 K8s RBAC 검증 스크립트

**목적**: Authentik 그룹별 K8s 네임스페이스 접근 권한 자동 검증

**파일**: `scripts/verify/verify-k8s-rbac.sh` (신규, idp 패턴 참조)

**테스트 항목** (~20개):
```
[그룹: cluster-admin]
  ✓ kubectl get pods -n platform-system
  ✓ kubectl get pods -n monitoring
  ✓ kubectl get nodes

[그룹: developer]
  ✓ kubectl get pods -n dev
  ✗ kubectl get pods -n platform-system (forbidden)

[그룹: viewer]
  ✓ kubectl get pods -n dev (read-only)
  ✗ kubectl create pod -n dev (forbidden)
```

**실행 방법**:
```bash
vagrant ssh master-1 -- bash /home/vagrant/scripts/verify/verify-k8s-rbac.sh
```

---

### 5.4 PgAdmin

**목적**: CNPG narwhal-db PostgreSQL 웹 UI

**파일**: `scripts/cluster/08-9-pgadmin.sh` (신규)

**Helm 차트**: `runix/pgadmin4`
**이미지**: `docker.io/dpage/pgadmin4` (대체 없음, 사유 명시 필요)
**네임스페이스**: `devtools`
**도메인**: `pgadmin.local.narwhal.internal`

**Authentik 연동**:
- OAuth2 Provider: `pgadmin` 클라이언트 (`11-2-authentik-config.sh`에 추가)
- Redirect URI: `https://pgadmin.local.narwhal.internal/oauth2/authorize`

**ApisixRoute**:
```yaml
# pgadmin.local.narwhal.internal → harbor:80
# openid-connect 플러그인으로 Authentik SSO
```

**리소스 제약** (Worker 6GB):
```yaml
resources:
  requests: {memory: 256Mi, cpu: 100m}
  limits:   {memory: 512Mi, cpu: 500m}
```

---

### 5.5 CLAUDE.md 교훈 반영

**목적**: idp CORRECTIONS.md의 핵심 교훈 4개를 narwhal CLAUDE.md Mistakes Log에 추가

**추가할 교훈**:

| 날짜 | 실수 | 해결책 |
|------|------|--------|
| 2026-03-23 (idp) | K8s 1.35 kube-apiserver OIDC 설정 후 static pod 미반영 | 기존 mirror pod가 etcd에 캐시됨. `kubeadm upgrade apply` 또는 모든 master 동시 재부팅 |
| 2026-03-23 (idp) | Grafana SSO `login_attribute_path=preferred_username` → local admin 충돌 | `login_attribute_path=email` 사용 (Grafana 10+) |
| 2026-03-23 (idp) | Keycloak 26.0+ `KC_HOSTNAME` 호스트명만 → 리다이렉트 포트 불일치 | `KC_HOSTNAME=https://hostname` (전체 URL 형식), `KC_PROXY_HEADERS=xforwarded` |
| 2026-03-23 (idp) | volume에 직접 마운트 시 기존 디렉터리 내용 전체 덮어씀 | `subPath` 마운트 사용하여 기존 파일 보존 |

---

### 5.6 Velero UI 업그레이드

**목적**: 0.10.1 → 0.14.0 (idp와 동일 버전)

**파일**: `gitops/apps/velero-ui.yaml`

**변경 내용**:
```yaml
# Before:
targetRevision: "0.10.0"  # tag: 0.10.1

# After:
targetRevision: "0.14.0"
```

**주의**: Docker Hub tag는 `0.14.0` (v prefix 없음)

---

## 6. 구현 순서 (실행 계획)

```
Step 1: CLAUDE.md 교훈 반영     (즉시, 파일 수정)
Step 2: Velero UI 업그레이드    (즉시, YAML 수정 → ArgoCD sync)
Step 3: Blackbox Exporter 추가  (스크립트 + GitOps app)
Step 4: Alertmanager Telegram   (Telegram 봇 생성 후 스크립트)
Step 5: PgAdmin 추가            (스크립트 + Authentik provider)
Step 6: RBAC 검증 스크립트      (신규 스크립트 작성)
```

**의존성**:
- Step 3, 4: Prometheus-stack 정상 동작 필요 (현재 완료)
- Step 5: Authentik 정상 동작 필요 (현재 완료)
- Step 4: Telegram 봇 수동 생성 필요 (사용자 수동 작업)

---

## 7. 파일 변경 목록

### 신규 파일

| 파일 | 설명 |
|------|------|
| `scripts/cluster/08-7-blackbox.sh` | Blackbox Exporter 설치 스크립트 |
| `scripts/cluster/08-8-alertmanager-telegram.sh` | Alertmanager Telegram 설정 스크립트 |
| `scripts/cluster/08-9-pgadmin.sh` | PgAdmin 설치 스크립트 |
| `scripts/verify/verify-k8s-rbac.sh` | K8s RBAC 검증 스크립트 |
| `gitops/apps/blackbox-exporter.yaml` | ArgoCD Application |
| `gitops/resources/alertmanager-telegram.yaml` | AlertmanagerConfig + PrometheusRule |
| `gitops/resources/pgadmin-route.yaml` | ApisixRoute for PgAdmin |

### 수정 파일

| 파일 | 변경 내용 |
|------|---------|
| `CLAUDE.md` | Mistakes Log 교훈 4개 추가 |
| `gitops/apps/velero-ui.yaml` | targetRevision 0.10.0 → 0.14.0 |
| `scripts/cluster/08-3-security.sh` 또는 `11-2-authentik-config.sh` | PgAdmin Authentik OAuth2 provider 추가 |
| `scripts/cluster/06-phase2-start.sh` | 08-7, 08-8, 08-9 스크립트 호출 추가 |
| `VERSIONS.md` | Blackbox Exporter, PgAdmin, velero-ui 버전 추가 |

---

## 8. 검증 방법

```bash
# Blackbox Exporter
kubectl get pods -n monitoring -l app.kubernetes.io/name=prometheus-blackbox-exporter
curl -s http://$(kubectl get svc -n monitoring -l app=blackbox-exporter -o jsonpath='{.items[0].spec.clusterIP}')/probe?target=https://argocd.local.narwhal.internal/healthz&module=http_2xx

# Alertmanager Telegram
kubectl get alertmanagerconfig -n monitoring
kubectl get prometheusrule narwhal-alerts -n monitoring

# PgAdmin
curl -sk https://pgadmin.local.narwhal.internal/login | grep pgAdmin

# RBAC 검증
bash scripts/verify/verify-k8s-rbac.sh

# Velero UI
kubectl get pods -n storage -l app.kubernetes.io/name=velero-ui
```

---

## 9. 리스크

| 리스크 | 가능성 | 대응 |
|--------|--------|------|
| PgAdmin docker.io rate limit | 낮음 | 최초 pull 이후 내부 캐시, Harbor mirror 검토 |
| Alertmanager Telegram 봇 설정 오류 | 중간 | 먼저 curl로 봇 응답 테스트 |
| Worker 6GB OOM (PgAdmin + Blackbox 추가) | 낮음 | 리소스 limits 보수적으로 설정 |
| Velero UI 0.14.0 API 변경 | 낮음 | changelog 확인 후 적용 |

---

## 10. 범위 외 (Deferred)

| 항목 | 이유 |
|------|------|
| External Secrets Operator | 현재 수동 복사 충분, 향후 OpenBao 확장 시 재검토 |
| OpenBao Transit 자동 Unseal | Vagrant 환경 재부팅 드물어 우선순위 낮음 |
| Forecastle 포털 | Headlamp가 대체, Worker 메모리 여유 감안 |
| 도메인 네이밍 변경 | 전체 CLAUDE.md/docs 파급 크고 기능 개선 없음 |
| Chaos Mesh | Worker 6GB에서 리소스 부담 |
| k6, E2E, Termix | 현재 개발 단계 우선순위 낮음 |

---

## Brainstorming Log

| Phase | 핵심 결정 | 근거 |
|-------|---------|------|
| Phase 0 | idp는 vSphere 프로덕션, narwhal은 Vagrant 로컬 — 완전 이식보다 선택적 적용 | 플랫폼 차이 (CSI, DNS, 규모) |
| Phase 1 | 목표: 전체 갭 분석 + 모두 메우기 | 사용자 요청 |
| Phase 2 | Approach A 선택 (단계적) | 즉시 가시적 효과 + 리스크 최소화 |
| Phase 3 | ESO, OpenBao Transit 지연 | 복잡도 대비 현재 필요성 낮음 |
| Phase 4 | PgAdmin = docker.io 불가피, 사유 주석 필수 | 공식 이미지 외 대안 없음 |
