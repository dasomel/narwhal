import { describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"
import { CapacityMeter, ServiceStatus } from "./architecture-primitives"

vi.mock("@/lib/i18n-client", () => ({ useT: () => (key: string) => key }))

describe("architecture capacity telemetry", () => {
  it.each([null, NaN])("does not turn absent/invalid usage %s into a zero meter", (percent) => {
    const html = renderToStaticMarkup(<CapacityMeter label="CPU" percent={percent} capacity="4 CPU" />)
    expect(html).toContain("arch640.noTelemetry")
    expect(html).not.toContain('role="meter"')
    expect(html).not.toContain("0%")
  })

  it("retains real zero usage and bounds a saturated meter", () => {
    const zero = renderToStaticMarkup(<CapacityMeter label="CPU" percent={0} capacity="4 CPU" />)
    expect(zero).toContain('aria-valuenow="0"')
    expect(zero).not.toContain("arch640.noTelemetry")
    expect(renderToStaticMarkup(<CapacityMeter label="CPU" percent={123} capacity="4 CPU" />)).toContain('aria-valuenow="100"')
  })

  it("renders zero-ready workloads as offline", () => {
    const html = renderToStaticMarkup(<ServiceStatus status="down" />)
    expect(html).toContain("arch.offline")
    expect(html).toContain("bg-narwhal-danger")
    expect(html).not.toContain("arch.unknown")
  })

  it("pairs unknown health with a text label instead of implying healthy", () => {
    const html = renderToStaticMarkup(<ServiceStatus status="unknown" />)
    expect(html).toContain("arch.unknown")
    expect(html).not.toContain("arch.healthy")
  })
})
