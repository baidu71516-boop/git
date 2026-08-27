# Web Release Gate Stability — Release Policy Contract Freeze

**Status:** FROZEN

**Decision:** Approach A — deterministic full-suite Vitest with one worker

**Contract baseline:** `4661ea0e85a74521a20482f45587b2d36447c7e9`

**Scope:** Web release/test architecture, test tooling, commands, CI wiring, evidence, and runbook only

## 1. Purpose and boundaries

This contract replaces default-max-parallel Web Vitest as release authority with one deterministic, resource-safe gate. It preserves every discovered Web test and assertion, preserves existing per-test timeout semantics, keeps cross-file process-lifetime visibility, and fails closed on coverage loss or ambiguous execution.

This contract does not implement the runner. It does not authorize product-code changes, test assertion changes, migrations, deployment, production access, or modifications to the C3A or D1A.1 feature trees.

## 2. Measured root problem

At the frozen baseline:

- `apps/web/package.json` defines `test` as `vitest run`.
- Root `pnpm test` delegates to the Web package.
- `make test` runs the backend/API/worker suites and then root `pnpm test`.
- CI invokes `make test`.
- `apps/web/vitest.config.ts` selects `jsdom`, globals, and `tests/setup.ts`, but leaves pool size, file parallelism, isolation, retries, and timeouts at Vitest defaults.
- Vitest `4.1.10` therefore uses `pool=forks`, `fileParallelism=true`, `isolate=true`, `retry=0`, a 5-second default test timeout, 10-second hook and teardown timeouts, and up to `availableParallelism - 1` workers.
- The release machine exposes 15 parallel slots, so the default run can create 14 fork workers. Each active UI file can load a separate jsdom environment plus React, AntD, and React Query.
- Vitest discovery finds 44 test files and 296 fully qualified tests. No skip/todo/only marker was found. These counts describe the baseline; they are not the coverage authority.

Measured on the exact baseline and worktree during this contract task:

| Execution | Result | Wall time | Interpretation |
| --- | --- | ---: | --- |
| Exact current `pnpm test` | 41/44 files and 293/296 tests passed; three timeouts | 42.83s | Failed under 14-worker pressure |
| The three affected files together with `--no-file-parallelism` | 3/3 files and 42/42 tests passed | 65.79s | Same assertions and timeouts pass under controlled pressure |
| Full suite with `--no-file-parallelism` | 44/44 files and 296/296 tests passed | 226.64s | Approach A is feasible on the release machine |
| Approved six-file interaction set with `--detectAsyncLeaks` | 6/6 files and 67/67 tests passed; 34 resources reported; exit zero | 93.32s | The detector is diagnostic only on the present baseline |

The default run timed out in unrelated Bulk Import, Data Collection, and Influencer List tests. It accumulated 306.43 seconds of test CPU inside 42.83 seconds of wall time. The full sequential run had approximately one unit of CPU work per unit of wall time. Together with shifting historical timeout locations and isolated/controlled passes, this measures CPU scheduling, jsdom/module duplication, and console work as the current contention mechanism. It does not prove that lifecycle defects are impossible; the authoritative gate must continue to expose them under controlled execution.

Vitest's current async-leak detector is not a blocking gate. It reports baseline framework/test resources, including React Query promises, timers, and AntD message ports, but exits successfully. This contract forbids converting those reports into a permanent allowlist. Making async-leak detection blocking requires a separate contract and a zero-leak baseline.

## 3. Options evaluated

| Option | Assertion equivalence | Cross-file leak detection | Resource isolation | Runtime and usability | Determinism / CI | Decision |
| --- | --- | --- | --- | --- | --- | --- |
| A. One full Vitest process, one worker | Full | Strong: one worker lives across every file in the invocation | One jsdom file at a time; no multi-worker surge | Measured at 226.64s; acceptable for a blocking release gate | Simple, fixed, portable | **Selected** |
| B. Deterministic fresh-process batches | Full if inventory is exact | Weaker across batch boundaries; needs compensating groups | Stronger memory reset | More startup cost and orchestration | Deterministic but more complex | Rejected while A remains stable |
| C. Fresh process for heavy files, batches for the rest | Full if classification stays correct | Weakest at manually chosen boundaries | Strongest isolation | Brittle heavy-file taxonomy and highest maintenance | CI-compatible but matrix-prone | Rejected |
| D. Default parallel remains canonical | Full in theory | Broad concurrency stress | Poor on the release machine | Fast when green, currently non-repeatable | Not reliable enough for a release decision | Diagnostic only |

