import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

const sessionState = vi.hoisted(() => ({ role: "cluster-admin" }))

vi.mock("@tanstack/react-query", () => ({
  useQuery: vi.fn(),
  useMutation: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}))

vi.mock("next-auth/react", () => ({
  useSession: () => ({ data: { user: { role: sessionState.role } } }),
}))

import { useQuery } from "@tanstack/react-query"
import { LocaleProvider } from "@/lib/i18n-client"
import type { Certificate } from "@/lib/k8s-client"
import {
  CERT_POLL_INTERVAL_MS,
  CERT_POLL_TIMEOUT_MS,
  CertsTable,
  createCertRenewController,
  formatCertTimestamp,
  hasCertificateRenewed,
  type CertRenewController,
} from "./certs-table"

const T1 = "2026-11-17T00:00:00.000Z"
const T2 = "2026-11-18T00:00:04.000Z"

function makeCert(overrides: Partial<Certificate> = {}): Certificate {
  return {
    name: "openbao-tls",
    namespace: "openbao",
    ready: true,
    issuing: false,
    revision: 1,
    notBefore: T1,
    notAfter: "2027-02-15T00:00:00.000Z",
    dnsNames: ["openbao.narwhal.local"],
    issuer: "narwhal-ca",
    renewalTime: "2027-01-16T00:00:00.000Z",
    ...overrides,
  }
}

function mockCertQuery(certs: Certificate[] | undefined, flags: { isLoading?: boolean; isError?: boolean } = {}) {
  vi.mocked(useQuery).mockReturnValue({
    data: certs,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
    ...flags,
  } as never)
}

function renderTable(certs: Certificate[] | undefined, locale: "ko" | "en" = "en", controller?: CertRenewController, flags: { isLoading?: boolean; isError?: boolean } = {}) {
  mockCertQuery(certs, flags)
  return renderToStaticMarkup(
    <LocaleProvider locale={locale}>
      <CertsTable controller={controller} />
    </LocaleProvider>,
  )
}

function lastRefetchInterval(): unknown {
  const calls = vi.mocked(useQuery).mock.calls
  const lastArg = calls[calls.length - 1]?.[0] as { refetchInterval?: unknown } | undefined
  return lastArg?.refetchInterval
}

function deferred<T = unknown>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe("hasCertificateRenewed", () => {
  it("returns false while issuing is true even when notBefore and revision changed", () => {
    expect(
      hasCertificateRenewed(
        { issuing: true, notBefore: T2, revision: 2 },
        { notBefore: T1, revision: 1 },
      ),
    ).toBe(false)
  })

  it("returns false when both sides are null, undefined, or empty", () => {
    expect(
      hasCertificateRenewed(
        { issuing: false, notBefore: null, revision: null },
        { notBefore: null, revision: null },
      ),
    ).toBe(false)
    expect(
      hasCertificateRenewed(
        { issuing: false, notBefore: null, revision: null },
        { notBefore: undefined, revision: undefined },
      ),
    ).toBe(false)
    expect(
      hasCertificateRenewed(
        { issuing: false, notBefore: "", revision: null },
        { notBefore: null, revision: null },
      ),
    ).toBe(false)
  })

  it("returns false when values are unchanged or regressed", () => {
    expect(
      hasCertificateRenewed(
        { issuing: false, notBefore: T1, revision: 1 },
        { notBefore: T1, revision: 1 },
      ),
    ).toBe(false)
    expect(
      hasCertificateRenewed(
        { issuing: false, notBefore: T1, revision: 1 },
        { notBefore: T1, revision: 2 },
      ),
    ).toBe(false)
    expect(
      hasCertificateRenewed(
        { issuing: false, notBefore: null, revision: 1 },
        { notBefore: T1, revision: 1 },
      ),
    ).toBe(false)
    expect(
      hasCertificateRenewed(
        { issuing: false, notBefore: null, revision: 1 },
        { notBefore: null, revision: undefined },
      ),
    ).toBe(false)
    expect(
      hasCertificateRenewed(
        { issuing: false, notBefore: T1, revision: null },
        { notBefore: T1, revision: 1 },
      ),
    ).toBe(false)
  })

  it("returns true on a non-null notBefore change or strictly greater revision", () => {
    expect(
      hasCertificateRenewed(
        { issuing: false, notBefore: T2, revision: 1 },
        { notBefore: T1, revision: 1 },
      ),
    ).toBe(true)
    expect(
      hasCertificateRenewed(
        { issuing: false, notBefore: T2, revision: null },
        { notBefore: null, revision: null },
      ),
    ).toBe(true)
    expect(
      hasCertificateRenewed(
        { issuing: false, notBefore: T1, revision: 2 },
        { notBefore: T1, revision: 1 },
      ),
    ).toBe(true)
    expect(
      hasCertificateRenewed(
        { issuing: false, notBefore: null, revision: 1 },
        { notBefore: null, revision: 0 },
      ),
    ).toBe(true)
  })
})

