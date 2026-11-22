# OpenBao TLS 갱신과 listener reload (#766)

## 근거와 선택

- **관측(오케스트레이터, 2026-11-22):** Secret은 revision 4(만료 2027-02-20, serial 5CCB…)인데 listener는 이전 인증서(2027-02-15, 44E5…)였다. 이 sandbox에서 재측정하지 않았다.
- **관측(저장소):** `scripts/cluster/08-4-storage.sh`만 `Certificate/storage/openbao-tls`를 만든다. GitOps는 이 Secret을 마운트하지만 Certificate를 생성하지 않는다. 소유권을 이전하거나 GitOps 사본을 추가하지 않았다. 따라서 현재 저장소 정의끼리 ArgoCD 소유권 충돌은 없다. 라이브 tracking annotation은 아래에서 별도로 확인한다.
- **결정 D-BAO-VALIDITY:** 다른 플랫폼 leaf와 같은 `duration: 8760h`, `renewBefore: 720h`(365일, 30일 전 갱신)를 명시한다. cert-manager 기본 90일이 89일 표시의 원인이다. 기존 Certificate의 duration 변경은 한 번 재발급을 유발한다([cert-manager 문서](https://cert-manager.io/docs/usage/certificate/#reissuance-triggered-by-user-actions)); 실제 notAfter는 발급 결과로 확인한다.
- **관측(버전 고정 upstream):** [2.5.4 TCP 문서](https://github.com/openbao/openbao/blob/v2.5.4/website/content/docs/configuration/listener/tcp.mdx)는 시작 시 설정한 cert/key 경로를 SIGHUP으로 다시 읽는다고 명시한다. [2.5.4 server 소스](https://github.com/openbao/openbao/blob/v2.5.4/command/server.go)의 SIGHUP 분기는 listener reload와 audit/plugin/config reload를 수행하며 shutdown 분기와 분리돼 있다. [system API 소스](https://github.com/openbao/openbao/blob/v2.5.4/vault/logical_system.go)의 `handleConfigReload`는 `logical.ErrUnsupportedPath`를 반환한다. `/sys/config/reload/{subsystem}` 경로 자체는 [등록돼 있지만](https://github.com/openbao/openbao/blob/v2.5.4/vault/logical_system_paths.go) listener TLS reload API로 쓸 수 없다. [자동 TLS polling은 2.7 기능](https://openbao.org/docs/release-notes/2-7-0/)이므로 2.5.4 설정에 넣지 않는다.
- **관측(버전 고정 chart):** [0.28.3 values](https://github.com/openbao/openbao-helm/blob/openbao-0.28.3/charts/openbao/values.yaml)와 [StatefulSet template](https://github.com/openbao/openbao-helm/blob/openbao-0.28.3/charts/openbao/templates/server-statefulset.yaml)는 `server.extraContainers`, `shareProcessNamespace`, `updateStrategyType`를 지원한다. 인증서 갱신 annotation만으로 hot reload하는 내장 기능은 없다.

| 대안 | 판단과 비용 |
| --- | --- |
| D-BAO-HUP: mounted/served SHA256 지문 비교 sidecar | 채택. 30초마다 localhost handshake, mismatch 때만 같은 UID의 `bao server` PID에 HUP. 재로딩 실패도 다음 비교에서 다시 검출한다. Secret object 이벤트보다 늦은 kubelet volume 교체를 직접 관측한다. CronJob exec 방식은 pods/exec 권한이 필요하므로 배제한다. |
| cert-manager 이벤트 → rollout restart | 배제. 현재 Shamir이고 restart는 seal을 일으킨다. `openbao-auto-unseal`은 `*/2 * * * *`라 최대 약 2분+스케줄링 지연 동안 portal, injector, snapshot, unseal HTTPS 경로가 영향을 받는다. Velero는 raft 볼륨 fs-backup 대신 snapshot을 보호하며 관련 hook은 추가하지 않는다. |
| 변경과 무관한 주기적 SIGHUP | 배제. 소스상 seal/raft 재시작/lease revoke는 아니지만 audit 파일 재열기, audit/plugin/config reload까지 수행하므로 완전한 no-op이 아니다. 불필요한 audit I/O·reload 실패 기회를 만들지 않는다. |

보안 비용: Pod 내부 PID 공유로 sidecar가 같은 UID 프로세스에 신호를 보낼 수 있다. hostPID·root·capability·API RBAC를 추가하지 않는다. 기존 snapshot의 digest 고정 Valkey Alpine 이미지(OpenSSL/BusyBox, airgap amd64/arm64 목록에 이미 포함)를 재사용하며 entrypoint를 shell로 대체한다. 서버와 같은 uid 100(낮은 UID의 기존 잔여 위험), non-root, ALL drop, no privilege escalation, RuntimeDefault, read-only rootfs다. sidecar의 명시적 Secret projection은 `tls.crt` directory mount뿐이며 key·unseal material을 추가하지 않는다. Pod의 기존 ServiceAccount 자동마운트 설정은 유지하지만 reloader는 API token을 읽거나 API를 호출하지 않으며 별도 ServiceAccount나 권한도 없다. 공유 PID의 `/proc`가 같은 Pod의 프로세스 환경을 노출할 수 있다는 비용은 남는다. 저장소 Kyverno host namespace 금지와 PSA restricted 규칙에 어긋나는 필드는 추가하지 않으며, **실제 admission·signal·이미지 실행은 미검증**이다.

D-BAO-WINDOW: `scripts/test/verify-openbao-tls.sh <kubeconfig>`는 Secret의 **공개 tls.crt만** 읽고 sidecar에서 localhost listener를 조회한다. 30초 간격으로 최대 180초(kubelet 약 2분 + poll 30초 + 여유)까지 비교 후 불일치면 FAIL, 연결/파싱/API 실패도 FAIL이다. 관측 시간을 Secret 생성 시각으로 오인하지 않는다. 전역 context는 사용하지 않는다. 기존 metric에는 served-vs-Secret 지문 비교가 없어 경보/exporter를 만들지 않았다. 운영 주기 점검에 이 명령을 추가해야 하며, 이 변경만으로 상시 알림이 생기지는 않는다.

## 오케스트레이터 실행 순서 (이 sandbox에서 미실행)

공유 클러스터 변경은 소유자 승인 범위에서 실행한다. 아래 wrapper는 모든 명령에 지정 kubeconfig를 전달한다.

```bash
KC=/private/tmp/claude-501/-Users-m-Documents-IdeaProjects-20-dasomel-narwhal/cb748b7d-4376-47ef-a504-41984680279b/scratchpad/kc-narwhal
k() { kubectl --kubeconfig "$KC" "$@"; }
k -n storage get certificate openbao-tls -o 'jsonpath={.metadata.annotations.argocd\.argoproj\.io/tracking-id}{"\n"}{.status.notAfter}{"\n"}'
```

1. **실행 중인 현재 Pod 즉시 복구, restart 없음.** 먼저 Secret과 mounted 파일 지문이 같은지 확인한다. 다르면 kubelet projection을 기다리고 재확인한다. 개인키는 읽거나 출력하지 않는다.

```bash
k -n storage get secret openbao-tls -o 'jsonpath={.data.tls\.crt}' | openssl base64 -d -A | openssl x509 -noout -fingerprint -sha256 -serial -enddate
k -n storage exec openbao-0 -c openbao -- cat /openbao/tls/tls.crt | openssl x509 -noout -fingerprint -sha256 -serial -enddate
k -n storage exec openbao-0 -c openbao -- /bin/sh -ec '
  found=false
  for proc in /proc/[0-9]*; do
    [ "$(cat "$proc/comm" 2>/dev/null)" = bao ] || continue
    [ "$(tr "\000" "\n" < "$proc/cmdline" | sed -n "2p")" = server ] || continue
    kill -HUP "${proc##*/}"
    found=true
  done
  [ "$found" = true ]'
k -n storage exec openbao-0 -c openbao -- bao status -tls-skip-verify
k -n storage get pod openbao-0 -o 'jsonpath={.metadata.uid}{"\n"}{.status.containerStatuses[*].restartCount}{"\n"}'
# 별도 터미널에서 유지한다(ready endpoint에 의존하지 않는 Pod 직접 연결).
k -n storage port-forward pod/openbao-0 18200:8200
# 원래 터미널에서 serial == Secret serial 및 notAfter 확인 후 port-forward 종료.
openssl s_client -connect 127.0.0.1:18200 -servername openbao.storage.svc </dev/null 2>/dev/null | openssl x509 -noout -serial -enddate
```

`bao status`의 `Sealed false`와 Pod UID/restartCount 불변도 확인한다. skip-verify는 기존 복구 CLI 방식이며 지문 비교가 신뢰 기준이다. TLS handshake는 새 연결로 관측하며 이미 연결된 클라이언트 세션은 계속 동작할 수 있다.

2. **명시적 유효기간 적용.** bootstrap 전체를 재실행하지 않고 기존 bootstrap 소유 리소스만 patch한다. 재발급된 새 Secret을 기다리고 1번 mounted 일치 확인과 HUP·served serial 비교를 반복한다.

```bash
k -n storage patch certificate openbao-tls --type=merge -p '{"spec":{"duration":"8760h","renewBefore":"720h"}}'
k -n storage get certificate openbao-tls -w
# revision 증가와 발급 완료 후 Ctrl-C. 이전 Ready=True만으로 새 발급 완료라고 판단하지 않는다.
```

3. **영구 sidecar 배포.** 기존 GitOps 배포 경로(`scripts/gitops/push-to-gitea.sh`, 저장소 루트 직접 push 금지)로 이 커밋을 배포한다. `OnDelete`를 유지하므로 sync만으로 현재 Pod가 재시작하지 않는다. 실제 StatefulSet의 공유 PID, cert-only volume, uid, read-only rootfs를 확인한다. **sidecar 최초 설치에는 계획된 일회성 Pod 교체가 필요하며 이때는 seal된다.** 승인된 유지보수 창에서 아래 순서로 실행하고 auto-unseal을 확인한다. 이후 인증서 갱신에는 Pod 교체가 필요 없다.

```bash
k -n storage get statefulset openbao -o yaml
k -n storage delete pod openbao-0
# Running 상태를 확인한다. sealed이면 Ready 대기는 완료되지 않는다.
k -n storage get pod openbao-0 -w
# 새 터미널에서 Running 확인 후 한 번 실행(동일 이름 Job이 있으면 새 이름 사용).
k -n storage create job --from=cronjob/openbao-auto-unseal openbao-tls-first-unseal
k -n storage wait --for=condition=complete job/openbao-tls-first-unseal --timeout=180s
k -n storage wait --for=condition=Ready pod/openbao-0 --timeout=180s
scripts/test/verify-openbao-tls.sh "$KC"
k -n storage exec openbao-0 -c tls-reloader -- /bin/sh -ec 'timeout 10 openssl s_client -connect 127.0.0.1:8200 </dev/null 2>/dev/null | openssl x509 -noout -serial -enddate'
k -n storage logs openbao-0 -c tls-reloader --tail=20
```

4. **실제 갱신 검증(승인된 경우).** `cmctl --kubeconfig "$KC" renew -n storage openbao-tls` 후 revision/Secret 교체를 확인하고 verifier를 실행한다. mounted 지문·served serial 일치, Sealed=false, Pod UID/restartCount 불변을 확인해야 hot reload 증명이 된다. portal 접근·injector·snapshot 다음 성공도 확인한다. 이 작업자는 라이브 실행하지 않았다.

5. **롤백.** 공유 namespace PID 1은 pause이므로 신호 대상은 항상 프로세스 인자로 식별한다. Git에서 두 값 사본의 `extraContainers`/`shareProcessNamespace`/cert-only volume을 되돌리고 sync한다. OnDelete라 현재 sidecar는 남으므로 긴급 중지는 sidecar의 shell PID를 `/proc`에서 확인해 그 PID에만 STOP을 보내고, maintenance 교체+위 auto-unseal로 제거한다. 임시 중지 해제는 같은 PID에 CONT다. 수동 SIGHUP 경로를 유지한다. 기간 롤백이 필요하면 `k -n storage patch certificate openbao-tls --type=merge -p '{"spec":{"duration":"2160h","renewBefore":"720h"}}'`(기본과 같은 90일을 명시)하고 재발급 후 수동 HUP한다. 이전 인증서/Secret이나 raft 데이터를 되돌리지 않는다.

## 로컬 증거와 한계

`R390`은 Helm/promtool 없이 YAML 계약·bootstrap 동일성·bash 3.2/5 fake OpenSSL/kubectl 동작(일치, 불일치 지속, 다음 poll 수렴, 빈 응답, 연결 실패, readiness CLI PID 제외)과 변이를 검증한다. 실제 OpenBao·kubelet·Kyverno·ArgoCD·클라이언트 경로의 증거를 대신하지 않는다. chart 0.28.3은 sandbox에 캐시가 없어 정확 버전 Helm 렌더링을 수행하지 못했다. 오케스트레이터는 아래 명령으로 정확한 chart의 StatefulSet을 렌더링해 확인해야 한다(sandbox 실행은 DNS `no such host`로 실패, 상위 narwhal-apps chart 렌더링은 통과).

```bash
yq '.spec.source.helm.valuesObject' gitops/charts/narwhal-apps/templates/openbao.yaml > /tmp/openbao-tls-values.yaml
helm template openbao openbao --repo https://openbao.github.io/openbao-helm --version 0.28.3 --namespace storage -f /tmp/openbao-tls-values.yaml > /tmp/openbao-tls-rendered.yaml
```
