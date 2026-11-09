import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { getNotificationResources, patchNotificationConfig, updateNotificationSecret } from "./k8s-client"

beforeEach(() => { vi.stubEnv("K8S_API_SERVER", "http://kubernetes.test"); vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => Response.json({ metadata: { resourceVersion: "7" } }))) })
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals() })
describe("fixed-name notification Kubernetes boundary", () => {
  it("gets only the two named resources and uses PUT/PATCH with conflict preconditions", async () => {
    const resources = await getNotificationResources()
    await updateNotificationSecret({ ...resources.secret, data: {} })
    await patchNotificationConfig("7", { receivers: [{ name: "null" }] })
    const calls = vi.mocked(fetch).mock.calls
    expect(calls.map(([url]) => String(url))).toEqual([
      "http://kubernetes.test/api/v1/namespaces/monitoring/secrets/narwhal-notification-channels",
      "http://kubernetes.test/apis/monitoring.coreos.com/v1alpha1/namespaces/monitoring/alertmanagerconfigs/narwhal-notification-channels",
      "http://kubernetes.test/api/v1/namespaces/monitoring/secrets/narwhal-notification-channels",
      "http://kubernetes.test/apis/monitoring.coreos.com/v1alpha1/namespaces/monitoring/alertmanagerconfigs/narwhal-notification-channels",
    ])
    expect(calls.map(([, init]) => init?.method ?? "GET")).toEqual(["GET", "GET", "PUT", "PATCH"])
    expect(JSON.parse(calls[2][1]?.body as string).metadata.resourceVersion).toBe("7")
    expect(calls[3][1]?.headers).toMatchObject({ "Content-Type": "application/json-patch+json" })
    expect(JSON.parse(calls[3][1]?.body as string)).toEqual([
      { op: "test", path: "/metadata/resourceVersion", value: "7" },
      { op: "add", path: "/spec", value: { receivers: [{ name: "null" }] } },
    ])
  })
})
