import { access, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
  INTERACTION_TEST_PATHS,
  lexicalReleaseOrder,
} from "./release-sequencer";

// The release runner is intentionally executable JavaScript so package scripts can
// invoke it without a TypeScript loader. Vitest resolves this ESM import at runtime.
// @ts-expect-error The executable runner intentionally has no TypeScript declaration.
const runner = await import("../scripts/run-release-tests.mjs");
const temporaryPaths: string[] = [];

afterEach(async () => {
  await Promise.all(
    temporaryPaths
      .splice(0)
      .map((temporaryPath) =>
        rm(temporaryPath, { recursive: true, force: true }),
      ),
  );
});

function expectedManifest() {
  return {
    schemaVersion: 1,
    files: [
      {
        path: "apps/web/tests/a.test.ts",
        tests: ["A > first", "A > second"],
      },
    ],
  };
}

function passingExecutionEvidence() {
  const tests = ["A > first", "A > second"];
  const records = tests.map((name, index) => ({
    path: "apps/web/tests/a.test.ts",
    name,
    id: `test-${index}`,
  }));
  return {
    resolvedConfig: {
      valid: true,
      timeoutOverridesSuppliedByRunner: false,
      timeoutOverrideProof: "none",
      config: {
        pool: "forks",
        maxWorkers: 1,
        fileParallelism: false,
        isolate: true,
        retry: 0,
        allowOnly: false,
        shuffle: false,
        testTimeout: 5000,
        hookTimeout: 10000,
        teardownTimeout: 10000,
      },
    },
    collected: records.map((record) => ({ ...record, mode: "run" })),
    ready: records,
    results: records.map((record) => ({ ...record, state: "passed" })),
    finalTests: records.map((record) => ({
      ...record,
      mode: "run",
      state: "passed",
    })),
    modules: [
      { path: "apps/web/tests/a.test.ts", state: "passed", errors: [] },
    ],
    final: { reason: "passed", unhandledErrors: [] },
  };
}

