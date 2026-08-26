#!/usr/bin/env node

import { createHash, randomUUID } from "node:crypto";
import { existsSync } from "node:fs";
import {
  mkdir,
  readFile,
  readdir,
  rename,
  rm,
  writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawn, spawnSync } from "node:child_process";

const SCRIPT_DIRECTORY = path.dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = path.resolve(SCRIPT_DIRECTORY, "..");
const REPOSITORY_ROOT = path.resolve(WEB_ROOT, "..", "..");
const TEST_ROOT = path.join(WEB_ROOT, "tests");
const MANIFEST_PATH = path.join(TEST_ROOT, "release-test-manifest.json");
const RELEASE_CONFIG = "release-vitest.config.ts";
const EVIDENCE_ROOT = process.env.WEB_RELEASE_EVIDENCE_DIR
  ? path.resolve(process.env.WEB_RELEASE_EVIDENCE_DIR)
  : path.join(REPOSITORY_ROOT, ".release-evidence");
const LOCK_SLUG = "influencer-outreach-web-release-gate";
const WATCHDOG_MS = 15 * 60 * 1000;
const TERMINATION_GRACE_MS = 5 * 1000;
const STREAM_CAPTURE_MAX_BYTES = 2 * 1024 * 1024;
const TEST_FILE_PATTERN = /\.(?:test|spec)\.(?:ts|tsx)$/;
const INTERACTION_TEST_PATHS = [
  "apps/web/tests/candidate-pool-detail-tabs.test.tsx",
  "apps/web/tests/candidate-selection.test.ts",
  "apps/web/tests/candidate-run-detail-selection.test.tsx",
  "apps/web/tests/bulk-import-workspace.test.tsx",
  "apps/web/tests/bulk-preview-workflow.test.tsx",
  "apps/web/tests/data-collection-workspace.test.tsx",
];

class GateError extends Error {
  constructor(classification, message, details = {}) {
    super(message);
    this.name = "GateError";
    this.classification = classification;
    this.details = details;
  }
}

function gateError(classification, message, details) {
  return new GateError(classification, message, details);
}

function lexicalSort(values) {
  return [...values].sort((left, right) =>
    left < right ? -1 : left > right ? 1 : 0,
  );
}

function normalizedRepositoryPath(filePath) {
  const relative = path
    .relative(REPOSITORY_ROOT, filePath)
    .replaceAll("\\", "/");
  if (!relative || relative.startsWith("../") || path.isAbsolute(relative)) {
    throw gateError(
      "RELEASE_BLOCKED",
      `Path is outside the repository: ${filePath}`,
    );
  }
  return relative;
}

function manifestKey(testPath, name) {
  return `${testPath}\u0000${name}`;
}

