# Kubernetes 및 OSS 호환성 행렬과 지원 정책

이 문서는 저장소에 선언된 버전 조합과 검증 증거를 구분한다. 설치 pin은 실제 배포 입력의 근거이고, 그 자체로 호환성 시험이나 upstream 지원 보증을 뜻하지 않는다. 현재 release 식별자별 검증 결과를 저장하는 자동화된 matrix는 없다.

## 상태 정의

| 상태 | 판정 기준 |
|---|---|
| `pinned` | 설치 스크립트 또는 GitOps manifest가 버전을 고정한다. 호환/지원 판정은 아니다. |
| `declared-compatible` | `VERSIONS.md`가 호환 범위를 선언했으나 이 문서에서 조합별 시험 증거를 확인하지 못했다. |
| `tested-static` | 저장소 정적 검사에서 해당 pin 및 manifest 조건이 통과했다. 런타임 호환 증거는 아니다. |
| `tested-runtime` | 지정된 Narwhal release, 아키텍처, 클러스터에서 시나리오를 실제 실행하고 결과를 기록했다. 현재 이 문서 작성 근거에서는 해당 조합 기록을 확인하지 못했다. |
| `deprecated` / `unsupported` | 각각 제거 계획이 승인된 상태 / 정책상 배포를 거부하는 상태. 판정 근거와 유효일이 필요하다. 현재 machine-readable 상태 기록은 없다. |

현재 버전 표의 `pinned`는 지원 상태 `supported`와 동의어가 아니다. EOL/EOS 날짜는 upstream 및 제품별 수명주기 근거로 확인해야 한다. 확인하지 못한 날짜는 `unknown`으로 둔다.

## 현재 저장소 pin 인벤토리

버전은 [VERSIONS.md](../../VERSIONS.md)의 선언과 설치 소스가 함께 확인되는 값을 기재했다. `Direct source`는 실제 적용 위치를 특정하며, 추가 세부 pin은 해당 파일과 `VERSIONS.md`를 확인한다.

