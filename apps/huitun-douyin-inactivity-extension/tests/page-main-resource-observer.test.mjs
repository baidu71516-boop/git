import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const require = createRequire(import.meta.url);
const contract = require("../runtime-contract.js");
const pageMainSource = readFileSync(new URL("../page-main.js", import.meta.url), "utf8");

const ARM_EVENT = "huitun-douyin-v1:arm";
const REQUEST_EVENT = "huitun-douyin-v1:aweme-request";
const RESULT_EVENT = "huitun-douyin-v1:semantic-result";

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

function createPageRuntime(resourceEntries = []) {
  const listeners = new Map();
  const dispatched = [];
  const observers = [];
  let runtimeId = 0;

  class FakeCustomEvent {
    constructor(type, init = {}) {
      this.type = type;
      this.detail = init.detail;
    }
  }

  class FakeXMLHttpRequest {
    open() {}
    send() {}
  }

  class FakePerformanceObserver {
    constructor(callback) {
      this.callback = callback;
      this.options = null;
      observers.push(this);
    }

    observe(options) {
      this.options = options;
    }
  }

  const runtime = {
    HuitunDouyinRuntimeContract: contract,
    CustomEvent: FakeCustomEvent,
    XMLHttpRequest: FakeXMLHttpRequest,
    PerformanceObserver: FakePerformanceObserver,
    crypto: {
      randomUUID() {
        runtimeId += 1;
        return `runtime-${runtimeId}`;
      },
    },
    fetch() {},
    performance: {
      getEntriesByType(type) {
        return type === "resource" ? resourceEntries : [];
      },
    },
    addEventListener(type, listener) {
      const handlers = listeners.get(type) || [];
      handlers.push(listener);
      listeners.set(type, handlers);
    },
    dispatchEvent(event) {
      dispatched.push(event);
      for (const listener of listeners.get(event.type) || []) listener(event);
      return true;
    },
  };
  runtime.globalThis = runtime;
  vm.runInNewContext(pageMainSource, runtime, { filename: "page-main.js" });

  return {
    arm(captureRequestId) {
      runtime.dispatchEvent(new FakeCustomEvent(ARM_EVENT, { detail: { captureRequestId } }));
    },
    requestEvents() {
      return dispatched
        .filter((event) => event.type === REQUEST_EVENT)
        .map((event) => plain(event.detail));
    },
    resultEvents() {
      return dispatched
        .filter((event) => event.type === RESULT_EVENT)
        .map((event) => plain(event.detail));
    },
    notifyResourceEntries(entries) {
      for (const observer of observers) {
        observer.callback({ getEntries: () => entries });
      }
    },
    observerOptions() {
      return observers.map((observer) => plain(observer.options));
    },
    bridge() {
      return runtime.__huitunDouyinInactivityV1Bridge;
    },
  };
}

test("records an awemeList fetch already in Resource Timing when capture arms", () => {
  const runtime = createPageRuntime([
    {
      name: "https://dyapi.huitun.com/user/awemeList?uid=runtime-uid-42&sort=desc",
      initiatorType: "fetch",
    },
    {
      name: "https://dyapi.huitun.com/user/awemeList?uid=ignored-script",
      initiatorType: "script",
    },
  ]);

  runtime.arm("capture-123");

  assert.deepEqual(runtime.observerOptions(), [{ type: "resource", buffered: true }]);
  assert.deepEqual(runtime.requestEvents(), [
    {
      capture_request_id: "capture-123",
      runtime_request_id: "runtime-1",
      actual_uid: "runtime-uid-42",
    },
  ]);
});

test("observes later fetch entries once and keeps the URL-derived uid bound to the result", () => {
  const runtime = createPageRuntime();
  runtime.arm("capture-456");
  const entry = {
    name: "https://dyapi.huitun.com/user/awemeList?uid=runtime-uid-99",
    initiatorType: "fetch",
  };

  runtime.notifyResourceEntries([entry]);
  runtime.notifyResourceEntries([entry]);

  const [request] = runtime.requestEvents();
  assert.deepEqual(runtime.requestEvents(), [
    {
      capture_request_id: "capture-456",
      runtime_request_id: "runtime-1",
      actual_uid: "runtime-uid-99",
    },
  ]);
  assert.equal(
    runtime.bridge().reportSemanticAwemeList({
      runtimeRequestId: request.runtime_request_id,
      semanticUid: "runtime-uid-99",
      outcome: "SUCCESS",
      paginationTerminal: true,
      coverageEndAt: "2026-08-29T00:00:00Z",
      publications: [{ publishTime: "2026-08-28 10:00:00" }],
    }),
    true,
  );
  assert.equal(runtime.resultEvents()[0].actual_uid, "runtime-uid-99");
});
