import fs from "node:fs"
import path from "node:path"
import yaml from "js-yaml"
import { beforeEach, describe, expect, it, vi } from "vitest"
import type {
  KisaFetchResult,
  KisaRawAlertmanagerConfig,
  KisaRawClusterPolicy,
  KisaRawCronJob,
  KisaRawDeployment,
  KisaRawNetworkPolicy,
  KisaRawPeerAuthentication,
  KisaRawPodStatus,
  KisaRawPolicyReport,
  KisaRawStatefulSet,
  KisaRbacSnapshot,
  KisaSecretMetadata,
  KisaWorkloadInventory,
} from "./k8s-client"

vi.mock("server-only", () => ({}))

const mockGetKisaDeployment = vi.fn<() => Promise<KisaFetchResult<KisaRawDeployment>>>()
const mockGetKisaStatefulSet = vi.fn<() => Promise<KisaFetchResult<KisaRawStatefulSet>>>()
const mockGetKisaCronJob = vi.fn<() => Promise<KisaFetchResult<KisaRawCronJob>>>()
const mockGetKisaSecretMetadata = vi.fn<() => Promise<KisaFetchResult<KisaSecretMetadata>>>()
const mockGetKisaNamespacedNetworkPolicies = vi.fn<() => Promise<KisaFetchResult<KisaRawNetworkPolicy[]>>>()
const mockGetKisaPeerAuthentications = vi.fn<() => Promise<KisaFetchResult<KisaRawPeerAuthentication[]>>>()
const mockGetKisaNotificationConfig = vi.fn<() => Promise<KisaFetchResult<KisaRawAlertmanagerConfig>>>()
const mockGetKisaNamespacedPods = vi.fn<() => Promise<KisaFetchResult<KisaRawPodStatus[]>>>()
const mockGetKisaKyvernoClusterPolicies = vi.fn<() => Promise<KisaFetchResult<KisaRawClusterPolicy[]>>>()
const mockGetKisaPolicyReports = vi.fn<() => Promise<KisaFetchResult<KisaRawPolicyReport[]>>>()
const mockGetKisaWorkloadInventory = vi.fn<() => Promise<KisaWorkloadInventory>>()
const mockGetKisaRbacSnapshot = vi.fn<() => Promise<KisaRbacSnapshot>>()

vi.mock("./k8s-client", () => ({
  getKisaDeployment: () => mockGetKisaDeployment(),
  getKisaStatefulSet: () => mockGetKisaStatefulSet(),
  getKisaCronJob: () => mockGetKisaCronJob(),
  getKisaSecretMetadata: () => mockGetKisaSecretMetadata(),
  getKisaNamespacedNetworkPolicies: () => mockGetKisaNamespacedNetworkPolicies(),
  getKisaPeerAuthentications: () => mockGetKisaPeerAuthentications(),
  getKisaNotificationConfig: () => mockGetKisaNotificationConfig(),
  getKisaNamespacedPods: () => mockGetKisaNamespacedPods(),
  getKisaKyvernoClusterPolicies: () => mockGetKisaKyvernoClusterPolicies(),
  getKisaPolicyReports: () => mockGetKisaPolicyReports(),
  getKisaWorkloadInventory: () => mockGetKisaWorkloadInventory(),
  getKisaRbacSnapshot: () => mockGetKisaRbacSnapshot(),
}))

import { checkEtcd02, checkImg01, checkSec01, checkTls01 } from "./kisa-checks"
import { checkImg02, checkObs01, checkRbacLive, evaluateRbacRules, KISA_POLICY_BASELINE, ruleGrants, type KisaPolicyBaseline, type RbacPolicyRule } from "./kisa-checks-ops"
import { computeKyvernoSpecDigest } from "./kisa-policy-canonical.mjs"

beforeEach(() => {
  vi.clearAllMocks()
})

describe("KISA-ETCD-02 live evaluation", () => {
  const etcdDepPlain: KisaRawDeployment = {
    metadata: { name: "apisix-etcd", namespace: "platform-system" },
    spec: {
      replicas: 1,
      template: {
        metadata: { labels: { app: "apisix-etcd" } },
        spec: {
          containers: [{ name: "etcd", command: ["etcd", "--listen-client-urls=http://0.0.0.0:2379"] }],
        },
      },
    },
    status: { replicas: 1, readyReplicas: 1 },
  }
  const etcdDepTls: KisaRawDeployment = {
    metadata: { name: "apisix-etcd", namespace: "platform-system" },
    spec: {
      replicas: 1,
      template: {
        metadata: { labels: { app: "apisix-etcd" } },
        spec: {
          containers: [{
            name: "etcd",
            command: [
              "etcd",
              "--listen-client-urls=https://0.0.0.0:2379",
              "--client-cert-auth",
              "--cert-file=/tls/tls.crt",
              "--key-file=/tls/tls.key",
              "--trusted-ca-file=/tls/ca.crt",
            ],
          }],
        },
      },
    },
    status: { replicas: 1, readyReplicas: 1 },
  }
  const strictPolicy: KisaRawNetworkPolicy = {
    metadata: { name: "apisix-etcd-ingress-policy", namespace: "platform-system" },
    spec: {
      podSelector: { matchLabels: { app: "apisix-etcd" } },
      policyTypes: ["Ingress"],
      ingress: [{ from: [{ podSelector: { matchLabels: { "app.kubernetes.io/name": "apisix" } } }], ports: [{ port: 2379 }] }],
    },
  }

  it("passes only when NetworkPolicy restricts to APISIX and client TLS+auth is enabled", async () => {
    mockGetKisaDeployment.mockResolvedValue({ status: "ok", data: etcdDepTls })
    mockGetKisaNamespacedNetworkPolicies.mockResolvedValue({ status: "ok", data: [strictPolicy] })
    const r = await checkEtcd02()
    expect(r.status).toBe("pass")
    expect(r.reasonKey).toBe("compliance.kisa.reason.etcdIsolatedTls")
  })

  it("warns when NetworkPolicy isolates APISIX but etcd listens on plain HTTP", async () => {
    mockGetKisaDeployment.mockResolvedValue({ status: "ok", data: etcdDepPlain })
    mockGetKisaNamespacedNetworkPolicies.mockResolvedValue({ status: "ok", data: [strictPolicy] })
    const r = await checkEtcd02()
    expect(r.status).toBe("warn")
    expect(r.reasonKey).toBe("compliance.kisa.reason.etcdIsolatedPlainHttp")
    expect(r.detailLocalized.en).toContain("etcd client TLS/auth not enabled")
  })

  it("fails when NetworkPolicy is missing or allow-all", async () => {
    mockGetKisaDeployment.mockResolvedValue({ status: "ok", data: etcdDepPlain })
    mockGetKisaNamespacedNetworkPolicies.mockResolvedValue({ status: "ok", data: [] })
    expect((await checkEtcd02()).status).toBe("fail")

    mockGetKisaNamespacedNetworkPolicies.mockResolvedValue({
      status: "ok",
      data: [{
        metadata: { name: "allow-all", namespace: "platform-system" },
        spec: { podSelector: { matchLabels: { app: "apisix-etcd" } }, policyTypes: ["Ingress"], ingress: [{ ports: [{ port: 2379 }] }] },
      }],
    })
    const broad = await checkEtcd02()
    expect(broad.status).toBe("fail")
    expect(broad.reasonKey).toBe("compliance.kisa.reason.etcdPolicyBroad")
  })

  it("returns manual with reason on 403 forbidden or missing Deployment", async () => {
    mockGetKisaDeployment.mockResolvedValue({ status: "forbidden", resource: "deployments.apps/platform-system/apisix-etcd" })
    mockGetKisaNamespacedNetworkPolicies.mockResolvedValue({ status: "ok", data: [] })
    const forb = await checkEtcd02()
    expect(forb.status).toBe("manual")
    expect(forb.partial).toBe(true)
    expect(forb.reasonKey).toBe("compliance.kisa.reason.apiForbidden")

    mockGetKisaDeployment.mockResolvedValue({ status: "not-found" })
    const miss = await checkEtcd02()
    expect(miss.status).toBe("manual")
    expect(miss.reasonKey).toBe("compliance.kisa.reason.resourceMissing")
  })

  it("reproduction (R5): ignores TLS args on sidecar containers and returns manual if 'etcd' container is missing", async () => {
    const depWithSidecarTls: KisaRawDeployment = {
      metadata: { name: "apisix-etcd", namespace: "platform-system" },
      spec: {
        template: {
          metadata: { labels: { app: "apisix-etcd" } },
          spec: {
            containers: [
              { name: "etcd", command: ["etcd", "--listen-client-urls=http://0.0.0.0:2379"] },
              {
                name: "tls-sidecar",
                command: [
                  "helper",
                  "--listen-client-urls=https://0.0.0.0:2379",
                  "--client-cert-auth",
                  "--cert-file=/tls/tls.crt",
                  "--key-file=/tls/tls.key",
                  "--trusted-ca-file=/tls/ca.crt",
                ],
              },
            ],
          },
        },
      },
    }
    mockGetKisaDeployment.mockResolvedValue({ status: "ok", data: depWithSidecarTls })
    mockGetKisaNamespacedNetworkPolicies.mockResolvedValue({ status: "ok", data: [strictPolicy] })
    const sidecarRes = await checkEtcd02()
    expect(sidecarRes.status).toBe("warn")
    expect(sidecarRes.reasonKey).toBe("compliance.kisa.reason.etcdIsolatedPlainHttp")

    mockGetKisaDeployment.mockResolvedValue({
      status: "ok",
      data: {
        metadata: { name: "apisix-etcd", namespace: "platform-system" },
        spec: { template: { spec: { containers: [{ name: "other-container", command: ["etcd", "--listen-client-urls=https://0.0.0.0:2379", "--client-cert-auth"] }] } } },
      },
    })
    const noEtcdRes = await checkEtcd02()
    expect(noEtcdRes.status).toBe("manual")
    expect(noEtcdRes.live).toBe(false)
    expect(noEtcdRes.reasonKey).toBe("compliance.kisa.reason.etcdContainerMissing")
  })

  it("reproduction (R7): rejects empty TLS flag values (--cert-file=) and replicas/readyReplicas=0", async () => {
    mockGetKisaNamespacedNetworkPolicies.mockResolvedValue({ status: "ok", data: [strictPolicy] })
    const emptyFlagsDep: KisaRawDeployment = {
      metadata: { name: "apisix-etcd", namespace: "platform-system" },
      spec: {
        replicas: 0,
        template: {
          metadata: { labels: { app: "apisix-etcd" } },
          spec: {
            containers: [{
              name: "etcd",
              command: [
                "etcd",
                "--listen-client-urls=https://0.0.0.0:2379",
                "--client-cert-auth",
                "--cert-file=",
                "--key-file=",
                "--trusted-ca-file=",
              ],
            }],
          },
        },
      },
      status: { replicas: 0, readyReplicas: 0 },
    }
    mockGetKisaDeployment.mockResolvedValue({ status: "ok", data: emptyFlagsDep })
    const emptyRes = await checkEtcd02()
    expect(emptyRes.status).toBe("warn")
    expect(emptyRes.reasonKey).toBe("compliance.kisa.reason.etcdIsolatedPlainHttp")

    const zeroReplicasTls: KisaRawDeployment = {
      ...etcdDepTls,
      spec: { ...etcdDepTls.spec, replicas: 0 },
      status: { replicas: 0, readyReplicas: 0 },
    }
    mockGetKisaDeployment.mockResolvedValue({ status: "ok", data: zeroReplicasTls })
    const zeroRes = await checkEtcd02()
    expect(zeroRes.status).toBe("warn")
    expect(zeroRes.reasonKey).toBe("compliance.kisa.reason.etcdWorkloadNotReady")

    const missingStatusTls: KisaRawDeployment = {
      metadata: etcdDepTls.metadata,
      spec: etcdDepTls.spec,
    }
    mockGetKisaDeployment.mockResolvedValue({ status: "ok", data: missingStatusTls })
    const missingRes = await checkEtcd02()
    expect(missingRes.status).toBe("manual")
    expect(missingRes.reasonKey).toBe("compliance.kisa.reason.etcdWorkloadNotReady")
  })
})