| 계층 / 컴포넌트 | 현재 선언 pin | Direct source / 비고 | 상태 |
|---|---|---|---|
| Kubernetes | `v1.35.7`; kubeadm/kubelet/kubectl도 `K8S_PATCH_VERSION`(`v1.35.7`)으로 설치됨 (`VERSIONS.md`의 kubeadm/kubelet `v1.35.5` 표기는 실제 pin과 어긋난 문서 드리프트) | `Vagrantfile`의 `K8S_PATCH_VERSION`, `scripts/common/03-k8s-install.sh`, `scripts/cluster/02-init-cluster.sh`. control plane과 node 도구 patch가 서로 다르게 선언됨 | pinned; skew 별도 gate 필요 |
| OS | Ubuntu `26.04 LTS`; box `dasomel/ubuntu-26.04-xfs v0.1.0` | `VERSIONS.md`; Vagrant box 기준. Kakao 노드 OS가 이 값과 같다는 보장은 없음 | pinned (Vagrant) |
| containerd | Ubuntu 24.04는 `1.7.x` 시도, 26.04는 distro default `2.x` | `scripts/common/02-containerd.sh`는 `CONTAINERD_VERSION` 미설정 시 저장소 기본값을 설치하고 실제 설치 버전을 출력. 고정 patch가 아님 | unpinned runtime version |
| CNI | Cilium `v1.19.4`; CLI `v0.19.4`; Hubble `v1.19.4` | `scripts/cluster/03-cni-install.sh`; `CNI_PLUGIN`은 기본 `cilium`, 스크립트에는 Calico 대체 pin도 존재 | pinned (기본 경로) |
| APISIX / ingress | APISIX app `3.15.0`, chart `2.13.0`; Ingress Controller `1.8.0` | GitOps `apisix.yaml`, ingress controller 세부는 `VERSIONS.md` 및 관련 manifests | pinned; controller 2.x는 major break 사유로 frozen |
| Istio ambient | Istio, `istio-cni`, `ztunnel` `v1.30.1` | `gitops/charts/narwhal-apps/templates/{istiod,istio-cni,ztunnel}.yaml`; 순서: istiod → istio-cni → ztunnel | pinned |
| CSI / storage | NFS CSI chart `4.13.2`; SeaweedFS chart `4.34.0` / app `v4.34` | `VERSIONS.md`; SeaweedFS GitOps `seaweedfs.yaml` | pinned |
| GitOps | Argo CD `v3.4.4`; Gitea `v1.26.2` / chart `12.6.0` | `scripts/cluster/13-argocd.sh`, `scripts/cluster/12-gitea.sh` | pinned |
| Identity / secrets | Keycloak 및 Operator `26.5.7`; OpenBao chart `0.28.3` / app `v2.5.4` | `scripts/cluster/11-keycloak.sh`, GitOps `openbao.yaml`, `VERSIONS.md` | pinned |
| Registry | Harbor `v2.15.1`, chart `1.19.1` | `gitops/charts/narwhal-apps/templates/harbor.yaml`; 6 versioned images 고정, exporter 비활성 상태는 `VERSIONS.md` 설명 참조 | pinned |
| Observability | kube-prometheus-stack chart `86.2.3`; Loki app `v3.7.3` / chart `18.4.0`; Tempo app `v2.9.0` / chart `2.2.3`; Alloy chart `4.2.0` | 각 GitOps 템플릿 및 `VERSIONS.md`. Tempo chart 기본 app보다 `2.9.0`을 명시적으로 유지 | pinned; Tempo 상향에는 block audit 전제 |
| Backup | Velero app `v1.18.1` / chart `12.0.3`; AWS plugin `v1.14.1` | `gitops/charts/narwhal-apps/templates/velero.yaml`, `scripts/cluster/08-4-storage.sh` | pinned |
| Portal | Narwhal Portal `1.0.19`; Next.js `16.2.1` | `gitops/charts/narwhal-platform/templates/narwhal-portal-k8s.yaml`, `VERSIONS.md` | pinned |
| 기타 주요 add-ons | cert-manager chart `v1.20.2`; CNPG `v1.29.1` / chart `0.28.3`; Kyverno `v1.18.1` / chart `3.8.1`; MetalLB chart `v0.16.1`; metrics-server `v0.8.1` / chart `3.13.1`; Headlamp `v0.42.0` | `gitops/charts/narwhal-apps/templates/` 및 `scripts/cluster/07-cnpg.sh`, `VERSIONS.md` | pinned |

전체 세부 버전과 예외 사유는 [VERSIONS.md](../../VERSIONS.md)가 우선 참조다. 거기에 있는 compatibility 표는 아래 선언 범위로만 취급하며 tested 조합 표로 읽지 않는다.

## 선언된 Kubernetes 호환 범위와 skew

`VERSIONS.md`의 현재 compatibility 표는 다음 범위를 선언한다.

| Kubernetes minor | Cilium | csi-driver-nfs | Istio |
|---|---|---|---|
| `v1.35` | `v1.19+` | `v4.13+` | `v1.29–v1.30` |
| `v1.34` | `v1.17+` | `v4.11+` | `v1.28–v1.30` |
| `v1.33` | `v1.16+` | `v4.10+` | `v1.27–v1.29` |

이 표는 runtime 결과, release별 지원 기간, 다른 component의 호환성을 담지 않는다. 1.35 row의 최소 버전 형식은 실제 pin `Cilium 1.19.4`, CSI `4.13.2`, Istio `1.30.1`과 일치하지만, 이를 조합 통합시험 증거로 승격하지 않는다.

Kubernetes skew 판정은 각 upgrade 대상 버전의 upstream 공식 정책을 기준으로 해야 한다. 저장소에서 확인되는 사실은 `scripts/common/03-k8s-install.sh`가 kubeadm·kubelet·kubectl을 모두 `K8S_PATCH_VERSION`(`1.35.7`)으로 설치하고 `scripts/cluster/02-init-cluster.sh`도 `v1.35.7`을 기록한다는 것이며, 따라서 설치 pin 사이 skew는 0이다. `VERSIONS.md`의 kubeadm/kubelet `1.35.5`는 실제 pin과 다른 문서 드리프트이므로 별도로 정정해야 한다 [PROPOSED]. **PROPOSED:** 사전 검사기는 control-plane, kubeadm, kubelet, kubectl의 minor/patch 관계를 지원 upstream skew 규칙과 비교하고, 정책 근거 버전이 없거나 차이가 허용 범위를 벗어나면 `BLOCKED`로 종료한다. 이 문서는 upstream 허용 범위를 자체적으로 재정의하지 않는다.