function stableJson(value) {
  return `${JSON.stringify(value, null, 2)}\n`;
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function effectiveUserIdentity() {
  return (
    process.env.USER ??
    process.env.USERNAME ??
    os.userInfo().username ??
    "unknown"
  );
}

function git(arguments_, options = {}) {
  const result = spawnSync("git", arguments_, {
    cwd: REPOSITORY_ROOT,
    encoding: "utf8",
    windowsHide: true,
  });
  if (result.error) {
    if (options.allowFailure) {
      return { code: -1, stdout: "", stderr: result.error.message };
    }
    throw gateError(
      "NOT_EXECUTED",
      `Unable to run git ${arguments_.join(" ")}: ${result.error.message}`,
    );
  }
  const output = {
    code: result.status ?? (result.signal ? -1 : 0),
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
  };
  if (output.code !== 0 && !options.allowFailure) {
    throw gateError(
      "NOT_EXECUTED",
      `git ${arguments_.join(" ")} failed: ${output.stderr.trim() || output.stdout.trim()}`,
    );
  }
  return output;
}

function gitMetadata() {
  const status = git(["status", "--porcelain=v1"], { allowFailure: true });
  return {
    repositoryPath: REPOSITORY_ROOT,
    branch:
      git(["branch", "--show-current"], { allowFailure: true }).stdout.trim() ||
      "DETACHED",
    sha: git(["rev-parse", "HEAD"]).stdout.trim(),
    clean: status.code === 0 && status.stdout.trim() === "",
    dirtyEntries:
      status.code === 0
        ? status.stdout.trim().split("\n").filter(Boolean)
        : ["UNAVAILABLE"],
  };
}

async function readProcessStartIdentity(pid) {
  if (process.platform !== "linux") {
    return undefined;
  }
  try {
    const stat = await readFile(`/proc/${pid}/stat`, "utf8");
    const closingParenthesis = stat.lastIndexOf(")");
    const fields = stat
      .slice(closingParenthesis + 2)
      .trim()
      .split(/\s+/);
    return fields[19];
  } catch {
    return undefined;
  }
}

async function inspectLockOwner(owner) {
  if (
    !owner ||
    !Number.isInteger(owner.pid) ||
    owner.pid <= 0 ||
    typeof owner.token !== "string"
  ) {
    return { state: "ambiguous", reason: "owner metadata is incomplete" };
  }
  try {
    process.kill(owner.pid, 0);
  } catch (error) {
    if (error && error.code === "ESRCH") {
      return { state: "stale", reason: "owner PID does not exist" };
    }
    return {
      state: "ambiguous",
      reason: "owner PID cannot be inspected safely",
    };
  }

  if (owner.processStartIdentity) {
    const actualIdentity = await readProcessStartIdentity(owner.pid);
    if (actualIdentity && actualIdentity !== owner.processStartIdentity) {
      return { state: "stale", reason: "owner PID was reused" };
    }
    if (!actualIdentity) {
      return {
        state: "ambiguous",
        reason: "owner process-start identity cannot be verified",
      };
    }
  }
  return { state: "live", reason: "owner process is alive" };
}

export class GateLock {
  constructor(options = {}) {
    const userHash = sha256(
      options.userIdentity ?? effectiveUserIdentity(),
    ).slice(0, 24);
    this.lockPath =
      options.lockPath ??
      path.join(os.tmpdir(), LOCK_SLUG, userHash, "gate.lock");
    this.ownerPath = path.join(this.lockPath, "owner.json");
    this.metadata = options.metadata ?? gitMetadata();
    this.inspectOwner = options.inspectOwner ?? inspectLockOwner;
    this.token = options.token ?? randomUUID();
    this.held = false;
  }

  async acquire() {
    await mkdir(path.dirname(this.lockPath), { recursive: true });
    for (;;) {
      try {
        await mkdir(this.lockPath);
        const owner = {
          pid: process.pid,
          processStartIdentity: await readProcessStartIdentity(process.pid),
          acquiredAt: new Date().toISOString(),
          worktree: REPOSITORY_ROOT,
          gitSha: this.metadata.sha,
          token: this.token,
        };
        await writeFile(this.ownerPath, stableJson(owner), "utf8");
        this.held = true;
        return { acquired: true, lockPath: this.lockPath, owner };
      } catch (error) {
        if (!error || error.code !== "EEXIST") {
          throw gateError(
            "NOT_EXECUTED",
            `Unable to acquire the host release lock: ${error?.message ?? error}`,
          );
        }
      }

      let owner;
      try {
        owner = JSON.parse(await readFile(this.ownerPath, "utf8"));
      } catch {
        throw gateError(
          "NOT_EXECUTED",
          "GATE_LOCK_AMBIGUOUS: existing lock has unreadable owner metadata",
        );
      }
      const inspection = await this.inspectOwner(owner);
      if (inspection.state === "live") {
        throw gateError(
          "NOT_EXECUTED",
          "GATE_LOCK_HELD: another release gate owns the host lock",
          { owner },
        );
      }
      if (inspection.state !== "stale") {
        throw gateError(
          "NOT_EXECUTED",
          `GATE_LOCK_AMBIGUOUS: ${inspection.reason}`,
          { owner },
        );
      }

      const quarantinePath = `${this.lockPath}.stale-${randomUUID()}`;
      try {
        await rename(this.lockPath, quarantinePath);
      } catch (error) {
        if (error && error.code === "ENOENT") {
          continue;
        }
        throw gateError(
          "NOT_EXECUTED",
          `Unable to quarantine proven-stale release lock: ${error?.message ?? error}`,
        );
      }
    }
  }

  async release() {
    if (!this.held) {
      return false;
    }
    try {
      const owner = JSON.parse(await readFile(this.ownerPath, "utf8"));
      if (owner.token !== this.token) {
        return false;
      }
      await rm(this.lockPath, { recursive: true, force: false });
      this.held = false;
      return true;
    } catch (error) {
      if (error && error.code === "ENOENT") {
        this.held = false;
        return false;
      }
      throw error;
    }
  }
}

let activeChild;
let activeChildTermination;
let activeLifecycle;

function pnpmInvocation(arguments_) {
  const npmExecPath = process.env.npm_execpath;
  if (npmExecPath && /pnpm(?:\.c?js)?$/i.test(npmExecPath)) {
    return { command: process.execPath, args: [npmExecPath, ...arguments_] };
  }
  const corepack = path.join(
    path.dirname(process.execPath),
    "node_modules",
    "corepack",
    "dist",
    "corepack.js",
  );
  if (existsSync(corepack)) {
    return {
      command: process.execPath,
      args: [corepack, "pnpm", ...arguments_],
    };
  }
  return {
    command: process.platform === "win32" ? "pnpm.cmd" : "pnpm",
    args: arguments_,
  };
}

export function runChild(command, arguments_, options = {}) {
  return new Promise((resolve) => {
    const lifecycle = options.lifecycle ?? activeLifecycle;
    if (lifecycle?.terminating) {
      resolve({
        code: -1,
        signal: lifecycle.signal,
        error: "release invocation is terminating",
      });
      return;
    }
    const child = spawn(command, arguments_, {
      cwd: options.cwd ?? WEB_ROOT,
      env: options.env ?? process.env,
      detached: process.platform !== "win32",
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    activeChild = child;
    child.stdout?.on("data", (chunk) => {
      process.stdout.write(chunk);
      options.streamCapture?.write("stdout", chunk);
    });
    child.stderr?.on("data", (chunk) => {
      process.stderr.write(chunk);
      options.streamCapture?.write("stderr", chunk);
    });
    child.once("error", (error) => {
      if (activeChild === child) {
        activeChild = undefined;
      }
      options.streamCapture?.finish();
      resolve({ code: -1, signal: undefined, error: error.message });
    });
    child.once("close", (code, signal) => {
      if (activeChild === child) {
        activeChild = undefined;
      }
      options.streamCapture?.finish();
      resolve({ code: code ?? -1, signal, error: undefined });
    });
  });
}

function waitForChild(child, milliseconds) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(false), milliseconds);
    child.once("close", () => {
      clearTimeout(timer);
      resolve(true);
    });
  });
}

export async function terminateChildProcessGroup(child) {
  const target = child ?? activeChild;
  if (!target || target.exitCode !== null || target.killed) {
    return { terminated: false };
  }

  try {
    if (process.platform === "win32") {
      spawnSync("taskkill", ["/pid", String(target.pid), "/t"], {
        windowsHide: true,
      });
    } else {
      process.kill(-target.pid, "SIGTERM");
    }
  } catch {
    target.kill("SIGTERM");
  }
  const exitedGracefully = await waitForChild(target, TERMINATION_GRACE_MS);
  if (!exitedGracefully) {
    try {
      if (process.platform === "win32") {
        spawnSync("taskkill", ["/pid", String(target.pid), "/t", "/f"], {
          windowsHide: true,
        });
      } else {
        process.kill(-target.pid, "SIGKILL");
      }
    } catch {
      target.kill("SIGKILL");
    }
  }
  return { terminated: true, exitedGracefully };
}

async function terminateActiveChild() {
  if (!activeChildTermination) {
    activeChildTermination = terminateChildProcessGroup(activeChild).finally(
      () => {
        activeChildTermination = undefined;
      },
    );
  }
  return activeChildTermination;
}

export class GateLifecycle {
  constructor(evidence, options = {}) {
    this.evidence = evidence;
    this.terminate = options.terminate ?? terminateActiveChild;
    this.signalEmitter = options.signalEmitter ?? process;
    this.signals = options.signals ?? ["SIGINT", "SIGTERM", "SIGHUP"];
    this.lock = undefined;
    this.terminating = false;
    this.signal = undefined;
    this.cleanupPromise = undefined;
    this.lockReleasePromise = undefined;
    this.handlers = new Map();
  }

  attachLock(lock) {
    this.lock = lock;
  }

  installSignalHandlers() {
    for (const signal of this.signals) {
      const handler = () => {
        void this.requestTermination(signal).catch((error) => {
          this.evidence.termination.cleanupError =
            error instanceof Error ? error.message : String(error);
        });
      };
      try {
        this.signalEmitter.on(signal, handler);
        this.handlers.set(signal, handler);
      } catch {
        // Signal registration is platform-dependent. The runner remains usable
        // where a runtime does not expose one of these POSIX signals.
      }
    }
  }

  removeSignalHandlers() {
    for (const [signal, handler] of this.handlers) {
      this.signalEmitter.removeListener?.(signal, handler);
    }
    this.handlers.clear();
  }