describe("KISA-SEC-01 live evaluation", () => {
  const hardenedCj: KisaRawCronJob = {
    metadata: { name: "openbao-auto-unseal", namespace: "storage" },
    spec: {
      suspend: false,
      jobTemplate: {
        spec: {
          template: {
            spec: {
              serviceAccountName: "openbao-auto-unseal",
              automountServiceAccountToken: false,
              containers: [{ name: "unseal", env: [{ name: "BAO_CACERT", value: "/ca.crt" }, { name: "UNSEAL_KEY", valueFrom: { secretKeyRef: { name: "openbao-init", key: "unseal_keys_b64" } } }] }],
            },
          },
        },
      },
    },
  }

  it("warns when CronJob and Secret metadata are hardened but no external KMS exists", async () => {
    mockGetKisaCronJob.mockResolvedValue({ status: "ok", data: hardenedCj })
    mockGetKisaStatefulSet.mockResolvedValue({ status: "ok", data: { spec: { template: { spec: { containers: [{ env: [] }] } } } } })
    mockGetKisaSecretMetadata.mockResolvedValue({ status: "ok", data: { name: "openbao-init", namespace: "storage", labels: { "velero.io/exclude-from-backup": "true" }, annotationKeys: [] } })
    const r = await checkSec01()
    expect(r.status).toBe("warn")
    expect(r.reasonKey).toBe("compliance.kisa.reason.secNoExternalKms")
  })

  it("degrades Secret metadata sub-signal to not evaluated when secrets read is 403", async () => {
    mockGetKisaCronJob.mockResolvedValue({ status: "ok", data: hardenedCj })
    mockGetKisaStatefulSet.mockResolvedValue({ status: "ok", data: { spec: { template: { spec: { containers: [] } } } } })
    mockGetKisaSecretMetadata.mockResolvedValue({ status: "forbidden", resource: "secrets/storage/openbao-init" })
    const r = await checkSec01()
    expect(r.status).toBe("warn")
    expect(r.reasonKey).toBe("compliance.kisa.reason.secSecretMetaNotEvaluated")
    expect(r.detailLocalized.en).toContain("not evaluated")
  })

  it("fails on CronJob weakness (BAO_SKIP_VERIFY / default SA) or exposed Secret metadata", async () => {
    mockGetKisaCronJob.mockResolvedValue({
      status: "ok",
      data: {
        spec: {
          jobTemplate: {
            spec: {
              template: {
                spec: { containers: [{ env: [{ name: "BAO_SKIP_VERIFY", value: "true" }] }] },
              },
            },
          },
        },
      },
    })
    mockGetKisaStatefulSet.mockResolvedValue({ status: "ok", data: {} })
    mockGetKisaSecretMetadata.mockResolvedValue({ status: "forbidden", resource: "secrets/storage/openbao-init" })
    expect((await checkSec01()).status).toBe("fail")

    mockGetKisaCronJob.mockResolvedValue({ status: "ok", data: hardenedCj })
    mockGetKisaSecretMetadata.mockResolvedValue({
      status: "ok",
      data: { name: "openbao-init", namespace: "storage", labels: {}, annotationKeys: ["kubectl.kubernetes.io/last-applied-configuration"] },
    })
    const exposed = await checkSec01()
    expect(exposed.status).toBe("fail")
    expect(exposed.reasonKey).toBe("compliance.kisa.reason.secSecretExposed")
  })

  it("returns manual with reason when CronJob is absent/suspended even if KMS env vars exist (never infers external KMS or returns pass)", async () => {
    mockGetKisaCronJob.mockResolvedValue({ status: "not-found" })
    mockGetKisaStatefulSet.mockResolvedValue({
      status: "ok",
      data: {
        spec: {
          template: {
            spec: {
              containers: [{
                env: [
                  { name: "BAO_SEAL_TYPE", value: "shamir" },
                  { name: "AWS_KMS_KEY_ID", value: "alias/unused-kms-key" },
                ],
              }],
            },
          },
        },
      },
    })
    mockGetKisaSecretMetadata.mockResolvedValue({ status: "not-found" })
    const r = await checkSec01()
    expect(r.status).toBe("manual")
    expect(r.live).toBe(false)
    expect(r.reasonKey).toBe("compliance.kisa.reason.secCronJobNotEvaluated")

    // Suspended CronJob also degrades to manual (missing/inactive CronJob never improves the result)
    mockGetKisaCronJob.mockResolvedValue({
      status: "ok",
      data: { spec: { suspend: true, jobTemplate: { spec: { template: { spec: { containers: [{ env: [{ name: "BAO_SKIP_VERIFY", value: "true" }] }] } } } } } },
    })
    const susp = await checkSec01()
    expect(susp.status).toBe("manual")
    expect(susp.live).toBe(false)
    expect(susp.reasonKey).toBe("compliance.kisa.reason.secCronJobNotEvaluated")
  })

  it("still fails on exposed Secret metadata even when CronJob is absent", async () => {
    mockGetKisaCronJob.mockResolvedValue({ status: "not-found" })
    mockGetKisaStatefulSet.mockResolvedValue({ status: "ok", data: {} })
    mockGetKisaSecretMetadata.mockResolvedValue({
      status: "ok",
      data: { name: "openbao-init", namespace: "storage", labels: {}, annotationKeys: ["kubectl.kubernetes.io/last-applied-configuration"] },
    })
    const r = await checkSec01()
    expect(r.status).toBe("fail")
    expect(r.reasonKey).toBe("compliance.kisa.reason.secSecretExposed")
  })

  it("returns manual when CronJob read is forbidden (403)", async () => {
    mockGetKisaCronJob.mockResolvedValue({ status: "forbidden", resource: "cronjobs.batch/storage/openbao-auto-unseal" })
    mockGetKisaStatefulSet.mockResolvedValue({ status: "ok", data: {} })
    const r = await checkSec01()
    expect(r.status).toBe("manual")
    expect(r.partial).toBe(true)
    expect(r.reasonKey).toBe("compliance.kisa.reason.apiForbidden")
  })
})

describe("KISA-IMG-01 honest manual status", () => {
  it("reports manual with check command and never hardcodes fail or pass", () => {
    const r = checkImg01()
    expect(r.status).toBe("manual")
    expect(r.live).toBe(false)
    expect(r.reasonKey).toBe("compliance.kisa.reason.imgNodeManual")
    expect(r.detail).toContain("hosts.toml")
  })
})

describe("KISA-TLS-01 live evaluation", () => {
  it("passes when mesh-wide STRICT has zero exceptions", async () => {
    mockGetKisaPeerAuthentications.mockResolvedValue({
      status: "ok",
      data: [{ metadata: { name: "default", namespace: "istio-system" }, spec: { mtls: { mode: "STRICT" } } }],
    })
    const r = await checkTls01()
    expect(r.status).toBe("pass")
    expect(r.reasonKey).toBe("compliance.kisa.reason.tlsStrictNoExceptions")
  })

  it("warns and lists both namespace-wide, selector, and portLevelMtls exceptions", async () => {
    mockGetKisaPeerAuthentications.mockResolvedValue({
      status: "ok",
      data: [
        { metadata: { name: "default", namespace: "istio-system" }, spec: { mtls: { mode: "STRICT" } } },
        { metadata: { name: "harbor-permissive", namespace: "devtools" }, spec: { mtls: { mode: "PERMISSIVE" }, selector: { matchLabels: { app: "harbor" } } } },
        { metadata: { name: "velero-metrics", namespace: "storage" }, spec: { mtls: { mode: "STRICT" }, portLevelMtls: { "8085": { mode: "PERMISSIVE" } }, selector: { matchLabels: { name: "velero" } } } },
      ],
    })
    const r = await checkTls01()
    expect(r.status).toBe("warn")
    expect(r.detail).toContain("devtools/harbor-permissive")
    expect(r.detail).toContain("storage/velero-metrics")
    expect(r.detail).toContain("port 8085:PERMISSIVE")
  })

  it("fails when mesh-wide PA is missing or not STRICT, warns on rootPa portLevelMtls exception, and returns manual on 403", async () => {
    mockGetKisaPeerAuthentications.mockResolvedValue({ status: "ok", data: [] })
    expect((await checkTls01()).status).toBe("fail")

    mockGetKisaPeerAuthentications.mockResolvedValue({
      status: "ok",
      data: [{ metadata: { name: "default", namespace: "istio-system" }, spec: { mtls: { mode: "STRICT" }, portLevelMtls: { "8080": { mode: "PERMISSIVE" } } } }],
    })
    const rootPortExc = await checkTls01()
    expect(rootPortExc.status).toBe("warn")
    expect(rootPortExc.detail).toContain("port 8080:PERMISSIVE")

    mockGetKisaPeerAuthentications.mockResolvedValue({ status: "forbidden", resource: "peerauthentications.security.istio.io" })
    const f = await checkTls01()
    expect(f.status).toBe("manual")
    expect(f.partial).toBe(true)
  })
})