## 지원 정책과 upgrade 순서

1. Narwhal release의 구성은 Git commit과 적용 대상(ARM64 Vagrant 또는 AMD64 Kakao)을 함께 식별한다. commit만으로 live runtime 상태를 추정하지 않는다.
2. 지원 후보 조합은 모든 직접 pin, chart의 실제 app image override, 아키텍처, OS 및 runtime 버전을 기록한다. 확인할 수 없는 버전은 추정하지 않고 `unknown` 처리한다.
3. 다음 순서로 호환성을 판단한다: Kubernetes/control plane → CNI → CSI/storage 및 SeaweedFS → cert-manager → APISIX/Istio → Argo CD/GitOps → Keycloak/OpenBao → Harbor → observability → Velero → Portal/add-ons. 이는 [upgrade-orchestration.md](upgrade-orchestration.md)의 dependency wave를 요약한 순서이며 각 컴포넌트의 실행 절차를 대체하지 않는다.
4. 먼저 `declared-compatible`과 skew gate를 통과하고, 다음 static manifest/chart 및 offline artifact 검사, 이후 target architecture의 runtime health 및 smoke evidence를 기록한다. 미통과/미실행 상태는 지원 조합으로 표시하지 않는다.
5. 한 wave가 실패하거나 증거가 불완전하면 다음 wave 진입을 차단한다. Kubernetes upgrade는 provider/node drain 계획과 별도 승인된 작업이 필요하다. `docs/common/upgrade-orchestration.md`의 cert-manager pilot은 정적 GitOps pilot이며 live upgrade 실행이나 자동 rollback을 증명하지 않는다.

**PROPOSED 지원 등급:** `supported`는 해당 release/아키텍처 조합의 필수 static+runtime acceptance가 통과하고 유효한 upstream support 기간 내일 때만 부여한다. `deprecated`는 종료일과 후속 버전/기한을 기록하고 신규 배포 금지 여부를 명시한다. `unsupported`는 호환 gate 실패, EOL 정책 만료 또는 필수 artifact/evidence 부재 중 하나가 있으면 부여한다. `unknown`은 데이터 부재이지 지원 승인이 아니다.

## 오프라인 검증 경로

현재 가능한 offline 검사는 고정된 repository 입력의 정합성 및 번들 내부 완전성 검사다. 외부 registry나 upstream EOL 확인이 필요한 단계는 offline 증거가 아니다.

| 목적 | 명령/입력 | 판정 범위와 제한 |
|---|---|---|
| 대표 버전 동기화 | `scripts/ci/check-version-consistency.sh` | `VERSIONS.md`와 Kubernetes, Cilium/CLI, Argo CD, Keycloak, APISIX chart pin 중 일부를 비교한다. 전체 component matrix 검사가 아님 |
| air-gap image 목록과 번들 | `scripts/airgap/09-verify-bundle-completeness.sh --bundle <bundle-dir>` | images.txt, manifest, OCI layout, `image-digests.tsv` 대응 및 실제 번들 파일을 검사한다. source image가 올바른 Kubernetes 조합인지 판정하지 않음 |
| chart 참조/소스 검사 | `scripts/airgap/lib/check-required-charts.py`, `scripts/airgap/lib/check-chart-upstream-sources.py` | 설치 참조의 번들 chart/version 커버리지와 upstream source mapping을 검사한다. chart 실행 성공/호환성은 보장하지 않음 |
| manifest 정적 회귀 | `scripts/test/regression-check-kakao.sh --static` | repo 전용 정적 회귀 검사. `--runtime` 없는 실행은 클러스터 health 증거가 아님 |
| upgrade 순서/정적 pilot | `scripts/cluster/preflight-cert-manager-upgrade.sh` | cert-manager GitOps shape만 확인한다. 다른 컴포넌트 upgrade 승인 게이트가 아님 |

