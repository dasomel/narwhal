# ADR-0002: Agent execution evidence chain

- Status: Proposed
- Date: 2026-10-17
- Supersedes: —
- Superseded by: —
- Korean: [0002-agent-execution-evidence-chain-ko.md](0002-agent-execution-evidence-chain-ko.md)

Decision IDs: D-EV-1..9 (core specification), D-EV-10..19 (independent review addendum 1), D-EV-20..27 (independent re-review addendum 2, binding), D-EV-28..35 (independent re-review 2 addendum 3, binding), D-EV-36..42 (final review addendum 4, binding).
D-EV-1 (roles and key separation), D-EV-2 (records and PEP ordering),
D-EV-3 (canonical JSON subset), D-EV-4 (domain-separated hashing and signing),
D-EV-5 (countersigned checkpoints and export store), D-EV-6 (strict verification and findings),
D-EV-7 (durability via fsync and open-time integrity verification),
D-EV-8 (spend-once derivation across restarts), D-EV-9 (invocation hash privacy),
D-EV-10 (isolated tempfile signing I/O), D-EV-11 (strict file reading and explicit no-checkpoints flag),
D-EV-12 (all checkpoint heads verification), D-EV-13 (countersigner chain pre-verification),
D-EV-14 (canonical DER public key separation), D-EV-15 (writer concurrency via flock and epoch guard),
D-EV-16 (writer poisoning on I/O error), D-EV-17 (strict canonical JSON line parsing and record shapes),
D-EV-18 (hostile mutation test coverage), D-EV-19 (ADR residual risks and review documentation),
D-EV-20 (empty checkpoints rejection and XOR opt-in), D-EV-21 (per-inode advisory flock on chain fd),
D-EV-22 (incremental verification caching and O(new entries) append cost, superseded in part by D-EV-28),
D-EV-23 (robust field shape/type validation without uncaught exceptions),
D-EV-24 (countersigner single shared flock window), D-EV-25 (fail-closed key separation),
D-EV-26 (addendum 2 mutation test coverage), D-EV-27 (residual risks and cost model updates),
D-EV-28 (writer poisoning on shrink or head mismatch), D-EV-29 (pre-write input validation and boundary guards),
D-EV-30 (strict checkpoint shape and type validation), D-EV-31 (file replacement rename swap detection),
D-EV-32 (uninterrupted write loops and I/O poisoning), D-EV-33 (countersigner entry validation and key separation),
D-EV-34 (round 3 mutation survivor defenses), D-EV-35 (decision record alignment for round 3),
D-EV-36 (countersigner atomicity and lock ordering), D-EV-37 (countersigner pre-verification of checkpoints),
D-EV-38 (standardized exit codes 64 and 2), D-EV-39 (canonical lowercase hex enforcement),
D-EV-40 (cached head hash in-place tamper detection), D-EV-41 (genesis checkpoint head and verifier key check),
D-EV-42 (decision record alignment and risk documentation for round 4).

## Context

Autonomous AI agents operating on platform infrastructure require robust governance and non-repudiation.

Narwhal issue #73 and OpenForge ADR-0013 establish a governance architecture separating four discrete
responsibilities:

- **Policy Decision Point (PDP):** Authorizes actions and issues cryptographically bounded, spend-once grants.
- **Signer:** Isolated cryptographic service holding private signing keys.
- **Policy Enforcement Point (PEP):** Intercepts agent actions, consumes grants, commits evidence, and invokes executors.
- **Executor:** Runs authorized tool calls, workloads, or infrastructure mutations.

A fundamental requirement is that audit evidence must remain verifiable even if the agent runtime or the
PEP host is compromised after the fact.

### Threat Model

A compromised agent runtime, untrusted tool process, or subverted PEP host is assumed to possess local
execution control and file system access. An attacker can attempt the following attacks:

1. **Stop writing:** Cease appending logs entirely to perform undetected out-of-band actions.
2. **Rewrite / forge:** Alter or falsify past log entries to rewrite history or fabricate authorization.
3. **Truncate tail:** Delete recent entries from the end of the log to obscure unauthorized actions.
4. **Roll back / fork:** Replace the tail of the log with a previously saved version or a validly signed alternate history.
5. **Replay a grant after restart:** Replay a previously issued grant token across process or node restarts.
6. **Drop the result after consuming:** Consume a valid grant, execute the mutation, but deliberately drop the result record to claim the action never executed.

This ADR defines the tamper-evident execution evidence chain designed to withstand this threat model.

## Decision

### D-EV-1: Roles and Key Separation

The evidence chain strictly enforces architectural role separation:

- **Writer (PEP side):** Appends entries to the local chain. The writer **never** holds private signing keys.
- **Signer:** Separate process holding private keys. The reference implementation `OpensslSigner(key_path)`
  executes `openssl pkeyutl -sign -rawin -inkey KEY -in MSG_FILE` via subprocess (passing message bytes via an
  isolated 0600 tempfile per D-EV-10 and emitting raw signature bytes to stdout).
- **Verifier:** Evaluates evidence integrity using public verification keys only via
  `openssl pkeyutl -verify -pubin -inkey PUB -rawin -sigfile SIG`.
- **Key Separation:** Two distinct Ed25519 keypairs are required:
  1. `entry_pub` / instance key: Signs individual execution entries.
  2. `cp_pub` / checkpoint countersigner key: Signs checkpoint summaries.
  Verification **must fail** if both public keys are identical.

- **Reason:** Restricts compromise blast radius. Even if a PEP host is breached, the attacker cannot forge
  signatures without compromising the isolated signer process. Distinct checkpoint keys prevent a compromised
  instance key from certifying its own fraudulent state.
- **Cost:** Subprocess IPC overhead per record and management of two distinct keypairs.
- **Escape Hatch:** In high-throughput production, subprocess OpenSSL execution can be replaced by an out-of-process
  gRPC daemon or UNIX domain socket connected to a hardware security module (HSM) or KMS without altering record formats.

### D-EV-2: Records and PEP Fail-Closed Ordering

The evidence chain records two entry kinds: `intent` and `result`.

- **Entry Fields:** `v` (=1), `seq` (contiguous integer starting at 0), `ts_ms` (integer timestamp),
  `kind` (`intent` | `result`), `instance_id` (string), `epoch` (non-decreasing integer), `grant_id` (string),
  `invocation_hash` (hex SHA-256 of canonical resolved invocation arguments), `prev_hash` (hex SHA-256),
  `entry_hash` (hex SHA-256), `sig` (hex signature).
- **Result-specific Fields:** `intent_seq` (the `seq` of the matching intent record) and `outcome` (`ok` | `error` | `aborted`).
- **PEP Enforcement Sequence:**
  1. Construct and append `intent` record.
  2. Ensure intent is durably flushed and fsynced.
  3. Consume grant token.
  4. Invoke executor.
  5. Construct and append `result` record (with `intent_seq` pointing to step 1) and fsync.
