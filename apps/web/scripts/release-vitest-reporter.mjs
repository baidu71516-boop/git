import { mkdir, rename, writeFile } from "node:fs/promises";
import path from "node:path";

const evidencePath = process.env.WEB_RELEASE_REPORTER_EVIDENCE;
const repositoryRoot = process.env.WEB_RELEASE_REPOSITORY_ROOT;

function normalizePath(moduleId) {
  return path.relative(repositoryRoot, moduleId).replaceAll("\\", "/");
}

function recordConfig(config) {
  return {
    pool: config.pool,
    maxWorkers: config.maxWorkers,
    fileParallelism: config.fileParallelism,
    isolate: config.isolate,
    retry: config.retry,
    allowOnly: config.allowOnly,
    shuffle: Boolean(config.sequence?.shuffle),
    concurrent: Boolean(config.sequence?.concurrent),
    testTimeout: config.testTimeout,
    hookTimeout: config.hookTimeout,
    teardownTimeout: config.teardownTimeout,
  };
}

function configurationMatches(config) {
  return (
    config.pool === "forks" &&
    config.maxWorkers === 1 &&
    config.fileParallelism === false &&
    config.isolate === true &&
    config.retry === 0 &&
    config.allowOnly === false &&
    config.sequence?.shuffle === false &&
    config.sequence?.concurrent === false &&
    config.testTimeout === 5000 &&
    config.hookTimeout === 10000 &&
    config.teardownTimeout === 10000 &&
    process.env.WEB_RELEASE_TIMEOUT_OVERRIDE_PROOF === "none"
  );
}

export default class ReleaseVitestReporter {
  constructor() {
    this.evidence = {
      schemaVersion: 1,
      leg: process.env.WEB_RELEASE_LEG,
      collected: [],
      ready: [],
      results: [],
      modules: [],
      finalTests: [],
    };
  }

  onInit(vitest) {
    const config = vitest.config;
    const projects = vitest.projects.map((project) => ({
      name: project.name,
      config: recordConfig(project.config),
      valid: configurationMatches(project.config),
    }));
    this.evidence.resolvedConfig = {
      vitestVersion: vitest.version,
      config: recordConfig(config),
      projects,
      timeoutOverridesSuppliedByRunner: false,
      timeoutOverrideProof: process.env.WEB_RELEASE_TIMEOUT_OVERRIDE_PROOF,
      valid:
        configurationMatches(config) &&
        projects.every((project) => project.valid),
    };

    if (!this.evidence.resolvedConfig.valid) {
      void this.writeEvidence();
      throw new Error(
        "Resolved Vitest configuration does not satisfy the release gate",
      );
    }
  }

  onTestModuleCollected(testModule) {
    for (const testCase of testModule.children.allTests()) {
      this.evidence.collected.push({
        path: normalizePath(testCase.module.moduleId),
        name: testCase.fullName,
        id: testCase.id,
        mode: testCase.options.mode,
      });
    }
  }

  onTestCaseReady(testCase) {
    this.evidence.ready.push({
      path: normalizePath(testCase.module.moduleId),
      name: testCase.fullName,
      id: testCase.id,
    });
  }

  onTestCaseResult(testCase) {
    const result = testCase.result();
    this.evidence.results.push({
      path: normalizePath(testCase.module.moduleId),
      name: testCase.fullName,
      id: testCase.id,
      state: result.state,
      errors: result.errors ?? [],
    });
  }

  onTestModuleEnd(testModule) {
    this.evidence.modules.push({
      path: normalizePath(testModule.moduleId),
      state: testModule.state(),
      errors: testModule.errors(),
    });
  }

  async onTestRunEnd(testModules, unhandledErrors, reason) {
    for (const testModule of testModules) {
      for (const testCase of testModule.children.allTests()) {
        this.evidence.finalTests.push({
          path: normalizePath(testCase.module.moduleId),
          name: testCase.fullName,
          id: testCase.id,
          mode: testCase.options.mode,
          state: testCase.result().state,
        });
      }
    }
    this.evidence.final = { reason, unhandledErrors };
    await this.writeEvidence();
  }

  async writeEvidence() {
    if (!evidencePath) {
      throw new Error(
        "WEB_RELEASE_REPORTER_EVIDENCE is required by the release reporter",
      );
    }
    await mkdir(path.dirname(evidencePath), { recursive: true });
    const temporaryPath = `${evidencePath}.${process.pid}.tmp`;
    await writeFile(
      temporaryPath,
      `${JSON.stringify(this.evidence, null, 2)}\n`,
      "utf8",
    );
    await rename(temporaryPath, evidencePath);
  }
}
