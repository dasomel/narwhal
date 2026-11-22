/**
 * Shared client fetch helper for Compliance API routes.
 * Fails loudly on non-2xx responses or explicit `{ unavailable: true }` payloads
 * so React Query surfaces `<DataState state="unavailable" />` instead of empty zeros.
 */
export async function fetchComplianceJson<T>(url: string): Promise<T> {
  const res = await fetch(url)
  let body: unknown = null
  try {
    body = await res.json()
  } catch {
    if (!res.ok) {
      throw new Error(`Compliance request failed (${res.status})`)
    }
    throw new Error("Invalid JSON from compliance endpoint")
  }
  if (!res.ok || (body && typeof body === "object" && "unavailable" in body && (body as { unavailable?: boolean }).unavailable === true)) {
    const errMsg =
      body && typeof body === "object" && "error" in body && typeof (body as { error?: unknown }).error === "string"
        ? (body as { error: string }).error
        : `Compliance data unavailable (${res.status})`
    throw new Error(errMsg)
  }
  return body as T
}
