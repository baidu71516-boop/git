# Web release gate runbook

The only Web release authority is:

```bash
pnpm test:web:release
```

It acquires the host-wide release lock, generates evidence in `.release-evidence/`, validates the filesystem/Vitest/manifest inventory, runs one fresh deterministic Vitest child, and runs the frozen Candidate/Bulk interaction group whenever its fail-safe diff trigger requires it.

## Commands

```bash
# Deliberately review and update the committed inventory; never done by release.
pnpm test:web:manifest:update

# Canonical release decision.
pnpm test:web:release

# Diagnostic-only default-parallel stress signal.
pnpm test:web:stress

# Diagnostic-only controlled adjudication for manifest paths.
pnpm test:web:release -- --controlled apps/web/tests/example.test.ts
```

`make test` and CI use `pnpm test:web:release` for the Web leg. No Makefile or CI command repeats the Vitest release flags.

## Release base and interaction gate

Release orchestration may supply a validated ancestor through `WEB_RELEASE_BASE` (or `RELEASE_BASE`). The runner compares that base with `HEAD`. If the base is missing, invalid, unreachable, unparseable, or touches a relevant surface, the interaction gate runs. It never skips on uncertainty.

## Evidence and decisions

Each invocation writes a machine-readable JSON record under `.release-evidence/`. It includes Git state, runtime versions, lock lifecycle, resolved Vitest configuration, ordered paths, manifest digest, inventory comparisons, per-leg terminal results, interaction decision, and watchdog details.

A dirty worktree may be used for development feedback but is recorded as `NOT_RELEASE_EVIDENCE`; it cannot release. Controlled mode can only report `CONTROLLED_PASS_DIAGNOSTIC_ONLY`. A stress timeout remains visible and is non-blocking only when a prior clean canonical pass, relevant interaction pass, and fresh controlled canonical adjudication satisfy the frozen contract.