Approach A is selected because it preserves the broadest process-lifetime interaction signal while directly removing the measured source of resource contention. It is not selected merely because one run passed.

## 4. Single authoritative command

The sole release authority is:

```text
pnpm test:web:release
```

The implementation must satisfy all of the following:

1. Root and Web package scripts may alias this entry point, but may not reproduce its Vitest flags independently.
2. `make test` must invoke this same implementation for its Web leg.
3. CI release validation must invoke this same implementation, directly or through `make test`.
4. No separately maintained "equivalent" release command is permitted.
5. `pnpm test:web:manifest:update` is the one explicit inventory-update command. It is not a release command and must never run automatically from `pnpm test:web:release`.
6. `pnpm test:web:release -- --controlled <test-path>...` may reuse the same runner for stress-failure adjudication. Controlled mode is diagnostic, may execute only named manifest tests, and can never produce a release PASS by itself.

## 5. Exact canonical execution model

### 5.1 Process boundaries

Every top-level `pnpm test:web:release` call is a fresh release invocation. There is no persistent daemon, server, or worker shared between invocations.

While holding the host-wide lock, the runner performs inventory collection and starts a fresh Vitest child for the canonical full-suite leg. That child has:

- one coordinator;
- `pool=forks`;
- exactly one worker (`maxWorkers=1`);
- `fileParallelism=false`;
- `isolate=true`;
- `retry=0`;
- `allowOnly=false`;
- file and test shuffling disabled;
- no CLI or runner override for Vitest test, hook, or teardown timeouts.

The worker is reusable across all files inside that Vitest leg. File isolation resets the file environment and module/mocking state, while the worker process lifetime remains shared so residual timers, listeners, handles, memory growth, or CPU work can affect later files and remain observable.

If the interaction trigger fires, the top-level command starts a second fresh Vitest child after the full-suite child exits successfully. That child uses the same resolved execution settings and one worker across the six interaction files. The full-suite and interaction children do not reuse a worker with each other, and no child survives the top-level command.

### 5.2 Required resolved configuration evidence

The runner must stream test output and log a machine-readable resolved-configuration record before each Vitest leg. At minimum it records:

- command version and leg name;
- repository path, branch, exact Git SHA, and clean/dirty status;
- Node, pnpm, and Vitest versions;
- `pool=forks`;
- `maxWorkers=1`;
- `fileParallelism=false`;
- `isolate=true`;
- `retry=0`;
- `allowOnly=false`;
- shuffle disabled;
- resolved default test, hook, and teardown timeouts;
- an explicit statement that the runner supplied no timeout override;
- normalized ordered test-file paths;
- manifest digest and discovered file/test diagnostics.

The resolved values must come from Vitest's runtime-resolved configuration through the release reporter/runner integration, not from echoing the requested CLI arguments. A requested/resolved mismatch aborts the leg before any test can support PASS.

A dirty worktree may be tested for development feedback, but its result must be labeled `NOT_RELEASE_EVIDENCE` and cannot support a release PASS.

### 5.3 Deterministic ordering

The full-suite order is generated from normalized repository-relative POSIX paths and sorted by a locale-independent string comparison. All current paths are ASCII. Tests inside a file retain source declaration order; concurrent test sequencing and random shuffling remain disabled.

The implementation must provide one release sequencer owned by the canonical runner. The sequencer has two modes:

- full-suite mode: normalized lexical file order;
- interaction mode: the exact order frozen in Section 7.

Vitest cache timing, prior failures, filesystem enumeration order, locale, and CLI filter order must not change either order. The resolved ordered list is included in evidence.

