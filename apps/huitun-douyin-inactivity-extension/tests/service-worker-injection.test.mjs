import { createRequire } from "node:module";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const require = createRequire(import.meta.url);
const worker = require("../service-worker.js");

test("manifest already authorizes the current dy.huitun.com top frame for scripting", () => {
  const manifest = JSON.parse(readFileSync(new URL("../manifest.json", import.meta.url), "utf8"));

  assert.ok(manifest.permissions.includes("scripting"));
  assert.ok(manifest.permissions.includes("activeTab"));
  assert.ok(manifest.host_permissions.includes("https://*.huitun.com/*"));
  assert.ok(manifest.content_scripts[0].matches.includes("https://*.huitun.com/*"));
});

test("injects MAIN bridge in the top frame, then repairs a missing static content bridge", async () => {
  const calls = [];
  let armAttempts = 0;
  const chromeApi = {
    runtime: {},
    scripting: {
      async executeScript(details) {
        calls.push({ kind: "execute", details });
      },
    },
    tabs: {
      async sendMessage(tabId, message, options) {
        calls.push({ kind: "message", tabId, message, options });
        armAttempts += 1;
        if (armAttempts === 1) throw new Error("Could not establish connection. Receiving end does not exist.");
      },
    },
  };

  await worker.injectBridgeAndArm(chromeApi, 41, "request-123");

  assert.deepEqual(calls, [
    {
      kind: "execute",
      details: {
        target: { tabId: 41, frameIds: [0] },
        world: "MAIN",
        files: ["runtime-contract.js", "page-main.js"],
      },
    },
    {
      kind: "message",
      tabId: 41,
      message: { type: "huitun-douyin-v1:arm", captureRequestId: "request-123" },
      options: { frameId: 0 },
    },
    {
      kind: "execute",
      details: {
        target: { tabId: 41, frameIds: [0] },
        world: "ISOLATED",
        files: ["content-bridge.js"],
      },
    },
    {
      kind: "message",
      tabId: 41,
      message: { type: "huitun-douyin-v1:arm", captureRequestId: "request-123" },
      options: { frameId: 0 },
    },
  ]);
});

test("surfaces the original MAIN-world execution failure safely", async () => {
  const chromeApi = {
    runtime: { lastError: { message: "Cannot access contents of url: https://dy.huitun.com/app/" } },
    scripting: {
      async executeScript() {
        throw new Error("Cannot access contents of url: https://dy.huitun.com/app/");
      },
    },
  };

  await assert.rejects(
    worker.injectMainWorldBridge(chromeApi, { tabId: 41, frameIds: [0] }),
    /MAIN_WORLD_INJECTION_FAILED: Cannot access contents of url: https:\/\/dy\.huitun\.com\/app\//,
  );
});

test("redacts sensitive-looking values before popup-visible diagnostics", () => {
  assert.equal(
    worker.safeChromeErrorDetail(
      new Error("capture_token=secret-value Authorization: bearer-value"),
      { runtime: {} },
    ),
    "capture_token=[redacted] Authorization=[redacted]",
  );
});
