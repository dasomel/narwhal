---
name: narwhal-ops
description: "Orchestrator for Narwhal IDP cluster infrastructure tasks. Use for adding new components, version upgrades, cluster debugging, and full validation. MUST trigger on: 'add component', 'upgrade', 'debug cluster', 'validate', 'install', 'version', 'shellcheck', 'new component', 'check cluster', 'fix', 'troubleshoot'. Always use this skill when working with scripts/cluster/, gitops/apps/, or gitops/resources/ files in the Narwhal project."
---

# Narwhal Ops -- Infrastructure Task Orchestrator

Orchestrates all infrastructure tasks for the Narwhal IDP cluster. Combines 4 specialized agents based on the task at hand.

## Execution Mode: Sub-agent

## Agent Roster

| Agent | subagent_type | Role | Model |
|-------|--------------|------|-------|
| infra-engineer | infra-engineer | Script/YAML implementation | sonnet |
| infra-scout | infra-scout | Version/compatibility research | sonnet |
| infra-validator | infra-validator | Validation & security review | sonnet |
| cluster-ops | cluster-ops | Cluster operations/debugging | sonnet |

Model names are **aliases** (`haiku`/`sonnet`/`opus`), never pinned ids — they track the newest
generation. Escalate a lane to `opus` only on an explicit trigger, not by default:
- a sonnet lane already produced a demonstrably wrong result on this run, or
- the pass is a **final security/approval gate on a high-risk change** (RBAC, secrets, admission
  policy, API-server flags, anything that can take the cluster down) — then run Workflow 4 Phase 1
  a second time with `infra-validator` on `opus`, in a separate context from whoever authored it.

## Workflow Routing

| Keywords | Workflow |
|----------|---------|
| add, install, new component, set up | Component Addition |
| upgrade, version, update, bump | Version Upgrade |
| debug, error, fail, broken, not working, fix | Cluster Debug |
| validate, check, verify, shellcheck, audit | Validation |

---

## Workflow 1: Component Addition

For adding new platform components.

### Phase 1: Preparation
1. Identify component name and requirements from user input
2. Create `_workspace/` directory
3. Check existing script numbering (`ls scripts/cluster/`)

### Phase 2: Research + Existing Code Analysis (parallel)

Launch 2 agents simultaneously in a single message:

```
Agent(subagent_type: "infra-scout", model: "sonnet", run_in_background: true,
  prompt: "[component] Research Helm chart, images, versions, ARM64 compatibility.
           Save results to _workspace/02_scout_research.md")

Agent(subagent_type: "Explore", model: "haiku", run_in_background: true,
  prompt: "Analyze existing similar script patterns. Review recently added scripts
           in scripts/cluster/ and summarize structure and patterns
           to _workspace/02_patterns.md")
```

### Phase 3: Implementation

Pass research results to infra-engineer:

```
Agent(subagent_type: "infra-engineer", model: "sonnet",
  prompt: "[component] implementation. Research: _workspace/02_scout_research.md.
           Patterns: _workspace/02_patterns.md.
           Provision patterns ref: .claude/skills/narwhal-ops/references/provision-patterns.md.
           Create:
           1. scripts/cluster/XX-[component].sh
           2. gitops/apps/[component].yaml
           3. gitops/resources/[component].yaml (if needed)
           4. Update VERSIONS.md
           5. Add reference to gitops/apps/app-of-apps.yaml")
```

### Phase 4: Validation

```
Agent(subagent_type: "infra-validator", model: "sonnet",
  prompt: "Validate newly created/modified files:
           - [file list]
           Validation checklist ref: .claude/skills/narwhal-ops/references/validation-checklist.md")
```

On FAIL -> re-invoke infra-engineer to fix (max 2 retries)

### Phase 5: Cleanup
1. Report results summary (list of created files)
2. Suggest next steps: `vagrant provision master-1 --provision-with phase2-platform` or manual execution

---

## Workflow 2: Version Upgrade

For upgrading existing component versions.

### Phase 1: Preparation
1. Identify target component and current version (from VERSIONS.md)
2. Identify related files (scripts + GitOps YAML)