## 6. Test inventory protection

### 6.1 Manifest contents

The manifest is `apps/web/tests/release-test-manifest.json`. It is generated and version-controlled, and must contain:

- a schema version;
- every normalized repository-relative Web test-file path;
- every fully qualified test identity associated with its file;
- stable sorting;
- no timestamps, absolute paths, machine data, or other noisy fields.

The baseline diagnostics are 44 files and 296 tests. The manifest identities—not those two counts—are authoritative.

### 6.2 Three-way equality

Before the canonical full-suite leg starts, the runner must prove exact equality among:

1. independent filesystem discovery under `apps/web/tests` for `*.test.ts`, `*.test.tsx`, `*.spec.ts`, and `*.spec.tsx`;
2. `vitest list --json` file paths and fully qualified identities under the canonical config;
3. the committed manifest.

After execution, the machine-readable Vitest result must prove that every manifest identity executed exactly once and that no unmanifested identity executed. Every identity must have a passing terminal status. A missing, duplicate, unexpected, skipped, pending, todo, or exclusive test blocks release.

`allowOnly=false` must remain an execution-level defense. Source scanning may provide an earlier error, but a regex scan is not the sole inventory or mode-safety mechanism.

### 6.3 Intentional updates

The only update path is:

```text
pnpm test:web:manifest:update
```

The update command regenerates the manifest deterministically and leaves the resulting change visible in Git. It must reject skip/todo/only modes rather than bless them. It must not run tests, commit, or be invoked by the release command.

Adding, deleting, moving, or renaming a test therefore creates a reviewable manifest diff. Any manifest removal or identity loss requires explicit review justification. A release gate never infers that a smaller manifest is intentional.

## 7. Candidate/Bulk/C3A interaction gate

The one approved controlled interaction group is, in exact order:

1. `apps/web/tests/candidate-pool-detail-tabs.test.tsx`
2. `apps/web/tests/candidate-selection.test.ts`
3. `apps/web/tests/candidate-run-detail-selection.test.tsx`
4. `apps/web/tests/bulk-import-workspace.test.tsx`
5. `apps/web/tests/bulk-preview-workflow.test.tsx`
6. `apps/web/tests/data-collection-workspace.test.tsx`

The interaction leg uses one fresh Vitest coordinator and one reusable fork worker for all six files, with file isolation enabled and unchanged per-test timeouts. Its baseline count of 6 files / 67 tests is diagnostic only; the same manifest identity and terminal-status checks apply to the selected identities.

This Candidate-to-Bulk order complements the full-suite lexical order, which exercises Bulk before Candidate. It provides both contamination directions without a manually maintained all-pairs matrix.

### 7.1 Fail-safe trigger

The runner determines the diff from an explicit, validated release base supplied by CI/release orchestration. If the base is absent, invalid, unreachable, or the diff cannot be parsed confidently, the interaction group runs.

The group must run when the diff contains any of the following:

- any path below `apps/web/`;
- root `package.json`, `pnpm-lock.yaml`, or `pnpm-workspace.yaml`;
- `Makefile` or CI/workflow changes that can affect Web test execution;
- a rename, copy, deletion, binary change, or unmerged path whose Web relevance cannot be classified confidently.

This deliberately covers Candidate Pool/Run components, selection helpers, Bulk Preview/Import code, QueryClient and test harnesses, shared Web setup, routing/polling infrastructure, and the relevant test files without maintaining a large domain matrix.

The group may be skipped only when a valid diff proves that every changed path is outside those surfaces. Any classification error, unknown Web path, or missing evidence resolves to **run**, never **skip**. The trigger decision and matching paths are logged.

## 8. Timeout, retry, skip, and watchdog policy

