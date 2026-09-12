# Research Evidence

Narwhal follows the OpenForge Research Evidence Collection Standard:
https://github.com/dasomel/openforge/blob/main/docs/research-evidence.md

During normal development, preserve machine-readable, longitudinal evidence when practical. Prefer JSON/JSONL records tied to a UTC timestamp, git revision, schema version, environment label, measured duration, result, attempts, human interventions, review corrections, CI retries, and allowlisted project metadata.

For Narwhal, useful evidence includes install/deploy duration, cluster verification counts and duration, SSO/integration/regression outcomes, upgrade/recovery behavior, runtime resource use/latency where relevant, and agent-assisted task outcomes. Preserve failures and retries, not only successful runs.

## Legacy evidence on discovery

Evidence collection is both prospective and retrospective. During any implementation, bug fix, verification, release, or documentation task, if existing QA reports, test outputs, benchmark results, traces, lessons logs, CI results, compatibility records, dated implementation reports, or other historical measurements are encountered, preserve the original and register/classify it as legacy evidence for future analysis.

Use the OpenForge legacy evidence catalog work (`dasomel/openforge#89`) as the portfolio-level source of truth. Record source/path, date when known, evidence class, evidence strength (`measured`, `observed`, `derived`, or `contextual`), environment scope, useful metrics/facts, limitations, and likely paper use. Do not invent values that were not historically measured and do not rewrite an old artifact merely to make later results look cleaner. Preserve failures, partial results, and obsolete records when they retain longitudinal value.

## Public-data rule

These repositories are personal OSS/test environments. Reproducibility-relevant technical identifiers such as RFC1918 test addresses, `*.local.*` domains, pod/node/namespace names, local cluster topology, and hardware/runtime details may be retained when they are intentionally part of the public OSS environment.

Never publish actual secrets or credentials (passwords, tokens, cookies, private keys, signed credentials, kubeconfig credentials) or accidental personal data. If a future artifact comes from a non-public third-party environment, review it separately before publication. Before committing structured public evidence, validate against the OpenForge schema and run secret/pattern checks; avoid arbitrary secret-bearing environment dumps.