- **Fail-Closed Guarantee:** If appending the `intent` fails (e.g. disk full, permission denied), the PEP
  **must not** consume the grant and **must not** invoke the executor.

- **Reason:** Prevents unrecorded actions and ensures that any consumed grant is permanently linked to an
  intent on disk before execution occurs, exposing crashed or suppressed operations.
- **Cost:** Requires two disk operations and two signature calls per action invocation.
- **Escape Hatch:** Read-only idempotent queries could be routed through an optional lighter auditing tier in a
  future revision, but mutating operations strictly require two-phase intent/result.

### D-EV-3: Canonical JSON Subset

Record canonicalization uses a strict subset of JSON:

- Serialized via `json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=False).encode("utf-8")`.
- Allowed data types: `str`, `int`, `bool`, `None`, `list`, `dict`.
- Floating-point numbers (`float`), `NaN`, and `Infinity` are **strictly rejected** (raise `ValueError`).

- **Reason:** Floating-point representations suffer from cross-architecture IEEE 754 precision ambiguities that
  cause hashing and signature verification failures. A deterministic subset allows cross-platform consistency
  using standard libraries without third-party dependencies.
- **Cost:** Float values cannot be serialized; callers must convert floating numbers to strings or fixed-precision integers.
- **Escape Hatch:** High-precision decimals must be encoded as string representations or scaled integers.

### D-EV-4: Domain-Separated Hashing and Signing

All hashing and signing operations prepend explicit domain separation tags:

- **Entry Hash:** `entry_hash = sha256(b"narwhal-evidence-entry-v1\0" + canonical(entry without entry_hash, sig)).hexdigest()`.
  Note that `prev_hash` is included inside the canonical entry body.
- **Entry Signature:** `sig = sign(b"narwhal-evidence-entry-v1\0" + entry_hash.encode())`.
- **Checkpoint Signature:** `sig = sign(b"narwhal-evidence-checkpoint-v1\0" + cp_hash.encode())`.
- All hashes and signatures are stored as lowercase hex strings.

- **Reason:** Domain separation prevents cross-protocol signature substitution attacks where a valid entry signature
  could be misinterpreted as a checkpoint signature or vice versa.
- **Cost:** Minor string concatenation overhead before hashing and signing.
- **Escape Hatch:** Protocol version upgrades can increment the domain prefix (`-v2\0`) to allow side-by-side evolution.

### D-EV-5: Countersigned Checkpoints and Export Store

Checkpoints periodically anchor the hash chain to an external store:

- **Checkpoint Fields:** `v` (=1), `kind` (`checkpoint`), `cp_seq` (integer from 0), `instance_id`, `epoch`,
  `count` (total entries covered), `head_hash` (`entry_hash` of entry at `count - 1`), `ts_ms`, `prev_cp_hash`,
  `cp_hash`, `sig`.
- **Genesis Value:** For `seq=0` or `cp_seq=0`, the initial `prev_hash` or `prev_cp_hash` is 64 hex zeros (`"0" * 64`).
- **Export Store:** Checkpoints are appended as JSON lines to a dedicated external checkpoint file/store.
  In production, the local PEP writer has **no write access** to this export store; an external countersigning agent
  queries the writer API head (`count`, `head_hash`), generates the checkpoint, signs it with `cp_key`, and commits
  it to the export store.

- **Reason:** Defeats tail truncation, rollback, and fork attacks. Once a checkpoint is committed to an append-only
  export store, an attacker cannot delete or rewrite the local chain prior to `count` without causing an immediate verification failure.
- **Cost:** Requires an external countersigning service and an append-only storage destination.
- **Escape Hatch:** Checkpoint generation frequency can be tuned (e.g. interval-based or entry-count-based)
  balancing anchoring latency against signing overhead.

### D-EV-6: Strict Verification and Findings Classification

The verification function `verify(entries, entry_pub, checkpoints, cp_pub)` enforces strict validation rules:

- **Mandatory Keys:** Public keys `entry_pub` and `cp_pub` are mandatory; omitting them raises `TypeError` or `ValueError`.
  Verification fails if `entry_pub == cp_pub`.
- **Structural Integrity:** `seq` must be contiguous starting from 0; `prev_hash` must match preceding `entry_hash`;
  all `entry_hash` and `sig` values must verify cryptographically; `instance_id` must remain uniform; `epoch` must be non-decreasing.
- **Checkpoint Validation:** `cp_seq` must be contiguous from 0; `prev_cp_hash` and `cp_hash` must verify;
  `count` and `epoch` must be monotonic; the latest checkpoint requires `len(entries) >= count` and
  `entries[count - 1].entry_hash == head_hash`.
- **Relational Integrity:** Every `result` must reference an earlier `intent_seq` with matching `grant_id` and
  `invocation_hash`. At most one `intent` per `grant_id` is allowed (double spend fails). At most one `result` per `intent` is allowed.
- **Findings Classification & Exit Codes:**
  - **Exit 1 (Integrity Failure):** Tampering, signature mismatch, sequence gap, checkpoint mismatch, rollback, or double-spend.
  - **Exit 0 (Clean / Warning):** Fully valid chain. In-flight intents without results ("consumed, outcome unknown")
    and unanchored tail entries (`unanchored_tail = len(entries) - count`) are reported as findings.
  - **Exit 2 (Unresolved Intents):** When invoked with `--fail-on-unresolved`, returns exit code 2 if any intent lacks a matching result.

- **Reason:** Distinguishes active malicious tampering (exit 1) from normal in-flight operations or crashes (exit 0/2),
  providing programmatic clarity to automated verification pipelines.
- **Cost:** Requires complete chain traversal and relational indexing.
- **Escape Hatch:** Verifiers can cache validated checkpoint states up to `count` for incremental tail verification in high-volume environments.

### D-EV-7: Durability via fsync and Open-Time Integrity Verification

- **Durability:** Appending an entry requires `write(json_line) + file.flush() + os.fsync(file.fileno())`.
  The parent directory is fsynced upon file creation.
- **Open-Time Verification:** When the writer opens an existing chain, it verifies the entire chain using `entry_pub`.
  If the tail is torn or corrupted, it refuses to open and raises an exception. **No silent recovery or automatic truncation is permitted.**
- **Reader Behavior:** Readers treat non-parseable JSON lines or malformed content as integrity violations.

- **Reason:** Prevents silent log loss during power interruptions and ensures that a writer never appends new records to a corrupted or tampered chain.
- **Cost:** Synchronous I/O latency for every entry append.
- **Escape Hatch:** High-throughput batch workloads may group multiple operations into a single fsync boundary,
  provided the PEP does not acknowledge or consume until the fsync returns.