describe("KISA-OBS-01 live evaluation", () => {
  const readyFalcoPods: KisaRawPodStatus[] = [
    {
      metadata: { name: "falco-abc", labels: { "app.kubernetes.io/name": "falco" } },
      status: { phase: "Running", conditions: [{ type: "Ready", status: "True" }], containerStatuses: [{ ready: true }] },
    },
    {
      metadata: { name: "falco-falcosidekick-xyz", labels: { "app.kubernetes.io/name": "falcosidekick" } },
      status: { phase: "Running", conditions: [{ type: "Ready", status: "True" }], containerStatuses: [{ ready: true }] },
    },
  ]

  it("passes when an external receiver is routed and Falco + falcosidekick are ready", async () => {
    mockGetKisaNotificationConfig.mockResolvedValue({
      status: "ok",
      data: {
        spec: {
          route: { receiver: "slack-sec" },
          receivers: [{ name: "null" }, { name: "slack-sec", slackConfigs: [{ channel: "#sec" }] }],
        },
      },
    })
    mockGetKisaNamespacedPods.mockResolvedValue({ status: "ok", data: readyFalcoPods })
    const r = await checkObs01()
    expect(r.status).toBe("pass")
    expect(r.reasonKey).toBe("compliance.kisa.reason.obsConfigured")
  })

  it("warns on null-only or unrouted receiver when Falco is ready; fails on missing config", async () => {
    mockGetKisaNotificationConfig.mockResolvedValue({
      status: "ok",
      data: { spec: { route: { receiver: "null" }, receivers: [{ name: "null" }, { name: "unrouted", slackConfigs: [{}] }] } },
    })
    mockGetKisaNamespacedPods.mockResolvedValue({ status: "ok", data: readyFalcoPods })
    const w = await checkObs01()
    expect(w.status).toBe("warn")
    expect(w.reasonKey).toBe("compliance.kisa.reason.obsNoChannel")

    mockGetKisaNotificationConfig.mockResolvedValue({ status: "not-found" })
    const miss = await checkObs01()
    expect(miss.status).toBe("fail")
    expect(miss.reasonKey).toBe("compliance.kisa.reason.obsConfigMissing")
  })

  it("returns manual on 403 forbidden for AlertmanagerConfig and does not match Falco pods by name prefix/substring without labels", async () => {
    mockGetKisaNotificationConfig.mockResolvedValue({ status: "forbidden", resource: "alertmanagerconfigs" })
    mockGetKisaNamespacedPods.mockResolvedValue({ status: "ok", data: readyFalcoPods })
    const r = await checkObs01()
    expect(r.status).toBe("manual")
    expect(r.partial).toBe(true)

    mockGetKisaNotificationConfig.mockResolvedValue({
      status: "ok",
      data: {
        spec: {
          route: { receiver: "slack-sec" },
          receivers: [{ name: "slack-sec", slackConfigs: [{ channel: "#sec" }] }],
        },
      },
    })
    mockGetKisaNamespacedPods.mockResolvedValue({
      status: "ok",
      data: [
        { metadata: { name: "falco-spoof", labels: { app: "unrelated" } }, status: { phase: "Running", containerStatuses: [{ ready: true }] } },
        { metadata: { name: "my-falcosidekick-spoof", labels: {} }, status: { phase: "Running", containerStatuses: [{ ready: true }] } },
      ],
    })
    const spoofRes = await checkObs01()
    expect(spoofRes.status).toBe("warn")
    expect(spoofRes.reasonKey).toBe("compliance.kisa.reason.obsFalcoDown")
  })

  it("reproduction (R7): requires authoritative Pod condition Ready=True (ignores containerStatuses.ready=true when Ready=False)", async () => {
    mockGetKisaNotificationConfig.mockResolvedValue({
      status: "ok",
      data: {
        spec: {
          route: { receiver: "slack-sec" },
          receivers: [{ name: "slack-sec", slackConfigs: [{ channel: "#sec" }] }],
        },
      },
    })
    mockGetKisaNamespacedPods.mockResolvedValue({
      status: "ok",
      data: [
        {
          metadata: { name: "falco-abc", labels: { "app.kubernetes.io/name": "falco" } },
          status: {
            phase: "Running",
            conditions: [{ type: "Ready", status: "False" }],
            containerStatuses: [{ ready: true }],
          },
        },
        {
          metadata: { name: "falco-falcosidekick-xyz", labels: { "app.kubernetes.io/name": "falcosidekick" } },
          status: {
            phase: "Running",
            conditions: [{ type: "Ready", status: "True" }],
            containerStatuses: [{ ready: true }],
          },
        },
      ],
    })
    const res = await checkObs01()
    expect(res.status).toBe("warn")
    expect(res.reasonKey).toBe("compliance.kisa.reason.obsFalcoDown")
  })
})

