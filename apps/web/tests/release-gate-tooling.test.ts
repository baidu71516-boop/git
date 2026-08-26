import { access, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { spawn } from "node:child_process";
import { EventEmitter, once } from "node:events";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

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

function vitest410TimeoutJson(pathname: string, name: string) {
  return {
    testResults: [
      {
        name: pathname.replace(/^apps\/web\//, ""),
        assertionResults: [
          {
            status: "failed",
            fullName: name,
            failureMessages: ["STACK_TRACE_ERROR"],
          },
        ],
      },
    ],
  };
}

function timeoutStream(
  failures: Array<{ path: string; name: string; kind?: string }>,
) {
  const capture = new runner.StreamedVitestOutputCapture();
  for (const failure of failures) {
    const timeoutKind = failure.kind ?? "test";
    const description =
      timeoutKind === "test"
        ? ["Test", "test", "testTimeout"]
        : timeoutKind === "hook"
          ? ["Hook", "hook", "hookTimeout"]
          : ["Teardown", "teardown", "teardownTimeout"];
    const timeoutLines =
      timeoutKind === "teardown"
        ? ['The teardown phase of "fixture" hook timed out after 5000ms.']
        : [
            `Error: ${description[0]} timed out in 5000ms.`,
            `If this is a long-running ${description[1]}, pass a timeout value as the last argument or configure it globally with \"${description[2]}\".`,
          ];
    capture.write(
      "stderr",
      [` FAIL  ${failure.path} > ${failure.name}`, ...timeoutLines].join("\n") +
        "\n",
    );
  }
  capture.finish();
  return capture.evidence();
}

function classifyTimeoutFixture(
  failures: Array<{ path: string; name: string; messages?: string[] }>,
  streamedOutput = timeoutStream(failures),
) {
  const correlation = runner.correlateStressFailures(failures, streamedOutput);
  return {
    correlation,
    classification: runner.classifyStressResult({
      exitCode: 1,
      failures: correlation.failures,
      identityMismatch: correlation.identityMismatch,
      canonicalPass: true,
      interactionPass: true,
      controlledPass: true,
    }),
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

  it("correlates the observed Vitest 4.1.10 STACK_TRACE_ERROR shape with exact timeout identities", () => {
    const pathname = "apps/web/tests/bulk-preview-workflow.test.tsx";
    const name =
      "Bulk preview > presents linked Queue summary, row evidence, stale/unresolved guidance, and Confirm without blocking";
    const jsonFailures = runner.parseStressFailures(
      vitest410TimeoutJson(pathname, name),
    );
    expect(jsonFailures).toEqual([
      expect.objectContaining({
        path: pathname,
        name,
        messages: ["STACK_TRACE_ERROR"],
      }),
    ]);

    const { correlation, classification } =
      classifyTimeoutFixture(jsonFailures);
    expect(correlation).toMatchObject({
      identityMismatch: false,
      failures: [expect.objectContaining({ timeoutKind: "test" })],
      timeoutBlocks: [expect.objectContaining({ path: pathname, name })],
    });
    expect(classification).toMatchObject({
      decision: "STRESS_RESOURCE_CONTENTION_NON_BLOCKING",
      exitCode: 0,
    });
  });

  it("fails closed for assertion, mixed, abnormal, and identity-mismatched stress failures", () => {
    const timeout = {
      path: "apps/web/tests/bulk-preview-workflow.test.tsx",
      name: "Bulk preview > timeout-shaped test",
      messages: ["STACK_TRACE_ERROR"],
    };
    const assertion = {
      path: "apps/web/tests/influencer-preview.test.tsx",
      name: "Influencer preview > assertion failure",
      messages: ["STACK_TRACE_ERROR"],
    };
    const assertionStream = new runner.StreamedVitestOutputCapture();
    assertionStream.write(
      "stderr",
      ` FAIL  ${assertion.path} > ${assertion.name}\nAssertionError: expected true to be false\n`,
    );
    assertionStream.finish();

    expect(
      classifyTimeoutFixture([assertion], assertionStream.evidence())
        .classification,
    ).toMatchObject({ decision: "RELEASE_BLOCKED" });
    expect(
      classifyTimeoutFixture([timeout, assertion], timeoutStream([timeout]))
        .classification,
    ).toMatchObject({ decision: "RELEASE_BLOCKED" });
    expect(
      runner.classifyStressResult({
        exitCode: -1,
        abnormal: true,
        failures: [],
      }),
    ).toMatchObject({ decision: "RELEASE_BLOCKED" });
    expect(
      classifyTimeoutFixture(
        [timeout],
        timeoutStream([{ ...timeout, name: "different test identity" }]),
      ).classification,
    ).toMatchObject({ decision: "RELEASE_BLOCKED" });
  });

  it("preserves multiple exact timeout identities and never lets controlled PASS rewrite assertion failure", () => {
    const failures = [
      {
        path: "apps/web/tests/bulk-preview-workflow.test.tsx",
        name: "Bulk preview > timeout one",
        messages: ["STACK_TRACE_ERROR"],
      },
      {
        path: "apps/web/tests/influencer-preview.test.tsx",
        name: "Influencer preview > timeout two",
        messages: ["STACK_TRACE_ERROR"],
      },
    ];
    const { correlation, classification } = classifyTimeoutFixture(failures);
    expect(correlation.timeoutBlocks).toEqual([
      expect.objectContaining({
        path: failures[0].path,
        name: failures[0].name,
      }),
      expect.objectContaining({
        path: failures[1].path,
        name: failures[1].name,
      }),
    ]);
    expect(classification.decision).toBe(
      "STRESS_RESOURCE_CONTENTION_NON_BLOCKING",
    );
    expect(
      runner.classifyStressResult({
        exitCode: 1,
        failures: [{ ...failures[0], timeoutKind: undefined }],
        canonicalPass: true,
        interactionPass: true,
        controlledPass: true,
      }),
    ).toMatchObject({ decision: "RELEASE_BLOCKED" });
  });

  it("distinguishes structured Vitest test, hook, and teardown timeout signatures", () => {
    const failures = [
      {
        path: "apps/web/tests/a.test.ts",
        name: "A > test timeout",
      },
      {
        path: "apps/web/tests/b.test.ts",
        name: "B > hook timeout",
      },
      {
        path: "apps/web/tests/c.test.ts",
        name: "C > teardown timeout",
      },
    ];
    const correlation = runner.correlateStressFailures(
      failures,
      timeoutStream([
        failures[0],
        { ...failures[1], kind: "hook" },
        { ...failures[2], kind: "teardown" },
      ]),
    );
    expect(correlation.identityMismatch).toBe(false);
    expect(
      correlation.failures.map(
        (failure: { timeoutKind?: string }) => failure.timeoutKind,
      ),
    ).toEqual(["test", "hook", "teardown"]);
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

  it.each(["SIGTERM", "SIGINT", "SIGHUP"])(
    "handles %s once, terminates the active child, records evidence, and releases only its lock",
    async (signal) => {
      const signalEmitter = new EventEmitter();
      const evidence: Record<string, unknown> = {};
      let terminationCalls = 0;
      let releases = 0;
      const lifecycle = new runner.GateLifecycle(evidence, {
        signalEmitter,
        terminate: async () => {
          terminationCalls += 1;
          return { terminated: true, exitedGracefully: true };
        },
      });
      lifecycle.attachLock({
        release: async () => {
          releases += 1;
          return true;
        },
      });
      lifecycle.installSignalHandlers();
      signalEmitter.emit(signal);
      signalEmitter.emit(signal);
      await lifecycle.finalize();
      lifecycle.removeSignalHandlers();

      expect(terminationCalls).toBe(1);
      expect(releases).toBe(1);
      expect(evidence).toMatchObject({
        termination: {
          signal,
          childTermination: { terminated: true, exitedGracefully: true },
          lockReleased: true,
        },
      });
      expect(() => lifecycle.assertNotTerminating()).toThrow(
        `RELEASE_TERMINATED_BY_${signal}`,
      );
    },
  );

  it("prevents a post-signal child start and preserves the normal one-time lock release path", async () => {
    const temporaryRoot = path.join(
      os.tmpdir(),
      "release-gate-no-child-after-signal-",
      `${process.pid}-${Date.now()}`,
    );
    temporaryPaths.push(temporaryRoot);
    await mkdir(temporaryRoot, { recursive: true });
    const markerPath = path.join(temporaryRoot, "child-started");
    const evidence: Record<string, unknown> = {};
    const lifecycle = new runner.GateLifecycle(evidence, {
      terminate: async () => ({ terminated: false }),
    });
    await lifecycle.requestTermination("SIGTERM");
    const result = await runner.runChild(
      process.execPath,
      [
        "-e",
        `require("node:fs").writeFileSync(${JSON.stringify(markerPath)}, "started")`,
      ],
      { lifecycle },
    );
    expect(result).toMatchObject({ code: -1, signal: "SIGTERM" });
    await expect(access(markerPath)).rejects.toThrow();

    let releases = 0;
    const normalLifecycle = new runner.GateLifecycle({});
    normalLifecycle.attachLock({
      release: async () => {
        releases += 1;
        return true;
      },
    });
    await normalLifecycle.finalize();
    await normalLifecycle.finalize();
    expect(releases).toBe(1);
  });

  it("serializes signal and watchdog cleanup, including lock ownership-token protection", async () => {
    const temporaryRoot = path.join(
      os.tmpdir(),
      "release-gate-lifecycle-",
      `${process.pid}-${Date.now()}`,
    );
    temporaryPaths.push(temporaryRoot);
    const lockPath = path.join(temporaryRoot, "gate.lock");
    const lock = new runner.GateLock({
      lockPath,
      metadata: { sha: "test-sha" },
      token: "owned-token",
    });
    await lock.acquire();
    await writeFile(
      path.join(lockPath, "owner.json"),
      JSON.stringify({ token: "successor-token" }),
    );

    const evidence: Record<string, unknown> = {};
    let terminationCalls = 0;
    let releaseTermination: (() => void) | undefined;
    const terminationStarted = new Promise<void>((resolve) => {
      releaseTermination = resolve;
    });
    const lifecycle = new runner.GateLifecycle(evidence, {
      terminate: async () => {
        terminationCalls += 1;
        await terminationStarted;
        return { terminated: true, exitedGracefully: false };
      },
    });
    lifecycle.attachLock(lock);
    const watchdog = new runner.GateWatchdog(evidence, {
      timeoutMs: 1,
      terminate: () => lifecycle.requestTermination("WATCHDOG"),
    });
    const signalCleanup = lifecycle.requestTermination("SIGTERM");
    const watchdogCleanup = watchdog.expire();
    releaseTermination?.();
    await Promise.all([signalCleanup, watchdogCleanup, lifecycle.finalize()]);

    expect(terminationCalls).toBe(1);
    expect(evidence).toMatchObject({
      termination: { signal: "SIGTERM", lockReleased: false },
      watchdog: {
        expired: true,
        termination: { terminated: true, exitedGracefully: false },
      },
    });
    await expect(access(lockPath)).resolves.toBeUndefined();
  });

  it("uses a real OS signal to terminate a disposable child process group", async () => {
    if (process.platform === "win32") {
      return;
    }
    const temporaryRoot = path.join(
      os.tmpdir(),
      "release-gate-signal-integration-",
      `${process.pid}-${Date.now()}`,
    );
    temporaryPaths.push(temporaryRoot);
    await mkdir(temporaryRoot, { recursive: true });
    const readyPath = path.join(temporaryRoot, "ready.json");
    const resultPath = path.join(temporaryRoot, "result.json");
    const runnerUrl = pathToFileURL(
      path.join(process.cwd(), "scripts/run-release-tests.mjs"),
    ).href;
    const source = `
      import { spawn } from "node:child_process";
      import { writeFile } from "node:fs/promises";
      import { GateLifecycle, terminateChildProcessGroup } from ${JSON.stringify(runnerUrl)};
      const activeChild = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], { detached: true, stdio: "ignore" });
      const evidence = {};
      const lifecycle = new GateLifecycle(evidence, { terminate: () => terminateChildProcessGroup(activeChild) });
      lifecycle.attachLock({ release: async () => true });
      lifecycle.installSignalHandlers();
      await writeFile(${JSON.stringify(readyPath)}, JSON.stringify({ pid: process.pid, childPid: activeChild.pid }));
      const timer = setInterval(async () => {
        if (!lifecycle.terminating) return;
        clearInterval(timer);
        await lifecycle.finalize();
        await writeFile(${JSON.stringify(resultPath)}, JSON.stringify(evidence));
        process.exit(1);
      }, 10);
    `;
    const processUnderTest = spawn(
      process.execPath,
      ["--input-type=module", "--eval", source],
      { stdio: ["ignore", "pipe", "pipe"] },
    );
    let childOutput = "";
    processUnderTest.stdout?.on("data", (chunk) => {
      childOutput += String(chunk);
    });
    processUnderTest.stderr?.on("data", (chunk) => {
      childOutput += String(chunk);
    });
    try {
      let ready = false;
      for (let attempt = 0; attempt < 100; attempt += 1) {
        try {
          await access(readyPath);
          ready = true;
          break;
        } catch {
          await new Promise((resolve) => setTimeout(resolve, 10));
        }
      }
      if (!ready) {
        throw new Error(`signal fixture did not start: ${childOutput}`);
      }
      process.kill(processUnderTest.pid!, "SIGTERM");
      const [code, signal] = (await once(processUnderTest, "close")) as [
        number,
        NodeJS.Signals | null,
      ];
      expect(signal).toBeNull();
      expect(code).not.toBe(0);
      expect(JSON.parse(await readFile(resultPath, "utf8"))).toMatchObject({
        termination: {
          signal: "SIGTERM",
          childTermination: { terminated: true },
          lockReleased: true,
        },
      });
    } finally {
      processUnderTest.kill("SIGKILL");
    }
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