### Phase 2: Research

```
Agent(subagent_type: "infra-scout", model: "sonnet",
  prompt: "[component] current vX.Y.Z -> latest stable version research.
           Check breaking changes, Helm values changes, dependency changes.
           Save results to _workspace/02_upgrade_research.md")
```

### Phase 3: Implementation

```
Agent(subagent_type: "infra-engineer", model: "sonnet",
  prompt: "[component] version upgrade. Research: _workspace/02_upgrade_research.md.
           Modify: [related file list]. Include VERSIONS.md update.")
```

### Phase 4: Validation (same as Workflow 1 Phase 4)

---

## Workflow 3: Cluster Debug

For cluster issue investigation.

### Phase 1: Symptom Assessment
Collect symptoms/error messages from user. Identify target namespace, pod.

### Phase 2: Information Gathering (parallel)

```
Agent(subagent_type: "cluster-ops", model: "sonnet", run_in_background: true,
  prompt: "[symptom description]. Collect logs/events/status via kubectl.
           Search CLAUDE.md Mistakes Log for similar patterns.
           Save results to _workspace/02_cluster_state.md")

Agent(subagent_type: "infra-validator", model: "sonnet", run_in_background: true,
  prompt: "[related files] configuration validation. Check scripts/YAML for misconfigurations.
           Save results to _workspace/02_config_review.md")
```

### Phase 3: Analysis and Resolution
1. Synthesize collected results
2. Final check against CLAUDE.md Mistakes Log
3. Present root cause + fix

Additional agent calls if needed:
- infra-scout: when version compatibility is suspected
- infra-engineer: when code changes are needed
- cluster-ops: for post-fix verification

---

## Workflow 4: Validation

Full configuration consistency + security audit.

### Phase 1: Offline Validation

```
Agent(subagent_type: "infra-validator", model: "sonnet",
  prompt: "Run full validation:
           1. shellcheck scripts/cluster/*.sh, scripts/common/*.sh
           2. yq validate gitops/apps/*.yaml, gitops/resources/*.yaml
           3. Full VERSIONS.md consistency check
           4. Security review (secrets, RBAC, image sources)
           5. CLAUDE.md Mistakes Log cross-reference
           Validation checklist ref: .claude/skills/narwhal-ops/references/validation-checklist.md")
```

### Phase 2: Cluster State Check (when VM is running)

```
Agent(subagent_type: "cluster-ops", model: "sonnet",
  prompt: "Full cluster state check:
           - kubectl get nodes
           - kubectl get pods -A (filter unhealthy pods)
           - kubectl get applications -n devtools (ArgoCD sync status)
           - Key service accessibility tests")
```

---

## Data Flow

```
[User Request]
       |
  [Routing] --> Select Workflow
       |
  [infra-scout] --> research --+
  [Explore]     --> patterns --+
                               |
              [infra-engineer] --> implementation
                               |
              [infra-validator] --> validation
                               |    (FAIL -> re-invoke infra-engineer)
              [cluster-ops] --> deploy/test (optional)
                               |
                          [Result Report]
```

## Error Handling

| Situation | Strategy |
|-----------|----------|
| Agent failure | Retry once. On second failure, proceed without that result; note in report |
| Validation failure | Re-invoke infra-engineer to fix (max 2 retries) |
| VM not running | Skip cluster-ops, perform offline validation only |
| Uncertain research | Flag uncertain info, request user confirmation |

## Test Scenarios

### Happy Path: New Component Addition
1. User: "Add a Redis cache component"
2. Phase 2: infra-scout researches Redis version/ARM64 + Explore analyzes existing patterns
3. Phase 3: infra-engineer creates `scripts/cluster/15-redis.sh`, `gitops/apps/redis.yaml`
4. Phase 4: infra-validator passes shellcheck/YAML/version/security checks
5. Result: files created + VERSIONS.md updated

### Error Path: Validation Failure
1. Phase 4: 2 FAILs detected (hardcoded secret, YAML syntax)
2. Re-invoke infra-engineer to fix
3. Re-validation passes
4. Report notes "1st validation FAIL -> fixed -> 2nd validation PASS"