### D-EV-8: Spend-Once Derivation Across Restarts

- The writer derives its set of consumed `grant_id`s by scanning the existing evidence chain upon opening.
- If a caller attempts to append an `intent` containing an already recorded `grant_id`, the writer rejects the call immediately.

- **Reason:** Guarantees spend-once semantics that survive process restarts or system reboots without requiring
  a separate mutable database.
- **Cost:** Memory footprint proportional to the number of active grants in the log.
- **Escape Hatch:** Expired grants can be garbage collected from memory if bounded by epoch-based validity windows.

### D-EV-9: Invocation Privacy via invocation_hash

- Evidence entries do not store raw execution arguments. Instead, records store `invocation_hash`, defined as the hex SHA-256 of the canonically serialized invocation request.
- The raw invocation arguments are retained only in local ephemeral execution logs or caller-side audit logs.

- **Reason:** Prevents credentials, authorization tokens, and sensitive customer data from being permanently
  persisted in tamper-evident chains distributed to external verifiers.
- **Cost:** Auditors cannot reconstruct exact input payloads from the evidence chain alone. Low-entropy arguments
  can be susceptible to dictionary search attacks.
- **Escape Hatch:** A future revision may incorporate a keyed HMAC or per-instance salt to blind low-entropy arguments.

### D-EV-10: Isolated Tempfile I/O for Signing and Verification

- Signing and verification operations **never** pass message data via stdin or `-in /dev/stdin`.
- On Linux, pipe `st_size=0` causes OpenSSL to fail with "Could not allocate 0 bytes". On macOS, pre-write stat races can cause OpenSSL to sign empty or truncated data.
- The reference implementation writes message bytes and signatures to 0600 temp files inside a 0700 temporary directory (`tempfile.mkstemp` inside `tempfile.mkdtemp`) and passes `-in <file>` and `-sigfile <file>`. Temp files and directories are unconditionally removed in `finally` blocks.

- **Reason:** Guarantees signing message integrity across Linux and macOS under arbitrary concurrency without pipe allocation failures or race conditions.
- **Cost:** Ephemeral filesystem I/O per signing operation.
- **Escape Hatch:** Out-of-process gRPC/UNIX socket signers passing binary payloads directly in memory.

### D-EV-11: Strict Chain and Checkpoint File Verification

- Verification, CLI commands, and chain readers treat missing or unreadable chain files or explicitly supplied checkpoint files as hard errors (Exit 1), never returning empty success.
- Evaluating an unanchored chain without checkpoints is permitted only when `--no-checkpoints` is explicitly passed by the caller (which reports `unanchored_tail=all`).

- **Reason:** Prevents silent verification passes when evidence log paths are misconfigured, nonexistent, or withheld by an attacker.
- **Cost:** Callers verifying unanchored chains must supply an explicit CLI flag.
- **Escape Hatch:** None; verification must fail closed on missing files.

### D-EV-12: Comprehensive Checkpoint Head Verification

- `verify()` checks **every** checkpoint in the export store against the chain (`entries[count-1].entry_hash == head_hash` and `count <= len(entries)`), rather than only evaluating the latest checkpoint record.
- Checkpoint `count` values must be strictly non-decreasing across the checkpoint sequence.
- Checkpoint `epoch` and `instance_id` must match the corresponding entry at `entries[count-1]` exactly. Empty `instance_id` values are rejected as errors rather than skipped.

- **Reason:** Prevents intermediate checkpoint forgery, tampering, or fork injections that could certifiably anchor conflicting history before the latest head.
- **Cost:** Verification time scales linearly with the number of checkpoints.
- **Escape Hatch:** None.

### D-EV-13: Countersigner Pre-Verification and Extension Check

- The CLI `checkpoint` command requires the instance public key (`--entry-pub`) and strictly verifies existing chain entries before signing any checkpoint.
- The countersigner requires the new checkpoint head to strictly extend the previous checkpoint head (verifying that `prev_head` remains present at `entries[prev_count-1]`).

- **Reason:** Ensures that an isolated countersigner cannot be tricked into signing a checkpoint over an invalid, malformed, or forked chain.
- **Cost:** Checkpoint creation includes chain verification overhead.
- **Escape Hatch:** None.

### D-EV-14: Canonical DER Public Key Separation

- The key separation guard compares the canonical DER encoding of the public keys (`openssl pkey -pubin -outform DER`), rather than comparing file paths, raw bytes, or PEM text.

- **Reason:** Prevents an attacker from bypassing key separation by presenting the same key with differing PEM headers, comments, whitespace, or encoding variants.
- **Cost:** One DER extraction per keypair during initialization.
- **Escape Hatch:** None.

### D-EV-15: Concurrency Control via Exclusive Flock

- `ChainWriter` acquires an exclusive POSIX `fcntl.flock` on the chain lockfile (`<chain>.lock`) around the entire sequence: re-reading the tail from disk, verifying integrity, appending, and fsyncing.
- On open, the writer verifies that the caller's epoch is greater than or equal to the last recorded epoch on disk.

- **Reason:** Prevents race conditions where two concurrent writer processes on the same host append duplicate sequence numbers or execute double-spends for the same `grant_id`.
- **Cost:** File lock acquisition latency on writes.
- **Escape Hatch:** None on POSIX hosts; distributed systems must isolate chains per instance.

### D-EV-16: Writer Poisoning on Write or Fsync Failure

- If an entry write or fsync operation raises an exception after bytes may have hit disk, the `ChainWriter` marks itself permanently poisoned and refuses all subsequent append or head queries.
- A new writer instance must be created, which safely re-reads and verifies committed entries from disk.

- **Reason:** Prevents in-memory state drift from corrupted disk state following transient I/O failures.
- **Cost:** Requires process restart or object re-instantiation upon disk I/O errors.
- **Escape Hatch:** None; fail-closed behavior is mandatory.

### D-EV-17: Strict Canonical JSON Line Parsing and Record Shapes

- Chain and checkpoint readers use `json.loads` with an `object_pairs_hook` that rejects duplicate dictionary keys.
- Every line in the file must match `canonical(record) + b"\n"` byte-for-byte; extra whitespace, blank lines, reordered keys, or non-canonical serialization cause immediate integrity failure.
- Malformed record shapes and unexpected field types are recorded as verification errors rather than throwing uncaught runtime exceptions.

- **Reason:** Enforces deterministic canonical formatting and prevents parser differential attacks across implementations.
- **Cost:** Byte comparison overhead per line during reading.
- **Escape Hatch:** None.

### D-EV-18: Hostile Mutation Testing Suite

