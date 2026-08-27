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

const ADAPTED_RETAINED_VITEST_4_1_10_FIXTURE = {
  label: "ADAPTED_RETAINED_VITEST_4_1_10_FIXTURE",
  path: "apps/web/tests/bulk-preview-workflow.test.tsx",
  jsonFullName:
    "Bulk unified preview review presents linked Queue summary, row evidence, stale/unresolved guidance, and Confirm without blocking",
  streamedHierarchy:
    "Bulk unified preview review > presents linked Queue summary, row evidence, stale/unresolved guidance, and Confirm without blocking",
} as const;

const ADAPTED_RETAINED_VITEST_4_1_10_SUCCESS_FIXTURE = {
  label: "ADAPTED_RETAINED_VITEST_4_1_10_SUCCESS_FIXTURE",
  path: "apps/web/tests/bulk-preview-workflow.test.tsx",
  tests: [
    "Bulk Confirm acceptance and recovery > accepts HTTP 200 dispatch data, closes the explicit Modal, and polls the Job without another Confirm",
    "Bulk unified preview review > presents linked Queue summary, row evidence, stale/unresolved guidance, and Confirm without blocking",
  ],
  numTotalTestSuites: 2,
} as const;

const ADAPTED_REAL_V3_MIXED_OUTPUT_FIXTURE = {
  label: "adapted real V3 mixed-output fixture",
  failures: [
    {
      failureKind: "timeout",
      jsonFullName:
        "AuthShell lets an authenticated super_admin without an Operator read Import Job history in Backend scope",
      path: "apps/web/tests/auth-shell.test.tsx",
      streamedHierarchy:
        "AuthShell > lets an authenticated super_admin without an Operator read Import Job history in Backend scope",
    },
    {
      failureKind: "timeout",
      jsonFullName:
        "Bulk unified preview review presents linked Queue summary, row evidence, stale/unresolved guidance, and Confirm without blocking",
      path: "apps/web/tests/bulk-preview-workflow.test.tsx",
      streamedHierarchy:
        "Bulk unified preview review > presents linked Queue summary, row evidence, stale/unresolved guidance, and Confirm without blocking",
    },
    {
      failureKind: "assertion",
      jsonFullName:
        "AuthShell keeps Campaign reading available without a selected Operator",
      path: "apps/web/tests/auth-shell.test.tsx",
      streamedHierarchy:
        "AuthShell > keeps Campaign reading available without a selected Operator",
    },
    {
      failureKind: "assertion",
      jsonFullName:
        "release gate deterministic tooling verifies a simple POSIX process group exits gracefully",
      path: "apps/web/tests/release-gate-tooling.test.ts",
      streamedHierarchy:
        "release gate deterministic tooling > verifies a simple POSIX process group exits gracefully",
    },
  ],
} as const;

type StressFailure = {
  path: string;
  name: string;
  messages?: string[];
  failureKind?: "timeout" | "assertion";
};

type StreamFailure = {
  path: string;
  name: string;
  kind?: string;
};

function stressManifest() {
  return {
    schemaVersion: 1,
    files: [
      {
        path: "apps/web/tests/bulk-preview-workflow.test.tsx",
        tests: [
          "Bulk unified preview review > presents linked Queue summary, row evidence, stale/unresolved guidance, and Confirm without blocking",
          "Bulk unified preview review > duplicate leaf",
          "Other bulk suite > duplicate leaf",
        ],
      },
      {
        path: "apps/web/tests/influencer-preview.test.tsx",
        tests: [
          "development influencer visual preview > opens and closes preview drawer from local preview table interactions",
          "development influencer visual preview > duplicate leaf",
        ],
      },
      {
        path: "apps/web/tests/other.test.ts",
        tests: ["Other file suite > duplicate leaf"],
      },
      {
        path: "apps/web/tests/timeout-kinds.test.ts",
        tests: [
          "Timeout kinds > test timeout",
          "Timeout kinds > hook timeout",
          "Timeout kinds > teardown timeout",
        ],
      },
    ],
  };
}

function adaptedVitest410SuccessManifest() {
  const fixture = ADAPTED_RETAINED_VITEST_4_1_10_SUCCESS_FIXTURE;
  return {
    schemaVersion: 1,
    files: [
      {
        path: fixture.path,
        tests: [...fixture.tests].sort(),
      },
    ],
  };
}