  assertNotTerminating() {
    if (this.terminating) {
      throw gateError(
        "RELEASE_BLOCKED",
        `RELEASE_TERMINATED_BY_${this.signal ?? "WATCHDOG"}`,
      );
    }
  }

  async releaseLock() {
    if (!this.lock) {
      return false;
    }
    if (!this.lockReleasePromise) {
      this.lockReleasePromise = this.lock.release();
    }
    return this.lockReleasePromise;
  }

  async requestTermination(signal) {
    if (this.cleanupPromise) {
      return this.cleanupPromise;
    }
    this.terminating = true;
    this.signal = signal;
    this.evidence.termination = {
      requested: true,
      signal,
      receivedAt: new Date().toISOString(),
    };
    this.cleanupPromise = (async () => {
      const termination = await this.terminate();
      this.evidence.termination.childTermination = termination;
      this.evidence.termination.lockReleased = await this.releaseLock();
      return termination;
    })();
    return this.cleanupPromise;
  }

  async finalize() {
    if (this.cleanupPromise) {
      await this.cleanupPromise;
      return this.evidence.termination?.lockReleased ?? false;
    }
    return this.releaseLock();
  }
}

export class GateWatchdog {
  constructor(evidence, options = {}) {
    this.evidence = evidence;
    this.timeoutMs = options.timeoutMs ?? WATCHDOG_MS;
    this.terminate = options.terminate ?? terminateActiveChild;
    this.expired = false;
    this.timer = undefined;
  }

  arm() {
    this.timer = setTimeout(() => {
      void this.expire().catch((error) => {
        this.evidence.watchdog = {
          expired: true,
          timeoutMs: this.timeoutMs,
          terminationError:
            error instanceof Error ? error.message : String(error),
        };
      });
    }, this.timeoutMs);
  }

  async expire() {
    if (this.expired) {
      return;
    }
    this.expired = true;
    console.error("WEB_RELEASE_WATCHDOG_EXPIRED");
    this.evidence.watchdog = {
      expired: true,
      timeoutMs: this.timeoutMs,
      termination: await this.terminate(),
    };
  }

  clear() {
    if (this.timer) {
      clearTimeout(this.timer);
    }
  }

  assertNotExpired() {
    if (this.expired) {
      throw gateError("RELEASE_BLOCKED", "WATCHDOG_EXPIRED");
    }
  }
}

async function writeEvidence(evidencePath, evidence) {
  await mkdir(path.dirname(evidencePath), { recursive: true });
  const temporaryPath = `${evidencePath}.${process.pid}.tmp`;
  await writeFile(temporaryPath, stableJson(evidence), "utf8");
  await rename(temporaryPath, evidencePath);
}

async function discoverTestFiles(directory = TEST_ROOT) {
  const entries = await readdir(directory, { withFileTypes: true });
  const paths = [];
  for (const entry of entries) {
    const fullPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      paths.push(...(await discoverTestFiles(fullPath)));
    } else if (entry.isFile() && TEST_FILE_PATTERN.test(entry.name)) {
      paths.push(normalizedRepositoryPath(fullPath));
    }
  }
  return lexicalSort(paths);
}

