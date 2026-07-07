# Narwhal Airgap (폐쇄망) 설치 가이드

Rancher airgap 방식을 Narwhal에 적용한 버전입니다. 외부 인터넷이 차단된 환경에서 프라이빗 레지스트리를 이용해 클러스터를 구축합니다.

## 아키텍처

```
[인터넷 가능 환경]                  [에어갭 내부]
┌─────────────────┐                ┌──────────────────────────┐
│ collect +       │                │ Private Registry         │
│ save images     │ ─── USB/NAS ──▶│ (Harbor / registry:2)    │
│ → tar bundle    │                │         ▲                │
└─────────────────┘                │         │ pull           │
                                   │  ┌──────┴─────┐          │
                                   │  │ Narwhal    │          │
                                   │  │ K8s Cluster│          │
                                   │  └────────────┘          │
                                   └──────────────────────────┘
```

## 워크플로우

### Phase A — 인터넷 가능 환경에서 번들 생성

```bash
# 1. 이미지 목록 생성 — 실행 중인 클러스터에서 실제 이미지 세트를 추출 (권장)
#    정적 소스 스캔(인자 없이 실행)은 Helm 차트 기본 이미지를 못 잡아 ~60개 누락되므로
#    반드시 --live 를 사용한다. images.txt 는 이미 이 방식으로 커밋되어 있다.
./scripts/airgap/01-generate-image-list.sh --live scripts/airgap/images.txt
#    (정적 교차검증용:  ./scripts/airgap/01-generate-image-list.sh > /tmp/static.txt)

# 2. 모든 이미지를 로컬 tar 번들로 저장 (skopeo + oci layout)
./scripts/airgap/02-save-images.sh --list images.txt --out ./narwhal-airgap-bundle

# 3. Helm chart 다운로드
./scripts/airgap/03-save-helm-charts.sh --out ./narwhal-airgap-bundle/charts

# 결과: narwhal-airgap-bundle/ 폴더를 USB/NAS로 전송
```

### Phase B — 에어갭 내부에서 레지스트리 준비

```bash
# 4. 임시 부트스트랩 레지스트리 (Harbor 설치 전에 필요한 이미지 호스팅용)
./scripts/airgap/04-bootstrap-registry.sh --addr registry.airgap.local:5000

# 5. 번들 이미지를 레지스트리로 로드
./scripts/airgap/05-load-images.sh --bundle ./narwhal-airgap-bundle --registry registry.airgap.local:5000
```

### Phase C — 에어갭 내부에서 Narwhal 클러스터 부팅

```bash
# 6. 모든 노드의 containerd에 mirror 설정 적용
AIRGAP_REGISTRY=registry.airgap.local:5000 ./scripts/airgap/06-configure-mirrors.sh

# 7. 일반 Narwhal 부팅 (VERSIONS 변경 없이 containerd 레벨에서 재작성)
export AIRGAP_REGISTRY=registry.airgap.local:5000
vagrant up --provider=vmware_desktop
```

## 구성 요소

| 스크립트 | 역할 |
|---------|------|
| `00-config.sh` | 공통 설정 (registry URL, 아키텍처) |
| `01-generate-image-list.sh` | `--live`: 실행 클러스터에서 실제 이미지 세트 추출(권장, 완전). 인자 없음: gitops/스크립트 정적 스캔(불완전, 교차검증용) |
| `02-save-images.sh` | skopeo copy → OCI layout tar |
| `03-save-helm-charts.sh` | Helm chart `.tgz` 번들링 |
| `04-bootstrap-registry.sh` | 부트스트랩 registry:2 배포 (Harbor 설치 전 사용) |
| `05-load-images.sh` | 번들 → 레지스트리 push |
| `06-configure-mirrors.sh` | containerd `hosts.toml` 미러 설정 |

## 이미지 소스 (14개 레지스트리 → 1개 미러)

외부 레지스트리 → 미러 매핑:
- `registry.k8s.io/*` → `<MIRROR>/registry.k8s.io/*`
- `quay.io/*` → `<MIRROR>/quay.io/*`
- `ghcr.io/*` → `<MIRROR>/ghcr.io/*`
- `docker.io/*` → `<MIRROR>/docker.io/*`

containerd `config_path = "/etc/containerd/certs.d"`를 이용하므로 이미지 참조는 **변경하지 않아도** 자동으로 미러로 라우팅됩니다.

## 주의사항

- **Private CA**: Harbor/레지스트리가 자체 서명 인증서를 사용하면 `hosts.toml`에 `skip_verify = true` 추가 또는 CA를 `/usr/local/share/ca-certificates/`에 설치 (`08-6-tls-routes.sh`의 DaemonSet 패턴 참고).
- **Multi-arch**: skopeo는 `--all` 플래그로 multi-arch manifest 처리. Narwhal은 현재 ARM64 기준, 다른 아키텍처 필요 시 `00-config.sh`에서 설정.
- **Helm 차트 의존성**: `helm pull --untar`로 서브차트까지 포함해 번들링.
- **Bitnami 금지**: Bitnami 이미지는 번들링 대상에서 제외 (프로젝트 정책, CLAUDE.md 참고).