describe("KISA-IMG-02 live evaluation", () => {
  const repoManifestPath = path.resolve(__dirname, "../../../gitops/resources/kyverno-policies.yaml")
  const repoPolicies = yaml
    .loadAll(fs.readFileSync(repoManifestPath, "utf8"))
    .filter((d): d is KisaRawClusterPolicy & { metadata: { name: string } } =>
      Boolean(d && typeof d === "object" && (d as { kind?: string }).kind === "ClusterPolicy"),
    )

  function clonePolicy(name: string, ready = true): KisaRawClusterPolicy {
    const found = repoPolicies.find((p) => p.metadata?.name === name)
    if (!found) throw new Error(`Missing ${name} in repo manifest`)
    const cloned = JSON.parse(JSON.stringify(found)) as KisaRawClusterPolicy
    if (ready) {
      cloned.status = { conditions: [{ type: "Ready", status: "True" }] }
    }
    return cloned
  }

  function withKyvernoAdmissionDefaults(pol: KisaRawClusterPolicy): KisaRawClusterPolicy {
    const cloned = JSON.parse(JSON.stringify(pol)) as KisaRawClusterPolicy
    if (cloned.spec) {
      cloned.spec.admission = true
      cloned.spec.emitWarning = false
      for (const r of cloned.spec.rules ?? []) {
        (r as Record<string, unknown>).skipBackgroundRequests = true
        if (r.validate) {
          (r.validate as Record<string, unknown>).allowExistingViolations = true
        }
        for (const vi of r.verifyImages ?? []) {
          (vi as Record<string, unknown>).useCache = true
          for (const att of (vi.attestors ?? []) as Array<{ entries?: Array<Record<string, unknown>> }>) {
            for (const entry of att.entries ?? []) {
              entry.signatureAlgorithm = "sha256"
              if (entry.keys && typeof entry.keys === "object") {
                (entry.keys as Record<string, unknown>).signatureAlgorithm = "sha256"
              }
            }
          }
          for (const att of (vi.attestations ?? []) as Array<{ attestors?: Array<{ entries?: Array<Record<string, unknown>> }> }>) {
            for (const inner of att.attestors ?? []) {
              for (const entry of inner.entries ?? []) {
                entry.signatureAlgorithm = "sha256"
                if (entry.keys && typeof entry.keys === "object") {
                  (entry.keys as Record<string, unknown>).signatureAlgorithm = "sha256"
                }
              }
            }
          }
        }
      }
    }
    return cloned
  }

  function makeInv(overrides?: Partial<KisaWorkloadInventory>): KisaWorkloadInventory {
    return {
      pods: {
        status: "ok",
        data: [{ metadata: { name: "portal-1", namespace: "devtools" }, spec: { containers: [{ image: "ghcr.io/dasomel/narwhal-portal:1.1.24" }] }, status: { phase: "Running" } }],
      },
      replicasets: {
        status: "ok",
        data: [
          { metadata: { name: "portal-old", namespace: "devtools", annotations: { "deployment.kubernetes.io/revision": "1" }, ownerReferences: [{ kind: "Deployment", name: "narwhal-portal" }] }, spec: { replicas: 0 }, status: { replicas: 0, terminatingReplicas: 0 } },
          { metadata: { name: "portal-new", namespace: "devtools", annotations: { "deployment.kubernetes.io/revision": "2" }, ownerReferences: [{ kind: "Deployment", name: "narwhal-portal" }] }, spec: { replicas: 1 }, status: { replicas: 1, terminatingReplicas: 0 } },
        ],
      },
      deployments: { status: "ok", data: [{ metadata: { name: "narwhal-portal", namespace: "devtools" } }] },
      statefulsets: { status: "ok", data: [] },
      daemonsets: { status: "ok", data: [{ metadata: { name: "chaos-daemon", namespace: "chaos-testing" } }] },
      cronjobs: { status: "ok", data: [] },
      ...overrides,
    }
  }

  it("baseline-equal (including Kyverno server-defaulted admission fields) satisfies sub-signals, excludes stale remnants, and stays warn due to baseline verify Audit/portal-only limitation", async () => {
    const basePolicies = [
      withKyvernoAdmissionDefaults(clonePolicy("disallow-latest-tag", true)),
      withKyvernoAdmissionDefaults(clonePolicy("verify-image-signatures", true)),
    ]
    mockGetKisaKyvernoClusterPolicies.mockResolvedValue({ status: "ok", data: basePolicies })
    mockGetKisaWorkloadInventory.mockResolvedValue(makeInv())
    mockGetKisaPolicyReports.mockResolvedValue({
      status: "ok",
      data: [
        {
          metadata: { name: "r1", namespace: "devtools" },
          scope: { kind: "ReplicaSet", name: "portal-old", namespace: "devtools" },
          results: [{ policy: "verify-image-signatures", result: "fail", message: "failed to verify image ghcr.io/dasomel/narwhal-portal:1.1.24: no sig" }],
        },
        {
          metadata: { name: "r2", namespace: "devtools" },
          scope: { kind: "Pod", name: "deleted-pod", namespace: "devtools" },
          results: [{ policy: "verify-image-signatures", result: "fail", message: "failed to verify image ghcr.io/dasomel/narwhal-portal:1.1.24: no sig" }],
        },
        {
          metadata: { name: "r3", namespace: "chaos-testing" },
          scope: { kind: "DaemonSet", name: "chaos-daemon", namespace: "chaos-testing" },
          results: [{ policy: "verify-image-signatures", result: "fail", message: "failed to verify image ghcr.io/chaos-mesh/chaos-daemon:v2.8.3: no sig" }],
        },
      ],
    })
    const r = await checkImg02()
    expect(r.status).toBe("warn")
    expect(r.reasonKey).toBe("compliance.kisa.reason.imgVerifyAudit")
    expect(r.detail).toContain("disallow-latest-tag=Enforce")
    expect(r.detail).toContain("verify-image-signatures=Audit(required=false)")
    expect(r.detail).toContain("현재 위반 0건")
    expect(r.detail).toContain("제외 3건")
    expect(r.detail).not.toContain("DiffersFromBaseline")
  })

  it("passes only when both policies match an enforceEligible=true baseline, are Ready=True, cover all running images, and have zero violations", async () => {
    const enforceVerifyPolicy: KisaRawClusterPolicy = {
      metadata: { name: "verify-image-signatures" },
      spec: {
        validationFailureAction: "Enforce",
        background: true,
        rules: [{
          name: "verify-signature-and-sbom",
          match: { any: [{ resources: { kinds: ["Pod"] } }] },
          verifyImages: [{
            imageReferences: ["*"],
            required: true,
            attestors: [{ count: 1, entries: [{ keys: { publicKeys: "-----BEGIN PUBLIC KEY-----\nMFkw\n-----END PUBLIC KEY-----" } }] }],
          }],
        }],
      },
      status: { conditions: [{ type: "Ready", status: "True" }] },
    }
    const upgradedBaseline: KisaPolicyBaseline = {
      ...KISA_POLICY_BASELINE,
      policies: {
        ...KISA_POLICY_BASELINE.policies,
        "verify-image-signatures": {
          name: "verify-image-signatures",
          role: "verify-images",
          specSha256: computeKyvernoSpecDigest(enforceVerifyPolicy.spec),
          validationFailureAction: "Enforce",
          hasOverrides: false,
          imageReferences: ["*"],
          required: true,
          enforceEligible: true,
          knownLimitations: [],
        },
      },
    }
    mockGetKisaKyvernoClusterPolicies.mockResolvedValue({
      status: "ok",
      data: [clonePolicy("disallow-latest-tag", true), enforceVerifyPolicy],
    })
    mockGetKisaWorkloadInventory.mockResolvedValue(makeInv())
    mockGetKisaPolicyReports.mockResolvedValue({ status: "ok", data: [] })

    const r = await checkImg02(upgradedBaseline)
    expect(r.status).toBe("pass")
    expect(r.reasonKey).toBe("compliance.kisa.reason.imgPolicyEnforced")
    expect(r.detail).toContain("required=true")
  })

  it("reproduction (R5): deny operators/all, negated CEL, match.namespaces=['dev'], exclude.namespaces=['*'], false preconditions, and any single-field deviation MUST NOT pass (warn)", async () => {
    const verifyPol = clonePolicy("verify-image-signatures", true)
    mockGetKisaWorkloadInventory.mockResolvedValue(makeInv())
    mockGetKisaPolicyReports.mockResolvedValue({ status: "ok", data: [] })

    const deviantSpecs: Array<[string, KisaRawClusterPolicy]> = [
      [
        "deny with non-denying operator / impossible all condition",
        {
          metadata: { name: "disallow-latest-tag" },
          status: { conditions: [{ type: "Ready", status: "True" }] },
          spec: {
            validationFailureAction: "Enforce",
            background: true,
            rules: [{
              name: "validate-image-tag",
              match: { any: [{ resources: { kinds: ["Pod"] } }] },
              validate: {
                deny: {
                  conditions: {
                    all: [
                      { key: "{{ request.object.spec.containers[0].image }}", operator: "NotEquals", value: "*:latest" },
                      { key: "a", operator: "Equals", value: "b" },
                    ],
                  },
                },
              },
            }],
          },
        },
      ],
      [
        "negated/inverted CEL permitting :latest",
        {
          metadata: { name: "disallow-latest-tag" },
          status: { conditions: [{ type: "Ready", status: "True" }] },
          spec: {
            validationFailureAction: "Enforce",
            background: true,
            rules: [{
              name: "validate-image-tag",
              match: { any: [{ resources: { kinds: ["Pod"] } }] },
              validate: {
                cel: { expressions: [{ expression: "object.spec.containers.exists(c, c.image.endsWith(':latest'))" }] },
              },
            }],
          },
        },
      ],
      [
        "match.namespaces=['dev'] scoping out prod",
        (() => {
          const p = clonePolicy("disallow-latest-tag", true)
          p.spec!.rules![0].match = { any: [{ resources: { kinds: ["Pod"], namespaces: ["dev"] } }] }
          return p
        })(),
      ],
      [
        "exclude.namespaces=['*'] excluding all pods",
        (() => {
          const p = clonePolicy("disallow-latest-tag", true)
          p.spec!.rules![0].exclude = { any: [{ resources: { namespaces: ["*"] } }] }
          return p
        })(),
      ],
      [
        "false precondition disabling rule",
        (() => {
          const p = clonePolicy("disallow-latest-tag", true)
          ;(p.spec!.rules![0] as Record<string, unknown>).preconditions = {
            all: [{ key: "1", operator: "Equals", value: "2" }],
          }
          return p
        })(),
      ],
      [
        "single-field deviation: spec.admission=false",
        (() => {
          const p = clonePolicy("disallow-latest-tag", true)
          p.spec!.admission = false
          return p
        })(),
      ],
      [
        "single-field deviation: validationFailureActionOverrides Audit",
        (() => {
          const p = clonePolicy("disallow-latest-tag", true)
          p.spec!.validationFailureActionOverrides = [{ action: "Audit", namespaceSelector: {} }]
          return p
        })(),
      ],
      [
        "single-field deviation: validate.failureAction=Audit",
        (() => {
          const p = clonePolicy("disallow-latest-tag", true)
          p.spec!.rules![0].validate!.failureAction = "Audit"
          return p
        })(),
      ],
      [
        "single-field deviation: validate.allowExistingViolations=false",
        (() => {
          const p = clonePolicy("disallow-latest-tag", true)
          ;(p.spec!.rules![0].validate as Record<string, unknown>).allowExistingViolations = false
          return p
        })(),
      ],
    ]

    for (const [label, pol] of deviantSpecs) {
      mockGetKisaKyvernoClusterPolicies.mockResolvedValue({ status: "ok", data: [pol, verifyPol] })
      const res = await checkImg02()
      expect(res.status, label).toBe("warn")
      expect(res.reasonKey, label).toBe("compliance.kisa.reason.imgPolicyBaselineDiff")
      expect(res.detailLocalized.en, label).toContain("differs from the reviewed baseline")
    }
  })

  it("reproduction (R5): Ready=False or missing Ready=True on either policy MUST NOT pass (warn with policy not ready)", async () => {
    mockGetKisaWorkloadInventory.mockResolvedValue(makeInv())
    mockGetKisaPolicyReports.mockResolvedValue({ status: "ok", data: [] })

    const notReadyLatest = clonePolicy("disallow-latest-tag", false)
    notReadyLatest.status = { conditions: [{ type: "Ready", status: "False" }] }
    const readyVerify = clonePolicy("verify-image-signatures", true)

    mockGetKisaKyvernoClusterPolicies.mockResolvedValue({ status: "ok", data: [notReadyLatest, readyVerify] })
    const r1 = await checkImg02()
    expect(r1.status).toBe("warn")
    expect(r1.reasonKey).toBe("compliance.kisa.reason.imgPolicyNotReady")
    expect(r1.detailLocalized.en).toContain("not ready (Ready!=True)")

    const readyLatest = clonePolicy("disallow-latest-tag", true)
    const noCondVerify = clonePolicy("verify-image-signatures", false)
    mockGetKisaKyvernoClusterPolicies.mockResolvedValue({ status: "ok", data: [readyLatest, noCondVerify] })
    const r2 = await checkImg02()
    expect(r2.status).toBe("warn")
    expect(r2.reasonKey).toBe("compliance.kisa.reason.imgPolicyNotReady")
  })

  it("fails on active current violation or :latest tag, and degrades to warn/manual on 403", async () => {
    mockGetKisaKyvernoClusterPolicies.mockResolvedValue({
      status: "ok",
      data: [clonePolicy("disallow-latest-tag", true), clonePolicy("verify-image-signatures", true)],
    })
    mockGetKisaWorkloadInventory.mockResolvedValue(makeInv())
    mockGetKisaPolicyReports.mockResolvedValue({
      status: "ok",
      data: [{
        metadata: { name: "r-active", namespace: "devtools" },
        scope: { kind: "Deployment", name: "narwhal-portal", namespace: "devtools" },
        results: [{ policy: "verify-image-signatures", result: "fail", message: "failed to verify image ghcr.io/dasomel/narwhal-portal:1.1.24: bad sig" }],
      }],
    })
    expect((await checkImg02()).status).toBe("fail")

    // PolicyReports 403 -> warn (not evaluated, partial=true, never pass)
    mockGetKisaPolicyReports.mockResolvedValue({ status: "forbidden", resource: "policyreports.wgpolicyk8s.io" })
    const prForb = await checkImg02()
    expect(prForb.status).toBe("warn")
    expect(prForb.partial).toBe(true)
    expect(prForb.reasonKey).toBe("compliance.kisa.reason.imgPolicyReportsNotEvaluated")

    // ClusterPolicy 403 -> manual
    mockGetKisaKyvernoClusterPolicies.mockResolvedValue({ status: "forbidden", resource: "clusterpolicies.kyverno.io" })
    expect((await checkImg02()).status).toBe("manual")
  })

  it("parses registry host:port, no-tag, @sha256, and tag@sha256 images in IMG-02 without dropping in-scope violations", async () => {
    const digest = "sha256:" + "a".repeat(64)
    const customVerifyPol: KisaRawClusterPolicy = {
      metadata: { name: "verify-image-signatures" },
      status: { conditions: [{ type: "Ready", status: "True" }] },
      spec: {
        validationFailureAction: "Enforce",
        background: true,
        rules: [{
          name: "verify-sig",
          match: { any: [{ resources: { kinds: ["Pod"] } }] },
          verifyImages: [{
            imageReferences: ["registry.example:5000/team/*", "localhost:5000/*", "repo@*", "repo:tag@*"],
            required: true,
            attestors: [{ count: 1, entries: [{ keys: { publicKeys: "-----BEGIN PUBLIC KEY-----\nMFkw\n-----END PUBLIC KEY-----" } }] }],
          }],
        }],
      },
    }
    const customBaseline: KisaPolicyBaseline = {
      ...KISA_POLICY_BASELINE,
      policies: {
        ...KISA_POLICY_BASELINE.policies,
        "verify-image-signatures": {
          name: "verify-image-signatures",
          role: "verify-images",
          specSha256: computeKyvernoSpecDigest(customVerifyPol.spec),
          validationFailureAction: "Enforce",
          hasOverrides: false,
          imageReferences: ["registry.example:5000/team/*", "localhost:5000/*", "repo@*", "repo:tag@*"],
          required: true,
          enforceEligible: false,
          knownLimitations: ["narrow_imageReferences_scope"],
        },
      },
    }
    mockGetKisaKyvernoClusterPolicies.mockResolvedValue({
      status: "ok",
      data: [clonePolicy("disallow-latest-tag", true), customVerifyPol],
    })
    mockGetKisaWorkloadInventory.mockResolvedValue(makeInv({
      pods: {
        status: "ok",
        data: [
          { metadata: { name: "app-port", namespace: "default" }, spec: { containers: [{ image: "registry.example:5000/team/app:v1" }] }, status: { phase: "Running" } },
          { metadata: { name: "app-dig", namespace: "default" }, spec: { containers: [{ image: `repo@${digest}` }] }, status: { phase: "Running" } },
          { metadata: { name: "app-tagdig", namespace: "default" }, spec: { containers: [{ image: `repo:tag@${digest}` }] }, status: { phase: "Running" } },
        ],
      },
    }))

    for (const ref of [
      "registry.example:5000/team/app:v1",
      "localhost:5000/app",
      `repo@${digest}`,
      `repo:tag@${digest}`,
      "unparseable::bad@image",
    ]) {
      mockGetKisaPolicyReports.mockResolvedValue({
        status: "ok",
        data: [{
          metadata: { name: "rep-1", namespace: "default" },
          scope: { kind: "Pod", name: "app-port", namespace: "default" },
          results: [{ policy: "verify-image-signatures", result: "fail", message: `failed to verify image ${ref}: no signature` }],
        }],
      })
      const r = await checkImg02(customBaseline)
      expect(r.status, `IMG-02 should fail for in-scope/unparseable ${ref}`).toBe("fail")
      expect(r.detail).toContain("현재 위반 1건")
    }

    // Untaggable localhost:5000/app on Pod must also trigger :latest/untagged violation (never dropped)
    mockGetKisaWorkloadInventory.mockResolvedValue(makeInv({
      pods: {
        status: "ok",
        data: [{ metadata: { name: "app-notag", namespace: "default" }, spec: { containers: [{ image: "localhost:5000/app" }] }, status: { phase: "Running" } }],
      },
    }))
    mockGetKisaPolicyReports.mockResolvedValue({ status: "ok", data: [] })
    const untaggedRes = await checkImg02(customBaseline)
    expect(untaggedRes.status).toBe("fail")
    expect(untaggedRes.detail).toContain(":latest 1/1건")
  })

  it("never returns pass when any IMG-02 workload list is forbidden/error/truncated and names the failing source", async () => {
    mockGetKisaKyvernoClusterPolicies.mockResolvedValue({
      status: "ok",
      data: [clonePolicy("disallow-latest-tag", true), clonePolicy("verify-image-signatures", true)],
    })
    mockGetKisaPolicyReports.mockResolvedValue({ status: "ok", data: [] })
    mockGetKisaWorkloadInventory.mockResolvedValue(makeInv({
      deployments: { status: "forbidden", resource: "deployments.apps" },
      cronjobs: { status: "truncated", resource: "cronjobs.batch" },
    }))
    const r = await checkImg02()
    expect(r.status).toBe("warn")
    expect(r.partial).toBe(true)
    expect(r.partialSources).toEqual(["deployments(forbidden)", "cronjobs(truncated)"])
    expect(r.reasonKey).toBe("compliance.kisa.reason.imgInventoryNotEvaluated")
    expect(r.detail).toContain("deployments(forbidden)")
    expect(r.detailLocalized.en).toContain("cronjobs(truncated)")
  })
})