**PROPOSED offline matrix gate:** release manifest와 air-gap bundle을 입력으로 받아 모든 component의 기대 chart/image/binary pin을 실제 local artifact metadata/digest와 비교한다. 외부 접속 금지를 만족하는 격리 실행에서 `PASS`를 내고, 누락/중복/미정 pin, 지원 범위 미기재, digest 불일치, 필수 정적 회귀 실패에는 0이 아닌 종료 코드를 반환해야 한다. 출력에는 release id, git commit, architecture, 각 component의 expected/resolved version 및 digest, 검사 이름/결과, 실패 사유를 기록한다. 현재 저장소에는 이 통합 checker나 release별 evidence schema가 없다.

## PROPOSED machine-readable record

다음은 구현 완료 스키마가 아니라 향후 checker 및 #45/#46/#47 연결을 위한 최소 제안이다. 새 endpoint, env var, Kubernetes namespace를 정의하지 않는다.

```yaml
apiVersion: narwhal.io/compatibility/v1alpha1
kind: CompatibilityRecord
release:
  id: "<PROPOSED release identifier>"
  gitCommit: "<40-hex commit>"
  architecture: [arm64, amd64]
components:
  - name: kubernetes
    version: "1.35.7"
    status: pinned
    support: unknown
    constraints: []
    artifacts: []
    evidence: []
    eol: null
    upgrade: {from: [], recommendation: unknown}
```

필수 field 의미: `name/version`은 실제 구성 식별, `status`는 위 판정 상태, `support`는 `supported|deprecated|unsupported|unknown`, `constraints`는 적용 대상/의존성/skew, `artifacts`는 bundle의 로컬 경로와 sha256, `evidence`는 검사 id·결과·환경·시각·commit, `eol`은 출처 URL 대신 오프라인에 보존한 source id와 날짜, `upgrade`는 허용 source 버전과 권고 target/주의사항이다. 값 미상은 생략하지 말고 `unknown`/`null`로 기록해야 누락이 통과로 처리되지 않는다. 실 schema validation, JSON Schema 형식, #45 bundle metadata 상호 참조, #46 prerequisite gate, #47 dependency graph 연동은 모두 **PROPOSED / 미구현**이다.

## EOL/EOS 데이터와 미해결 범위

현재 확인한 저장소 기록 중 Kubernetes `v1.35` 지원 종료일 `2027-02-28` 선언은 [VERSIONS.md](../../VERSIONS.md)에 있다. 이 날짜는 문서에 기록된 값이며, 본 작업에서는 offline upstream 근거로 재검증하지 않았다. 나머지 OSS, Ubuntu, containerd, Portal/Next.js 및 add-on의 EOL/EOS 일괄 inventory는 없다. 따라서 이 문서에서 나머지 날짜는 모두 `unknown`이며 신규 날짜를 추정하지 않는다.

**PROPOSED:** 매 release마다 component별 EOL/EOS 날짜, upstream 근거 수집일, 교체/업그레이드 권고와 마지막 허용 release를 저장한다. 날짜가 지나거나 근거가 stale한 경우 release support를 재평가하고, 권고는 data migration/API/CRD 변경 및 frozen component 조건을 검토한 뒤 정한다. 특히 APISIX Ingress Controller 2.x, Velero UI, Tempo 2.10.x 전환 조건 등 `VERSIONS.md`의 freeze/hold 사유는 해제 증거가 나오기 전 유지한다.

## 현재 충족되지 않은 issue 항목

- Release별 `tested` 조합 및 component별 지원 상태를 생성/보존하는 source of truth.
- 전체 대상(OS/runtime/CNI/CSI/ingress/GitOps/identity/registry/observability/backup/Portal/add-ons)의 자동 호환 checker.
- 전체 구성요소 EOL/EOS inventory와 정기 갱신 근거.
- air-gap bundle, upgrade prerequisite gate, impact analyzer 및 compatibility 회귀 결과 간 자동 연결.
- 멀티 아키텍처 조합별 live integration 증거. 기존 문서의 bundle completeness 및 static check 결과는 이를 대신하지 않는다.
