import { NextResponse } from "next/server"

export type RoleGateFailure = { error: "unauthorized" | "forbidden"; code?: string }

export interface RoleGateErrorBody {
  error: string
  code?: string
}

export interface RoleGateErrorOptions {
  forbiddenMessage?: string
  extra?: Record<string, unknown>
  headers?: HeadersInit
}

/**
 * Converts a failed RoleGate (`requireRole` / `requireAdmin`) into a NextResponse,
 * preserving `gate.code` (e.g. `ACCESS_NOT_GRANTED`) across every route handler.
 */
export function roleGateErrorResponse<T = RoleGateErrorBody>(
  gate: RoleGateFailure,
  options?: RoleGateErrorOptions,
): NextResponse<T> {
  const isUnauthorized = gate.error === "unauthorized"
  const body = {
    ...(options?.extra ?? {}),
    error: isUnauthorized ? "Unauthorized" : (options?.forbiddenMessage ?? "Forbidden"),
    ...(gate.code ? { code: gate.code } : {}),
  } as T
  return NextResponse.json<T>(
    body,
    {
      status: isUnauthorized ? 401 : 403,
      ...(options?.headers ? { headers: options.headers } : {}),
    },
  )
}