function vitest410TimeoutJson(pathname: string, fullName: string) {
  return {
    testResults: [
      {
        name: pathname.replace(/^apps\/web\//, ""),
        assertionResults: [
          {
            status: "failed",
            fullName,
            failureMessages: ["STACK_TRACE_ERROR"],
          },
        ],
      },
    ],
  };
}

function timeoutStream(failures: StreamFailure[]) {
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

function completeArtifact(pathname: string) {
  return {
    path: pathname,
    present: true,
    bytes: 1,
    sha256: "0".repeat(64),
  };
}

type StressManifest = {
  schemaVersion: number;
  files: Array<{ path: string; tests: string[] }>;
};

function vitest410Report(
  manifest: StressManifest,
  options: {
    failures?: StressFailure[];
    numTotalTestSuites?: number;
  } = {},
) {
  const failedKeys = new Set(
    (options.failures ?? []).map(
      (failure) => `${failure.path}\u0000${failure.name}`,
    ),
  );
  const failureMessagesByKey = new Map(
    (options.failures ?? []).map((failure) => [
      `${failure.path}\u0000${failure.name}`,
      failure.messages ?? ["STACK_TRACE_ERROR"],
    ]),
  );
  const catalog = runner.manifestStressIdentityCatalog(manifest);
  const assertionsByPath = new Map<string, Array<Record<string, unknown>>>();
  for (const identity of catalog.entries) {
    const assertions = assertionsByPath.get(identity.path) ?? [];
    const failed = failedKeys.has(
      `${identity.path}\u0000${identity.jsonProjection}`,
    );
    assertions.push({
      status: failed ? "failed" : "passed",
      fullName: identity.jsonProjection,
      failureMessages: failed
        ? (failureMessagesByKey.get(
            `${identity.path}\u0000${identity.jsonProjection}`,
          ) ?? ["STACK_TRACE_ERROR"])
        : [],
    });
    assertionsByPath.set(identity.path, assertions);
  }
  const testResults = manifest.files.map((file) => ({
    name: file.path.replace(/^apps\/web\//, ""),
    assertionResults: assertionsByPath.get(file.path) ?? [],
  }));
  const assertions = testResults.flatMap(
    (testResult) => testResult.assertionResults,
  );
  const failedTests = assertions.filter(
    (assertion) => assertion.status === "failed",
  ).length;
  return {
    testResults,
    numTotalTests: assertions.length,
    numPassedTests: assertions.length - failedTests,
    numFailedTests: failedTests,
    numPendingTests: 0,
    numTodoTests: 0,
    numTotalTestSuites: options.numTotalTestSuites ?? manifest.files.length,
    success: failedTests === 0,
  };
}

function classifyTimeoutFixture(
  failures: StressFailure[],
  streamedOutput = timeoutStream(failures),
  manifest = stressManifest(),
  resultCode = 1,
) {
  const correlation = runner.correlateStressFailures(failures, streamedOutput, {
    manifest,
  });
  const report = vitest410Report(manifest, { failures });
  const evidenceIntegrity = runner.validateStressEvidence({
    result: { code: resultCode },
    report,
    rawVitestJson: completeArtifact("raw.json"),
    rawStream: completeArtifact("stream.json"),
    streamedOutput,
    correlation,
    failures: correlation.failures,
    identityCatalog: runner.manifestStressIdentityCatalog(manifest),
  });
  return {
    correlation,
    evidenceIntegrity,
    classification: runner.classifyStressResult({
      exitCode: resultCode,
      failures: correlation.failures,
      identityMismatch: correlation.identityMismatch,
      evidenceIntegrity,
      canonicalPass: true,
      interactionPass: true,
      controlledPass: true,
    }),
  };
}

function wait(milliseconds: number) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function pidIsAlive(pid: number) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return (error as NodeJS.ErrnoException).code !== "ESRCH";
  }
}

async function waitForPidGone(pid: number) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (!pidIsAlive(pid)) {
      return;
    }
    await wait(10);
  }
  throw new Error(`PID ${pid} remained alive after process-group cleanup`);
}

async function waitForPath(filePath: string) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      await access(filePath);
      return;
    } catch {
      await wait(10);
    }
  }
  throw new Error(`Timed out waiting for ${filePath}`);
}