- A comprehensive suite of behavioural tamper and mutation tests (`scripts/test/lib/evidence_attacks.py`) verifies that every integrity check, sequence link, kind constraint, epoch monotonicity rule, and key separation mechanism actively catches hostile corruption.
- Tests cover verification of double-spend detection, duplicate results, invocation hash mismatches, forward intent sequences, epoch decreases, instance ID mismatches, checkpoint sequence gaps, broken cryptographic hashes, non-latest checkpoint head mismatches, writer open-time signature checks, non-canonical whitespace lines, concurrent writer serialization under flock, canonical DER key comparison, and isolated tempfile message integrity.

- **Reason:** Code coverage alone does not ensure that adversarial tampering is rejected. Explicit behavioural mutation tests prove that each validation branch actively fails closed.
- **Cost:** Extended test execution time and maintenance overhead for mutation test cases.
- **Escape Hatch:** Fast local iteration may run happy paths; full mutation suites are mandatory for CI and release preflights.

### D-EV-19: Threat Model Documentation and Review Traceability

- All architectural assumptions, residual risks, adversarial mutation behaviors, and independent review findings (including D-EV-10..17) are formally documented within this Decision Record and traceable to concrete negative test cases.

- **Reason:** Prevents degradation of security guarantees during subsequent maintenance and provides cryptographic auditability for platform security reviews.
- **Cost:** Documentation maintenance overhead across multiple language editions.
- **Escape Hatch:** None.

### D-EV-20: Empty Checkpoints Rejection and XOR Opt-In

- `verify()` and the CLI treat an explicitly supplied but empty checkpoint file or empty list as an integrity error.
- Verification without checkpoints is permitted only when the caller explicitly passes `--no-checkpoints` (API: `allow_no_checkpoints=True`), in which case `unanchored_tail=all` is reported.
- The verifier and CLI enforce mutual exclusion (XOR): callers must specify either `--checkpoints` or `--no-checkpoints`, never both and never neither.

- **Reason:** Prevents silent verification passes when checkpoint files are empty or truncated, closing the gap where an attacker or misconfiguration could bypass checkpoint anchoring without explicit opt-in.
- **Cost:** Callers must explicitly opt in when verifying unanchored chains.
- **Escape Hatch:** None; fail-closed behavior is mandatory.

### D-EV-21: Inode-Based Advisory Flock

- `ChainWriter` acquires an exclusive POSIX `fcntl.flock` on the chain file's own open file descriptor (opened via `os.O_RDWR | os.O_CREAT`), rather than on a sibling `.lock` file.
- Advisory file locking operates per-inode, ensuring that access via symlinks, hardlinks, or alternate path representations serializes on the same underlying file descriptor / inode.

- **Reason:** Closes lock bypass vulnerabilities where alternate paths or symlinks bypassed sibling `.lock` files.
- **Cost:** File descriptor maintenance during write operations. Flock is advisory.
- **Escape Hatch:** None on local POSIX filesystems.

### D-EV-22: Incremental Verification and O(New Entries) Append Cost (Superseded in Part by D-EV-28)

- On initialization, `ChainWriter` performs a full verification of the chain and caches `(count, head_hash, last_seq, epoch, consumed_grants, offset, last_entry_offset)`.
- On each subsequent append under the held lock, the writer checks whether the file size has changed:
  - If truncated (`st_size < cached_offset`) or the entry at `cached_count - 1` fails verification against `cached_head_hash`, the open writer MUST poison itself and raise an exception immediately (`ChainIntegrityError`, per D-EV-28). **No silent recovery, re-read, or automatic truncation is permitted**, and the in-memory consumed-grant set is never discarded (preventing keyless spend-spend replay). Only a fresh `ChainWriter` open does full verification.
  - If new bytes were appended by another process (`st_size > cached_offset`), only the newly appended lines are read, verified with `entry_pub`, and incorporated into the cache.
  - In steady state without concurrent writes, appends require 0 verification subprocess calls and exactly 1 signing subprocess call (O(1) relative to chain length).

- **Reason:** Eliminates quadratic O(N^2) scaling for sequential chain writes, enabling high-throughput appending while preserving full open-time tamper detection and strictly preventing keyless spend-spend bypass on shrunken chains.
- **Cost:** In-memory caching and offset tracking.
- **Escape Hatch:** None; an open writer poisons itself on shrink or tampering, and only a fresh `ChainWriter` initialization performs full verification from disk.

### D-EV-23: Robust Field Shape and Type Validation

- The verification engine strictly validates the schema, field types, and value bounds of every entry and checkpoint record (`scripts/evidence/shapes.py`).

- Fields such as `count`, `seq`, `epoch`, and `ts_ms` are enforced as non-negative integers; `grant_id`, `instance_id`, `invocation_hash`, and signatures are enforced as non-empty strings.
- Any malformed record structure (e.g. non-dictionary lines, string counts, list grant IDs) is recorded as a structured integrity error, preventing unhandled runtime exceptions.

- **Reason:** Parser and type robustness against fuzzing, malformed payloads, and adversarial type confusion attacks.
- **Cost:** Shape validation overhead during reading.
- **Escape Hatch:** None.

### D-EV-24: Countersigner Single Shared Flock Window

- The CLI `checkpoint` subcommand opens the chain file and acquires a shared lock (`fcntl.flock(fd, fcntl.LOCK_SH)`).
- Inside this single atomic lock window, it reads the chain, verifies all entries with `entry_pub`, confirms that the new head extends the previous checkpoint head, and signs the new checkpoint.

- **Reason:** Guarantees that the head signed by the countersigner is the exact head verified, preventing concurrent writers from modifying the chain tail between verification and signing.
- **Cost:** Shared lock acquisition during checkpoint generation.
- **Escape Hatch:** None.

### D-EV-25: Fail-Closed Key Separation Check

- `keys_are_identical` enforces a strict fail-closed contract: any error in parsing or extracting the canonical DER encoding of a public key raises an exception immediately, rather than falling back or returning `False`.
- `verify()` catches key comparison failures and records them as verification errors.

- **Reason:** Prevents an attacker from presenting unparseable or malformed key files to silently bypass the key separation check.
- **Cost:** None.
- **Escape Hatch:** None.

### D-EV-26: Addendum 2 Mutation Test Coverage

- A dedicated test suite (`scripts/test/lib/evidence_addendum2.py`) validates all Addendum 2 requirements against mutation survivors, including:
  1. Writer open-time signature verification.
  2. Writer reload-derived spend-once enforcement across restarts.
  3. Checkpoint epoch and instance ID cross-checks against chain entries.
  4. Writer epoch lower-bound enforcement on open.
  5. CLI checkpoint extension check failure on non-extending heads.
  6. `--checkpoints` XOR `--no-checkpoints` mutual exclusion.
  7. Duplicate JSON key rejection independent of canonical bytes check.
  8. Directory fsync on creation.
  9. Symlink and hardlink writer serialization on the same inode.
  10. 300-entry incremental append performance (bounded openssl calls <= 3).
  11. Tampered earlier line detection on fresh open.
  12. Non-crashing validation of malformed record shapes.
  13. Fail-closed behavior of `keys_are_identical`.