describe("KISA-RBAC-02 RBAC rule semantics, truth-table, and exact acceptance consistency", () => {
  it.each<{
    name: string
    rule: RbacPolicyRule
    apiGroup: string
    resource: string
    verb: string
    expected: boolean
  }>([
    { name: "exact match core group", rule: { apiGroups: [""], resources: ["users"], verbs: ["impersonate"] }, apiGroup: "", resource: "users", verb: "impersonate", expected: true },
    { name: "wildcard all in same rule", rule: { apiGroups: ["*"], resources: ["*"], verbs: ["*"] }, apiGroup: "authentication.k8s.io", resource: "userextras/scopes", verb: "impersonate", expected: true },
    { name: "read-only wildcard does not grant impersonate", rule: { apiGroups: ["*"], resources: ["*"], verbs: ["get", "list", "watch"] }, apiGroup: "", resource: "users", verb: "impersonate", expected: false },
    { name: "read-only wildcard grants secrets get", rule: { apiGroups: ["*"], resources: ["*"], verbs: ["get", "list", "watch"] }, apiGroup: "", resource: "secrets", verb: "get", expected: true },
    { name: "missing apiGroups (nonResourceURLs) does not match core group", rule: { resources: ["*"], verbs: ["*"] }, apiGroup: "", resource: "secrets", verb: "get", expected: false },
    { name: "pods does not grant pods/exec", rule: { apiGroups: [""], resources: ["pods"], verbs: ["create"] }, apiGroup: "", resource: "pods/exec", verb: "create", expected: false },
    { name: "pods/* does not grant pods/exec per upstream ResourceMatches", rule: { apiGroups: [""], resources: ["pods/*"], verbs: ["create"] }, apiGroup: "", resource: "pods/exec", verb: "create", expected: false },
    { name: "pods/* does not grant bare pods", rule: { apiGroups: [""], resources: ["pods/*"], verbs: ["create"] }, apiGroup: "", resource: "pods", verb: "create", expected: false },
    { name: "*/exec grants pods/exec", rule: { apiGroups: [""], resources: ["*/exec"], verbs: ["create"] }, apiGroup: "", resource: "pods/exec", verb: "create", expected: true },
    { name: "uids in authentication.k8s.io grants impersonate", rule: { apiGroups: ["authentication.k8s.io"], resources: ["uids"], verbs: ["impersonate"] }, apiGroup: "authentication.k8s.io", resource: "uids", verb: "impersonate", expected: true },
    { name: "uids in core group does not grant authentication.k8s.io uids", rule: { apiGroups: [""], resources: ["uids"], verbs: ["impersonate"] }, apiGroup: "authentication.k8s.io", resource: "uids", verb: "impersonate", expected: false },
    { name: "userextras/scopes matches userextras/* query", rule: { apiGroups: ["authentication.k8s.io"], resources: ["userextras/scopes"], verbs: ["impersonate"] }, apiGroup: "authentication.k8s.io", resource: "userextras/*", verb: "impersonate", expected: true },
    { name: "userextras/* grants literal '*' extra key per upstream impersonation.go + ResourceMatches", rule: { apiGroups: ["authentication.k8s.io"], resources: ["userextras/*"], verbs: ["impersonate"] }, apiGroup: "authentication.k8s.io", resource: "userextras/*", verb: "impersonate", expected: true },
    { name: "*/* grants literal '*' extra key per upstream impersonation.go + ResourceMatches", rule: { apiGroups: ["authentication.k8s.io"], resources: ["*/*"], verbs: ["impersonate"] }, apiGroup: "authentication.k8s.io", resource: "userextras/*", verb: "impersonate", expected: true },
    { name: "*/* does not grant concrete userextras/scopes per upstream ResourceMatches", rule: { apiGroups: ["authentication.k8s.io"], resources: ["*/*"], verbs: ["impersonate"] }, apiGroup: "authentication.k8s.io", resource: "userextras/scopes", verb: "impersonate", expected: false },
    { name: "userextras/* does not grant userextras/scopes per upstream ResourceMatches", rule: { apiGroups: ["authentication.k8s.io"], resources: ["userextras/*"], verbs: ["impersonate"] }, apiGroup: "authentication.k8s.io", resource: "userextras/scopes", verb: "impersonate", expected: false },
    { name: "*/scopes matches userextras/* query", rule: { apiGroups: ["authentication.k8s.io"], resources: ["*/scopes"], verbs: ["impersonate"] }, apiGroup: "authentication.k8s.io", resource: "userextras/*", verb: "impersonate", expected: true },
    { name: "bare userextras without subresource does not match userextras/*", rule: { apiGroups: ["authentication.k8s.io"], resources: ["userextras"], verbs: ["impersonate"] }, apiGroup: "authentication.k8s.io", resource: "userextras/*", verb: "impersonate", expected: false },
    { name: "userextras/scopes with get verb does not grant impersonate", rule: { apiGroups: ["authentication.k8s.io"], resources: ["userextras/scopes"], verbs: ["get"] }, apiGroup: "authentication.k8s.io", resource: "userextras/*", verb: "impersonate", expected: false },
    { name: "resource literal named impersonate does not grant users impersonate", rule: { apiGroups: [""], resources: ["impersonate"], verbs: ["get"] }, apiGroup: "", resource: "users", verb: "impersonate", expected: false },
    { name: "*/* does not match bare resource or wildcard subresource", rule: { apiGroups: ["*"], resources: ["*/*"], verbs: ["*"] }, apiGroup: "", resource: "pods", verb: "create", expected: false },
    { name: "nonResourceURLs-only rule with verbs:['*'] does not match resource request", rule: { nonResourceURLs: ["/healthz"], verbs: ["*"] }, apiGroup: "", resource: "pods", verb: "create", expected: false },
  ])("ruleGrants truth table: $name", ({ rule, apiGroup, resource, verb, expected }) => {
    expect(ruleGrants(rule, apiGroup, resource, verb)).toBe(expected)
  })

  it.each<{
    name: string
    rules: RbacPolicyRule[]
    criticalWildcard: boolean
    escalation: boolean
    writeAccess: boolean
    secretsAccess: boolean
    anyWildcard?: boolean
    includeIds: string[]
    excludeIds: string[]
  }>([
    {
      name: "istiod cross-rule * pattern is not criticalWildcard",
      rules: [
        { apiGroups: ["networking.istio.io"], resources: ["*"], verbs: ["get", "list", "watch"] },
        { apiGroups: ["networking.k8s.io"], resources: ["ingresses/status"], verbs: ["*"] },
      ],
      criticalWildcard: false,
      escalation: false,
      writeAccess: true,
      secretsAccess: false,
      includeIds: ["KSV046"],
      excludeIds: ["KSV044", "KSV050", "KSV053"],
    },
    {
      name: "CRD-only wildcard is not criticalWildcard",
      rules: [{ apiGroups: ["chaos-mesh.org"], resources: ["*"], verbs: ["*"] }],
      criticalWildcard: false,
      escalation: false,
      writeAccess: true,
      secretsAccess: false,
      includeIds: ["KSV046"],
      excludeIds: ["KSV044", "KSV050", "KSV053"],
    },
    {
      name: "full wildcard in single rule is criticalWildcard",
      rules: [{ apiGroups: ["*"], resources: ["*"], verbs: ["*"] }],
      criticalWildcard: true,
      escalation: true,
      writeAccess: true,
      secretsAccess: true,
      includeIds: ["KSV041", "KSV044", "KSV046", "KSV048", "KSV049", "KSV050", "KSV053", "KSV056", "KSV114"],
      excludeIds: [],
    },
    {
      name: "read-only wildcard (*, *, get/list/watch) is NOT escalation and NOT criticalWildcard",
      rules: [{ apiGroups: ["*"], resources: ["*"], verbs: ["get", "list", "watch"] }],
      criticalWildcard: false,
      escalation: false,
      writeAccess: false,
      secretsAccess: true,
      includeIds: ["KSV041", "KSV046"],
      excludeIds: ["KSV044", "KSV050", "KSV053"],
    },
    {
      name: "verbs:['*'] on rbac.authorization.k8s.io/roles is escalation",
      rules: [{ apiGroups: ["rbac.authorization.k8s.io"], resources: ["roles"], verbs: ["*"] }],
      criticalWildcard: false,
      escalation: true,
      writeAccess: true,
      secretsAccess: false,
      includeIds: ["KSV049", "KSV050"],
      excludeIds: ["KSV053"],
    },
    {
      name: "explicit escalate on clusterroles is escalation",
      rules: [{ apiGroups: ["rbac.authorization.k8s.io"], resources: ["clusterroles"], verbs: ["escalate"] }],
      criticalWildcard: false,
      escalation: true,
      writeAccess: false,
      secretsAccess: false,
      includeIds: ["KSV050"],
      excludeIds: ["KSV053"],
    },
    {
      name: "explicit bind on rolebindings is NOT escalation (bind/escalate apply only to roles/clusterroles)",
      rules: [{ apiGroups: ["rbac.authorization.k8s.io"], resources: ["rolebindings"], verbs: ["bind"] }],
      criticalWildcard: false,
      escalation: false,
      writeAccess: false,
      secretsAccess: false,
      anyWildcard: false,
      includeIds: [],
      excludeIds: ["KSV049", "KSV050", "KSV053"],
    },
    {
      name: "bind/escalate on clusterrolebindings is NOT escalation",
      rules: [{ apiGroups: ["rbac.authorization.k8s.io"], resources: ["clusterrolebindings"], verbs: ["bind", "escalate"] }],
      criticalWildcard: false,
      escalation: false,
      writeAccess: false,
      secretsAccess: false,
      anyWildcard: false,
      includeIds: [],
      excludeIds: ["KSV049", "KSV050", "KSV053"],
    },
    {
      name: "explicit bind on roles is escalation KSV050",
      rules: [{ apiGroups: ["rbac.authorization.k8s.io"], resources: ["roles"], verbs: ["bind"] }],
      criticalWildcard: false,
      escalation: true,
      writeAccess: false,
      secretsAccess: false,
      includeIds: ["KSV050"],
      excludeIds: ["KSV053"],
    },
    {
      name: "create/update/patch on rolebindings/clusterrolebindings is writeAccess KSV049 (not escalation)",
      rules: [{ apiGroups: ["rbac.authorization.k8s.io"], resources: ["rolebindings", "clusterrolebindings"], verbs: ["create", "update", "patch"] }],
      criticalWildcard: false,
      escalation: false,
      writeAccess: true,
      secretsAccess: false,
      includeIds: ["KSV049"],
      excludeIds: ["KSV050", "KSV053"],
    },
    {
      name: "impersonate on core users/groups/serviceaccounts is escalation KSV053",
      rules: [{ apiGroups: [""], resources: ["users", "groups", "serviceaccounts"], verbs: ["impersonate"] }],
      criticalWildcard: false,
      escalation: true,
      writeAccess: false,
      secretsAccess: false,
      includeIds: ["KSV053"],
      excludeIds: ["KSV050"],
    },
    {
      name: "impersonate on authentication.k8s.io uids is escalation KSV053",
      rules: [{ apiGroups: ["authentication.k8s.io"], resources: ["uids"], verbs: ["impersonate"] }],
      criticalWildcard: false,
      escalation: true,
      writeAccess: false,
      secretsAccess: false,
      includeIds: ["KSV053"],
      excludeIds: ["KSV050"],
    },
    {
      name: "impersonate on authentication.k8s.io userextras/scopes is escalation KSV053",
      rules: [{ apiGroups: ["authentication.k8s.io"], resources: ["userextras/scopes"], verbs: ["impersonate"] }],
      criticalWildcard: false,
      escalation: true,
      writeAccess: false,
      secretsAccess: false,
      includeIds: ["KSV053"],
      excludeIds: ["KSV050"],
    },
    {
      name: "impersonate on authentication.k8s.io userextras/* (literal '*' extra key) is escalation KSV053",
      rules: [{ apiGroups: ["authentication.k8s.io"], resources: ["userextras/*"], verbs: ["impersonate"] }],
      criticalWildcard: false,
      escalation: true,
      writeAccess: false,
      secretsAccess: false,
      includeIds: ["KSV053"],
      excludeIds: ["KSV050"],
    },
    {
      name: "impersonate on authentication.k8s.io */* (literal '*' extra key) is escalation KSV053",
      rules: [{ apiGroups: ["authentication.k8s.io"], resources: ["*/*"], verbs: ["impersonate"] }],
      criticalWildcard: false,
      escalation: true,
      writeAccess: false,
      secretsAccess: false,
      includeIds: ["KSV053"],
      excludeIds: ["KSV050"],
    },
    {
      name: "impersonate on core uids (wrong apiGroup) is NOT flagged",
      rules: [{ apiGroups: [""], resources: ["uids"], verbs: ["impersonate"] }],
      criticalWildcard: false,
      escalation: false,
      writeAccess: false,
      secretsAccess: false,
      includeIds: [],
      excludeIds: ["KSV053"],
    },
    {
      name: "lookalike resources/verbs in unrelated apiGroup are NOT flagged",
      rules: [{ apiGroups: ["custom.example.io"], resources: ["roles", "secrets", "deployments", "users", "uids", "userextras/scopes"], verbs: ["*", "bind", "escalate", "impersonate"] }],
      criticalWildcard: false,
      escalation: false,
      writeAccess: true,
      secretsAccess: false,
      includeIds: ["KSV046"],
      excludeIds: ["KSV041", "KSV048", "KSV049", "KSV050", "KSV053", "KSV056", "KSV114"],
    },
    {
      name: "pods/exec create triggers KSV048 writeAccess",
      rules: [{ apiGroups: [""], resources: ["pods/exec"], verbs: ["create"] }],
      criticalWildcard: false,
      escalation: false,
      writeAccess: true,
      secretsAccess: false,
      includeIds: ["KSV048"],
      excludeIds: ["KSV050", "KSV053"],
    },
    {
      name: "pods/* create does NOT trigger KSV048 for pods/exec per upstream ResourceMatches",
      rules: [{ apiGroups: [""], resources: ["pods/*"], verbs: ["create"] }],
      criticalWildcard: false,
      escalation: false,
      writeAccess: true,
      secretsAccess: false,
      anyWildcard: false,
      includeIds: [],
      excludeIds: ["KSV048", "KSV050", "KSV053"],
    },
    {
      name: "*/exec create triggers KSV048 for pods/exec per upstream ResourceMatches",
      rules: [{ apiGroups: [""], resources: ["*/exec"], verbs: ["create"] }],
      criticalWildcard: false,
      escalation: false,
      writeAccess: true,
      secretsAccess: false,
      includeIds: ["KSV048"],
      excludeIds: ["KSV050", "KSV053"],
    },
    {
      name: "nonResourceURLs-only rule with verbs:['*'] does not trigger any resource category",
      rules: [{ nonResourceURLs: ["/healthz"], verbs: ["*"] }],
      criticalWildcard: false,
      escalation: false,
      writeAccess: false,
      secretsAccess: false,
      anyWildcard: false,
      includeIds: [],
      excludeIds: ["KSV041", "KSV044", "KSV046", "KSV048", "KSV049", "KSV050", "KSV053", "KSV056", "KSV114"],
    },
    {
      name: "nonResourceURLs-only rule with verbs:['create'] does not trigger writeAccess",
      rules: [{ nonResourceURLs: ["/healthz"], verbs: ["create"] }],
      criticalWildcard: false,
      escalation: false,
      writeAccess: false,
      secretsAccess: false,
      anyWildcard: false,
      includeIds: [],
      excludeIds: ["KSV046", "KSV048", "KSV049", "KSV050", "KSV053"],
    },
    {
      name: "nonResourceURLs rule paired with resource rule still evaluates the resource rule",
      rules: [
        { nonResourceURLs: ["/healthz"], verbs: ["*"] },
        { apiGroups: [""], resources: ["secrets"], verbs: ["get"] },
      ],
      criticalWildcard: false,
      escalation: false,
      writeAccess: false,
      secretsAccess: true,
      anyWildcard: false,
      includeIds: ["KSV041"],
      excludeIds: ["KSV046", "KSV049", "KSV050"],
    },
    {
      name: "resourceNames-scoped impersonate stays fail-closed (single named admin can still escalate)",
      rules: [{ apiGroups: [""], resources: ["users"], resourceNames: ["system:admin"], verbs: ["impersonate"] }],
      criticalWildcard: false,
      escalation: true,
      writeAccess: false,
      secretsAccess: false,
      includeIds: ["KSV053"],
      excludeIds: [],
    },
    {
      name: "resourceNames-scoped webhook write stays fail-closed (matches metallb:controller KSV114)",
      rules: [{ apiGroups: ["admissionregistration.k8s.io"], resources: ["validatingwebhookconfigurations"], resourceNames: ["metallb-webhook-configuration"], verbs: ["update"] }],
      criticalWildcard: false,
      escalation: false,
      writeAccess: true,
      secretsAccess: false,
      includeIds: ["KSV114"],
      excludeIds: ["KSV050", "KSV053"],
    },
  ])("evaluateRbacRules: $name", ({ rules, criticalWildcard, escalation, writeAccess, secretsAccess, anyWildcard, includeIds, excludeIds }) => {
    const ev = evaluateRbacRules(rules)
    expect(ev.criticalWildcard).toBe(criticalWildcard)
    expect(ev.escalation).toBe(escalation)
    expect(ev.writeAccess).toBe(writeAccess)
    expect(ev.secretsAccess).toBe(secretsAccess)
    if (anyWildcard !== undefined) expect(ev.anyWildcard).toBe(anyWildcard)
    for (const id of includeIds) expect(ev.triggeredCheckIds).toContain(id)
    for (const id of excludeIds) expect(ev.triggeredCheckIds).not.toContain(id)
  })

  it.each<{
    name: string
    snapshot: KisaRbacSnapshot
    expectedStatus: "pass" | "warn" | "fail"
    detailContains?: string
  }>([
    {
      name: "read-only wildcard Role + RoleBinding is NOT escalation -> pass",
      snapshot: {
        clusterRoleBindings: { status: "ok", data: [] },
        roleBindings: {
          status: "ok",
          data: [{ metadata: { name: "readonly-all", namespace: "dev" }, roleRef: { kind: "Role", name: "readonly-all" }, subjects: [{ kind: "ServiceAccount", name: "app", namespace: "dev" }] }],
        },
        clusterRoles: { status: "ok", data: [] },
        roles: {
          status: "ok",
          data: [{ metadata: { name: "readonly-all", namespace: "dev" }, rules: [{ apiGroups: ["*"], resources: ["*"], verbs: ["get", "list", "watch"] }] }],
        },
      },
      expectedStatus: "pass",
    },
    {
      name: "read-only wildcard ClusterRole + ClusterRoleBinding is High warn, NOT Critical fail",
      snapshot: {
        clusterRoleBindings: {
          status: "ok",
          data: [{ metadata: { name: "cluster-readonly-all" }, roleRef: { kind: "ClusterRole", name: "cluster-readonly-all" }, subjects: [{ kind: "ServiceAccount", name: "app", namespace: "dev" }] }],
        },
        roleBindings: { status: "ok", data: [] },
        clusterRoles: {
          status: "ok",
          data: [{ metadata: { name: "cluster-readonly-all" }, rules: [{ apiGroups: ["*"], resources: ["*"], verbs: ["get", "list", "watch"] }] }],
        },
        roles: { status: "ok", data: [] },
      },
      expectedStatus: "warn",
      detailContains: "High 1건 (Critical 0건)",
    },
    {
      name: "impersonate on authentication.k8s.io uids fails RBAC-02 Critical",
      snapshot: {
        clusterRoleBindings: {
          status: "ok",
          data: [{ metadata: { name: "uid-impersonator" }, roleRef: { kind: "ClusterRole", name: "uid-impersonator" }, subjects: [{ kind: "ServiceAccount", name: "app", namespace: "dev" }] }],
        },
        roleBindings: { status: "ok", data: [] },
        clusterRoles: {
          status: "ok",
          data: [{ metadata: { name: "uid-impersonator" }, rules: [{ apiGroups: ["authentication.k8s.io"], resources: ["uids"], verbs: ["impersonate"] }] }],
        },
        roles: { status: "ok", data: [] },
      },
      expectedStatus: "fail",
      detailContains: "uid-impersonator",
    },
    {
      name: "impersonate on authentication.k8s.io userextras/scopes fails RBAC-02 Critical",
      snapshot: {
        clusterRoleBindings: {
          status: "ok",
          data: [{ metadata: { name: "extras-impersonator" }, roleRef: { kind: "ClusterRole", name: "extras-impersonator" }, subjects: [{ kind: "ServiceAccount", name: "app", namespace: "dev" }] }],
        },
        roleBindings: { status: "ok", data: [] },
        clusterRoles: {
          status: "ok",
          data: [{ metadata: { name: "extras-impersonator" }, rules: [{ apiGroups: ["authentication.k8s.io"], resources: ["userextras/scopes"], verbs: ["impersonate"] }] }],
        },
        roles: { status: "ok", data: [] },
      },
      expectedStatus: "fail",
      detailContains: "extras-impersonator",
    },
    {
      name: "namespaced Role + RoleBinding granting verbs:['*'] on rbac.authorization.k8s.io/roles fails RBAC-02 Critical",
      snapshot: {
        clusterRoleBindings: { status: "ok", data: [] },
        roleBindings: {
          status: "ok",
          data: [{ metadata: { name: "team-rbac-admin", namespace: "dev" }, roleRef: { kind: "Role", name: "team-role-star" }, subjects: [{ kind: "ServiceAccount", name: "app", namespace: "dev" }] }],
        },
        clusterRoles: { status: "ok", data: [] },
        roles: {
          status: "ok",
          data: [{ metadata: { name: "team-role-star", namespace: "dev" }, rules: [{ apiGroups: ["rbac.authorization.k8s.io"], resources: ["roles"], verbs: ["*"] }] }],
        },
      },
      expectedStatus: "fail",
      detailContains: "dev/team-rbac-admin",
    },
    {
      name: "bind/escalate on (cluster)rolebindings alone passes RBAC-02 (no Critical escalation false positive)",
      snapshot: {
        clusterRoleBindings: {
          status: "ok",
          data: [{ metadata: { name: "binding-verb-only" }, roleRef: { kind: "ClusterRole", name: "binding-verb-only" }, subjects: [{ kind: "ServiceAccount", name: "app", namespace: "dev" }] }],
        },
        roleBindings: {
          status: "ok",
          data: [{ metadata: { name: "ns-binding-verb-only", namespace: "dev" }, roleRef: { kind: "Role", name: "ns-binding-verb-only" }, subjects: [{ kind: "ServiceAccount", name: "app", namespace: "dev" }] }],
        },
        clusterRoles: {
          status: "ok",
          data: [{ metadata: { name: "binding-verb-only" }, rules: [{ apiGroups: ["rbac.authorization.k8s.io"], resources: ["clusterrolebindings", "rolebindings"], verbs: ["bind", "escalate"] }] }],
        },
        roles: {
          status: "ok",
          data: [{ metadata: { name: "ns-binding-verb-only", namespace: "dev" }, rules: [{ apiGroups: ["rbac.authorization.k8s.io"], resources: ["rolebindings"], verbs: ["bind", "escalate"] }] }],
        },
      },
      expectedStatus: "pass",
    },
    {
      name: "nonResourceURLs:['/healthz'] with verbs:['*'] ClusterRoleBinding passes RBAC-02 (no High warn false positive)",
      snapshot: {
        clusterRoleBindings: {
          status: "ok",
          data: [{ metadata: { name: "healthz-reader" }, roleRef: { kind: "ClusterRole", name: "healthz-reader" }, subjects: [{ kind: "ServiceAccount", name: "probe", namespace: "monitoring" }] }],
        },
        roleBindings: { status: "ok", data: [] },
        clusterRoles: {
          status: "ok",
          data: [{ metadata: { name: "healthz-reader" }, rules: [{ nonResourceURLs: ["/healthz"], verbs: ["*"] }] }],
        },
        roles: { status: "ok", data: [] },
      },
      expectedStatus: "pass",
    },
  ])("checkRbacLive: $name", async ({ snapshot, expectedStatus, detailContains }) => {
    mockGetKisaRbacSnapshot.mockResolvedValue(snapshot)
    const res = await checkRbacLive("KISA-RBAC-02")
    expect(res.status).toBe(expectedStatus)
    if (detailContains) expect(res.detail).toContain(detailContains)
  })

  it("uses exact compliance-acceptance-rules.json (pass when only accepted High exists, fail on unaccepted Critical, manual on 403)", async () => {
    // Only accepted platform-admin binding -> pass
    mockGetKisaRbacSnapshot.mockResolvedValue({
      clusterRoleBindings: {
        status: "ok",
        data: [{ metadata: { name: "oidc-cluster-admin" }, roleRef: { kind: "ClusterRole", name: "platform-admin" }, subjects: [{ kind: "Group", name: "oidc:admin" }] }],
      },
      roleBindings: { status: "ok", data: [] },
      clusterRoles: {
        status: "ok",
        data: [{ metadata: { name: "platform-admin" }, rules: [{ apiGroups: [""], resources: ["secrets"], verbs: ["get", "list"] }] }],
      },
      roles: { status: "ok", data: [] },
    })
    const passRes = await checkRbacLive("KISA-RBAC-02")
    expect(passRes.status).toBe("pass")
    expect(passRes.detail).toContain("정확 수용 1건")

    // Unaccepted Critical binding -> fail
    mockGetKisaRbacSnapshot.mockResolvedValue({
      clusterRoleBindings: {
        status: "ok",
        data: [{ metadata: { name: "argocd-application-controller" }, roleRef: { kind: "ClusterRole", name: "argocd-application-controller" }, subjects: [{ kind: "ServiceAccount", name: "argocd-application-controller", namespace: "devtools" }] }],
      },
      roleBindings: { status: "ok", data: [] },
      clusterRoles: {
        status: "ok",
        data: [{ metadata: { name: "argocd-application-controller" }, rules: [{ apiGroups: ["*"], resources: ["*"], verbs: ["*"] }] }],
      },
      roles: { status: "ok", data: [] },
    })
    const failRes = await checkRbacLive("KISA-RBAC-02")
    expect(failRes.status).toBe("fail")
    expect(failRes.detail).toContain("argocd-application-controller")

    // Forbidden -> manual (never false pass)
    mockGetKisaRbacSnapshot.mockResolvedValue({
      clusterRoleBindings: { status: "forbidden", resource: "clusterrolebindings.rbac.authorization.k8s.io" },
      roleBindings: { status: "ok", data: [] },
      clusterRoles: { status: "ok", data: [] },
      roles: { status: "ok", data: [] },
    })
    const forbRes = await checkRbacLive("KISA-RBAC-02")
    expect(forbRes.status).toBe("manual")
    expect(forbRes.partial).toBe(true)
  })

  it("reproduction (R5): flags system:custom-dangerous binding granting users/impersonate to a normal SA and requires exact bootstrap metadata", async () => {
    mockGetKisaRbacSnapshot.mockResolvedValue({
      clusterRoleBindings: {
        status: "ok",
        data: [{
          metadata: { name: "system:custom-dangerous" },
          roleRef: { kind: "ClusterRole", name: "custom-impersonator" },
          subjects: [{ kind: "ServiceAccount", name: "app", namespace: "dev" }],
        }],
      },
      roleBindings: { status: "ok", data: [] },
      clusterRoles: {
        status: "ok",
        data: [{
          metadata: { name: "custom-impersonator" },
          rules: [{ apiGroups: [""], resources: ["users"], verbs: ["impersonate"] }],
        }],
      },
      roles: { status: "ok", data: [] },
    })
    const dangerousRes = await checkRbacLive("KISA-RBAC-02")
    expect(dangerousRes.status).toBe("fail")
    expect(dangerousRes.detail).toContain("system:custom-dangerous")

    // Real Kubernetes bootstrap binding WITH bootstrapping=rbac-defaults & autoupdate=true -> exact-accepted
    mockGetKisaRbacSnapshot.mockResolvedValue({
      clusterRoleBindings: {
        status: "ok",
        data: [{
          metadata: {
            name: "system:controller:clusterrole-aggregation-controller",
            labels: { "kubernetes.io/bootstrapping": "rbac-defaults" },
            annotations: { "rbac.authorization.kubernetes.io/autoupdate": "true" },
          },
          roleRef: { kind: "ClusterRole", name: "system:controller:clusterrole-aggregation-controller" },
          subjects: [{ kind: "ServiceAccount", name: "clusterrole-aggregation-controller", namespace: "kube-system" }],
        }],
      },
      roleBindings: { status: "ok", data: [] },
      clusterRoles: {
        status: "ok",
        data: [{
          metadata: { name: "system:controller:clusterrole-aggregation-controller" },
          rules: [{ apiGroups: ["rbac.authorization.k8s.io"], resources: ["clusterroles"], verbs: ["escalate", "get", "list", "patch", "update", "watch"] }],
        }],
      },
      roles: { status: "ok", data: [] },
    })
    const bootPass = await checkRbacLive("KISA-RBAC-02")
    expect(bootPass.status).toBe("pass")
    expect(bootPass.detail).toContain("정확 수용 1건")

    // Missing autoupdate=true on the same binding name -> NOT accepted (fail)
    mockGetKisaRbacSnapshot.mockResolvedValue({
      clusterRoleBindings: {
        status: "ok",
        data: [{
          metadata: {
            name: "system:controller:clusterrole-aggregation-controller",
            labels: { "kubernetes.io/bootstrapping": "rbac-defaults" },
          },
          roleRef: { kind: "ClusterRole", name: "system:controller:clusterrole-aggregation-controller" },
          subjects: [{ kind: "ServiceAccount", name: "app", namespace: "dev" }],
        }],
      },
      roleBindings: { status: "ok", data: [] },
      clusterRoles: {
        status: "ok",
        data: [{
          metadata: { name: "system:controller:clusterrole-aggregation-controller" },
          rules: [{ apiGroups: ["rbac.authorization.k8s.io"], resources: ["clusterroles"], verbs: ["escalate", "get", "list", "patch", "update", "watch"] }],
        }],
      },
      roles: { status: "ok", data: [] },
    })
    expect((await checkRbacLive("KISA-RBAC-02")).status).toBe("fail")

    // RBAC-01: bootstrap cluster-admin with metadata passes; kubeadm:cluster-admins without metadata fails
    mockGetKisaRbacSnapshot.mockResolvedValue({
      clusterRoleBindings: {
        status: "ok",
        data: [
          {
            metadata: {
              name: "cluster-admin",
              labels: { "kubernetes.io/bootstrapping": "rbac-defaults" },
              annotations: { "rbac.authorization.kubernetes.io/autoupdate": "true" },
            },
            roleRef: { kind: "ClusterRole", name: "cluster-admin" },
            subjects: [{ kind: "Group", name: "system:masters" }],
          },
          {
            metadata: { name: "kubeadm:cluster-admins" },
            roleRef: { kind: "ClusterRole", name: "cluster-admin" },
            subjects: [{ kind: "Group", name: "kubeadm:cluster-admins" }],
          },
        ],
      },
      roleBindings: { status: "ok", data: [] },
      clusterRoles: { status: "ok", data: [] },
      roles: { status: "ok", data: [] },
    })
    const rbac01 = await checkRbacLive("KISA-RBAC-01")
    expect(rbac01.status).toBe("fail")
    expect(rbac01.detail).toContain("kubeadm:cluster-admins")
    expect(rbac01.detail).toContain("정확 수용 1건")
    expect(rbac01.detail).toContain("정확 규칙 미수용")
    expect(rbac01.detail).not.toContain("불필요")
    expect(rbac01.detailLocalized.en).toContain("not accepted by an exact rule")
    expect(rbac01.detailLocalized.en).not.toContain("unnecessary")
  })

  it("reproduction (R7): never excludes namespaced Role/dev/cluster-admin with roles/escalate", async () => {
    mockGetKisaRbacSnapshot.mockResolvedValue({
      clusterRoleBindings: { status: "ok", data: [] },
      roleBindings: {
        status: "ok",
        data: [{
          metadata: { name: "dev-cluster-admin-binding", namespace: "dev" },
          roleRef: { kind: "Role", name: "cluster-admin" },
          subjects: [{ kind: "ServiceAccount", name: "app", namespace: "dev" }],
        }],
      },
      clusterRoles: { status: "ok", data: [] },
      roles: {
        status: "ok",
        data: [{
          metadata: { name: "cluster-admin", namespace: "dev" },
          rules: [{ apiGroups: ["rbac.authorization.k8s.io"], resources: ["roles"], verbs: ["escalate"] }],
        }],
      },
    })
    const rbac02 = await checkRbacLive("KISA-RBAC-02")
    expect(rbac02.status).toBe("fail")
    expect(rbac02.detail).toContain("dev/dev-cluster-admin-binding")
  })

  it("reproduction (R7): flags bootstrap system:basic-user when users/impersonate or extra SA subject is added", async () => {
    // 1. Both users/impersonate AND extra SA added while preserving bootstrap metadata -> fail
    mockGetKisaRbacSnapshot.mockResolvedValue({
      clusterRoleBindings: {
        status: "ok",
        data: [{
          metadata: {
            name: "system:basic-user",
            labels: { "kubernetes.io/bootstrapping": "rbac-defaults" },
            annotations: { "rbac.authorization.kubernetes.io/autoupdate": "true" },
          },
          roleRef: { kind: "ClusterRole", name: "system:basic-user" },
          subjects: [
            { kind: "Group", name: "system:authenticated" },
            { kind: "ServiceAccount", name: "rogue", namespace: "dev" },
          ],
        }],
      },
      roleBindings: { status: "ok", data: [] },
      clusterRoles: {
        status: "ok",
        data: [{
          metadata: { name: "system:basic-user" },
          rules: [
            { apiGroups: ["authorization.k8s.io"], resources: ["selfsubjectaccessreviews"], verbs: ["create"] },
            { apiGroups: [""], resources: ["users"], verbs: ["impersonate"] },
          ],
        }],
      },
      roles: { status: "ok", data: [] },
    })
    const impersonateAndSa = await checkRbacLive("KISA-RBAC-02")
    expect(impersonateAndSa.status).toBe("fail")
    expect(impersonateAndSa.detail).toContain("system:basic-user")

    // 2. Exact subjects + bootstrap metadata, but ClusterRole grants users/impersonate (outside rule checks) -> fail
    mockGetKisaRbacSnapshot.mockResolvedValue({
      clusterRoleBindings: {
        status: "ok",
        data: [{
          metadata: {
            name: "system:basic-user",
            labels: { "kubernetes.io/bootstrapping": "rbac-defaults" },
            annotations: { "rbac.authorization.kubernetes.io/autoupdate": "true" },
          },
          roleRef: { kind: "ClusterRole", name: "system:basic-user" },
          subjects: [{ kind: "Group", name: "system:authenticated" }],
        }],
      },
      roleBindings: { status: "ok", data: [] },
      clusterRoles: {
        status: "ok",
        data: [{
          metadata: { name: "system:basic-user" },
          rules: [{ apiGroups: [""], resources: ["users"], verbs: ["impersonate"] }],
        }],
      },
      roles: { status: "ok", data: [] },
    })
    expect((await checkRbacLive("KISA-RBAC-02")).status).toBe("fail")

    // 3. Unmodified ClusterRole + bootstrap metadata, but extra SA in subjects -> warn (never accepted)
    mockGetKisaRbacSnapshot.mockResolvedValue({
      clusterRoleBindings: {
        status: "ok",
        data: [{
          metadata: {
            name: "system:controller:deployment-controller",
            labels: { "kubernetes.io/bootstrapping": "rbac-defaults" },
            annotations: { "rbac.authorization.kubernetes.io/autoupdate": "true" },
          },
          roleRef: { kind: "ClusterRole", name: "system:controller:deployment-controller" },
          subjects: [
            { kind: "ServiceAccount", name: "deployment-controller", namespace: "kube-system" },
            { kind: "ServiceAccount", name: "rogue", namespace: "dev" },
          ],
        }],
      },
      roleBindings: { status: "ok", data: [] },
      clusterRoles: {
        status: "ok",
        data: [{
          metadata: { name: "system:controller:deployment-controller" },
          rules: [{ apiGroups: ["apps"], resources: ["deployments"], verbs: ["create", "update"] }],
        }],
      },
      roles: { status: "ok", data: [] },
    })
    const extraSa = await checkRbacLive("KISA-RBAC-02")
    expect(extraSa.status).toBe("warn")
    expect(extraSa.detail).toContain("High 1건")

    // 4. Positive case: exact bootstrap metadata, subjects, and checks -> pass
    mockGetKisaRbacSnapshot.mockResolvedValue({
      clusterRoleBindings: {
        status: "ok",
        data: [{
          metadata: {
            name: "system:basic-user",
            labels: { "kubernetes.io/bootstrapping": "rbac-defaults" },
            annotations: { "rbac.authorization.kubernetes.io/autoupdate": "true" },
          },
          roleRef: { kind: "ClusterRole", name: "system:basic-user" },
          subjects: [{ kind: "Group", name: "system:authenticated" }],
        }],
      },
      roleBindings: { status: "ok", data: [] },
      clusterRoles: {
        status: "ok",
        data: [{
          metadata: { name: "system:basic-user" },
          rules: [{ apiGroups: ["authorization.k8s.io"], resources: ["selfsubjectaccessreviews"], verbs: ["create"] }],
        }],
      },
      roles: { status: "ok", data: [] },
    })
    const cleanBoot = await checkRbacLive("KISA-RBAC-02")
    expect(cleanBoot.status).toBe("pass")
    expect(cleanBoot.detail).toContain("정확 수용 1건")
  })
})