- Vitest's current 5-second default test timeout remains unchanged.
- Vitest's current 10-second hook and teardown defaults remain unchanged.
- Existing explicit per-test timeouts remain unchanged.
- The runner must not pass `testTimeout`, `hookTimeout`, or `teardownTimeout` overrides.
- Automatic test retry remains exactly zero in every blocking and controlled leg.
- No skipped/pending/todo/only test, flaky-test allowlist, ignored failure, or assertion weakening is permitted.
- Output is streamed so assertions and timeouts are visible when Vitest reports them. The runner does not wait until the end to hide output.

The top-level release invocation has one 15-minute process-level watchdog, measured after lock acquisition and covering inventory, the full-suite leg, and any required interaction leg. It does not alter Vitest's per-test clocks.

If the watchdog expires, the runner must:

1. mark the release gate failed;
2. send graceful termination to the active Vitest process group;
3. wait no more than five seconds;
4. force-terminate any remaining descendants;
5. write the partial evidence and watchdog classification;
6. release the host-wide lock in a `finally` path.

Watchdog termination is never a skipped or retried run.

## 9. Host-wide concurrency lock

The canonical runner must serialize release invocations across worktrees owned by the same host user. Controlled adjudication, release-evidence stress runs, and standalone manifest collection/update must use the same lock so they cannot contend with a canonical leg. The lock namespace is derived from the OS temporary root, the stable project slug `influencer-outreach-web-release-gate`, and the effective user identity; it must not include the worktree path.

Acquisition uses an atomic lock-directory operation. Owner metadata includes PID, OS process-start identity when available, acquisition time, worktree, Git SHA, and a random ownership token.

- If ownership is live, acquisition fails fast as `GATE_LOCK_HELD`; no tests execute and the release cannot PASS.
- A lock is stale only when the implementation can positively establish that its owner no longer exists or no longer matches the recorded process-start identity.
- If staleness cannot be proven, acquisition fails closed rather than permitting concurrency.
- Proven-stale recovery atomically renames the old lock to a unique quarantine name before reacquiring. Competing recovery attempts must not both enter the gate.
- Normal exit, failure, signal handling, and watchdog handling release only the lock whose ownership token matches the current runner. A runner must never delete a successor's lock.

Lock acquisition/recovery races may repeat the atomic acquisition step; this is coordination, not a test retry. No Vitest leg is automatically retried.

## 10. Default-parallel stress role

Default-max-parallel Vitest is renamed/exposed as:

```text
pnpm test:web:stress
```

It remains a truthful stress/diagnostic signal and is not release authority. Release-candidate validation and the baseline/feature proof in Section 12 must execute it once and retain its output. `NOT_EXECUTED` means evidence is incomplete and cannot support a release PASS.

A default-parallel timeout is classified `STRESS_RESOURCE_CONTENTION_NON_BLOCKING` only when all of the following are true:

1. the canonical full-suite gate is completely green;
2. the relevant interaction gate is completely green;
3. every affected test/file passes in a fresh controlled mode of the canonical runner with the same one-worker settings and unchanged timeouts;
4. the stress run contains no assertion failure;
5. controlled execution does not reproduce a lifecycle defect;
6. the original stress failure remains recorded and reported.

The controlled run is adjudication, not an automatic retry and not a way to rewrite the stress result as green.

Any stress assertion failure, abnormal exit, or reproducible changed-area/lifecycle defect blocks release. A timeout that does not satisfy every condition above also blocks release. No flaky-test allowlist is permitted.

## 11. Failure classification and release decision

| Classification | Required result |
| --- | --- |
| Canonical assertion failure | `RELEASE_BLOCKED` |
| Canonical Vitest test/hook/teardown timeout | `RELEASE_BLOCKED` |
| 15-minute watchdog expiry | `RELEASE_BLOCKED` |
| Canonical abnormal exit, signal, crash, or missing result | `RELEASE_BLOCKED` |
| Filesystem/list/manifest/executed identity mismatch | `RELEASE_BLOCKED` |
| Skip, pending, todo, only, duplicate execution, or missing execution | `RELEASE_BLOCKED` |
| Required interaction assertion, timeout, inventory, or process failure | `RELEASE_BLOCKED` |
| Live/ambiguous lock ownership or unusable environment | `NOT_EXECUTED`; release cannot PASS |
| Default-parallel timeout satisfying every Section 10 condition | `STRESS_RESOURCE_CONTENTION_NON_BLOCKING`; report truthfully |
| Default-parallel assertion, abnormal exit, or reproducible controlled defect | `RELEASE_BLOCKED` |