- **Reason:** Eliminates blind spots where checks could be disabled without failing any test in the regression suite.
- **Cost:** Test suite maintenance and execution time.
- **Escape Hatch:** None.

### D-EV-27: Residual Risks and Cost Model Updates

- Residual risks updated to document the advisory nature of POSIX flock, the same-filesystem / NFS limitation, the steady-state per-append O(1) cost model, and explicit "no checkpoints" opt-in semantics.

- **Reason:** Accurate architecture documentation and transparency regarding operational constraints.
- **Cost:** None.
- **Escape Hatch:** None.

### D-EV-28: Writer Immediate Poisoning on Shrink or Head Mismatch

- Supersedes the silent re-verify clause of D-EV-22. If an open `ChainWriter` discovers under the held lock that the file size has decreased (`st_size < cached_offset`) or the entry at `cached_count - 1` fails verification against `cached_head_hash`, it MUST permanently poison itself and raise `ChainIntegrityError`.
- An already-open writer never silently re-reads or repairs a truncated chain from disk; its in-memory consumed grant set is permanently preserved to prevent keyless spend-spend replay on the active writer.
- Only a fresh process initialization (`ChainWriter(path, ...)`) performs a full verification from disk.

- **Reason:** Silent reloading of truncated files on an open writer would discard the in-memory record of already-consumed grants, opening a window for double-spending.
- **Cost:** An open writer cannot recover if an external process truncates the file; requires process restart.
- **Escape Hatch:** A fresh `ChainWriter` open scans and verifies surviving records from disk.

### D-EV-29: Pre-Write Input Validation and Boundary Guards

- `ChainWriter` validates the shape, types, and values of input parameters (`grant_id`, `invocation_hash`, `instance_id`, `epoch`, `outcome`, `intent_seq`) before acquiring locks, calling signers, or writing bytes to disk.
- Non-empty strings are required for identifiers and hashes; `epoch` must be a non-negative integer and >= the writer's last recorded epoch; `outcome` must be one of `ok`, `error`, `aborted`; `intent_seq` must be an integer >= 0.

- **Reason:** Fail early with `ValueError` on malformed inputs without generating signatures or committing corrupted entries.
- **Cost:** Minimal type and range check overhead before signing.
- **Escape Hatch:** None; input validation is strict.

### D-EV-30: Strict Checkpoint Shape and Type Validation

- `validate_checkpoint_shape` strictly enforces field types, schemas, and non-empty/non-negative constraints on all checkpoint records (`cp_seq`, `epoch`, `count`, `ts_ms` as non-negative ints; `instance_id` as non-empty str; hashes as 64-character lowercase hex strings; `sig` as even-length lowercase hex).
- Malformed records are recorded as verification errors, guaranteeing that `verify()` never raises uncaught exceptions.

- **Reason:** Robust parsing and fuzzing defense against malformed or hostile checkpoint payloads.
- **Cost:** Validation pass during checkpoint ingestion.
- **Escape Hatch:** None.

### D-EV-31: File Replacement (Rename Swap) Detection

- `ChainWriter` and the CLI `checkpoint` countersigner verify under the held flock that `os.fstat(locked_fd)` and `os.stat(path)` agree on `st_ino` and `st_dev`.
- If an adversary swaps or replaces the file via an atomic rename/replace while the writer holds an open descriptor, the writer detects the mismatch on the next operation and poisons itself immediately.

- **Reason:** Defends against atomic file replacement bypasses of advisory POSIX flock descriptors.
- **Cost:** One additional `os.stat(path)` system call per locked operation.
- **Escape Hatch:** None on local filesystems.

### D-EV-32: Short Writes and Poisoning on I/O Exception

- All record serialization appends loop on `os.write` buffer slices until all bytes are committed to the file descriptor prior to calling `os.fsync`.
- Any exception during the write loop or `os.fsync` immediately marks the writer poisoned.

- **Reason:** Guards against torn records caused by signal interruptions or partial writes.
- **Cost:** Loop logic overhead around `os.write`.
- **Escape Hatch:** None.

### D-EV-33: Countersigner Entry Chain Validation and Key Separation

- The CLI `checkpoint` subcommand verifies the entry chain with `entry_pub` and asserts key separation (`priv_matches_pub(key, entry_pub) == False`) before signing a checkpoint.
- If the chain contains double-spends or the checkpoint private key corresponds to the entry public key, signing is refused.

- **Reason:** Countersigners must never certify invalid chains or collapse role separation.
- **Cost:** Pre-verification and canonical DER comparison prior to checkpoint issuance.
- **Escape Hatch:** None.

### D-EV-34: Mutation Survivor Defenses for Round 3

- Dedicated test cases in `scripts/test/lib/evidence_addendum3.py` validate checkpoint instance/epoch cross-checks, CLI option mutual exclusion, writer reload spend-once, shared flock in checkpoint generation, and full verification on fresh open.

- **Reason:** Closes all mutation test survivor paths identified during third-round architectural review.
- **Cost:** Test maintenance.
- **Escape Hatch:** None.

### D-EV-35: Decision Record Alignment for Round 3

- Updates ADR documentation to reflect poison-on-shrink semantics (D-EV-28), advisory flock rename-swap residual risks, and short-write handling.

- **Reason:** Preserves documentation integrity and architectural accuracy.
- **Cost:** None.
- **Escape Hatch:** None.

### D-EV-36: Countersigner Atomicity and Lock Ordering

- `cmd_checkpoint` must hold an exclusive lock (`fcntl.LOCK_EX`) on the checkpoint export file (`os.O_RDWR | os.O_CREAT`) for the entire countersigning sequence: reading existing checkpoints -> verifying them -> acquiring `LOCK_SH` on the chain file -> reading and verifying the chain -> building the checkpoint -> signing -> appending -> fsyncing.
- Lock ordering is strictly hierarchical: **checkpoint file first (`LOCK_EX`), then chain file (`LOCK_SH`)**. No reverse lock order exists anywhere in the platform.
- New checkpoints must strictly advance the chain (`count > prev_count`). If `count <= prev_count`, the countersigner refuses with an error message and exits non-zero; losing concurrent processes never write duplicate `cp_seq` numbers.

- **Reason:** Prevents race conditions where concurrent countersigners generate duplicate `cp_seq` numbers or corrupt the export store.
- **Cost:** Serialization of checkpoint export operations.
- **Escape Hatch:** Checkpoints are generated periodically, so serialization does not impede writer performance.

