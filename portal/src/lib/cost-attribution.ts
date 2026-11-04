/**
 * Cost 서비스 탭 귀속(attribution) 체인 — pure 함수 (Prometheus/IO 없음).
 *
 * D1: 서비스 탭은 이전에 label_app_kubernetes_io_instance 하나로만 묶어서 라벨이 없는
 * 워크로드를 전부 버렸다. pod마다 아래 순서의 첫 식별자로 귀속한다.
 *   instance → name → app → k8s-app → owner(Deployment/StatefulSet/DaemonSet/Job) → pod 이름 stem
 * 어느 것도 못 구하면 하나의 "unclassified" 행으로 모은다 (버리지 않는다).
 * D2: instance 행의 id는 기존대로 값 그대로(상세 링크/교차 namespace 병합 유지). 그 외
 * 행은 `source:namespace/name`이라 instance 행과 이름이 같아도 병합되지 않는다.
 * Escape hatch: kube-state-metrics 라벨 allowlist가 비어 있으면 앞 4단계는 건너뛰고
 * owner 단계부터 쓰이는 것이 정상 동작이다.
 */

export type AttributionSource = "instance" | "name" | "app" | "k8s-app" | "owner" | "pod" | "unclassified"

export const UNCLASSIFIED_ID = "__unclassified__"

export interface PodUsage {
  namespace: string
  pod: string
  cpu: number // cores
  memBytes: number
}

export interface PodLabels {
  instance?: string
  name?: string
  app?: string
  k8sApp?: string
}

export interface PodOwner {
  kind: string
  name: string
}

export interface AttributionInput {
  usage: PodUsage[]
  labels: Map<string, PodLabels> // key: `${namespace}/${pod}`
  owners: Map<string, PodOwner> // kube_pod_owner, key: `${namespace}/${pod}`
  replicaSetOwners: Map<string, PodOwner> // kube_replicaset_owner, key: `${namespace}/${replicaset}`
}

export interface AttributedGroup {
  id: string
  source: AttributionSource
  namespace?: string
  name: string
  kind?: string // owner 단계의 Deployment/StatefulSet/DaemonSet/Job/ReplicaSet
  pods: number
  cpu: number
  memBytes: number
}

export const podKey = (namespace: string, pod: string) => `${namespace}/${pod}`

const usable = (v: string | undefined): v is string => !!v && v !== "unknown"

// Job 이름의 CronJob 타임스탬프(…-29012345) 접미사, ReplicaSet/Pod 해시 접미사를 제거한다.
export function podNameStem(pod: string): string {
  // ReplicaSet 해시(숫자 포함 8~10자) + pod 접미사 → 하나만 제거. 해시에 숫자가 없으면
  // "node-exporter-xxxxx"처럼 이름의 일부일 수 있어 접미사 5자만 제거한다.
  const stem = /-(?=[a-z]*\d)[a-z0-9]{8,10}-[a-z0-9]{5}$/.test(pod)
    ? pod.replace(/-[a-z0-9]{8,10}-[a-z0-9]{5}$/, "")
    : /-[a-z0-9]{5}$/.test(pod)
      ? pod.replace(/-[a-z0-9]{5}$/, "")
      : pod.replace(/-\d+$/, "")
  return stem || pod
}

function resolveOwner(
  owner: PodOwner | undefined,
  namespace: string,
  replicaSetOwners: Map<string, PodOwner>
): { kind: string; name: string } | null {
  if (!owner || !owner.name) return null
  switch (owner.kind) {
    case "ReplicaSet": {
      const rs = replicaSetOwners.get(podKey(namespace, owner.name))
      return rs?.name ? { kind: rs.kind, name: rs.name } : { kind: "ReplicaSet", name: owner.name }
    }
    case "StatefulSet":
    case "DaemonSet":
    case "ReplicationController":
      return { kind: owner.kind, name: owner.name }
    case "Job":
      return { kind: "Job", name: owner.name.replace(/-\d{5,}$/, "") }
    default:
      // Node(static pod)/Cluster/CRD 소유자는 워크로드 식별자로 쓰기엔 모호하다.
      return null
  }
}

export function attributePods(input: AttributionInput): AttributedGroup[] {
  const groups = new Map<string, AttributedGroup>()
  const add = (g: Omit<AttributedGroup, "pods" | "cpu" | "memBytes">, u: PodUsage) => {
    const cur = groups.get(g.id) ?? { ...g, pods: 0, cpu: 0, memBytes: 0 }
    cur.pods += 1
    cur.cpu += u.cpu
    cur.memBytes += u.memBytes
    groups.set(g.id, cur)
  }

  for (const u of input.usage) {
    if (!u.pod) {
      add({ id: UNCLASSIFIED_ID, source: "unclassified", name: UNCLASSIFIED_ID }, u)
      continue
    }
    const key = podKey(u.namespace, u.pod)
    const l = input.labels.get(key) ?? {}
    if (usable(l.instance)) {
      add({ id: l.instance, source: "instance", name: l.instance }, u)
      continue
    }
    const labelHit: [AttributionSource, string | undefined][] = [
      ["name", l.name],
      ["app", l.app],
      ["k8s-app", l.k8sApp],
    ]
    const hit = labelHit.find(([, v]) => usable(v))
    if (hit) {
      const [source, name] = hit as [AttributionSource, string]
      add({ id: `${source}:${u.namespace}/${name}`, source, namespace: u.namespace, name }, u)
      continue
    }
    const owner = resolveOwner(input.owners.get(key), u.namespace, input.replicaSetOwners)
    if (owner) {
      add(
        { id: `owner:${u.namespace}/${owner.kind}/${owner.name}`, source: "owner", namespace: u.namespace, name: owner.name, kind: owner.kind },
        u
      )
      continue
    }
    add({ id: `pod:${u.namespace}/${podNameStem(u.pod)}`, source: "pod", namespace: u.namespace, name: podNameStem(u.pod) }, u)
  }
  return [...groups.values()]
}
