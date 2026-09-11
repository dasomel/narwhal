# Research Evidence

Narwhal follows the OpenForge Research Evidence Collection Standard:
https://github.com/dasomel/openforge/blob/main/docs/research-evidence.md

During normal development, preserve machine-readable, longitudinal evidence when practical. Prefer sanitized JSON/JSONL records tied to a UTC timestamp, git revision, schema version, normalized environment label, measured duration, result, attempts, human interventions, review corrections, CI retries, and allowlisted project metadata.

For Narwhal, useful evidence includes install/deploy duration, cluster verification counts and duration, SSO/integration/regression outcomes, upgrade/recovery behavior, runtime resource use/latency where relevant, and agent-assisted task outcomes. Preserve failures and retries, not only successful runs.

## Public-data rule

Only sanitized records may be committed publicly. Never publish credentials/tokens, private URLs/IPs/hostnames, customer/employer/tenant data, personal data, raw cluster dumps, confidential prompts/source, real infrastructure topology, security-sensitive logs, or arbitrary environment dumps. Prefer normalized labels and aggregate measurements. Raw kubectl output, traces, CI logs, prompts, screenshots, and security output are sensitive-by-default.

Before public storage: validate against the OpenForge schema, run secret/pattern checks, review free-form fields, and publish only normalized aggregates when redaction cannot be proven safe.