### D-EV-37: Countersigner Pre-Verification of Existing Checkpoints

- Before extending a checkpoint export store, the countersigner verifies all existing checkpoints with the checkpoint public key (validating `cp_hash`, `prev_cp_hash` link, contiguous `cp_seq`, monotonically increasing `count`/`epoch`, and cryptographic signatures).
- If the existing checkpoint chain contains a forged, corrupted, or unsigned record (e.g. invalid `cp0`), the countersigner refuses to extend and exits 1.

- **Reason:** Prevents a countersigner from appending valid signatures to an already-compromised or tampered checkpoint store.
- **Cost:** O(checkpoints) verification pass on each checkpoint creation.
- **Escape Hatch:** None; checkpoint stores are small.

### D-EV-38: Standardized Usage and Diagnostic Exit Codes

- The evidence CLI uses `EvidenceArgumentParser`, overriding `argparse.ArgumentParser.error` to exit with standard code `64` (`EX_USAGE`) on syntax or usage errors.
- Exit code `2` is strictly and exclusively reserved for `cmd_verify`: indicating that cryptographic integrity is verified, but unresolved intents remain and `--fail-on-unresolved` was specified.

- **Reason:** Enables orchestration pipelines to distinguish CLI invocation errors from unresolved execution findings.
- **Cost:** Custom ArgumentParser error handler.
- **Escape Hatch:** Standard UNIX sysexits conventions.

### D-EV-39: Canonical Lowercase Hexadecimal Enforcement

- All 64-character cryptographic hashes (`prev_hash`, `entry_hash`, `head_hash`, `prev_cp_hash`, `cp_hash`) and signatures (`sig`) must consist exclusively of lowercase hexadecimal characters (`[0-9a-f]`).
- Any uppercase or mixed-case hexadecimal characters are rejected as structural integrity errors during shape validation.

- **Reason:** Ensures deterministic byte-for-byte canonical reproducibility and prevents canonical bypass attacks via casing variations.
- **Cost:** Character set validation restricted to lowercase hex.
- **Escape Hatch:** None.

### D-EV-40: Cached Head Hash In-Place Tamper Detection (Mutation M10)

- During incremental synchronization under lock (`_sync_under_lock`), `ChainWriter` verifies that the entry at `cached_count - 1` re-computes to `cached_head_hash`.
- In-place modifications to the last entry's body on disk (even if file size remains identical) are detected, causing the writer to poison itself and raise `ChainIntegrityError`.

- **Reason:** Eliminates mutation survivor M10 where an in-place modification of the head entry went undetected.
- **Cost:** Re-hashing the cached last entry on append.
- **Escape Hatch:** None.

### D-EV-41: Genesis Checkpoint Head Anchor and Verifier Key Type Strictness

- A checkpoint with `count=0` explicitly anchors an empty chain and MUST specify `head_hash == GENESIS_PREV_HASH` (64 ASCII zeros); any other `head_hash` is rejected as an integrity error.
- `OpensslVerifier` verifies on initialization that the provided key is a valid public key (`openssl pkey -pubin -in K -noout` succeeds) and that its PEM header does not contain `PRIVATE KEY`. Passing a private key to `OpensslVerifier` raises `ValueError`.

- **Reason:** Enforces explicit genesis boundary semantics and prevents key confusion or accidental leakage of private keys into verifier contexts.
- **Cost:** Key inspection and openssl preflight on verifier construction.
- **Escape Hatch:** None.

### D-EV-42: Decision Record Alignment and Risk Documentation (Round 4)

- Updates this ADR to formally specify all Addendum 4 decisions (D-EV-36..42), documents the strict checkpoint-first lock hierarchy, fixes D-EV-1 I/O descriptions, clarifies spend-once restart limitations in residual risk 4, and repairs markdown table formatting.

- **Reason:** Comprehensive auditability and synchronization between implementation, tests, and documentation.
- **Cost:** Documentation maintenance.
- **Escape Hatch:** None.

## Protocol Sequence

The following sequence illustrates the normal lifecycle and failure behaviors across the governance components:

```text

Caller / PEP                Signer                  Evidence File           Executor
    |                          |                          |                     |
    |--- 1. build intent ----->|                          |                     |
    |    (grant_id, hash)      |                          |                     |
    |<-- sign entry -----------|                          |                     |
    |--- 2. append_intent ------------------------------->| (fsync)             |
    |                                                     |                     |
    |=== 3. consume grant (PDP) ===                       |                     |
    |                                                                           |
    |--- 4. execute action ---------------------------------------------------->|
    |<-- execution outcome -----------------------------------------------------|
    |                                                                           |
    |--- 5. build result ----->|                          |                     |
    |    (intent_seq, outcome) |                          |                     |
    |<-- sign entry -----------|                          |                     |
    |--- 6. append_result ------------------------------->| (fsync)             |
```

### Failure Behaviors and Verifier Reports

| Step | Failure Condition | PEP / System Action | Chain State | Verifier Report |
|---|---|---|---|---|
| 1-2 | Intent append fails (disk full, permission, I/O error) | **Fail-closed:** Abort immediately. Grant is **not** consumed; executor is **not** called. | No entry added. | Clean. No double-spend recorded. |
| 3 | Intent appended, but grant consumption fails | Abort execution. No result entry created. Grant remains spent in local chain. | Intent recorded without result. | Finding: "consumed, outcome unknown". Exit 0 (Exit 2 if `--fail-on-unresolved`). |
| 4 | Execution crashes, times out, or host panics | Host dies or process terminated before result written. | Intent recorded without result. | Finding: "consumed, outcome unknown". On restart, grant replay is refused. |
| 5-6 | Execution completes, but result append fails | Execution occurred, but result record cannot be committed. | Intent recorded without result. | Finding: "consumed, outcome unknown". System marks grant consumed. |
| Full | Happy path completion | Intent and result both committed and fsynced. | Both entries present and linked. | Clean verification (PASS). Exit 0. |

## Residual Risks

