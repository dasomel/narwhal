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

## 아키텍처별 번들 (arm64 / amd64)

한 번들 = 한 아키텍처다(skopeo `--override-arch`가 멀티아치 매니페스트에서 단일
아치를 골라 저장). `AIRGAP_ARCH`로 선택하며, 번들 디렉토리는 아치 접미사가 붙어
서로 덮어쓰지 않는다:

| 대상 | `AIRGAP_ARCH` | 번들 디렉토리(기본) |
|------|---------------|---------------------|
| 로컬 Apple-Silicon Vagrant 클러스터 | `linux/arm64` (기본) | `narwhal-airgap-bundle-arm64` |
| **Kakao Cloud 등 x86_64** | `linux/amd64` | `narwhal-airgap-bundle-amd64` |

`images.txt`(이미지 목록)는 아키텍처와 무관하게 공용이다 — 같은 목록으로 두 아치
번들을 만든다. 저장되는 바이트(OCI 레이어)와 부트스트랩 `registry.tar`만 아치별로
다르다. **Kakao Cloud용 amd64 번들**은 Phase A를 `AIRGAP_ARCH=linux/amd64`로 한 번
더 돌리면 된다(아래 각 명령 앞에 `AIRGAP_ARCH=linux/amd64` prefix). amd64 변형이
없는 이미지가 있으면 `02-save-images.sh`가 그 이미지에서 실패하므로 조기에 드러난다.

## 워크플로우

### Phase A — 인터넷 가능 환경에서 번들 생성

```bash
# 1. 이미지 목록 생성 — 실행 중인 클러스터에서 실제 이미지 세트를 추출 (권장)
#    정적 소스 스캔(인자 없이 실행)은 Helm 차트 기본 이미지를 못 잡아 ~60개 누락되므로
#    반드시 --live 를 사용한다. images.txt 는 이미 이 방식으로 커밋되어 있다. (아치 공용)
./scripts/airgap/01-generate-image-list.sh --live scripts/airgap/images.txt
#    (정적 교차검증용:  ./scripts/airgap/01-generate-image-list.sh > /tmp/static.txt)

# 2. 모든 이미지를 로컬 tar 번들로 저장 (skopeo + oci layout)
#    --out 을 생략하면 AIRGAP_BUNDLE_DIR(아치 접미사 자동) 로 저장된다.
./scripts/airgap/02-save-images.sh --list scripts/airgap/images.txt
#    Kakao Cloud(amd64):
#    AIRGAP_ARCH=linux/amd64 ./scripts/airgap/02-save-images.sh --list scripts/airgap/images.txt

# 3. Helm chart 다운로드 (아치 공용 — .tgz 는 아치 무관)
./scripts/airgap/03-save-helm-charts.sh
#    AIRGAP_ARCH=linux/amd64 ./scripts/airgap/03-save-helm-charts.sh   # amd64 번들에도 채우려면

# 4. 바이너리 · OS 패키지
./scripts/airgap/07-save-binaries.sh
./scripts/airgap/07-save-apt-packages.sh

# 5. SBOM 생성 — 번들 조립이 끝난 뒤 마지막에 실행 (내용물을 읽어 목록을 만든다)
./scripts/airgap/08-generate-sbom.sh --bundle ./narwhal-airgap-bundle-amd64

# 6. 완전성 게이트 — release 아티팩트로 취급하기 전 반드시 통과해야 한다 (narwhal#51).
#    images.txt의 이미지 전부가 manifest.txt에 기록되고 oci/ 아래 실제 layout(index.json)이
#    있는지 1:1로 대조한다. 하나라도 빠지면 exit 1 — CI/release에서는 이 결과를 게이트로 써야 한다.
./scripts/airgap/09-verify-bundle-completeness.sh --bundle ./narwhal-airgap-bundle-amd64

# 결과: narwhal-airgap-bundle-<arch>/ 폴더를 대상 클러스터로 전송
```

**SBOM (`<bundle>/sbom.cdx.json`)**: CycloneDX 1.5 형식으로 번들 내용물 전체를 나열한다 —
컨테이너 이미지(OCI layout에서 읽은 digest), Helm 차트, .deb, 바이너리, 원격 매니페스트가
각각 sha256과 함께 들어간다. 폐쇄망 반입 시 "무엇을 설치했는가"에 기계적으로 답하기 위한
것이고, 규제 환경에서 요구되는 자료가 대개 이것이다.

