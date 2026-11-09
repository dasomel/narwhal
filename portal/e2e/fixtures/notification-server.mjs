// Disposable upstream fixture; never connects to Kubernetes or real channels.
export function notificationFixture() {
  let active = false
  let unreachable = false
  let secret = { metadata: { resourceVersion: "1" }, data: {} }
  let config = { metadata: { resourceVersion: "1" }, spec: { receivers: [{ name: "null" }], route: { receiver: "null" } } }
  let tests = []
  let writes = []
  const read = async (req) => {
    const chunks = []
    for await (const chunk of req) chunks.push(chunk)
    return JSON.parse(Buffer.concat(chunks).toString())
  }
  return async (req, res, url, send) => {
    if (url.pathname === "/__test/notifications" && req.method === "POST") {
      active = true
      unreachable = url.searchParams.has("unreachable")
      secret = { metadata: { resourceVersion: "1" }, data: {} }
      config = { metadata: { resourceVersion: "1" }, spec: { receivers: [{ name: "null" }], route: { receiver: "null" } } }
      tests = []
      writes = []
      send(res, 200, { ok: true })
      return true
    }
    if (!active) return false
    if (url.pathname === "/__test/notifications") {
      send(res, 200, { config, writes, tests })
      return true
    }
    const secretPath = "/api/v1/namespaces/monitoring/secrets/narwhal-notification-channels"
    const configPath = "/apis/monitoring.coreos.com/v1alpha1/namespaces/monitoring/alertmanagerconfigs/narwhal-notification-channels"
    if (url.pathname === secretPath && req.method === "GET") send(res, 200, secret)
    else if (url.pathname === configPath && req.method === "GET") send(res, 200, config)
    else if (url.pathname === secretPath && req.method === "PUT") {
      const body = await read(req)
      if (body.metadata.resourceVersion !== secret.metadata.resourceVersion) send(res, 409, {})
      else {
        writes.push({ method: "PUT", path: secretPath })
        secret = { ...body, metadata: { resourceVersion: String(Number(secret.metadata.resourceVersion) + 1) } }
        send(res, 200, secret)
      }
    } else if (url.pathname === configPath && req.method === "PATCH") {
      const body = await read(req)
      if (body[0].op !== "test" || body[0].value !== config.metadata.resourceVersion || body[1].path !== "/spec") send(res, 409, {})
      else {
        writes.push({ method: "PATCH", path: configPath })
        config = { metadata: { resourceVersion: String(Number(config.metadata.resourceVersion) + 1) }, spec: body[1].value }
        send(res, 200, config)
      }
    } else if (url.pathname === "/api/v2/alerts" && req.method === "POST") {
      tests.push(...await read(req))
      send(res, 200, {})
    } else if (url.pathname === "/api/v2/alerts" && req.method === "GET") {
      if (unreachable) send(res, 503, {})
      else send(res, 200, [{ labels: { alertname: "FixtureDiskFull", severity: "critical", namespace: "apps" }, annotations: { summary: "Fixture storage warning", description: "Fixture description" }, startsAt: new Date().toISOString(), fingerprint: "fixture", status: { state: "suppressed", silencedBy: ["fixture-silence"], inhibitedBy: ["fixture-inhibitor"] } }])
    } else return false
    return true
  }
}