There is no automatic rerun path from a blocking canonical result to PASS. A new operator-initiated invocation is new evidence and does not erase the prior failure.

## 12. Required implementation proof

### 12.1 Validation composition invariant

The new command cannot exist in an untouched historical SHA. Each proof therefore uses a clean, disposable validation worktree composed of:

1. the exact product/tree SHA named below; and
2. the candidate release-tooling commit containing only the paths permitted by Section 14.

Evidence records `product_tree_sha`, `release_tooling_sha`, the resulting validation `HEAD`, and the complete diff from the product SHA. That diff must contain only approved release tooling, generated manifest, tooling tests, CI/Make/package wiring, and documentation. Application source, existing feature tests/assertions, migrations, and dependencies unrelated to the runner must remain byte-for-byte at the named product SHA.

The historical feature branch/ref is never moved or edited. The disposable validation composition and its worktree must be clean when tested. A composition containing any unapproved product/test behavior change is `NOT_EXECUTED` for this proof, not PASS.

### 12.2 Production baseline

Using the exact product tree `4661ea0e85a74521a20482f45587b2d36447c7e9` under the validation invariant above:

1. run `pnpm test:web:release` three consecutive times;
2. each call must create fresh Vitest child processes and acquire/release the shared lock;
3. all three canonical full-suite legs must PASS with exact inventory equality;
4. any required interaction leg must PASS;
5. no source, dependency, manifest, configuration, or environment change may occur between the three runs;
6. a failure resets the consecutive-pass sequence and blocks implementation acceptance until investigated;
7. run and retain one default-parallel stress result and adjudicate it under Section 10 if necessary.

Three consecutive fresh passes are the minimum stability proof. More repetitions are not required unless one of the three fails or evidence is internally inconsistent.

### 12.3 C3A

Using the exact C3A product tree `c7d5bba6ff20462fe5ef3621afe8ea5d59be730a` under the validation invariant above:

- canonical full-suite gate: PASS;
- Candidate/Bulk/C3A interaction gate: PASS;
- inventory: PASS against the manifest generated in the disposable validation composition;
- default-parallel stress: executed and reported, with any failure adjudicated under Section 10.

The C3A branch/ref must not be modified. It must not receive product/test behavior changes merely to make the tooling gate pass. A genuine C3A lifecycle defect belongs to C3A follow-up work and blocks release.

### 12.4 D1A.1 integration candidate

D1A.1 must first be reconstructed as an exact, clean candidate SHA. The evidence records that SHA before testing. Absence of an exact candidate SHA is `NOT_EXECUTED`, not PASS.

The reconstructed product tree plus the release-tooling commit must satisfy the validation composition invariant. It must pass the canonical gate, inventory, and every interaction gate triggered by its diff, followed by a recorded default-parallel stress run. The reconstructed feature branch/ref remains unchanged. A genuine feature defect belongs to the D1A.1 feature work; the release-tooling branch must not patch product behavior around it.

## 13. Local and CI usage

- Developers and release operators use `pnpm test:web:release` for the authoritative Web decision.
- `make test` retains the backend/API/worker legs and delegates its Web leg to that same command.
- CI release validation calls the same implementation and publishes its machine-readable evidence.
- `pnpm test:web:stress` runs in a separately labeled diagnostic step; its raw failure must remain visible even when classified nonblocking.
- Focused developer commands may remain available, but they cannot claim release PASS.

Each release evidence bundle must contain:

- exact path, branch, SHA, and clean status;
- dependency/runtime versions;
- resolved configuration for every Vitest leg;
- lock acquisition and release record;
- manifest SHA-256 digest;
- filesystem, list, and executed inventory comparison;
- ordered file list and interaction-trigger decision;
- per-leg start/end time, duration, exit status, file/test diagnostics, and failure classification;
- watchdog or termination details when applicable;
- the default-parallel stress result and any controlled adjudication;
- one final `PASS`, `RELEASE_BLOCKED`, or `NOT_EXECUTED` decision.