async function rejectForbiddenSourceModes(testPaths) {
  const forbidden = [];
  const patterns = [
    /\b(?:describe|context|suite|it|test)\s*\.\s*(?:skip|todo|only|skipIf)\b/,
    /\b(?:xdescribe|xit|xtest|fdescribe|fit|ftest)\s*\(/,
  ];
  for (const testPath of testPaths) {
    const source = await readFile(path.join(REPOSITORY_ROOT, testPath), "utf8");
    if (patterns.some((pattern) => pattern.test(source))) {
      forbidden.push(testPath);
    }
  }
  if (forbidden.length > 0) {
    throw gateError(
      "RELEASE_BLOCKED",
      `Forbidden skip/todo/only test mode found: ${forbidden.join(", ")}`,
    );
  }
}

function normalizeListEntry(entry) {
  if (
    !entry ||
    typeof entry.name !== "string" ||
    typeof entry.file !== "string"
  ) {
    throw gateError(
      "RELEASE_BLOCKED",
      "Vitest list produced an invalid identity record",
    );
  }
  return {
    path: normalizedRepositoryPath(path.resolve(WEB_ROOT, entry.file)),
    name: entry.name,
  };
}

function manifestFromList(list) {
  const files = new Map();
  for (const entry of list.map(normalizeListEntry)) {
    const tests = files.get(entry.path) ?? [];
    tests.push(entry.name);
    files.set(entry.path, tests);
  }
  const manifestFiles = lexicalSort([...files.keys()]).map((testPath) => ({
    path: testPath,
    tests: lexicalSort(files.get(testPath)),
  }));
  for (const file of manifestFiles) {
    const keys = file.tests.map((name) => manifestKey(file.path, name));
    if (new Set(keys).size !== keys.length) {
      throw gateError(
        "RELEASE_BLOCKED",
        `Vitest list produced duplicate identities for ${file.path}`,
      );
    }
  }
  return { schemaVersion: 1, files: manifestFiles };
}

function validateManifest(manifest) {
  if (
    !manifest ||
    manifest.schemaVersion !== 1 ||
    !Array.isArray(manifest.files)
  ) {
    throw gateError(
      "RELEASE_BLOCKED",
      "Release manifest has an unsupported schema",
    );
  }
  const canonical = { schemaVersion: 1, files: [] };
  const seenPaths = new Set();
  const seenKeys = new Set();
  for (const file of manifest.files) {
    if (
      !file ||
      typeof file.path !== "string" ||
      !Array.isArray(file.tests) ||
      !file.path.startsWith("apps/web/tests/")
    ) {
      throw gateError(
        "RELEASE_BLOCKED",
        "Release manifest contains an invalid file record",
      );
    }
    if (
      path.isAbsolute(file.path) ||
      file.path.includes("\\") ||
      seenPaths.has(file.path)
    ) {
      throw gateError(
        "RELEASE_BLOCKED",
        `Release manifest contains a duplicate or non-normalized path: ${file.path}`,
      );
    }
    seenPaths.add(file.path);
    const tests = lexicalSort(file.tests);
    if (tests.some((name) => typeof name !== "string" || !name)) {
      throw gateError(
        "RELEASE_BLOCKED",
        `Release manifest contains an invalid test identity for ${file.path}`,
      );
    }
    for (const name of tests) {
      const key = manifestKey(file.path, name);
      if (seenKeys.has(key)) {
        throw gateError(
          "RELEASE_BLOCKED",
          `Release manifest contains duplicate identity ${file.path} :: ${name}`,
        );
      }
      seenKeys.add(key);
    }
    canonical.files.push({ path: file.path, tests });
  }
  canonical.files = canonical.files.sort((left, right) =>
    left.path < right.path ? -1 : left.path > right.path ? 1 : 0,
  );
  if (stableJson(canonical) !== stableJson(manifest)) {
    throw gateError(
      "RELEASE_BLOCKED",
      "Release manifest is not in canonical deterministic form",
    );
  }
  return canonical;
}

async function readManifest() {
  let source;
  try {
    source = await readFile(MANIFEST_PATH, "utf8");
  } catch {
    throw gateError("RELEASE_BLOCKED", "Committed release manifest is missing");
  }
  try {
    return { manifest: validateManifest(JSON.parse(source)), source };
  } catch (error) {
    if (error instanceof GateError) {
      throw error;
    }
    throw gateError(
      "RELEASE_BLOCKED",
      `Release manifest is invalid JSON: ${error.message}`,
    );
  }
}

function selectedManifest(manifest, selectedPaths) {
  const selected = new Set(selectedPaths);
  const files = manifest.files.filter((file) => selected.has(file.path));
  if (files.length !== selected.size) {
    const known = new Set(files.map((file) => file.path));
    throw gateError(
      "RELEASE_BLOCKED",
      `Controlled mode named a path that is absent from the release manifest: ${[...selected].filter((testPath) => !known.has(testPath)).join(", ")}`,
    );
  }
  return {
    schemaVersion: 1,
    files: files.sort((left, right) =>
      left.path < right.path ? -1 : left.path > right.path ? 1 : 0,
    ),
  };
}

function compareValues(label, expected, actual) {
  const expectedJson = stableJson(expected);
  const actualJson = stableJson(actual);
  if (expectedJson !== actualJson) {
    throw gateError("RELEASE_BLOCKED", `${label} mismatch`, {
      expected,
      actual,
    });
  }
}

async function runVitestList(testPaths, leg, evidenceDirectory, watchdog) {
  const listPath = path.join(evidenceDirectory, `${leg}-vitest-list.json`);
  const reporterPath = path.join(
    evidenceDirectory,
    `${leg}-list-resolved-config.json`,
  );
  const invocation = pnpmInvocation([
    "exec",
    "vitest",
    "list",
    "--run",
    "--config",
    RELEASE_CONFIG,
    "--json",
    listPath,
    ...testPaths.map((testPath) =>
      path.relative(WEB_ROOT, path.join(REPOSITORY_ROOT, testPath)),
    ),
  ]);
  const result = await runChild(invocation.command, invocation.args, {
    env: {
      ...process.env,
      WEB_RELEASE_REPOSITORY_ROOT: REPOSITORY_ROOT,
      WEB_RELEASE_SEQUENCE_MODE: "full",
      WEB_RELEASE_TIMEOUT_OVERRIDE_PROOF: "none",
      WEB_RELEASE_REPORTER_EVIDENCE: reporterPath,
      WEB_RELEASE_LEG: `${leg}-list`,
    },
  });
  watchdog?.assertNotExpired();
  if (result.code !== 0 || result.signal) {
    throw gateError("RELEASE_BLOCKED", `Vitest list failed for ${leg}`, {
      result,
    });
  }
  try {
    return JSON.parse(await readFile(listPath, "utf8"));
  } catch (error) {
    throw gateError(
      "RELEASE_BLOCKED",
      `Vitest list produced no readable JSON for ${leg}: ${error.message}`,
    );
  }
}

async function preflightInventory(manifest, options) {
  const fullFilesystem = await discoverTestFiles();
  const expected = options.selectedPaths
    ? selectedManifest(manifest, options.selectedPaths)
    : manifest;
  const testPaths = options.selectedPaths ?? fullFilesystem;
  await rejectForbiddenSourceModes(testPaths);

  if (!options.selectedPaths) {
    compareValues(
      "Filesystem/manifest file inventory",
      fullFilesystem,
      manifest.files.map((file) => file.path),
    );
  }
  const list = await runVitestList(
    testPaths,
    options.leg,
    options.evidenceDirectory,
    options.watchdog,
  );
  const listed = manifestFromList(list);
  compareValues("Vitest-list/manifest inventory", expected, listed);
  return {
    expected,
    testPaths,
    filesystemFiles: options.selectedPaths ? testPaths : fullFilesystem,
    listed,
  };
}

function expectedIdentityKeys(manifest) {
  return manifest.files.flatMap((file) =>
    file.tests.map((name) => manifestKey(file.path, name)),
  );
}

function checkExactlyOnce(label, records, expectedKeys, stateCheck) {
  const counts = new Map();
  const unexpected = [];
  for (const record of records) {
    const key = manifestKey(record.path, record.name);
    if (!expectedKeys.has(key)) {
      unexpected.push(key);
    }
    counts.set(key, (counts.get(key) ?? 0) + 1);
    stateCheck?.(record);
  }
  const missing = [...expectedKeys].filter((key) => !counts.has(key));
  const duplicates = [...counts.entries()]
    .filter(([, count]) => count !== 1)
    .map(([key]) => key);
  if (unexpected.length || missing.length || duplicates.length) {
    throw gateError("RELEASE_BLOCKED", `${label} identity mismatch`, {
      unexpected,
      missing,
      duplicates,
    });
  }
}

export function verifyExecutionEvidence(report, expected) {
  if (!report?.resolvedConfig?.valid) {
    throw gateError(
      "RELEASE_BLOCKED",
      "Resolved Vitest configuration mismatch or missing reporter evidence",
    );
  }
  const config = report.resolvedConfig.config;
  if (
    config.pool !== "forks" ||
    config.maxWorkers !== 1 ||
    config.fileParallelism !== false ||
    config.isolate !== true ||
    config.retry !== 0 ||
    config.allowOnly !== false ||
    config.shuffle !== false ||
    config.testTimeout !== 5000 ||
    config.hookTimeout !== 10000 ||
    config.teardownTimeout !== 10000 ||
    report.resolvedConfig.timeoutOverridesSuppliedByRunner !== false ||
    report.resolvedConfig.timeoutOverrideProof !== "none"
  ) {
    throw gateError(
      "RELEASE_BLOCKED",
      "Resolved Vitest configuration does not preserve the frozen release settings",
      { config },
    );
  }
  if (
    !report.final ||
    report.final.reason !== "passed" ||
    report.final.unhandledErrors?.length
  ) {
    throw gateError("RELEASE_BLOCKED", "Vitest execution ended abnormally", {
      final: report.final,
    });
  }

  const expectedKeys = new Set(expectedIdentityKeys(expected));
  checkExactlyOnce(
    "Collected",
    report.collected ?? [],
    expectedKeys,
    (record) => {
      if (record.mode !== "run") {
        throw gateError(
          "RELEASE_BLOCKED",
          `Collected test has forbidden mode ${record.mode}: ${record.path} :: ${record.name}`,
        );
      }
    },
  );
  checkExactlyOnce("Started", report.ready ?? [], expectedKeys);
  checkExactlyOnce("Executed", report.results ?? [], expectedKeys, (record) => {
    if (record.state !== "passed") {
      throw gateError(
        "RELEASE_BLOCKED",
        `Test did not pass: ${record.path} :: ${record.name}`,
        { record },
      );
    }
  });
  checkExactlyOnce("Final", report.finalTests ?? [], expectedKeys, (record) => {
    if (record.mode !== "run" || record.state !== "passed") {
      throw gateError(
        "RELEASE_BLOCKED",
        `Final test status is not passing/runnable: ${record.path} :: ${record.name}`,
        { record },
      );
    }
  });
  for (const testModule of report.modules ?? []) {
    if (testModule.state !== "passed" || testModule.errors?.length) {
      throw gateError(
        "RELEASE_BLOCKED",
        `Test module did not pass: ${testModule.path}`,
        { testModule },
      );
    }
  }
  return {
    files: expected.files.length,
    tests: expectedIdentityKeys(expected).length,
  };
}

async function executeLeg(preflight, options) {
  const reporterPath = path.join(
    options.evidenceDirectory,
    `${options.leg}-execution.json`,
  );
  const sequenceMode = options.sequenceMode ?? "full";
  const invocation = pnpmInvocation([
    "exec",
    "vitest",
    "run",
    "--config",
    RELEASE_CONFIG,
    ...preflight.testPaths.map((testPath) =>
      path.relative(WEB_ROOT, path.join(REPOSITORY_ROOT, testPath)),
    ),
  ]);
  const result = await runChild(invocation.command, invocation.args, {
    env: {
      ...process.env,
      WEB_RELEASE_REPOSITORY_ROOT: REPOSITORY_ROOT,
      WEB_RELEASE_SEQUENCE_MODE: sequenceMode,
      WEB_RELEASE_CONTROLLED_ORDER:
        sequenceMode === "controlled"
          ? JSON.stringify(preflight.testPaths)
          : undefined,
      WEB_RELEASE_TIMEOUT_OVERRIDE_PROOF: "none",
      WEB_RELEASE_REPORTER_EVIDENCE: reporterPath,
      WEB_RELEASE_LEG: options.leg,
    },
  });
  options.watchdog?.assertNotExpired();

  let report;
  try {
    report = JSON.parse(await readFile(reporterPath, "utf8"));
  } catch (error) {
    throw gateError(
      "RELEASE_BLOCKED",
      `Vitest execution reporter evidence is missing for ${options.leg}: ${error.message}`,
      { result },
    );
  }
  if (result.code !== 0 || result.signal) {
    throw gateError(
      "RELEASE_BLOCKED",
      `Vitest execution failed for ${options.leg}`,
      { result, report },
    );
  }
  return {
    result,
    report,
    summary: verifyExecutionEvidence(report, preflight.expected),
  };
}

export function interactionDecisionForChanges(input) {
  if (!input.baseValid) {
    return {
      run: true,
      reason:
        input.baseReason ?? "release base is absent, invalid, or unreachable",
      matchingPaths: [],
    };
  }
  if (input.parseError || input.binary || input.unmerged) {
    return {
      run: true,
      reason: "release diff cannot be classified safely",
      matchingPaths: [],
    };
  }
  for (const change of input.changes ?? []) {
    const status = change.status ?? "";
    const paths = change.paths ?? [];
    if (!/^[AM]$/.test(status) || paths.length !== 1) {
      return {
        run: true,
        reason: `unsafe diff status ${status || "unknown"}`,
        matchingPaths: paths,
      };
    }
    const [testPath] = paths;
    if (
      testPath.startsWith("apps/web/") ||
      [
        "package.json",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "Makefile",
      ].includes(testPath) ||
      testPath.startsWith(".github/workflows/")
    ) {
      return {
        run: true,
        reason: "diff touches a Web-test-relevant surface",
        matchingPaths: [testPath],
      };
    }
  }
  return {
    run: false,
    reason: "validated diff contains no Web-test-relevant path",
    matchingPaths: [],
  };
}

function parseNameStatus(output) {
  const tokens = output.split("\0").filter(Boolean);
  const changes = [];
  for (let index = 0; index < tokens.length;) {
    const status = tokens[index++];
    if (!status) {
      throw new Error("empty diff status");
    }
    const code = status.slice(0, 1);
    if (code === "R" || code === "C") {
      const oldPath = tokens[index++];
      const newPath = tokens[index++];
      if (!oldPath || !newPath) {
        throw new Error("rename/copy record is incomplete");
      }
      changes.push({ status: code, paths: [oldPath, newPath] });
    } else {
      const testPath = tokens[index++];
      if (!testPath) {
        throw new Error("diff record is incomplete");
      }
      changes.push({ status: code, paths: [testPath] });
    }
  }
  return changes;
}

function diffContainsBinary(output) {
  return output.split("\0").some((entry) => entry.startsWith("-\t-\t"));
}

function workingTreeHasUnmergedPath() {
  const result = git(["status", "--porcelain=v1", "-z"], {
    allowFailure: true,
  });
  if (result.code !== 0) {
    return true;
  }
  return result.stdout
    .split("\0")
    .some(
      (entry) =>
        /^[ MADRCU?!]{2}/.test(entry) && entry.slice(0, 2).includes("U"),
    );
}

function determineInteraction() {
  const releaseBase = process.env.WEB_RELEASE_BASE ?? process.env.RELEASE_BASE;
  if (!releaseBase) {
    return interactionDecisionForChanges({
      baseValid: false,
      baseReason: "release base was not supplied",
    });
  }
  const object = git(
    ["rev-parse", "--verify", "--quiet", `${releaseBase}^{commit}`],
    { allowFailure: true },
  );
  const reachable = git(["merge-base", "--is-ancestor", releaseBase, "HEAD"], {
    allowFailure: true,
  });
  if (object.code !== 0 || reachable.code !== 0) {
    return interactionDecisionForChanges({
      baseValid: false,
      baseReason: "release base is invalid or not reachable from HEAD",
    });
  }
  const names = git(
    [
      "diff",
      "--name-status",
      "-z",
      "--find-renames",
      "--find-copies",
      `${releaseBase}..HEAD`,
    ],
    { allowFailure: true },
  );
  const numstat = git(["diff", "--numstat", "-z", `${releaseBase}..HEAD`], {
    allowFailure: true,
  });
  if (names.code !== 0 || numstat.code !== 0) {
    return interactionDecisionForChanges({ baseValid: true, parseError: true });
  }
  try {
    return interactionDecisionForChanges({
      baseValid: true,
      changes: parseNameStatus(names.stdout),
      binary: diffContainsBinary(numstat.stdout),
      unmerged: workingTreeHasUnmergedPath(),
    });
  } catch {
    return interactionDecisionForChanges({ baseValid: true, parseError: true });
  }
}

async function runManifestUpdate(evidence, watchdog) {
  const filesystemFiles = await discoverTestFiles();
  await rejectForbiddenSourceModes(filesystemFiles);
  const list = await runVitestList(
    filesystemFiles,
    "manifest-update",
    evidence.directory,
    watchdog,
  );
  const manifest = manifestFromList(list);
  compareValues(
    "Filesystem/generated-manifest file inventory",
    filesystemFiles,
    manifest.files.map((file) => file.path),
  );
  await writeFile(MANIFEST_PATH, stableJson(manifest), "utf8");
  evidence.manifest = {
    updated: true,
    digest: sha256(stableJson(manifest)),
    files: manifest.files.length,
    tests: expectedIdentityKeys(manifest).length,
  };
  return { decision: "MANIFEST_UPDATED", exitCode: 0 };
}

async function runCanonical(
  evidence,
  watchdog,
  mode = "release",
  controlledPaths = [],
) {
  const { manifest, source } = await readManifest();
  evidence.manifest = {
    digest: sha256(source),
    files: manifest.files.length,
    tests: expectedIdentityKeys(manifest).length,
  };

  if (mode === "controlled") {
    if (controlledPaths.length === 0) {
      throw gateError(
        "NOT_EXECUTED",
        "Controlled mode requires one or more manifest test paths",
      );
    }
    const preflight = await preflightInventory(manifest, {
      selectedPaths: controlledPaths,
      leg: "controlled",
      evidenceDirectory: evidence.directory,
      watchdog,
    });
    evidence.legs.push({
      name: "controlled",
      orderedTestPaths: preflight.testPaths,
      inventory: preflight.expected,
      execution: await executeLeg(preflight, {
        leg: "controlled",
        evidenceDirectory: evidence.directory,
        watchdog,
        sequenceMode: "controlled",
      }),
    });
    return { decision: "CONTROLLED_PASS_DIAGNOSTIC_ONLY", exitCode: 0 };
  }

  const fullPreflight = await preflightInventory(manifest, {
    leg: "canonical-full",
    evidenceDirectory: evidence.directory,
    watchdog,
  });
  evidence.legs.push({
    name: "canonical-full",
    orderedTestPaths: fullPreflight.testPaths,
    inventory: fullPreflight.expected,
    execution: await executeLeg(fullPreflight, {
      leg: "canonical-full",
      evidenceDirectory: evidence.directory,
      watchdog,
      sequenceMode: "full",
    }),
  });

  const interaction = determineInteraction();
  evidence.interaction = interaction;
  if (interaction.run) {
    const interactionPreflight = await preflightInventory(manifest, {
      selectedPaths: INTERACTION_TEST_PATHS,
      leg: "interaction",
      evidenceDirectory: evidence.directory,
      watchdog,
    });
    evidence.legs.push({
      name: "interaction",
      orderedTestPaths: INTERACTION_TEST_PATHS,
      inventory: interactionPreflight.expected,
      execution: await executeLeg(interactionPreflight, {
        leg: "interaction",
        evidenceDirectory: evidence.directory,
        watchdog,
        sequenceMode: "interaction",
      }),
    });
  }

  if (!evidence.git.clean) {
    return { decision: "NOT_RELEASE_EVIDENCE", exitCode: 1 };
  }
  return { decision: "PASS", exitCode: 0 };
}

export function parseStressFailures(report) {
  const failures = [];
  for (const testResult of report?.testResults ?? []) {
    const testPath = normalizedStressTestPath(
      testResult.name ?? testResult.file ?? "",
    );
    for (const assertion of testResult.assertionResults ?? []) {
      if (assertion.status === "failed") {
        failures.push({
          path: testPath,
          name: assertion.fullName ?? assertion.title ?? "unknown test",
          messages: assertion.failureMessages ?? [],
          errorTypes: (assertion.failureDetails ?? []).map(
            (detail) => detail?.error?.name ?? detail?.name,
          ),
        });
      }
    }
  }
  return failures;
}

function normalizedStressTestPath(testPath) {
  return normalizedRepositoryPath(
    path.isAbsolute(testPath)
      ? testPath
      : testPath.startsWith("apps/web/")
        ? path.join(REPOSITORY_ROOT, testPath)
        : path.resolve(WEB_ROOT, testPath),
  );
}

export class StreamedVitestOutputCapture {
  constructor(options = {}) {
    this.maxBytes = options.maxBytes ?? STREAM_CAPTURE_MAX_BYTES;
    this.bytes = 0;
    this.truncated = false;
    this.lines = [];
    this.pending = new Map();
  }

  write(source, chunk) {
    if (this.truncated) {
      return;
    }
    const text = String(chunk);
    const byteLength = Buffer.byteLength(text);
    if (this.bytes + byteLength > this.maxBytes) {
      this.truncated = true;
      return;
    }
    this.bytes += byteLength;
    const value = `${this.pending.get(source) ?? ""}${text}`;
    const parts = value.split(/\r?\n/);
    this.pending.set(source, parts.pop() ?? "");
    for (const line of parts) {
      this.lines.push({ source, line: this.lines.length + 1, text: line });
    }
  }

  finish() {
    if (this.truncated) {
      return;
    }
    for (const [source, text] of this.pending) {
      if (text) {
        this.lines.push({ source, line: this.lines.length + 1, text });
      }
    }
    this.pending.clear();
  }

  evidence() {
    return {
      maxBytes: this.maxBytes,
      capturedBytes: this.bytes,
      truncated: this.truncated,
      lines: this.lines,
    };
  }
}

function stripAnsi(value) {
  return value.replace(/\u001B\[[0-?]*[ -/]*[@-~]/g, "");
}

function timeoutKindFromVitestBlock(lines) {
  const text = lines.map((line) => stripAnsi(line.text)).join("\n");
  const signatures = [
    [
      "test",
      /(?:^|\n)(?:Error:\s*)?Test timed out in \d+ms\.\nIf this is a long-running test,[\s\S]*?(?:\"|`)testTimeout(?:\"|`)/,
    ],
    [
      "hook",
      /(?:^|\n)(?:Error:\s*)?Hook timed out in \d+ms\.\nIf this is a long-running hook,[\s\S]*?(?:\"|`)hookTimeout(?:\"|`)/,
    ],
    [
      "teardown",
      /(?:^|\n)The teardown phase of \"[^\"]+\" hook timed out after \d+ms\./,
    ],
    [
      "hook",
      /(?:^|\n)The setup phase of \"[^\"]+\" hook timed out after \d+ms\./,
    ],
  ];
  return signatures.find(([, pattern]) => pattern.test(text))?.[0];
}

function streamedFailureBlocks(lines) {
  const blocks = [];
  let current;
  for (const line of lines) {
    const heading = stripAnsi(line.text).match(
      /^\s*FAIL\s+(.+\.(?:test|spec)\.(?:ts|tsx))\s+>\s+(.+?)\s*$/,
    );
    if (heading) {
      current = {
        path: normalizedStressTestPath(heading[1]),
        name: heading[2],
        lines: [line],
      };
      blocks.push(current);
    } else if (current) {
      current.lines.push(line);
    }
  }
  return blocks.map((block) => ({
    ...block,
    timeoutKind: timeoutKindFromVitestBlock(block.lines),
  }));
}

export function correlateStressFailures(failures, streamedOutput) {
  const blocks = streamedFailureBlocks(streamedOutput?.lines ?? []);
  const timeoutBlocks = blocks.filter((block) => block.timeoutKind);
  const keys = failures.map((failure) =>
    manifestKey(failure.path, failure.name),
  );
  const uniqueKeys = new Set(keys);
  const timeoutByKey = new Map();
  for (const block of timeoutBlocks) {
    const key = manifestKey(block.path, block.name);
    const matching = timeoutByKey.get(key) ?? [];
    matching.push(block);
    timeoutByKey.set(key, matching);
  }
  const mismatch =
    streamedOutput?.truncated === true ||
    keys.length === 0 ||
    uniqueKeys.size !== keys.length ||
    timeoutBlocks.length !== failures.length ||
    [...timeoutByKey.entries()].some(
      ([key, matching]) => !uniqueKeys.has(key) || matching.length !== 1,
    ) ||
    failures.some(
      (failure) =>
        (timeoutByKey.get(manifestKey(failure.path, failure.name)) ?? [])
          .length !== 1,
    );
  return {
    failures: failures.map((failure) => {
      const matching = timeoutByKey.get(
        manifestKey(failure.path, failure.name),
      );
      return matching?.length === 1
        ? { ...failure, timeoutKind: matching[0].timeoutKind }
        : failure;
    }),
    identityMismatch: mismatch,
    timeoutBlocks: timeoutBlocks.map((block) => ({
      path: block.path,
      name: block.name,
      timeoutKind: block.timeoutKind,
      lineStart: block.lines[0]?.line,
      lineEnd: block.lines.at(-1)?.line,
    })),
  };
}

function isTimeoutFailure(failure) {
  return ["test", "hook", "teardown"].includes(failure.timeoutKind);
}

async function latestCanonicalPass() {
  if (!existsSync(EVIDENCE_ROOT)) {
    return undefined;
  }
  const entries = await readdir(EVIDENCE_ROOT, { withFileTypes: true });
  const candidates = [];
  for (const entry of entries) {
    if (!entry.isFile() || !entry.name.endsWith(".json")) {
      continue;
    }
    try {
      const evidence = JSON.parse(
        await readFile(path.join(EVIDENCE_ROOT, entry.name), "utf8"),
      );
      if (
        evidence.mode === "release" &&
        evidence.decision === "PASS" &&
        evidence.git?.clean &&
        evidence.git?.sha === gitMetadata().sha &&
        evidence.legs?.some((leg) => leg.name === "canonical-full") &&
        (evidence.interaction?.run !== true ||
          evidence.legs?.some((leg) => leg.name === "interaction"))
      ) {
        candidates.push({
          path: path.join(EVIDENCE_ROOT, entry.name),
          evidence,
        });
      }
    } catch {
      // A partial or malformed historical evidence file cannot support adjudication.
    }
  }
  return candidates.sort((left, right) => (left.path < right.path ? 1 : -1))[0];
}

export function classifyStressResult(input) {
  if (input.exitCode === 0 && !input.signal) {
    return { decision: "STRESS_PASS", exitCode: 0 };
  }
  if (
    input.signal ||
    input.abnormal ||
    input.identityMismatch ||
    input.failures.length === 0
  ) {
    return {
      decision: "RELEASE_BLOCKED",
      exitCode: 1,
      reason:
        "stress abnormal exit, identity mismatch, or unclassified failure",
    };
  }
  if (!input.failures.every(isTimeoutFailure)) {
    return {
      decision: "RELEASE_BLOCKED",
      exitCode: 1,
      reason: "stress assertion failure",
    };
  }
  if (!input.canonicalPass || !input.interactionPass || !input.controlledPass) {
    return {
      decision: "RELEASE_BLOCKED",
      exitCode: 1,
      reason: "stress timeout lacks required canonical adjudication evidence",
    };
  }
  return { decision: "STRESS_RESOURCE_CONTENTION_NON_BLOCKING", exitCode: 0 };
}

async function runStress(evidence, watchdog) {
  const stressPath = path.join(evidence.directory, "stress-vitest.json");
  const invocation = pnpmInvocation([
    "exec",
    "vitest",
    "run",
    "--reporter=default",
    "--reporter=json",
    "--outputFile",
    stressPath,
  ]);
  const streamCapture = new StreamedVitestOutputCapture();
  const result = await runChild(invocation.command, invocation.args, {
    streamCapture,
  });
  watchdog?.assertNotExpired();
  let report;
  try {
    report = JSON.parse(await readFile(stressPath, "utf8"));
  } catch {
    report = undefined;
  }
  const parsedFailures = report ? parseStressFailures(report) : [];
  const streamedOutput = streamCapture.evidence();
  const correlation = correlateStressFailures(parsedFailures, streamedOutput);
  const failures = correlation.failures;
  evidence.stress = {
    result,
    reportPath: stressPath,
    failures,
    streamedOutput,
    timeoutCorrelation: {
      identityMismatch: correlation.identityMismatch,
      timeoutBlocks: correlation.timeoutBlocks,
    },
  };

  if (result.code === 0 && !result.signal) {
    return classifyStressResult({
      exitCode: result.code,
      signal: result.signal,
      identityMismatch: correlation.identityMismatch,
      failures,
    });
  }
  const prior = await latestCanonicalPass();
  const affectedPaths = lexicalSort([
    ...new Set(failures.map((failure) => failure.path)),
  ]);
  let controlledPass = false;
  if (
    prior &&
    !correlation.identityMismatch &&
    affectedPaths.length > 0 &&
    failures.every(isTimeoutFailure)
  ) {
    try {
      const controlled = await runCanonical(
        evidence,
        watchdog,
        "controlled",
        affectedPaths,
      );
      controlledPass =
        controlled.decision === "CONTROLLED_PASS_DIAGNOSTIC_ONLY";
    } catch (error) {
      evidence.stress.controlledFailure = serializeError(error);
    }
  }
  const classification = classifyStressResult({
    exitCode: result.code,
    signal: result.signal,
    abnormal: !report,
    identityMismatch: correlation.identityMismatch,
    failures,
    canonicalPass: Boolean(prior),
    interactionPass: Boolean(
      prior &&
      (prior.evidence.interaction?.run !== true ||
        prior.evidence.legs?.some((leg) => leg.name === "interaction")),
    ),
    controlledPass,
  });
  evidence.stress.priorCanonicalEvidence = prior?.path;
  evidence.stress.classification = classification;
  return classification;
}

function serializeError(error) {
  if (error instanceof GateError) {
    return {
      classification: error.classification,
      message: error.message,
      details: error.details,
    };
  }
  return {
    classification: "RELEASE_BLOCKED",
    message: error instanceof Error ? error.message : String(error),
  };
}

function parseArguments(arguments_) {
  const parsedArguments =
    arguments_[0] === "--" ? arguments_.slice(1) : arguments_;
  const timeoutOption =
    /^(?:--(?:test|hook|teardown)[Tt]imeout(?:=|$)|--testTimeout(?:=|$)|--hookTimeout(?:=|$)|--teardownTimeout(?:=|$))/;
  if (parsedArguments.some((argument) => timeoutOption.test(argument))) {
    throw gateError(
      "NOT_EXECUTED",
      "Timeout override arguments are forbidden by the release contract",
    );
  }
  if (
    parsedArguments[0] === "--manifest-update" &&
    parsedArguments.length === 1
  ) {
    return { mode: "manifest-update" };
  }
  if (parsedArguments[0] === "--stress" && parsedArguments.length === 1) {
    return { mode: "stress" };
  }
  if (parsedArguments[0] === "--controlled") {
    const paths = parsedArguments.slice(1).map((testPath) => {
      const absolute = path.resolve(REPOSITORY_ROOT, testPath);
      return normalizedRepositoryPath(absolute);
    });
    return { mode: "controlled", paths };
  }
  if (parsedArguments.length === 0) {
    return { mode: "release" };
  }
  throw gateError(
    "NOT_EXECUTED",
    `Unsupported release runner arguments: ${parsedArguments.join(" ")}`,
  );
}

async function main() {
  const parsed = parseArguments(process.argv.slice(2));
  const token = randomUUID();
  const evidencePath = path.join(
    EVIDENCE_ROOT,
    `${new Date().toISOString().replaceAll(":", "-")}-${parsed.mode}-${process.pid}-${token}.json`,
  );
  const evidence = {
    schemaVersion: 1,
    commandVersion: 1,
    mode: parsed.mode,
    startedAt: new Date().toISOString(),
    directory: path.dirname(evidencePath),
    git: gitMetadata(),
    runtime: {
      node: process.version,
      pnpm: undefined,
      vitest: undefined,
    },
    timeoutOverridesSuppliedByRunner: false,
    legs: [],
  };
  let lock;
  let watchdog;
  const lifecycle = new GateLifecycle(evidence);
  activeLifecycle = lifecycle;
  lifecycle.installSignalHandlers();
  let outcome = { decision: "RELEASE_BLOCKED", exitCode: 1 };
  try {
    const pnpmVersion = pnpmInvocation(["--version"]);
    const versionResult = spawnSync(pnpmVersion.command, pnpmVersion.args, {
      encoding: "utf8",
      cwd: WEB_ROOT,
    });
    evidence.runtime.pnpm =
      versionResult.status === 0 ? versionResult.stdout.trim() : "UNAVAILABLE";
    const vitestVersion = pnpmInvocation(["exec", "vitest", "--version"]);
    const vitestResult = spawnSync(vitestVersion.command, vitestVersion.args, {
      encoding: "utf8",
      cwd: WEB_ROOT,
    });
    evidence.runtime.vitest =
      vitestResult.status === 0 ? vitestResult.stdout.trim() : "UNAVAILABLE";

    lock = new GateLock({ metadata: evidence.git, token });
    evidence.lock = await lock.acquire();
    lifecycle.attachLock(lock);
    if (parsed.mode !== "manifest-update") {
      watchdog = new GateWatchdog(evidence, {
        terminate: () => lifecycle.requestTermination("WATCHDOG"),
      });
      watchdog.arm();
    }

    if (parsed.mode === "manifest-update") {
      outcome = await runManifestUpdate(evidence, watchdog);
    } else if (parsed.mode === "stress") {
      outcome = await runStress(evidence, watchdog);
    } else {
      outcome = await runCanonical(
        evidence,
        watchdog,
        parsed.mode,
        parsed.paths ?? [],
      );
    }
  } catch (error) {
    const serialized = serializeError(error);
    evidence.error = serialized;
    outcome = {
      decision:
        serialized.classification === "NOT_EXECUTED"
          ? "NOT_EXECUTED"
          : "RELEASE_BLOCKED",
      exitCode: 1,
    };
  } finally {
    watchdog?.clear();
    try {
      evidence.lockReleased = await lifecycle.finalize();
    } catch (error) {
      evidence.lockReleaseError = serializeError(error);
      outcome = { decision: "RELEASE_BLOCKED", exitCode: 1 };
    }
    if (lifecycle.terminating) {
      outcome = { decision: "RELEASE_BLOCKED", exitCode: 1 };
    }
    evidence.finishedAt = new Date().toISOString();
    evidence.decision = outcome.decision;
    await writeEvidence(evidencePath, evidence);
    lifecycle.removeSignalHandlers();
    if (activeLifecycle === lifecycle) {
      activeLifecycle = undefined;
    }
  }
  console.log(
    `WEB_RELEASE_GATE_RESULT ${outcome.decision} evidence=${evidencePath}`,
  );
  process.exitCode = outcome.exitCode;
}

export {
  GateError,
  INTERACTION_TEST_PATHS,
  lexicalSort,
  manifestFromList,
  selectedManifest,
  validateManifest,
};

const invokedPath = process.argv[1] ? path.resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  await main();
}