두 가지를 알고 써야 한다. **번들 수준 목록이지 이미지 내부 패키지 SBOM이 아니다** —
"apisix 안의 libc 버전" 같은 질문에 답하려면 `oci/` 아래 레이아웃을 대상으로 syft를 돌려
중첩 BOM으로 붙여야 한다. 그리고 **타임스탬프를 넣지 않아 재현 가능하다** — 같은 번들이면
몇 번을 생성해도 파일이 동일하므로, diff가 나면 번들이 바뀐 것이다.

**번들 전송 (Kakao Cloud):** amd64 번들을 `scp -r` 로 master-1 에 복사한 뒤 그 노드에서
Phase B/C 를 실행한다. 노드는 프라이빗 서브넷이므로 bastion 을 ProxyJump 로 경유한다
(`tofu output bastion_ssh` 가 명령을 그대로 뱉는다). 번들은 약 5.7G 라 전송이 길다.

```bash
cd csp/kakao-cloud/terraform
BASTION=$(tofu output -raw bastion_public_ip)
MASTER1=$(tofu output -json master_private_ips | jq -r '.[0]')
cd -

# 전송 전 무결성 기준값 (전송 후 대상에서 같은 명령으로 대조)
find narwhal-airgap-bundle-amd64 -type f -not -name .DS_Store | sort | \
  xargs shasum -a 256 | shasum -a 256

scp -r -o ProxyJump=ubuntu@"${BASTION}" \
  narwhal-airgap-bundle-amd64 ubuntu@"${MASTER1}":~/
```

### Phase B — 에어갭 내부에서 레지스트리 준비

Kakao Cloud 에서는 번들을 복사해 둔 **master-1 에서 실행**한다(bastion 경유 SSH).

```bash
# 4. 임시 부트스트랩 레지스트리 (Harbor 설치 전에 필요한 이미지 호스팅용)
./scripts/airgap/04-bootstrap-registry.sh --addr registry.airgap.local:5000

# 5. 번들 이미지를 레지스트리로 로드
#    --bundle 은 아치 접미사가 붙은 실제 디렉토리를 가리켜야 한다(생략하면
#    AIRGAP_BUNDLE_DIR 기본값이 AIRGAP_ARCH 를 따라간다).
./scripts/airgap/05-load-images.sh --bundle ./narwhal-airgap-bundle-amd64 --registry registry.airgap.local:5000
```

### Phase C — 에어갭 내부에서 Narwhal 클러스터 부팅

```bash
# 6. 모든 노드의 containerd에 mirror 설정 적용
AIRGAP_REGISTRY=registry.airgap.local:5000 ./scripts/airgap/06-configure-mirrors.sh

# 7. 일반 Narwhal 부팅 (VERSIONS 변경 없이 containerd 레벨에서 재작성)
export AIRGAP_REGISTRY=registry.airgap.local:5000

#    로컬 Vagrant:
vagrant up --provider=vmware_desktop

#    Kakao Cloud: vagrant 가 없으므로 각 노드에서 프로비저닝 스크립트를 직접 돌린다.
#    PROVIDER=kakao 가 kube-vip / MetalLB / dnsmasq 등 Vagrant 전용 경로를 건너뛰고
#    Kakao LB + 클라우드 리졸버를 쓰게 만든다 (scripts/up.sh 는 Vagrant 전용이라 사용하지 않음).
export PROVIDER=kakao
sudo -E ./scripts/common/01-prerequisites.sh     # 전 노드
sudo -E ./scripts/cluster/02-init-cluster.sh     # master-1
#    이후 join/Phase 2 순서는 파일명 접두사 순서를 따른다 (CLAUDE.md "Core Flows").
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
- **Multi-arch**: 한 번들 = 한 아치다. `AIRGAP_ARCH` 로 고르며(위 per-arch 섹션 참고) 로컬 Vagrant 는 `linux/arm64`, Kakao Cloud 는 `linux/amd64`. 기본값만 `00-config.sh` 에 있다.
- **Helm 차트 의존성**: `helm pull --untar`로 서브차트까지 포함해 번들링.
- **Bitnami 금지**: Bitnami 이미지는 번들링 대상에서 제외 (프로젝트 정책, CLAUDE.md 참고).