## 14. Implementation scope

Permitted implementation changes are limited to:

- root/Web package scripts;
- `apps/web/scripts/run-release-tests.mjs` as the small canonical runner and manifest-update implementation;
- `apps/web/tests/release-sequencer.ts` as the deterministic sequencer;
- `apps/web/tests/release-test-manifest.json` as the generated inventory manifest;
- Makefile and CI test-command wiring;
- test-tooling tests for runner, manifest, ordering, trigger, lock, watchdog, and classification behavior;
- release runbook/documentation and ignored evidence-output paths.

The implementation must not change production application behavior to reduce resource use. It must not change existing test assertions or per-test timeout values, add retries/skips/allowlists, suppress failures, access production, deploy, or edit C3A/D1A.1 product code.

If Approach A cannot achieve the required three-pass baseline proof, implementation is blocked. Switching to batching, per-file processes, timeout changes, or a different gate requires a new contract decision rather than an implementation-time fallback.

## 15. Acceptance criteria

The implementation is acceptable only when all of the following are true:

1. `pnpm test:web:release` is the single canonical authority used by `make test` and CI.
2. Every release invocation and every Vitest leg is fresh; no daemon or worker survives it.
3. The full suite runs with forks, exactly one worker, file parallelism disabled, isolation enabled, zero retry, and `allowOnly=false`.
4. The actual resolved configuration and absence of timeout overrides are present in evidence.
5. File order and interaction order are deterministic and logged.
6. Filesystem discovery, Vitest collection, manifest, and execution identities agree exactly.
7. No skip/pending/todo/only or unexpected inventory change can pass silently.
8. The approved interaction group triggers conservatively and fails closed on classification uncertainty.
9. The 15-minute process watchdog kills the process tree, records failure, and releases only its own lock.
10. The host-wide lock prevents concurrent cross-worktree release gates and recovers only positively stale ownership.
11. Default parallel is reported as stress evidence and can be nonblocking only under every Section 10 condition.
12. Exact baseline proof is three consecutive fresh PASS invocations.
13. C3A and the reconstructed D1A.1 candidate satisfy Section 12 without product-code workarounds.
14. Implementation changes stay inside the tooling/documentation boundary.

## 16. Explicit anti-patterns

The following are contract violations:

- treating plain `vitest run` or default-max-parallel output as release authority;
- duplicating canonical flags in Makefile, CI, or another script;
- keeping a persistent coordinator/worker across release invocations;
- using multiple workers in any blocking canonical or interaction leg;
- fresh process per file or arbitrary batching without a new contract;
- raising, disabling, or selectively overriding existing Vitest timeouts;
- retrying a failed test or leg to turn it green;
- skipping, quarantining, allowlisting, or weakening a failing test/assertion;
- auto-updating or count-only checking the inventory manifest;
- trusting Vitest discovery without independent filesystem and executed-result equality;
- skipping the interaction group on an unknown or ambiguously classified Web diff;
- treating async-leak detector reports as clean or hiding them behind an allowlist;
- swallowing default-parallel failures or relabeling them green;
- classifying a stress timeout nonblocking without controlled evidence;
- allowing two worktrees to run the canonical gate concurrently;
- deleting a lock whose ownership cannot be proven;
- treating dirty-worktree output, a near-match SHA, missing D1A.1 SHA, or incomplete evidence as release PASS;
- changing product code, feature tests, migrations, or production state to solve machine contention.

## 17. Frozen release-policy decision

Canonical release authority is the deterministic full Web suite with one fork worker, file parallelism disabled, per-file isolation enabled, and original test timeouts intact. The controlled Candidate/Bulk/C3A group is an additional blocking lifecycle/coexistence gate when conservatively triggered. Default parallel is non-authoritative stress evidence.

This contract freeze is complete when this document is the only committed change in the contract worktree.
