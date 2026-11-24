export declare const KYVERNO_ADMISSION_DEFAULTS: readonly string[]
export declare const NETWORK_POLICY_DEFAULTS: readonly string[]
export declare const NON_SEMANTIC_TOP_LEVEL_FIELDS: readonly string[]
export declare const POLICY_TOP_LEVEL_ALLOWLIST_BY_KIND: Record<
  string,
  { nonSemantic: readonly string[]; knownPolicyFields: readonly string[] }
>
export declare function canonicalJson(value: unknown): string
export declare function canonicalizeKyvernoPolicySpec(spec: unknown): Record<string, unknown>
export declare function canonicalizeNetworkPolicySpec(kind: string, spec: unknown): Record<string, unknown>
export declare function canonicalizePolicyObject(kind: string, policyObj: unknown): Record<string, unknown>
export declare function computePolicyDigest(kind: string, policyObj: unknown): string
export declare function computeNetworkPolicyDigest(kind: string, policyObj: unknown): string
export declare function computeKyvernoPolicyDigest(policyObj: unknown): string
export declare function computeKyvernoSpecDigest(spec: unknown): string
export declare function computeNetworkPolicySpecDigest(kind: string, spec: unknown): string
export declare function specHasOverrides(spec: unknown): boolean
