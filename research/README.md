# Research Evidence

Narwhal follows the [OpenForge Research Evidence Collection Standard](https://github.com/dasomel/openforge/blob/main/docs/research-evidence.md).

During normal development, preserve machine-readable, longitudinal evidence when practical. Prefer JSON/JSONL records tied to a UTC timestamp, git revision, schema version, environment label, measured duration, result, attempts, human interventions, review corrections, CI retries, and allowlisted project metadata.

For Narwhal, useful evidence includes install/deploy duration, cluster verification counts and duration, SSO/integration/regression outcomes, upgrade/recovery behavior, runtime resource use/latency where relevant, and agent-assisted task outcomes. Preserve failures and retries, not only successful runs.

## Prospective records

`schemas/evidence-1.0.schema.json` is the versioned JSON Schema for prospective records. It follows the OpenForge field contract and rejects unknown fields. `scripts/research/run-recorded.py` executes argv directly (no shell interpolation), measures elapsed wall time, and appends one JSONL record for both successful and failed commands. Before append, it checks the schema fields/types, task/event allowlists, and credential patterns. It records only a controlled task label, revision, result, normalized environment, retry/intervention counts, exit status, and dirty-tree state; command arguments, environment variables, and raw logs are never copied into the record.

Use the runner for a local check:

```sh
python3 scripts/research/run-recorded.py --task regression-static --event-type test -- \
  ./scripts/test/regression-check-kakao.sh --static
```

`make test` uses this wrapper by default and supplies the regression check's machine report, from which only pass/fail/warning counts are copied. Local records append to `research/evidence/YYYY-MM.jsonl`; review them before committing. Set `RESEARCH_EVIDENCE_DIR` to an external directory when a run should not change the checkout. CI's static regression job writes to runner temp storage and uploads its structured JSONL artifact with 30-day retention, including when the regression command fails. The raw regression report is not uploaded. This bounded artifact preserves routine CI attempts without committing generated output from a runner.

The event types allow recording build, install, deploy, runtime, recovery, release, benchmark, and agent-task executions through the same wrapper using the fixed task-label allowlist in the script. Optional count/resource/artifact/failure-stage fields accept only validated values. `agent_task` requires measured `--human-interventions` and `--review-corrections`; never infer counts, token use, resource consumption, or other metrics not collected by the caller. Current automation records static regression only. Deployment/runtime measurements and automated agent-session metadata remain uninstrumented until those execution paths expose safe, measurable inputs.

## Legacy evidence on discovery

Evidence collection is both prospective and retrospective. During any implementation, bug fix, verification, release, or documentation task, if existing QA reports, test outputs, benchmark results, traces, lessons logs, CI results, compatibility records, dated implementation reports, or other historical measurements are encountered, preserve the original and register/classify it as legacy evidence for future analysis.

Use the OpenForge legacy evidence catalog work (`dasomel/openforge#89`) as the portfolio-level source of truth. Record source/path, date when known, evidence class, evidence strength (`measured`, `observed`, `derived`, or `contextual`), environment scope, useful metrics/facts, limitations, and likely paper use. Do not invent values that were not historically measured and do not rewrite an old artifact merely to make later results look cleaner. Preserve failures, partial results, and obsolete records when they retain longitudinal value.

## Public-data rule

These repositories are personal OSS/test environments. Reproducibility-relevant technical identifiers such as RFC1918 test addresses, `*.local.*` domains, pod/node/namespace names, local cluster topology, and hardware/runtime details may be retained when they are intentionally part of the public OSS environment.

Never publish actual secrets or credentials (passwords, tokens, cookies, private keys, signed credentials, kubeconfig credentials) or accidental personal data. If a future artifact comes from a non-public third-party environment, review it separately before publication. Before committing structured public evidence, validate against the OpenForge schema and run secret/pattern checks; avoid arbitrary secret-bearing environment dumps.
