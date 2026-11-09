import { describe, expect, it } from "vitest"
import { buildAlertmanagerSpec, ChannelValidationError, mergeChannelValues, parseChannelInput, redactChannelSettings } from "./notification-channel-validation"
import { notificationKeys, type ChannelValues } from "@/types/notification-channels"

const values: ChannelValues = {
  slack_webhook_url: "https://hooks.example.com/private", slack_channel: "#ops",
  smtp_smarthost: "smtp.example.com:587", smtp_from: "alerts@example.com", smtp_to: "one@example.com, two@example.com",
  smtp_auth_username: "sender", smtp_auth_password: "private-password", smtp_require_tls: "true",
}
const disabled = { slackEnabled: false, emailEnabled: false, values: {} }

describe("notification validation", () => {
  it.each([null, [], {}, { ...disabled, slackEnabled: "false" }, { ...disabled, values: [] }, { ...disabled, extra: true }, { ...disabled, values: { unexpected: "x" } }, { ...disabled, values: { smtp_from: 3 } }, { ...disabled, values: { smtp_auth_password: "x\r\nbcc:other" } }, { ...disabled, values: { slack_channel: "x".repeat(4097) } }])("rejects malformed input %j", (input) => {
    expect(() => parseChannelInput(input)).toThrow(ChannelValidationError)
  })
  it.each([
    ["slack_webhook_url", "http://hooks.example.com/x"], ["slack_webhook_url", "not-a-url"], ["slack_webhook_url", "https://user:pass@hooks.example.com/x"],
    ["smtp_smarthost", "smtp.example.com"], ["smtp_smarthost", "smtp.example.com:0"], ["smtp_smarthost", "smtp.example.com:65536"], ["smtp_smarthost", "-bad:25"],
    ["smtp_from", "alerts@example.com\nBcc:x@y.com"], ["smtp_from", "bad"], ["smtp_to", "a@example.com, bad"], ["smtp_to", "a@example.com,"], ["smtp_require_tls", "yes"],
  ])("rejects invalid %s", (key, value) => {
    expect(() => mergeChannelValues({}, parseChannelInput({ ...disabled, values: { [key]: value } }))).toThrow(ChannelValidationError)
  })
  it.each(["smtp.example.com:587", "localhost:25", "[::1]:25"])("accepts smarthost %s", (smtp_smarthost) => {
    expect(mergeChannelValues({}, { ...disabled, values: { smtp_smarthost } }).smtp_smarthost).toBe(smtp_smarthost)
  })
  it("keeps omitted values, replaces provided ones and clears null values", () => {
    const merged = mergeChannelValues(values, { ...disabled, values: { slack_channel: " #new ", smtp_auth_password: null } })
    expect(merged).toEqual({ ...values, slack_channel: "#new", smtp_auth_password: undefined })
    expect(values.smtp_auth_password).toBe("private-password")
  })
  it("preserves password whitespace", () => {
    expect(mergeChannelValues({}, { ...disabled, values: { smtp_auth_password: " pass " } }).smtp_auth_password).toBe(" pass ")
  })
  it.each([
    { slackEnabled: true, emailEnabled: false, values: {} },
    { slackEnabled: false, emailEnabled: true, values: {} },
    { ...disabled, emailEnabled: true, values: { ...values, smtp_auth_password: null } },
    { ...disabled, slackEnabled: true, values: { ...values, slack_webhook_url: null } },
  ])("rejects incomplete enabled channels", (input) => expect(() => mergeChannelValues({}, input)).toThrow(ChannelValidationError))
})

describe("AlertmanagerConfig builder", () => {
  it.each([[false, false], [true, false], [false, true], [true, true]])("routes slack=%s email=%s exactly", (slackEnabled, emailEnabled) => {
    const spec = buildAlertmanagerSpec(values, { slackEnabled, emailEnabled })
    const names = ["null", ...(slackEnabled ? ["slack"] : []), ...(emailEnabled ? ["email"] : [])]
    expect(spec.receivers.map((r) => r.name)).toEqual(names)
    expect(spec.route).toEqual({ receiver: "null", routes: names.slice(1).map((receiver) => ({ receiver, continue: true })), groupBy: ["alertname", "namespace"], groupWait: "30s", groupInterval: "5m", repeatInterval: "4h" })
    expect(JSON.stringify(spec)).not.toContain(values.slack_webhook_url)
    expect(JSON.stringify(spec)).not.toContain(values.smtp_auth_password)
    if (slackEnabled) expect(spec.receivers.find((r) => r.name === "slack")).toMatchObject({ slackConfigs: [{ apiURL: { name: "narwhal-notification-channels", key: "slack_webhook_url" }, channel: "#ops", sendResolved: true }] })
    if (emailEnabled) expect(spec.receivers.find((r) => r.name === "email")).toMatchObject({ emailConfigs: [{ authUsername: "sender", authPassword: { name: "narwhal-notification-channels", key: "smtp_auth_password" }, requireTLS: true }] })
  })
  it("defaults TLS on and permits unauthenticated SMTP with explicit TLS off", () => {
    const { smtp_auth_username: _u, smtp_auth_password: _p, smtp_require_tls: _t, ...unauthenticated } = values
    const spec = (smtp_require_tls?: string) => buildAlertmanagerSpec({ ...unauthenticated, smtp_require_tls }, { slackEnabled: false, emailEnabled: true })
    expect(spec().receivers[1]).toMatchObject({ emailConfigs: [{ requireTLS: true }] })
    expect(spec("false").receivers[1]).toEqual({ name: "email", emailConfigs: [{ to: values.smtp_to, from: values.smtp_from, smarthost: values.smtp_smarthost, requireTLS: false }] })
  })
})

describe("secret redaction", () => {
  it("returns only booleans for every stored field", () => {
    const secret = { metadata: { resourceVersion: "1" }, data: Object.fromEntries(notificationKeys.map((key) => [key, Buffer.from(`sensitive-${key}`).toString("base64")])) }
    const result = redactChannelSettings(secret, { metadata: { resourceVersion: "2" }, spec: { receivers: [{ name: "null" }, { name: "email" }] } })
    expect(result).toEqual({ slackEnabled: false, emailEnabled: true, configured: Object.fromEntries(notificationKeys.map((key) => [key, true])) })
    expect(JSON.stringify(result)).not.toContain("sensitive")
    expect(Object.values(result.configured).every((v) => typeof v === "boolean")).toBe(true)
  })
  it("handles the GitOps disabled empty Secret", () => {
    expect(redactChannelSettings({ metadata: { resourceVersion: "1" } }, { metadata: { resourceVersion: "2" } }).configured).toEqual(Object.fromEntries(notificationKeys.map((key) => [key, false])))
  })
})