1. **Signing Oracle:** The writer process does not hold private keys, but an attacker with root or runtime execution access on the PEP host can invoke the local signing subprocess to sign arbitrary new unanchored entries. This is mitigated primarily by frequent countersigned checkpoints exported to an isolated WORM store and rate/policy controls at the signer boundary.
2. **Advisory POSIX Flock and Shared Filesystem Limitations (NFS caveat):** Chain concurrency relies on `fcntl.flock` on the chain file's own open file descriptor. `fcntl.flock` is advisory on POSIX systems; non-cooperating processes that do not acquire the lock can still write to the file. Furthermore, flock semantics across Network File Systems (NFS) or distributed filesystems are notoriously unreliable, non-blocking, or disabled. The evidence chain strictly requires a local POSIX filesystem; distributed multi-host agents must maintain independent chain files rather than sharing a single network-mounted file.
3. **Out-of-Band Checkpoint / Anchor Delivery and Explicit Opt-in:** A verifier must receive authentic checkpoints or an expected anchored count out of band. Supplying an empty checkpoint file or list is treated as a hard failure (D-EV-20). If an adversary intercepts verifier inputs, they cannot bypass verification with an empty checkpoint file; verifying an unanchored chain requires explicit caller opt-in via `--no-checkpoints` (or API `allow_no_checkpoints=True`), which explicitly reports `unanchored_tail=all`.
4. **Scope of Replay Protection and Spend-Once Across Restarts:** An open `ChainWriter` retains all consumed grants in memory and poisons itself immediately upon detecting truncation or tampering, preventing keyless spend-spend replay on the active writer (D-EV-28). However, the writer does not read checkpoints; after a restart, if a chain file was truncated, the restarted writer only derives consumed grants from the surviving on-disk entries and could re-spend a grant that was already anchored in an earlier exported checkpoint. The verifier catches this double-spend (exit 1) when checking against the checkpoint store. Spend-once at write time across restarts is therefore best effort, and the Policy Decision Point (PDP) must bound grant validity windows out of band.
5. **Checkpoint Store Immutability (WORM):** The security model relies on the checkpoint export store being append-only (e.g. AWS S3 Object Lock in compliance mode or GCP Bucket Lock). If an attacker can overwrite or delete checkpoints in the export store, rollback and fork detection is compromised.
6. **Instance-Key Certification and Epoch Rotation:** Key issuance, attestation (e.g. SPIFFE / TPM), and epoch rollover lifecycle are not handled by this reference specification and remain the responsibility of the deployment platform.
7. **Low-Entropy Argument Guessing:** Storing a plain SHA-256 `invocation_hash` without a secret salt means callers with access to the chain could brute-force guess low-entropy arguments (e.g. boolean flags, small identifiers).
8. **Canonical JSON Subset vs Full RFC 8785:** The specification uses Python's standard library `json.dumps` with strict sorting and separators rather than a complete RFC 8785 parser. Cross-language re-implementations must strictly adhere to these canonical formatting rules.
9. **Reference Implementation Status:** This specification establishes the reference algorithms and CLI under `scripts/evidence/`. It is not yet wired into production PEP gateways or cluster controllers.
10. **Per-Append Cost Model after Incremental Caching (D-EV-22):** Steady-state entry appends operate in O(1) cryptographic operations: exactly 1 Ed25519 signing subprocess call and 0 verification calls, with O(new bytes) disk I/O. Only writer initialization (a fresh open) or process restart performs a full O(chain) scan verifying all historical signatures; an already-open writer encountering truncation poisons itself immediately without silent recovery (D-EV-28).
11. **Advisory Flock and Rename-Swap Residual Risk (D-EV-31):** Because POSIX `fcntl.flock` is advisory, non-cooperating processes or attackers with directory write access could perform an atomic rename/swap (`os.rename`/`os.replace`) of the chain file. `ChainWriter` detects rename-swaps only best-effort at append time by verifying under the held lock that `os.fstat(locked_fd)` and `os.stat(path)` match on `st_ino` and `st_dev`. If an atomic swap occurs between appends, the writer detects the mismatch on the next append attempt and poisons itself.
12. **Short-Write Handling (D-EV-32):** To guard against partial writes caused by signals or non-blocking fd behavior, all record appends loop over `os.write` until all bytes are committed before invoking `os.fsync`. Any write or fsync failure poisons the writer immediately.
13. **Lock Hierarchy and Countersigner Atomicity (D-EV-36):** To prevent deadlocks, the lock hierarchy is strictly defined: the countersigner holds `fcntl.LOCK_EX` on the checkpoint export file across the entire countersigning cycle before acquiring `fcntl.LOCK_SH` on the chain file. No component in the system ever acquires locks in reverse order (chain file then checkpoint file). A losing concurrent countersigner detects that the chain has not advanced (`count <= prev_count`), refuses to sign, and exits non-zero without writing duplicate checkpoints.

## Negative Test Mapping

The test suite (`scripts/test/lib/check-evidence-chain.py`, `scripts/test/lib/evidence_attacks.py`, `scripts/test/lib/evidence_addendum2.py`, `scripts/test/lib/evidence_addendum3.py`, and `scripts/test/lib/evidence_addendum4.py`) validates the evidence chain against hostile mutations and edge cases:

| Threat / Test Condition | Attack Simulation | Expected Behavior | Verifier Exit Code |
|---|---|---|---|
| Happy path | Standard sequence with valid checkpoint | Passes with 0 errors | Exit 0 |
| Field tampering | Mutate a single payload field in an entry | Detection of `entry_hash` and `sig` mismatch | Exit 1 |
| Entry deletion (gap) | Delete an intermediate entry (`seq=1`) | Detection of sequence gap and broken `prev_hash` | Exit 1 |
| Entry reordering | Swap positions of two valid entries | Detection of `prev_hash` and `seq` mismatch | Exit 1 |
| Tail truncation (with checkpoint) | Truncate entries covered by exported checkpoint | Detection: `len(entries) < count` or `head_hash` mismatch | Exit 1 |
| Tail truncation (no checkpoint) | Truncate unanchored entries | Passes integrity, reports reduced `unanchored_tail` | Exit 0 |
| Torn last line | Partial/truncated JSON line at end of file | Writer refuses to open (raises); reader fails integrity | Exit 1 |
| Signature forgery | Re-compute `entry_hash` after edit without private key | Cryptographic signature verification fails | Exit 1 |
| Missing verification key | Call `verify()` with missing/None public key | Strict rejection: raises `TypeError` or `ValueError` | N/A (Exception) |
| Identical public keys | Provide identical key for `entry_pub` and `cp_pub` | Key separation rejection: fails verification | Exit 1 |
| Chain rollback / fork | Replace tail with validly signed alternate entries | Checkpoint `head_hash` mismatch | Exit 1 |
| Grant replay | Append second `intent` with same `grant_id` | Writer rejects append; verifier detects duplicate grant | Exit 1 |
| Corrupted result linkage | Result with mismatched `grant_id` or bad `intent_seq` | Relational integrity failure | Exit 1 |
| Unresolved intent | Intent appended without corresponding result | Finding reported: "consumed, outcome unknown" | Exit 0 (Exit 2 if requested) |
| Float rejected | Attempt to serialize entry containing float/NaN | Canonicalizer raises `ValueError` (D-EV-3) | N/A (Exception) |
| Checkpoint key confusion | Checkpoint signed with entry instance key | Checkpoint cryptographic signature verification fails | Exit 1 |
| Append failure fail-closed | Append attempted on read-only / failing file | Writer raises I/O error; no intent written | Fail-closed |
| Double spend in verify (D-EV-18) | Forged chain containing two intents with same grant | Verifier detects duplicate grant_id | Exit 1 |
| Duplicate result (D-EV-18) | Forged chain containing two results for same intent | Verifier detects duplicate result | Exit 1 |
| Invocation hash mismatch (D-EV-18) | Result invocation_hash differs from matching intent | Relational integrity failure | Exit 1 |
| Forward/invalid intent_seq (D-EV-18) | Result points to forward or nonexistent intent_seq | Sequence ordering failure | Exit 1 |
| Epoch decrease (D-EV-18) | Entry with epoch lower than previous entry | Monotonicity failure | Exit 1 |
| Instance ID mismatch (D-EV-18) | Entry with differing instance_id | Scope isolation failure | Exit 1 |
| Checkpoint cp_seq gap (D-EV-18) | Missing intermediate checkpoint record | Sequence gap failure | Exit 1 |
| Previous checkpoint hash break (D-EV-18) | Checkpoint prev_cp_hash mismatch | Cryptographic link failure | Exit 1 |
| Checkpoint hash tamper (D-EV-18) | Checkpoint cp_hash corrupted | Checkpoint integrity failure | Exit 1 |
| Checkpoint count decrease (D-EV-18) | Checkpoint count lower than preceding checkpoint | Monotonicity failure | Exit 1 |
| Non-latest checkpoint mismatch (D-EV-18) | Historical checkpoint head does not match entry | Head link failure | Exit 1 |
| Writer open verification (D-EV-18) | Corrupt entry hash before opening writer | Writer refuses initialization | Fail-closed |
| Missing chain/checkpoint file (D-EV-18) | CLI / verify invoked on nonexistent paths | File existence check fails | Exit 1 |
| Duplicate JSON key (D-EV-18) | Line containing duplicate dictionary keys | Parser rejects duplicate key | Exit 1 |
| Non-canonical whitespace (D-EV-18) | Line with uncompact whitespace or spacing | Canonical byte comparison fails | Exit 1 |
| Concurrent writers flock (D-EV-18) | 4 concurrent processes append same grant | Exactly 1 succeeds, 3 rejected | Safe serialization |
| Key separation DER (D-EV-18) | Keys with identical DER but different PEM text | DER equality detects identical key | Exit 1 |
| Fsync failure poisoning (D-EV-18) | Monkeypatched fsync failure marks writer poisoned | Subsequent writer calls fail closed | Fail-closed |
| Concurrent signing integrity (D-EV-18) | 200 threads concurrently sign and verify | All signatures verify cleanly | Thread-safe |
| Writer open sig verify (D-EV-26) | Valid hash but corrupted signature on disk | Writer initialization refuses to open | Fail-closed |
| Writer reload spend-once (D-EV-26) | Reopened writer attempts consumed grant | Reloaded consumed grants prevents replay | Fail-closed |
| Checkpoint cross-checks (D-EV-26) | Checkpoint epoch or instance mismatch with entry | Relational and scope integrity failure | Exit 1 |
| Writer epoch lower-bound (D-EV-26) | Writer opened with epoch lower than chain | Monotonicity failure on open | Fail-closed |
| CLI checkpoint extension (D-EV-26) | Checkpoint head does not extend previous head | Countersigner refuses to sign non-extending head | Exit 1 |
| Checkpoints XOR requirement (D-EV-26) | Both or neither of checkpoints and no-checkpoints | Strict XOR validation error | Exit 1 |
| Duplicate JSON key independent (D-EV-26) | Line with duplicate keys parsed directly | Object pairs hook rejects duplicate key | Exception |
| Directory fsync on creation (D-EV-26) | First write in new directory triggers fsync | Directory fd verified fsynced via monkeypatch | Durability |
| Symlink writers serialize (D-EV-26) | Two writers via symlink / hardlink contend on grant | Per-inode flock serializes append operations | Safe serialization |
| Incremental append scale (D-EV-22) | 300 appends tested; 300th append subprocess calls | Verified <= 3 subprocess calls (O(1) append) | Pass |
| Malformed record types (D-EV-23) | Invalid types (string count, list grant, list entry) | Structured integrity errors without crash | Exit 1 |
| Key separation fail-closed (D-EV-25) | Unparseable key passed to keys_are_identical | Fails closed with exception / report error | Fail-closed |
| Countersigner atomicity (D-EV-36) | 6 concurrent countersigners on unadvanced chain | Exactly 1 succeeds, 5 fail cleanly; no duplicate cp_seq | Safe serialization |
| Checkpoint store pre-verification (D-EV-37) | Corrupt or forged cp0 in checkpoint export file | Countersigner refuses extension | Exit 1 |
| CLI exit codes (D-EV-38) | Missing or invalid arguments to CLI subcommands | Exits with code 64 (EX_USAGE); exit 2 reserved for verify | Exit 64 |
| Canonical lowercase hex (D-EV-39) | Uppercase or mixed-case characters in hashes or signatures | Shape validation rejects non-lowercase hex | Exit 1 |
| Cached head hash tamper (D-EV-40) | In-place byte modification of last entry keeping file size | Writer catches head hash mismatch, poisons immediately | Fail-closed |
| Checkpoint count=0 anchor (D-EV-41) | Checkpoint with count=0 but non-genesis head_hash | Verifier rejects non-genesis anchor | Exit 1 |
| Verifier rejects private key (D-EV-41) | Pass private key PEM file to OpensslVerifier | Rejected on initialization (not public key) | ValueError |

## Alternatives Considered and External Prototypes

- **Vendor-Neutral Architecture:** The architecture deliberately uses standard POSIX semantics, OpenSSL Ed25519,

  and Python 3.10 standard library components without proprietary vendor cloud SDKs or blockchain frameworks.
- **External Prototype Evaluation (`shagunlabs/mcp-guard`):** During architecture review, the external prototype
  `shagunlabs/mcp-guard` was evaluated. At the time of review, its repository README explicitly marked core capabilities
  as "Not yet built," lacking countersigned checkpoints, two-phase intent/result commit ordering, and restart-resilient
  spend-once grant derivation. Consequently, it was not adopted or depended upon.
- **Centralized Database Audit Logging:** Logging directly to a remote relational database or Elasticsearch was rejected
  because network partitions would break local execution, and a compromised node could forge past logs without cryptographic chaining.
- **Distributed Consensus (Raft / BFT):** Running an in-cluster consensus cluster for evidence writes was rejected due
  to excessive latency and resource overhead on constrained agent edge nodes.
