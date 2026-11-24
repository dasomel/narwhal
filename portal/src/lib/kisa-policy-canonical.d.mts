export declare const KYVERNO_ADMISSION_DEFAULTS: readonly string[]
export declare function canonicalJson(value: unknown): string
export declare function canonicalizeKyvernoPolicySpec(spec: unknown): Record<string, unknown>
export declare function computeKyvernoSpecDigest(spec: unknown): string
export declare function specHasOverrides(spec: unknown): boolean