describe("CertsTable", () => {
  beforeEach(() => {
    sessionState.role = "cluster-admin"
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-11-18T00:00:00.000Z"))
    vi.mocked(useQuery).mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it("renders loading, error, and empty states deterministically", () => {
    expect(renderTable(undefined, "en", undefined, { isLoading: true })).toContain("Loading data.")
    expect(renderTable(undefined, "en", undefined, { isError: true })).toContain("Could not load data.")
    expect(renderTable([], "en")).toContain("No data to display.")
  })

  it.each(["en", "ko"] as const)("renders issued timestamp, revision, and role-gated action in %s", (locale) => {
    const html = renderTable([makeCert({ notBefore: T1, revision: 1 })], locale)
    expect(html).toContain(locale === "ko" ? "발급일" : "Issued")
    expect(html).toContain(formatCertTimestamp(T1, locale))
    expect(html).toContain("rev 1")
    expect(html).toContain(locale === "ko" ? "갱신" : "Renew")

    sessionState.role = "viewer"
    const viewerHtml = renderTable([makeCert({ notBefore: T1, revision: 1 })], locale)
    expect(viewerHtml).not.toContain(locale === "ko" ? "갱신" : "Renew")
  })

  it("transitions issuing -> renewed at T2 and toggles polling refetchInterval", async () => {
    const onRenewAccepted = vi.fn()
    const postRenew = vi.fn().mockResolvedValue({ success: true, pending: true })
    const controller = createCertRenewController({ postRenew, onRenewAccepted })

    const certV1 = makeCert({ notBefore: T1, revision: 1, issuing: false })
    expect(renderTable([certV1], "en", controller)).toContain("Renew")
    expect(lastRefetchInterval()).toBe(false)

    await controller.renew(certV1)
    expect(postRenew).toHaveBeenCalledTimes(1)
    expect(onRenewAccepted).toHaveBeenCalledTimes(1)

    const issuingHtml = renderTable([{ ...certV1, issuing: true }], "en", controller)
    expect(issuingHtml).toContain("Issuing…")
    expect(issuingHtml).not.toContain("Renewed at")
    expect(lastRefetchInterval()).toBe(CERT_POLL_INTERVAL_MS)

    controller.reconcile([{ ...certV1, revision: 2, issuing: true }])
    expect(renderTable([{ ...certV1, revision: 2, issuing: true }], "en", controller)).toContain("Issuing…")

    const certV2 = makeCert({ notBefore: T2, revision: 2, issuing: false })
    controller.reconcile([certV2])

    const renewedHtml = renderTable([certV2], "en", controller)
    const formattedT2 = formatCertTimestamp(T2, "en")
    expect(renewedHtml).toContain(`Renewed at ${formattedT2}`)
    expect(renewedHtml).toContain(formattedT2)
    expect(renewedHtml).toContain("rev 2")
    expect(renewedHtml).not.toContain("Issuing…")
    expect(lastRefetchInterval()).toBe(false)

    controller.dispose()
  })

  it("falls through to still-pending at 30s timeout when baseline and refetched values are null or unchanged", async () => {
    const postRenew = vi.fn().mockResolvedValue({ success: true, pending: true })
    const controller = createCertRenewController({ postRenew })
    const nullCert = makeCert({ notBefore: null, revision: null, issuing: false })

    await controller.renew(nullCert)
    controller.reconcile([nullCert])
    expect(renderTable([nullCert], "en", controller)).toContain("Issuing…")

    await vi.advanceTimersByTimeAsync(CERT_POLL_TIMEOUT_MS)

    const timedOutHtml = renderTable([nullCert], "en", controller)
    expect(timedOutHtml).toContain("Still pending — check again later")
    expect(timedOutHtml).not.toContain("Renewed at")
    expect(timedOutHtml).not.toContain("Issuing…")
    expect(lastRefetchInterval()).toBe(false)

    controller.dispose()
  })

  it("shows error badge and schedules no timeout when renew POST fails", async () => {
    const setTimeoutSpy = vi.spyOn(globalThis, "setTimeout")
    const postRenew = vi.fn().mockRejectedValue(new Error("Internal Server Error"))
    const controller = createCertRenewController({ postRenew })
    const cert = makeCert()

    await controller.renew(cert)

    const errorHtml = renderTable([cert], "en", controller)
    expect(errorHtml).toContain("Renewal failed")
    expect(errorHtml).toContain('role="alert"')
    expect(setTimeoutSpy).not.toHaveBeenCalled()
    expect(lastRefetchInterval()).toBe(false)

    controller.dispose()
  })

  it("keeps two concurrent row renewals independent with no cross-talk", async () => {
    const postRenew = vi.fn().mockResolvedValue({ success: true, pending: true })
    const controller = createCertRenewController({ postRenew })

    const certA = makeCert({ name: "openbao-tls", namespace: "openbao", notBefore: T1, revision: 1 })
    const certB = makeCert({ name: "argocd-tls", namespace: "argocd", notBefore: T1, revision: 1 })

    await controller.renew(certA)
    await vi.advanceTimersByTimeAsync(5_000)
    await controller.renew(certB)

    const bothIssuingHtml = renderTable([certA, certB], "en", controller)
    expect(bothIssuingHtml.split("Issuing…")).toHaveLength(3)

    const certARenewed = { ...certA, notBefore: T2, revision: 2, issuing: false }
    controller.reconcile([certARenewed, { ...certB, issuing: true }])

    const partialHtml = renderTable([certARenewed, certB], "en", controller)
    expect(partialHtml).toContain(`Renewed at ${formatCertTimestamp(T2, "en")}`)
    expect(partialHtml).toContain("Issuing…")
    expect(lastRefetchInterval()).toBe(CERT_POLL_INTERVAL_MS)

    await vi.advanceTimersByTimeAsync(CERT_POLL_TIMEOUT_MS)

    const finalHtml = renderTable([certARenewed, certB], "en", controller)
    expect(finalHtml).toContain(`Renewed at ${formatCertTimestamp(T2, "en")}`)
    expect(finalHtml).toContain("Still pending — check again later")
    expect(lastRefetchInterval()).toBe(false)

    controller.dispose()
  })

  it("disables the row button and ignores second clicks while POST or poll is in flight", async () => {
    const setTimeoutSpy = vi.spyOn(globalThis, "setTimeout")
    const postGate = deferred()
    const postRenew = vi.fn().mockImplementation(() => postGate.promise)
    const controller = createCertRenewController({ postRenew })
    const cert = makeCert()

    const firstAttempt = controller.renew(cert)
    const submittingHtml = renderTable([cert], "en", controller)
    expect(submittingHtml).toContain("disabled")
    expect(submittingHtml).toContain("Renewing...")

    // Second click while POST is in flight must be ignored.
    await controller.renew(cert)
    expect(postRenew).toHaveBeenCalledTimes(1)

    postGate.resolve({ success: true })
    await firstAttempt
    expect(setTimeoutSpy).toHaveBeenCalledTimes(1)

    // Second invocation while poll/issuing is in flight must not POST or add overlapping timers.
    await controller.renew(cert)
    expect(postRenew).toHaveBeenCalledTimes(1)
    expect(setTimeoutSpy).toHaveBeenCalledTimes(1)

    controller.dispose()
  })

  it("clears timers and suppresses state updates, new timers, and console errors after dispose", async () => {
    const setTimeoutSpy = vi.spyOn(globalThis, "setTimeout")
    const clearTimeoutSpy = vi.spyOn(globalThis, "clearTimeout")
    const consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {})
    const onRenewAccepted = vi.fn()

    // Case 1: pending timeout timer is cleared on unmount/dispose.
    const controller1 = createCertRenewController({
      postRenew: vi.fn().mockResolvedValue({ success: true }),
      onRenewAccepted,
    })
    const listener1 = vi.fn()
    controller1.subscribe(listener1)

    const cert = makeCert()
    await controller1.renew(cert)
    expect(setTimeoutSpy).toHaveBeenCalledTimes(1)
    listener1.mockClear()

    controller1.dispose()
    expect(clearTimeoutSpy).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(CERT_POLL_TIMEOUT_MS * 2)
    expect(listener1).not.toHaveBeenCalled()

    // Case 2: in-flight POST settling or reconcile called after dispose causes no updates or timers.
    const postGate = deferred()
    const controller2 = createCertRenewController({
      postRenew: () => postGate.promise,
      onRenewAccepted,
    })
    const listener2 = vi.fn()
    controller2.subscribe(listener2)

    const inflight = controller2.renew(cert)
    listener2.mockClear()
    const timersBeforeDispose = setTimeoutSpy.mock.calls.length

    controller2.dispose()
    postGate.resolve({ success: true })
    await inflight
    controller2.reconcile([makeCert({ notBefore: T2, revision: 2 })])

    expect(listener2).not.toHaveBeenCalled()
    expect(setTimeoutSpy).toHaveBeenCalledTimes(timersBeforeDispose)
    expect(onRenewAccepted).toHaveBeenCalledTimes(1) // Only from controller1 before dispose.
    expect(consoleErrorSpy).not.toHaveBeenCalled()
  })
})