async function waitForJson(filePath: string) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      return JSON.parse(await readFile(filePath, "utf8"));
    } catch {
      await wait(10);
    }
  }
  throw new Error(`Timed out waiting for JSON evidence at ${filePath}`);
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

  it("correlates the ADAPTED_RETAINED_VITEST_4_1_10_FIXTURE through exact manifest projections", () => {
    const manifest = stressManifest();
    const fixture = ADAPTED_RETAINED_VITEST_4_1_10_FIXTURE;
    const manifestName = manifest.files[0].tests[0];
    expect(manifestName).toBe(fixture.streamedHierarchy);
    const jsonFailures = runner.parseStressFailures(
      vitest410TimeoutJson(fixture.path, fixture.jsonFullName),
    );
    expect(fixture.label).toBe("ADAPTED_RETAINED_VITEST_4_1_10_FIXTURE");
    expect(jsonFailures).toEqual([
      expect.objectContaining({
        path: fixture.path,
        name: fixture.jsonFullName,
        messages: ["STACK_TRACE_ERROR"],
        failureKind: "timeout",
      }),
    ]);

    const { correlation, classification } = classifyTimeoutFixture(
      jsonFailures,
      timeoutStream([{ path: fixture.path, name: manifestName }]),
      manifest,
    );
    expect(correlation).toMatchObject({
      identityMismatch: false,
      failures: [
        expect.objectContaining({
          timeoutKind: "test",
          identity: expect.objectContaining({ manifestName }),
        }),
      ],
    });
    expect(classification).toMatchObject({
      decision: "STRESS_RESOURCE_CONTENTION_NON_BLOCKING",
      exitCode: 0,
    });
  });

  it("resolves exact stream hierarchy and unique leaf-only projections without splitting JSON words", () => {
    const manifest = stressManifest();
    const pathname = "apps/web/tests/influencer-preview.test.tsx";
    const manifestName = manifest.files[1].tests[0];
    const jsonFailures = runner.parseStressFailures(
      vitest410TimeoutJson(
        pathname,
        "development influencer visual preview opens and closes preview drawer from local preview table interactions",
      ),
    );
    expect(
      classifyTimeoutFixture(
        jsonFailures,
        timeoutStream([{ path: pathname, name: manifestName }]),
        manifest,
      ).correlation.identityMismatch,
    ).toBe(false);
    expect(
      classifyTimeoutFixture(
        jsonFailures,
        timeoutStream([
          {
            path: pathname,
            name: "opens and closes preview drawer from local preview table interactions",
          },
        ]),
        manifest,
      ).correlation.identityMismatch,
    ).toBe(false);
  });

  it("scopes same leaf titles by exact file identity and rejects a cross-file mismatch", () => {
    const manifest = stressManifest();
    const bulkPath = "apps/web/tests/bulk-preview-workflow.test.tsx";
    const otherPath = "apps/web/tests/other.test.ts";
    const bulkJson = [
      {
        path: bulkPath,
        name: "Bulk unified preview review duplicate leaf",
      },
    ];
    expect(
      runner.correlateStressFailures(
        bulkJson,
        timeoutStream([{ path: bulkPath, name: "duplicate leaf" }]),
        { manifest },
      ).identityMismatch,
    ).toBe(true);
    expect(
      runner.correlateStressFailures(
        [
          {
            path: otherPath,
            name: "Other file suite duplicate leaf",
          },
        ],
        timeoutStream([{ path: otherPath, name: "duplicate leaf" }]),
        { manifest },
      ).identityMismatch,
    ).toBe(false);
  });

  it("recognizes only structured test, hook, and teardown timeout blocks after exact identity resolution", () => {
    const manifest = stressManifest();
    const pathname = "apps/web/tests/timeout-kinds.test.ts";
    const failures = [
      { path: pathname, name: "Timeout kinds test timeout" },
      { path: pathname, name: "Timeout kinds hook timeout" },
      { path: pathname, name: "Timeout kinds teardown timeout" },
    ];
    const { correlation, classification } = classifyTimeoutFixture(
      failures,
      timeoutStream([
        { path: pathname, name: "Timeout kinds > test timeout" },
        {
          path: pathname,
          name: "Timeout kinds > hook timeout",
          kind: "hook",
        },
        {
          path: pathname,
          name: "Timeout kinds > teardown timeout",
          kind: "teardown",
        },
      ]),
      manifest,
    );
    expect(
      correlation.failures.map(
        (failure: { timeoutKind?: string }) => failure.timeoutKind,
      ),
    ).toEqual(["test", "hook", "teardown"]);
    expect(classification).toMatchObject({
      decision: "STRESS_RESOURCE_CONTENTION_NON_BLOCKING",
    });
  });

  it("fails closed for ambiguous leaf-only, wrong-file, wrong-hierarchy, duplicate, missing, and random timeout blocks", () => {
    const manifest = stressManifest();
    const pathname = "apps/web/tests/bulk-preview-workflow.test.tsx";
    const jsonFailures = [
      {
        path: pathname,
        name: "Bulk unified preview review presents linked Queue summary, row evidence, stale/unresolved guidance, and Confirm without blocking",
      },
    ];
    const exactBlock = {
      path: pathname,
      name: manifest.files[0].tests[0],
    };
    const ambiguousLeaf = {
      path: pathname,
      name: "duplicate leaf",
    };
    const randomCapture = new runner.StreamedVitestOutputCapture();
    randomCapture.write("stderr", "unrelated timeout text\n");
    randomCapture.finish();
    const cases = [
      timeoutStream([ambiguousLeaf]),
      timeoutStream([
        {
          path: "apps/web/tests/influencer-preview.test.tsx",
          name: manifest.files[1].tests[0],
        },
      ]),
      timeoutStream([
        {
          path: "apps/web/tests/other.test.ts",
          name: manifest.files[0].tests[0],
        },
      ]),
      timeoutStream([
        {
          path: pathname,
          name: "Bulk unified preview review > wrong hierarchy and exact leaf text not enough",
        },
      ]),
      timeoutStream([exactBlock, exactBlock]),
      new runner.StreamedVitestOutputCapture().evidence(),
      randomCapture.evidence(),
    ];
    for (const streamedOutput of cases) {
      expect(
        runner.correlateStressFailures(jsonFailures, streamedOutput, {
          manifest,
        }).identityMismatch,
      ).toBe(true);
    }
    expect(
      runner.correlateStressFailures(
        [
          {
            ...jsonFailures[0],
            name: jsonFailures[0].name.replace(
              "preview review presents",
              "preview review  presents",
            ),
          },
        ],
        timeoutStream([exactBlock]),
        { manifest },
      ).identityMismatch,
    ).toBe(true);
  });

  it("fails closed for assertion, mixed, abnormal, and controlled PASS after assertion", async () => {
    const manifest = stressManifest();
    const timeout = {
      path: "apps/web/tests/bulk-preview-workflow.test.tsx",
      name: "Bulk unified preview review presents linked Queue summary, row evidence, stale/unresolved guidance, and Confirm without blocking",
      messages: ["STACK_TRACE_ERROR"],
    };
    const assertion = {
      path: "apps/web/tests/influencer-preview.test.tsx",
      name: "development influencer visual preview opens and closes preview drawer from local preview table interactions",
      messages: ["AssertionError: expected true to be false"],
    };
    const assertionStream = new runner.StreamedVitestOutputCapture();
    assertionStream.write(
      "stderr",
      ` FAIL  ${assertion.path} > ${manifest.files[1].tests[0]}\nAssertionError: expected true to be false\n`,
    );
    assertionStream.finish();
    expect(
      classifyTimeoutFixture([assertion], assertionStream.evidence(), manifest)
        .classification,
    ).toMatchObject({ decision: "RELEASE_BLOCKED" });
    expect(
      classifyTimeoutFixture(
        [timeout, assertion],
        timeoutStream([
          { path: timeout.path, name: manifest.files[0].tests[0] },
        ]),
        manifest,
      ).classification,
    ).toMatchObject({ decision: "RELEASE_BLOCKED" });

    const fixture = ADAPTED_REAL_V3_MIXED_OUTPUT_FIXTURE;
    expect(fixture.label).toBe("adapted real V3 mixed-output fixture");
    const committedManifest = JSON.parse(
      await readFile(
        path.join(process.cwd(), "tests/release-test-manifest.json"),
        "utf8",
      ),
    ) as StressManifest;
    const fixtureTestsByPath = new Map<string, string[]>();
    for (const failure of fixture.failures) {
      const committedFile = committedManifest.files.find(
        (file) => file.path === failure.path,
      );
      expect(committedFile?.tests).toContain(failure.streamedHierarchy);
      const tests = fixtureTestsByPath.get(failure.path) ?? [];
      tests.push(failure.streamedHierarchy);
      fixtureTestsByPath.set(failure.path, tests);
    }
    const fixtureManifest: StressManifest = {
      schemaVersion: committedManifest.schemaVersion,
      files: [...fixtureTestsByPath.entries()].map(([path, tests]) => ({
        path,
        tests,
      })),
    };
    const assertionsByPath = new Map<string, Array<Record<string, unknown>>>();
    for (const failure of fixture.failures) {
      const assertions = assertionsByPath.get(failure.path) ?? [];
      assertions.push({
        status: "failed",
        fullName: failure.jsonFullName,
        failureMessages:
          failure.failureKind === "timeout"
            ? ["Error: STACK_TRACE_ERROR\n    at adapted fixture"]
            : ["AssertionError: adapted real V3 assertion block"],
      });
      assertionsByPath.set(failure.path, assertions);
    }
    const parsedMixedFailures = runner.parseStressFailures({
      testResults: [...assertionsByPath.entries()].map(
        ([pathname, assertionResults]) => ({
          name: pathname.replace(/^apps\/web\//, ""),
          assertionResults,
        }),
      ),
    });
    expect(
      parsedMixedFailures.map((failure: StressFailure) => failure.failureKind),
    ).toEqual(["timeout", "assertion", "timeout", "assertion"]);

    const mixedCapture = new runner.StreamedVitestOutputCapture();
    for (const failure of fixture.failures) {
      mixedCapture.write(
        "stderr",
        ` FAIL  ${failure.path} > ${failure.streamedHierarchy}\n`,
      );
      mixedCapture.write(
        "stderr",
        failure.failureKind === "timeout"
          ? 'Error: Test timed out in 5000ms.\nIf this is a long-running test, pass a timeout value as the last argument or configure it globally with "testTimeout".\n'
          : "AssertionError: adapted real V3 assertion block\n",
      );
    }
    mixedCapture.finish();
    const mixedStream = mixedCapture.evidence();
    const mixedCorrelation = runner.correlateStressFailures(
      parsedMixedFailures,
      mixedStream,
      { manifest: fixtureManifest },
    );
    const mixedEvidenceIntegrity = runner.validateStressEvidence({
      result: { code: 1 },
      report: vitest410Report(fixtureManifest, {
        failures: parsedMixedFailures,
      }),
      rawVitestJson: completeArtifact("mixed.json"),
      rawStream: completeArtifact("mixed.stream"),
      streamedOutput: mixedStream,
      correlation: mixedCorrelation,
      failures: mixedCorrelation.failures,
      identityCatalog: runner.manifestStressIdentityCatalog(fixtureManifest),
    });
    const mixedClassification = runner.classifyStressResult({
      exitCode: 1,
      failures: mixedCorrelation.failures,
      identityMismatch: mixedCorrelation.identityMismatch,
      evidenceIntegrity: mixedEvidenceIntegrity,
      canonicalPass: true,
      interactionPass: true,
      controlledPass: true,
    });
    expect(mixedCorrelation).toMatchObject({
      identityMismatch: false,
      nonTimeoutIdentityMismatch: false,
      timeoutFailures: [
        expect.objectContaining({
          failureKind: "timeout",
          timeoutKind: "test",
        }),
        expect.objectContaining({
          failureKind: "timeout",
          timeoutKind: "test",
        }),
      ],
      nonTimeoutFailures: [
        expect.objectContaining({ failureKind: "assertion" }),
        expect.objectContaining({ failureKind: "assertion" }),
      ],
    });
    expect(mixedCorrelation.timeoutBlocks).toHaveLength(2);
    expect(mixedCorrelation.nonTimeoutBlocks).toHaveLength(2);
    expect(mixedEvidenceIntegrity).toMatchObject({ valid: true });
    expect(mixedClassification).toMatchObject({
      decision: "RELEASE_BLOCKED",
      reason: "stress assertion failure",
    });
    expect(
      runner.canAttemptControlledAdjudication({
        prior: { path: "prior-canonical.json" },
        evidenceIntegrity: mixedEvidenceIntegrity,
        correlation: mixedCorrelation,
        failures: mixedCorrelation.failures,
        affectedPaths: fixtureManifest.files.map((file) => file.path),
      }),
    ).toBe(false);

    expect(
      runner.classifyStressResult({
        exitCode: -1,
        abnormal: true,
        failures: [],
      }),
    ).toMatchObject({ decision: "RELEASE_BLOCKED" });
  });

  it("validates evidence integrity before every STRESS_PASS result", () => {
    const manifest = adaptedVitest410SuccessManifest();
    const identityCatalog = runner.manifestStressIdentityCatalog(manifest);
    const cleanReport = vitest410Report(manifest, {
      numTotalTestSuites:
        ADAPTED_RETAINED_VITEST_4_1_10_SUCCESS_FIXTURE.numTotalTestSuites,
    });
    const cleanCapture = new runner.StreamedVitestOutputCapture().evidence();
    const cleanCorrelation = runner.correlateStressFailures([], cleanCapture, {
      identityCatalog,
    });
    const complete = runner.validateStressEvidence({
      result: { code: 0 },
      report: cleanReport,
      rawVitestJson: completeArtifact("clean.json"),
      rawStream: completeArtifact("clean.stream"),
      streamedOutput: cleanCapture,
      correlation: cleanCorrelation,
      failures: [],
      identityCatalog,
    });
    expect(complete.valid).toBe(true);
    expect(
      runner.classifyStressResult({
        exitCode: 0,
        failures: [],
        evidenceIntegrity: complete,
      }),
    ).toMatchObject({ decision: "STRESS_PASS" });

    const malformed = [
      { abnormal: true, failures: [], identityMismatch: true },
      { failures: [], evidenceIntegrity: { valid: false } },
      { failures: [], identityMismatch: true, evidenceIntegrity: complete },
      { failures: [], signal: "SIGTERM", evidenceIntegrity: complete },
      { failures: [], captureTruncated: true, evidenceIntegrity: complete },
      { failures: [], countMismatch: true, evidenceIntegrity: complete },
    ];
    for (const input of malformed) {
      expect(
        runner.classifyStressResult({ exitCode: 0, ...input }),
      ).toMatchObject({ decision: "RELEASE_BLOCKED" });
    }
  });

  it("admits only exit codes zero and one before timeout adjudication", () => {
    const manifest = stressManifest();
    const timeout = {
      path: "apps/web/tests/bulk-preview-workflow.test.tsx",
      name: "Bulk unified preview review presents linked Queue summary, row evidence, stale/unresolved guidance, and Confirm without blocking",
    };
    const timeoutStreamEvidence = timeoutStream([
      { path: timeout.path, name: manifest.files[0].tests[0] },
    ]);
    const adjudicated = classifyTimeoutFixture(
      [timeout],
      timeoutStreamEvidence,
      manifest,
      1,
    );
    expect(adjudicated.correlation.identityMismatch).toBe(false);
    expect(adjudicated.evidenceIntegrity.valid).toBe(true);
    expect(adjudicated.classification).toMatchObject({
      decision: "STRESS_RESOURCE_CONTENTION_NON_BLOCKING",
    });

    for (const exitCode of [2, 130, 137]) {
      const unexpected = classifyTimeoutFixture(
        [timeout],
        timeoutStreamEvidence,
        manifest,
        exitCode,
      );
      expect(unexpected.correlation.identityMismatch).toBe(false);
      expect(unexpected.evidenceIntegrity.valid).toBe(false);
      expect(unexpected.evidenceIntegrity.issues).toContain(
        "stress child exited with an unexpected code",
      );
      expect(unexpected.classification).toMatchObject({
        decision: "RELEASE_BLOCKED",
      });
    }

    const assertion = {
      path: "apps/web/tests/influencer-preview.test.tsx",
      name: "development influencer visual preview opens and closes preview drawer from local preview table interactions",
    };
    const assertionStream = new runner.StreamedVitestOutputCapture();
    assertionStream.write(
      "stderr",
      ` FAIL  ${assertion.path} > ${manifest.files[1].tests[0]}\nAssertionError: expected true to be false\n`,
    );
    assertionStream.finish();
    expect(
      classifyTimeoutFixture(
        [assertion],
        assertionStream.evidence(),
        manifest,
        1,
      ).classification,
    ).toMatchObject({ decision: "RELEASE_BLOCKED" });

    const cleanManifest = adaptedVitest410SuccessManifest();
    const cleanCatalog = runner.manifestStressIdentityCatalog(cleanManifest);
    const cleanCapture = new runner.StreamedVitestOutputCapture().evidence();
    const cleanCorrelation = runner.correlateStressFailures([], cleanCapture, {
      identityCatalog: cleanCatalog,
    });
    const cleanEvidence = runner.validateStressEvidence({
      result: { code: 0 },
      report: vitest410Report(cleanManifest, {
        numTotalTestSuites:
          ADAPTED_RETAINED_VITEST_4_1_10_SUCCESS_FIXTURE.numTotalTestSuites,
      }),
      rawVitestJson: completeArtifact("clean.json"),
      rawStream: completeArtifact("clean.stream"),
      streamedOutput: cleanCapture,
      correlation: cleanCorrelation,
      failures: [],
      identityCatalog: cleanCatalog,
    });
    expect(cleanEvidence.valid).toBe(true);
    expect(
      runner.classifyStressResult({
        exitCode: 0,
        failures: [],
        evidenceIntegrity: cleanEvidence,
      }),
    ).toMatchObject({ decision: "STRESS_PASS" });
    const exitZeroWithFailure = classifyTimeoutFixture(
      [timeout],
      timeoutStreamEvidence,
      manifest,
      0,
    );
    expect(exitZeroWithFailure.evidenceIntegrity.valid).toBe(false);
    expect(exitZeroWithFailure.classification).toMatchObject({
      decision: "RELEASE_BLOCKED",
    });
  });

  it("uses real Vitest file records and exact JSON identities instead of suite counters", () => {
    const fixture = ADAPTED_RETAINED_VITEST_4_1_10_SUCCESS_FIXTURE;
    const manifest = adaptedVitest410SuccessManifest();
    const identityCatalog = runner.manifestStressIdentityCatalog(manifest);
    const capture = new runner.StreamedVitestOutputCapture().evidence();
    const correlation = runner.correlateStressFailures([], capture, {
      identityCatalog,
    });
    const report = vitest410Report(manifest, {
      numTotalTestSuites: fixture.numTotalTestSuites,
    });
    const validate = (candidate: typeof report) =>
      runner.validateStressEvidence({
        result: { code: 0 },
        report: candidate,
        rawVitestJson: completeArtifact("success.json"),
        rawStream: completeArtifact("success.stream"),
        streamedOutput: capture,
        correlation,
        failures: [],
        identityCatalog,
      });

    expect(fixture.label).toBe(
      "ADAPTED_RETAINED_VITEST_4_1_10_SUCCESS_FIXTURE",
    );
    expect(report.numTotalTestSuites).toBe(2);
    expect(report.testResults).toHaveLength(1);
    expect(report.numTotalTestSuites).not.toBe(report.testResults.length);
    expect(
      report.testResults[0].assertionResults.map(
        (assertion) => assertion.fullName,
      ),
    ).toEqual(
      identityCatalog.entries.map(
        (identity: { jsonProjection: string }) => identity.jsonProjection,
      ),
    );
    const coherent = validate(report);
    expect(coherent).toMatchObject({
      valid: true,
      executionInventory: {
        valid: true,
        files: { expected: 1, actualRecords: 1 },
        identities: { expected: 2, executedRecords: 2 },
      },
    });
    expect(
      runner.classifyStressResult({
        exitCode: 0,
        failures: [],
        evidenceIntegrity: coherent,
      }),
    ).toMatchObject({ decision: "STRESS_PASS" });

    const unexpectedFile = structuredClone(report);
    unexpectedFile.testResults.push({
      name: "tests/unexpected.test.ts",
      assertionResults: [
        {
          status: "passed",
          fullName: report.testResults[0].assertionResults[0].fullName,
        },
      ],
    });
    unexpectedFile.numTotalTests += 1;
    unexpectedFile.numPassedTests += 1;

    const missingFile = structuredClone(report);
    missingFile.testResults = [];
    missingFile.numTotalTests = 0;
    missingFile.numPassedTests = 0;

    const duplicateFile = structuredClone(report);
    duplicateFile.testResults.push(structuredClone(report.testResults[0]));
    duplicateFile.numTotalTests += report.numTotalTests;
    duplicateFile.numPassedTests += report.numPassedTests;

    const missingIdentity = structuredClone(report);
    missingIdentity.testResults[0].assertionResults.pop();
    missingIdentity.numTotalTests -= 1;
    missingIdentity.numPassedTests -= 1;

    const unexpectedIdentity = structuredClone(report);
    unexpectedIdentity.testResults[0].assertionResults[0].fullName =
      "Bulk unified preview review presents a different exact title";

    const duplicateIdentity = structuredClone(report);
    duplicateIdentity.testResults[0].assertionResults.push(
      structuredClone(report.testResults[0].assertionResults[0]),
    );
    duplicateIdentity.numTotalTests += 1;
    duplicateIdentity.numPassedTests += 1;

    const contradictoryTotals = structuredClone(report);
    contradictoryTotals.numTotalTests = 1;
    contradictoryTotals.numPassedTests = 1;

    const malformed = [
      [
        unexpectedFile,
        "Vitest file-result inventory contains unexpected files",
      ],
      [missingFile, "Vitest file-result inventory is missing expected files"],
      [duplicateFile, "Vitest file-result inventory contains duplicate files"],
      [
        missingIdentity,
        "Vitest executed assertion inventory is missing expected identities",
      ],
      [
        unexpectedIdentity,
        "Vitest executed assertion identity is unresolved or ambiguous",
      ],
      [
        duplicateIdentity,
        "Vitest executed assertion inventory contains duplicate identities",
      ],
      [
        contradictoryTotals,
        "reported total-test count does not match assertion results",
      ],
    ] as const;
    for (const [candidate, expectedIssue] of malformed) {
      const evidenceIntegrity = validate(candidate);
      expect(evidenceIntegrity.valid).toBe(false);
      expect(evidenceIntegrity.issues).toEqual(
        expect.arrayContaining([expect.stringContaining(expectedIssue)]),
      );
    }
  });

  it("fails closed for missing JSON, truncated capture, explicit failure, and count mismatch on an exit-zero stress result", () => {
    const manifest = adaptedVitest410SuccessManifest();
    const identityCatalog = runner.manifestStressIdentityCatalog(manifest);
    const cleanReport = vitest410Report(manifest, {
      numTotalTestSuites:
        ADAPTED_RETAINED_VITEST_4_1_10_SUCCESS_FIXTURE.numTotalTestSuites,
    });
    const capture = new runner.StreamedVitestOutputCapture().evidence();
    const truncatedCapture = new runner.StreamedVitestOutputCapture({
      maxBytes: 1,
    });
    truncatedCapture.write("stderr", "required evidence\n");
    truncatedCapture.finish();
    expect(truncatedCapture.evidence().truncated).toBe(true);
    expect(
      runner.correlateStressFailures([], truncatedCapture.evidence(), {
        identityCatalog,
      }).identityMismatch,
    ).toBe(true);
    const correlation = runner.correlateStressFailures([], capture, {
      identityCatalog,
    });
    const cases = [
      runner.validateStressEvidence({
        result: { code: 0 },
        rawVitestJson: { path: "missing.json", present: false },
        rawStream: completeArtifact("stream.json"),
        streamedOutput: capture,
        correlation,
        failures: [],
        identityCatalog,
      }),
      runner.validateStressEvidence({
        result: { code: 0 },
        report: cleanReport,
        rawVitestJson: completeArtifact("raw.json"),
        rawStream: completeArtifact("stream.json"),
        streamedOutput: truncatedCapture.evidence(),
        correlation,
        failures: [],
        identityCatalog,
      }),
      runner.validateStressEvidence({
        result: { code: 0 },
        report: { ...cleanReport, numTotalTests: 1 },
        rawVitestJson: completeArtifact("raw.json"),
        rawStream: completeArtifact("stream.json"),
        streamedOutput: capture,
        correlation,
        failures: [],
        identityCatalog,
      }),
      runner.validateStressEvidence({
        result: { code: 0 },
        report: { ...cleanReport, testResults: undefined },
        rawVitestJson: completeArtifact("raw.json"),
        rawStream: completeArtifact("stream.json"),
        streamedOutput: capture,
        correlation,
        failures: [],
        identityCatalog,
      }),
      runner.validateStressEvidence({
        result: { code: 0 },
        report: { ...cleanReport, success: false },
        rawVitestJson: completeArtifact("raw.json"),
        rawStream: completeArtifact("stream.json"),
        streamedOutput: capture,
        correlation,
        failures: [],
        identityCatalog,
      }),
    ];
    for (const evidenceIntegrity of cases) {
      expect(evidenceIntegrity.valid).toBe(false);
      expect(
        runner.classifyStressResult({
          exitCode: 0,
          failures: [],
          evidenceIntegrity,
        }),
      ).toMatchObject({ decision: "RELEASE_BLOCKED" });
    }
    expect(
      runner.classifyStressResult({
        exitCode: 0,
        failures: [{ path: "apps/web/tests/a.test.ts", timeoutKind: "test" }],
        evidenceIntegrity: { valid: true },
      }),
    ).toMatchObject({ decision: "RELEASE_BLOCKED" });
  });

  it("derives immutable per-invocation master, raw JSON, and stdout/stderr artifact paths", async () => {
    const temporaryRoot = path.join(
      os.tmpdir(),
      "release-gate-immutable-artifacts-",
      `${process.pid}-${Date.now()}`,
    );
    temporaryPaths.push(temporaryRoot);
    await mkdir(temporaryRoot, { recursive: true });
    const first = runner.stressArtifactPaths({
      directory: temporaryRoot,
      invocationId: "stress-a",
    });
    const second = runner.stressArtifactPaths({
      directory: temporaryRoot,
      invocationId: "stress-b",
    });
    expect(first).not.toEqual(second);
    await writeFile(first.rawVitestJsonPath, "raw-json-a\n");
    await writeFile(first.rawStreamPath, "raw-stream-a\n");
    await writeFile(
      first.masterEvidencePath,
      JSON.stringify({
        artifacts: {
          masterEvidencePath: first.masterEvidencePath,
          rawVitestJsonPath: first.rawVitestJsonPath,
          rawStreamPath: first.rawStreamPath,
        },
      }),
    );
    const firstJson = await readFile(first.rawVitestJsonPath, "utf8");
    const firstStream = await readFile(first.rawStreamPath, "utf8");
    const firstMaster = await readFile(first.masterEvidencePath, "utf8");
    await writeFile(second.rawVitestJsonPath, "raw-json-b\n");
    await writeFile(second.rawStreamPath, "raw-stream-b\n");
    await writeFile(
      second.masterEvidencePath,
      JSON.stringify({
        artifacts: {
          masterEvidencePath: second.masterEvidencePath,
          rawVitestJsonPath: second.rawVitestJsonPath,
          rawStreamPath: second.rawStreamPath,
        },
      }),
    );

    expect(await readFile(first.rawVitestJsonPath, "utf8")).toBe(firstJson);
    expect(await readFile(first.rawStreamPath, "utf8")).toBe(firstStream);
    expect(await readFile(first.masterEvidencePath, "utf8")).toBe(firstMaster);
    expect(JSON.parse(firstMaster).artifacts).toEqual({
      masterEvidencePath: first.masterEvidencePath,
      rawVitestJsonPath: first.rawVitestJsonPath,
      rawStreamPath: first.rawStreamPath,
    });
    expect(await readFile(second.rawVitestJsonPath, "utf8")).toBe(
      "raw-json-b\n",
    );
    expect(second.masterEvidencePath).not.toBe(first.masterEvidencePath);
    expect(first.rawVitestJsonPath).not.toBe(
      path.join(temporaryRoot, "stress-vitest.json"),
    );
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

  it("retains its lock when POSIX group cleanup cannot prove the group is gone", async () => {
    const evidence: Record<string, unknown> = {};
    let releases = 0;
    const lifecycle = new runner.GateLifecycle(evidence, {
      terminate: async () => ({
        terminated: true,
        childPid: 12_345,
        processGroupId: 12_345,
        leaderExited: true,
        processGroupGone: false,
        forceKillRequired: true,
        cleanupVerification: "unknown",
      }),
    });
    lifecycle.attachLock({
      release: async () => {
        releases += 1;
        return true;
      },
    });

    await lifecycle.requestTermination("SIGTERM");
    await lifecycle.finalize();

    expect(releases).toBe(0);
    expect(evidence).toMatchObject({
      termination: {
        signal: "SIGTERM",
        childTermination: {
          processGroupGone: false,
          cleanupVerification: "unknown",
        },
        lockReleased: false,
        lockReleaseDeferred: true,
      },
    });

    if (process.platform === "win32") {
      return;
    }
    const persistentUnknownLeader = spawn(
      process.execPath,
      [
        "-e",
        'process.on("SIGTERM", () => process.exit(0)); console.log("ready"); setInterval(() => {}, 1000)',
      ],
      { detached: true, stdio: ["ignore", "pipe", "ignore"] },
    );
    let persistentProbeAttempts = 0;
    try {
      await once(persistentUnknownLeader.stdout!, "data");
      const cleanup = await runner.terminateChildProcessGroup(
        persistentUnknownLeader,
        {
          graceMs: 50,
          forceVerificationMs: 50,
          pollMs: 5,
          probeProcessGroup: () => {
            persistentProbeAttempts += 1;
            throw Object.assign(
              new Error("redacted persistent probe failure"),
              {
                code: "EPERM",
              },
            );
          },
        },
      );
      expect(cleanup).toMatchObject({
        processGroupGone: false,
        exitedGracefully: false,
        forceKillRequired: true,
        cleanupVerification: "unknown",
        groupCheckError: {
          code: "EPERM",
          name: "Error",
          phase: "post-SIGKILL",
        },
        groupProbe: {
          lastError: { code: "EPERM", name: "Error" },
          phase: "post-SIGKILL",
        },
      });
      expect(cleanup.groupProbe.attempts).toBeGreaterThan(1);
      expect(persistentProbeAttempts).toBeGreaterThan(2);
      await waitForPidGone(persistentUnknownLeader.pid!);
    } finally {
      if (
        persistentUnknownLeader.pid &&
        pidIsAlive(persistentUnknownLeader.pid)
      ) {
        try {
          process.kill(-persistentUnknownLeader.pid, "SIGKILL");
        } catch {
          // The process group may already have disappeared between checks.
        }
      }
    }
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
    let disposableChildPid: number | undefined;
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
      disposableChildPid = (await waitForJson(readyPath)).childPid;
      process.kill(processUnderTest.pid!, "SIGTERM");
      const [code, signal] = (await once(processUnderTest, "close")) as [
        number,
        NodeJS.Signals | null,
      ];
      expect(signal).toBeNull();
      expect(code).not.toBe(0);
      const evidence = JSON.parse(await readFile(resultPath, "utf8"));
      expect(evidence).toMatchObject({
        termination: {
          signal: "SIGTERM",
          childTermination: { terminated: true },
        },
      });
      expect(disposableChildPid).toEqual(expect.any(Number));
      await waitForPidGone(disposableChildPid!);
      if (evidence.termination.childTermination.processGroupGone === true) {
        expect(evidence.termination.lockReleased).toBe(true);
      } else {
        expect(evidence).toMatchObject({
          termination: {
            childTermination: { cleanupVerification: "unknown" },
            lockReleased: false,
            lockReleaseDeferred: true,
          },
        });
      }
    } finally {
      processUnderTest.kill("SIGKILL");
      if (disposableChildPid && pidIsAlive(disposableChildPid)) {
        try {
          process.kill(disposableChildPid, "SIGKILL");
        } catch {
          // The fixture child can exit between the liveness check and cleanup.
        }
      }
    }
  });

  it("verifies a simple POSIX process group exits gracefully", async () => {
    if (process.platform === "win32") {
      return;
    }
    const leader = spawn(
      process.execPath,
      [
        "-e",
        'process.on("SIGTERM", () => process.exit(0)); console.log("ready"); setInterval(() => {}, 1000)',
      ],
      { detached: true, stdio: ["ignore", "pipe", "ignore"] },
    );
    try {
      await once(leader.stdout!, "data");
      const cleanup = await runner.terminateChildProcessGroup(leader, {
        graceMs: 250,
        forceVerificationMs: 250,
        pollMs: 10,
      });
      expect(cleanup).toMatchObject({
        terminated: true,
        childPid: leader.pid,
        processGroupId: leader.pid,
        leaderExited: true,
        processGroupGone: true,
        exitedGracefully: true,
        forceKillRequired: false,
        cleanupVerification: "verified-gone",
      });
      await waitForPidGone(leader.pid!);
    } finally {
      if (leader.pid && pidIsAlive(leader.pid)) {
        try {
          process.kill(-leader.pid, "SIGKILL");
        } catch {
          // The process group may already have disappeared between checks.
        }
      }
    }

    const transientLeader = spawn(
      process.execPath,
      [
        "-e",
        'process.on("SIGTERM", () => process.exit(0)); console.log("ready"); setInterval(() => {}, 1000)',
      ],
      { detached: true, stdio: ["ignore", "pipe", "ignore"] },
    );
    let transientProbeAttempts = 0;
    try {
      await once(transientLeader.stdout!, "data");
      const cleanup = await runner.terminateChildProcessGroup(transientLeader, {
        graceMs: 250,
        forceVerificationMs: 250,
        pollMs: 10,
        probeProcessGroup: (processGroupId: number) => {
          transientProbeAttempts += 1;
          if (transientProbeAttempts === 1) {
            throw Object.assign(new Error("redacted transient probe failure"), {
              code: "EAGAIN",
              name: "TransientProbeError",
            });
          }
          process.kill(-processGroupId, 0);
        },
      });
      expect(cleanup).toMatchObject({
        processGroupGone: true,
        exitedGracefully: true,
        forceKillRequired: false,
        cleanupVerification: "verified-gone",
        groupProbe: {
          lastError: { code: "EAGAIN", name: "TransientProbeError" },
          phase: "graceful",
        },
      });
      expect(cleanup.groupProbe.attempts).toBeGreaterThan(1);
      await waitForPidGone(transientLeader.pid!);
    } finally {
      if (transientLeader.pid && pidIsAlive(transientLeader.pid)) {
        try {
          process.kill(-transientLeader.pid, "SIGKILL");
        } catch {
          // The process group may already have disappeared between checks.
        }
      }
    }
  });

  it("force-kills a resistant descendant group after its leader exits and verifies every recorded PID disappears", async () => {
    if (process.platform === "win32") {
      return;
    }
    const temporaryRoot = path.join(
      os.tmpdir(),
      "release-gate-resistant-group-",
      `${process.pid}-${Date.now()}`,
    );
    temporaryPaths.push(temporaryRoot);
    await mkdir(temporaryRoot, { recursive: true });
    const readyPath = path.join(temporaryRoot, "descendants.json");
    const descendantReadyPaths = [
      path.join(temporaryRoot, "descendant-0-ready"),
      path.join(temporaryRoot, "descendant-1-ready"),
    ];
    const source = `
      const { spawn } = require("node:child_process");
      const { writeFileSync } = require("node:fs");
      const descendantReadyPaths = ${JSON.stringify(descendantReadyPaths)};
      const descendants = descendantReadyPaths.map((readyPath) => spawn(process.execPath, ["-e", "process.on('SIGTERM', () => {}); require('node:fs').writeFileSync(process.argv[1], 'ready'); setInterval(() => {}, 1000)", readyPath], { stdio: "ignore" }));
      writeFileSync(${JSON.stringify(readyPath)}, JSON.stringify({ descendants: descendants.map((child) => child.pid) }));
      process.exit(0);
    `;
    const leader = spawn(process.execPath, ["-e", source], {
      detached: true,
      stdio: "ignore",
    });
    const leaderClosed = once(leader, "close");
    let descendants: number[] = [];
    try {
      await waitForPath(readyPath);
      descendants = (await waitForJson(readyPath)).descendants;
      expect(descendants).toHaveLength(2);
      await Promise.all(descendantReadyPaths.map(waitForPath));
      await leaderClosed;
      expect(leader.exitCode).toBe(0);
      const evidence: Record<string, unknown> = {};
      let terminationCalls = 0;
      let releases = 0;
      const lifecycle = new runner.GateLifecycle(evidence, {
        terminate: async () => {
          terminationCalls += 1;
          return runner.terminateChildProcessGroup(leader, {
            graceMs: 100,
            forceVerificationMs: 1_000,
            pollMs: 10,
          });
        },
      });
      lifecycle.attachLock({
        release: async () => {
          releases += 1;
          return true;
        },
      });
      const watchdog = new runner.GateWatchdog(evidence, {
        timeoutMs: 1,
        terminate: () => lifecycle.requestTermination("WATCHDOG"),
      });
      const firstSignal = lifecycle.requestTermination("SIGTERM");
      const repeatedSignal = lifecycle.requestTermination("SIGINT");
      const watchdogCleanup = watchdog.expire();
      const [cleanup] = await Promise.all([
        firstSignal,
        repeatedSignal,
        watchdogCleanup,
      ]);
      await lifecycle.finalize();
      expect(cleanup).toMatchObject({
        terminated: true,
        childPid: leader.pid,
        processGroupId: leader.pid,
        leaderExited: true,
        processGroupGone: true,
        exitedGracefully: false,
        forceKillRequired: true,
        cleanupVerification: "verified-gone",
      });
      expect(terminationCalls).toBe(1);
      expect(releases).toBe(1);
      expect(evidence).toMatchObject({
        termination: {
          signal: "SIGTERM",
          childTermination: { forceKillRequired: true, processGroupGone: true },
          lockReleased: true,
        },
        watchdog: {
          expired: true,
          termination: { forceKillRequired: true, processGroupGone: true },
        },
      });
      await Promise.all([
        waitForPidGone(leader.pid!),
        ...descendants.map(waitForPidGone),
      ]);
      expect(descendants.every((pid) => !pidIsAlive(pid))).toBe(true);
    } finally {
      if (leader.pid && pidIsAlive(leader.pid)) {
        try {
          process.kill(-leader.pid, "SIGKILL");
        } catch {
          // The group may have exited between the liveness check and cleanup.
        }
      }
      for (const pid of descendants) {
        if (pidIsAlive(pid)) {
          try {
            process.kill(pid, "SIGKILL");
          } catch {
            // The descendant can exit during fixture cleanup.
          }
        }
      }
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
