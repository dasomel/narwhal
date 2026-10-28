import { describe, it, expect, vi, beforeEach } from "vitest"

vi.mock("server-only", () => ({}))

const mockCache = new Map<string, unknown>()
vi.mock("./valkey", () => ({
  cacheGet: vi.fn(async (key: string) => mockCache.get(key) ?? null),
  cacheSet: vi.fn(async (key: string, val: unknown) => {
    mockCache.set(key, val)
  }),
}))

vi.mock("./config", () => ({
  getDependencyUrl: (_name: string, fallback: string) => fallback,
}))

import { getRuntimeEvents } from "./falco"
import { assertLogQLSafe } from "./validation"

// Real capture, 2026-10-27 16:48 UTC: Falco 0.45.1 (modern_ebpf, container plugin 0.7.5, kernel 7.0.14) ->
// http_output -> falcosidekick 2.35.0 with the exact env the falco chart 9.2.1 renders from
// gitops/charts/narwhal-apps/templates/falco.yaml (LOKI_FORMAT=json) -> POST /loki/api/v1/push to a
// local stub. A container ran `cat /etc/shadow`. Values are verbatim. The stream labels are what
// falcosidekick's outputs/loki.go emits (byte-identical in 2.32.0 and 2.35.0, and the earlier 2.32.0
// capture had the same labels, line keys and output_fields keys): `priority` is the Capitalized name
// and `source` is the Falco event source, never "falco".
const CAPTURED_STREAM = {
  hostname: "lab-node",
  priority: "Warning",
  rule: "Read sensitive file untrusted",
  source: "syscall",
  tags: "T1555,container,filesystem,host,maturity_stable,mitre_credential_access",
}
const CAPTURED_TS_NS = "1793119706183083438"
const CAPTURED_LINE = {
  uuid: "ea83afdd-f649-4a51-abeb-7a9fbb991ad5",
  output:
    "16:48:26.183083438: Warning Sensitive file opened for reading by non-trusted program | file=/etc/shadow gparent=containerd-shim ggparent=initd gggparent=<NA> evt_type=openat user=root user_uid=0 user_loginuid=-1 process=cat proc_exepath=/bin/busybox parent=sh command=cat /etc/shadow terminal=0 container_id=aa198ad825c2 container_name=victim3 container_image_repository=alpine container_image_tag=3.20 k8s_pod_name=<NA> k8s_ns_name=<NA>",
  priority: "Warning",
  rule: "Read sensitive file untrusted",
  time: "2026-10-27T16:48:26.183083438Z",
  output_fields: {
    "container.id": "aa198ad825c2",
    "container.image.repository": "alpine",
    "container.image.tag": "3.20",
    "container.name": "victim3",
    "evt.time": 1793119706183083438,
    "evt.type": "openat",
    "fd.name": "/etc/shadow",
    "k8s.ns.name": null,
    "k8s.pod.name": null,
    "proc.aname[2]": "containerd-shim",
    "proc.aname[3]": "initd",
    "proc.aname[4]": null,
    "proc.cmdline": "cat /etc/shadow",
    "proc.exepath": "/bin/busybox",
    "proc.name": "cat",
    "proc.pname": "sh",
    "proc.tty": 0,
    "user.loginuid": -1,
    "user.name": "root",
    "user.uid": 0,
  } as Record<string, string | number | null>,
  source: "syscall",
  tags: ["T1555", "container", "filesystem", "host", "maturity_stable", "mitre_credential_access"],
  hostname: "lab-node",
}

// fetchWithPolicy -> readJsonWithPolicy reads the body through response.body, so the mock must be a
// real Response (see prometheus.test.ts).
function lokiResponse(streams: { stream: Record<string, string>; values: [string, string][] }[]): Response {
  return new Response(JSON.stringify({ status: "success", data: { resultType: "streams", result: streams } }), {
    status: 200,
    headers: { "content-type": "application/json" },
  })
}

function stubLoki(streams: { stream: Record<string, string>; values: [string, string][] }[]) {
  const fetchMock = vi.fn().mockImplementation(async () => lokiResponse(streams))
  vi.stubGlobal("fetch", fetchMock)
  return fetchMock
}

function queryOf(fetchMock: ReturnType<typeof vi.fn>): string {
  const url = new URL(String(fetchMock.mock.calls[0][0]))
  return url.searchParams.get("query") ?? ""
}