describe("release gate deterministic tooling", () => {
  it("uses locale-independent lexical full-suite order and the frozen interaction order", () => {
    expect(
      lexicalReleaseOrder([
        "apps/web/tests/z.test.ts",
        "apps/web/tests/A.test.ts",
        "apps/web/tests/a.test.ts",
      ]),
    ).toEqual([
      "apps/web/tests/A.test.ts",
      "apps/web/tests/a.test.ts",
      "apps/web/tests/z.test.ts",
    ]);
    expect(INTERACTION_TEST_PATHS).toEqual([
      "apps/web/tests/candidate-pool-detail-tabs.test.tsx",
      "apps/web/tests/candidate-selection.test.ts",
      "apps/web/tests/candidate-run-detail-selection.test.tsx",
      "apps/web/tests/bulk-import-workspace.test.tsx",
      "apps/web/tests/bulk-preview-workflow.test.tsx",
      "apps/web/tests/data-collection-workspace.test.tsx",
    ]);
  });

  it("generates deterministic normalized manifest inventory and rejects non-canonical form", () => {
    const manifest = runner.manifestFromList([
      { file: "tests/a.test.ts", name: "A > second" },
      { file: "tests/a.test.ts", name: "A > first" },
    ]);
    expect(manifest).toEqual(expectedManifest());
    expect(() =>
      runner.validateManifest({
        ...manifest,
        files: [...manifest.files].reverse(),
      }),
    ).not.toThrow();
    expect(() =>
      runner.validateManifest({
        ...manifest,
        files: [{ ...manifest.files[0], tests: ["A > second", "A > first"] }],
      }),
    ).toThrow("canonical deterministic form");
  });

  it("fails closed for uncertain, relevant, renamed, and binary interaction diffs", () => {
    expect(runner.interactionDecisionForChanges({ baseValid: false }).run).toBe(
      true,
    );
    expect(
      runner.interactionDecisionForChanges({
        baseValid: true,
        parseError: true,
      }).run,
    ).toBe(true);
    expect(
      runner.interactionDecisionForChanges({ baseValid: true, binary: true })
        .run,
    ).toBe(true);
    expect(
      runner.interactionDecisionForChanges({
        baseValid: true,
        changes: [{ status: "R", paths: ["docs/a.md", "docs/b.md"] }],
      }).run,
    ).toBe(true);
    expect(
      runner.interactionDecisionForChanges({
        baseValid: true,
        changes: [{ status: "M", paths: ["apps/web/src/page.tsx"] }],
      }).run,
    ).toBe(true);
    expect(
      runner.interactionDecisionForChanges({
        baseValid: true,
        changes: [{ status: "M", paths: ["docs/runbook.md"] }],
      }),
    ).toMatchObject({ run: false });
  });

  it("rejects resolved config mismatch, modes, missing, and duplicate execution identities", () => {
    const expected = expectedManifest();
    expect(
      runner.verifyExecutionEvidence(passingExecutionEvidence(), expected),
    ).toEqual({ files: 1, tests: 2 });

    const invalidConfig = passingExecutionEvidence();
    invalidConfig.resolvedConfig.config.maxWorkers = 2;
    expect(() =>
      runner.verifyExecutionEvidence(invalidConfig, expected),
    ).toThrow("Resolved Vitest configuration");

    const skipped = passingExecutionEvidence();
    skipped.collected[0].mode = "skip";
    expect(() => runner.verifyExecutionEvidence(skipped, expected)).toThrow(
      "forbidden mode",
    );

    const missing = passingExecutionEvidence();
    missing.results.pop();
    expect(() => runner.verifyExecutionEvidence(missing, expected)).toThrow(
      "Executed identity mismatch",
    );

    const duplicate = passingExecutionEvidence();
    duplicate.results.push(duplicate.results[0]);
    expect(() => runner.verifyExecutionEvidence(duplicate, expected)).toThrow(
      "Executed identity mismatch",
    );
  });

  it("classifies stress timeouts as non-blocking only after every frozen adjudication condition", () => {
    const timeoutFailure = [
      {
        path: "apps/web/tests/a.test.ts",
        name: "A > first",
        messages: ["Test timed out in 5000ms."],
      },
    ];
    expect(
      runner.classifyStressResult({
        exitCode: 1,
        failures: timeoutFailure,
        canonicalPass: true,
        interactionPass: true,
        controlledPass: true,
      }),
    ).toMatchObject({
      decision: "STRESS_RESOURCE_CONTENTION_NON_BLOCKING",
      exitCode: 0,
    });
    expect(
      runner.classifyStressResult({
        exitCode: 1,
        failures: [
          { ...timeoutFailure[0], messages: ["expected true to be false"] },
        ],
        canonicalPass: true,
        interactionPass: true,
        controlledPass: true,
      }),
    ).toMatchObject({ decision: "RELEASE_BLOCKED" });
    expect(
      runner.classifyStressResult({
        exitCode: 1,
        failures: timeoutFailure,
        canonicalPass: true,
        interactionPass: true,
        controlledPass: false,
      }),
    ).toMatchObject({ decision: "RELEASE_BLOCKED" });
  });

  it("fails fast for a live lock, recovers only a proven stale lock, and does not remove a successor lock", async () => {
    const temporaryRoot = await mkdir(
      path.join(os.tmpdir(), "release-gate-tooling-"),
      { recursive: true },
    ).then(() =>
      path.join(
        os.tmpdir(),
        "release-gate-tooling-",
        `${process.pid}-${Date.now()}`,
      ),
    );
    temporaryPaths.push(temporaryRoot);
    const lockPath = path.join(temporaryRoot, "gate.lock");
    const metadata = { sha: "test-sha" };
    const first = new runner.GateLock({ lockPath, metadata, token: "first" });
    await first.acquire();
    const live = new runner.GateLock({
      lockPath,
      metadata,
      token: "live",
      inspectOwner: async () => ({ state: "live", reason: "test owner" }),
    });
    await expect(live.acquire()).rejects.toThrow("GATE_LOCK_HELD");

    await first.release();
    await mkdir(lockPath, { recursive: true });
    await writeFile(
      path.join(lockPath, "owner.json"),
      JSON.stringify({ pid: 999_999, token: "stale" }),
    );
    const recovered = new runner.GateLock({
      lockPath,
      metadata,
      token: "recovered",
      inspectOwner: async () => ({ state: "stale", reason: "PID gone" }),
    });
    await recovered.acquire();
    expect(
      (await readFile(path.join(lockPath, "owner.json"), "utf8")).includes(
        "recovered",
      ),
    ).toBe(true);
    await writeFile(
      path.join(lockPath, "owner.json"),
      JSON.stringify({ pid: process.pid, token: "successor" }),
    );
    expect(await recovered.release()).toBe(false);
    await expect(access(lockPath)).resolves.toBeUndefined();
  });

  it("records watchdog expiry and process-tree cleanup without changing Vitest timeout policy", async () => {
    const evidence: Record<string, unknown> = {};
    const watchdog = new runner.GateWatchdog(evidence, {
      timeoutMs: 1,
      terminate: async () => ({ terminated: true, exitedGracefully: false }),
    });
    await watchdog.expire();
    expect(evidence).toEqual({
      watchdog: {
        expired: true,
        timeoutMs: 1,
        termination: { terminated: true, exitedGracefully: false },
      },
    });
    expect(() => watchdog.assertNotExpired()).toThrow("WATCHDOG_EXPIRED");
  });
});