describe("getRuntimeEvents (Falco -> falcosidekick -> Loki contract)", () => {
  beforeEach(() => {
    mockCache.clear()
    vi.unstubAllGlobals()
  })

  it("selects every falcosidekick stream by the rule label when no priority is requested", async () => {
    const fetchMock = stubLoki([])
    await getRuntimeEvents({ sinceMinutes: 60, limit: 100 })
    expect(queryOf(fetchMock)).toBe('{rule=~".+"}')
  })

  it("matches the priority label whether it is Capitalized (falcosidekick) or lowercase", async () => {
    const fetchMock = stubLoki([])
    await getRuntimeEvents({ priority: "Warning", sinceMinutes: 60, limit: 100 })
    const query = queryOf(fetchMock)
    expect(query).toBe('{rule=~".+", priority=~"Warning|warning"}')
    expect(() => assertLogQLSafe(query)).not.toThrow()

    // Loki anchors =~ matchers: the label value must equal one alternative exactly.
    const matcher = /priority=~"([^"]+)"/.exec(query)?.[1]
    expect(matcher).toBeDefined()
    const anchored = new RegExp(`^(?:${matcher})$`)
    expect(anchored.test(CAPTURED_STREAM.priority)).toBe(true) // the real falcosidekick label
    expect(anchored.test("warning")).toBe(true)
    expect(anchored.test("Critical")).toBe(false)
  })

  it("builds a selector that actually selects the captured falcosidekick stream", async () => {
    const fetchMock = stubLoki([])
    await getRuntimeEvents({ priority: "Warning", sinceMinutes: 60, limit: 100 })
    const query = queryOf(fetchMock)
    // Every label the selector names must be a label falcosidekick really writes on the stream.
    const selectorLabels = [...query.matchAll(/([a-z_]+)=~?"/g)].map((m) => m[1])
    expect(selectorLabels.sort()).toEqual(["priority", "rule"])
    for (const label of selectorLabels) expect(CAPTURED_STREAM).toHaveProperty(label)
    expect(CAPTURED_STREAM.rule).toMatch(/.+/)
    // source is the Falco event source; the portal must not depend on a literal "falco".
    expect(CAPTURED_STREAM.source).toBe("syscall")
    expect(query).not.toContain("source")
  })

  it("parses the real falcosidekick JSON line: container fields present, null k8s fields absent", async () => {
    stubLoki([{ stream: CAPTURED_STREAM, values: [[CAPTURED_TS_NS, JSON.stringify(CAPTURED_LINE)]] }])
    const events = await getRuntimeEvents({ sinceMinutes: 60, limit: 100 })
    expect(events).toHaveLength(1)
    expect(events[0]).toEqual({
      id: `lab-node-${CAPTURED_TS_NS}`,
      time: new Date(Math.floor(parseInt(CAPTURED_TS_NS, 10) / 1_000_000)).toISOString(),
      priority: "Warning",
      rule: "Read sensitive file untrusted",
      output: CAPTURED_LINE.output,
      source: "syscall",
      tags: CAPTURED_LINE.tags,
      pod: undefined,
      namespace: undefined,
      container: "victim3",
      image: "alpine",
    })
  })

  it("reads pod and namespace from output_fields when Falco resolves them through the CRI socket", async () => {
    const line = {
      ...CAPTURED_LINE,
      output_fields: { ...CAPTURED_LINE.output_fields, "k8s.ns.name": "dev", "k8s.pod.name": "demo-0" },
    }
    stubLoki([{ stream: CAPTURED_STREAM, values: [[CAPTURED_TS_NS, JSON.stringify(line)]] }])
    const [event] = await getRuntimeEvents({ sinceMinutes: 60, limit: 100 })
    expect(event.namespace).toBe("dev")
    expect(event.pod).toBe("demo-0")
  })

  it("capitalizes a lowercase priority from the stream label when the line carries none", async () => {
    const { priority: _drop, ...lineWithoutPriority } = CAPTURED_LINE
    void _drop
    stubLoki([
      {
        stream: { ...CAPTURED_STREAM, priority: "critical" },
        values: [[CAPTURED_TS_NS, JSON.stringify(lineWithoutPriority)]],
      },
    ])
    const [event] = await getRuntimeEvents({ sinceMinutes: 60, limit: 100 })
    expect(event.priority).toBe("Critical")
  })

  it("drops text-format lines: falcosidekick loki.format must stay json", async () => {
    stubLoki([{ stream: CAPTURED_STREAM, values: [[CAPTURED_TS_NS, CAPTURED_LINE.output]] }])
    const events = await getRuntimeEvents({ sinceMinutes: 60, limit: 100 })
    expect(events).toEqual([])
  })
})
