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

function createPageRuntime() {
  const listeners = new Map();
  const dispatched = [];
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

  const runtime = {
    HuitunDouyinRuntimeContract: contract,
    CustomEvent: FakeCustomEvent,
    XMLHttpRequest: FakeXMLHttpRequest,
    URL,
    crypto: {
      randomUUID() {
        runtimeId += 1;
        return `runtime-${runtimeId}`;
      },
    },
    fetch() {},
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
  const context = vm.createContext(runtime);
  vm.runInContext(pageMainSource, context, { filename: "page-main.js" });

  return {
    arm(captureRequestId) {
      runtime.dispatchEvent(new FakeCustomEvent(ARM_EVENT, { detail: { captureRequestId } }));
    },
    observeRequest(url) {
      runtime.fetch(url);
    },
    async fulfill(value, callbackReturn) {
      context.semanticValue = value;
      context.callbackReturn = callbackReturn;
      return vm.runInContext(
        "Promise.resolve(semanticValue).then(() => callbackReturn)",
        context,
      );
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
  };
}

function decodedResponse(uid, changes = {}) {
  return {
    code: 0,
    dataList: [],
    hasMore: false,
    data: [{ uid, publishTime: "2026-08-25 08:30:00" }],
    ...changes,
  };
}

function currentShanghaiDate() {
  const wallClock = new Date(Date.now() + 8 * 60 * 60 * 1000);
  return [
    wallClock.getUTCFullYear(),
    String(wallClock.getUTCMonth() + 1).padStart(2, "0"),
    String(wallClock.getUTCDate()).padStart(2, "0"),
  ].join("-");
}

function emptyRequestUrl(uid, changes = {}) {
  const params = new URLSearchParams({
    uid,
    from: "1",
    queryTimeStart: "2025-08-29",
    queryTimeEnd: currentShanghaiDate(),
    ...changes,
  });
  return `https://dyapi.huitun.com/user/awemeList?${params}`;
}

test("observes only an armed, request-bound decoded fulfillment and preserves Promise output", async () => {
  const runtime = createPageRuntime();
  const uid = "42000000001";
  const url = `https://dyapi.huitun.com/user/awemeList?uid=${uid}`;

  assert.equal(await runtime.fulfill(decodedResponse(Number(uid)), "before-arm"), "before-arm");
  assert.deepEqual(runtime.resultEvents(), []);

  runtime.arm("capture-123");
  runtime.observeRequest(url);
  assert.equal(await runtime.fulfill(decodedResponse(Number(uid)), "business-return"), "business-return");

  assert.deepEqual(runtime.requestEvents(), [
    {
      capture_request_id: "capture-123",
      runtime_request_id: "runtime-1",
      actual_uid: uid,
    },
  ]);
  const [result] = runtime.resultEvents();
  assert.match(result.coverage_end_at, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
  assert.deepEqual(
    { ...result, coverage_end_at: "<fulfillment-time>" },
    {
      capture_request_id: "capture-123",
      runtime_request_id: "runtime-1",
      outcome: "SUCCESS",
      actual_uid: uid,
      semantic_uid: uid,
      publications: [{ published_at: "2026-08-25T00:30:00.000Z" }],
      pagination_terminal: true,
      latest_page_proven: false,
      coverage_start_at: null,
      coverage_end_at: "<fulfillment-time>",
    },
  );
});

test("uid mismatch dispatches only the existing UNKNOWN-safe rejection", async () => {
  const runtime = createPageRuntime();
  runtime.arm("capture-456");
  runtime.observeRequest("https://dyapi.huitun.com/user/awemeList?uid=runtime-uid-42");

  await runtime.fulfill(decodedResponse("other-uid"), "business-return");

  assert.deepEqual(runtime.resultEvents(), [
    {
      capture_request_id: "capture-456",
      runtime_request_id: "runtime-1",
      outcome: "INVALID_PAYLOAD",
      actual_uid: "runtime-uid-42",
      semantic_uid: null,
      publications: [],
      pagination_terminal: null,
      latest_page_proven: false,
      coverage_start_at: null,
      coverage_end_at: null,
      rejection_reason: "UID_MISMATCH",
    },
  ]);
});

test("a decoded array without terminal pagination cannot claim SUCCESS", async () => {
  const runtime = createPageRuntime();
  runtime.arm("capture-789");
  runtime.observeRequest("https://dyapi.huitun.com/user/awemeList?uid=runtime-uid-42");

  await runtime.fulfill(
    decodedResponse("runtime-uid-42", { hasMore: undefined, dataList: [] }),
    "business-return",
  );

  assert.deepEqual(runtime.resultEvents(), [
    {
      capture_request_id: "capture-789",
      runtime_request_id: "runtime-1",
      outcome: "PARTIAL",
      actual_uid: "runtime-uid-42",
      semantic_uid: "runtime-uid-42",
      publications: [],
      pagination_terminal: false,
      latest_page_proven: false,
      coverage_start_at: null,
      coverage_end_at: null,
    },
  ]);
});

test("a first newest-first page proves exact latest even when total exceeds page length", async () => {
  const runtime = createPageRuntime();
  const uid = "runtime-uid-42";
  runtime.arm("capture-first-page");
  runtime.observeRequest(
    `https://dyapi.huitun.com/user/awemeList?uid=${uid}&from=1&sortMod=desc&sortField=`,
  );

  await runtime.fulfill(
    decodedResponse(uid, {
      hasMore: undefined,
      total: 52,
      data: [
        { uid, publishTime: "2026-08-25 08:30:00" },
        { uid, publishTime: "2026-08-24 08:30:00" },
      ],
    }),
    "business-return",
  );

  const [result] = runtime.resultEvents();
  assert.equal(result.outcome, "SUCCESS");
  assert.equal(result.pagination_terminal, false);
  assert.equal(result.latest_page_proven, true);
  assert.equal(result.publications.length, 2);
});

test("a non-first page remains PARTIAL even with descending publication times", async () => {
  const runtime = createPageRuntime();
  const uid = "runtime-uid-42";
  runtime.arm("capture-non-first-page");
  runtime.observeRequest(
    `https://dyapi.huitun.com/user/awemeList?uid=${uid}&from=2&sortMod=desc&sortField=`,
  );

  await runtime.fulfill(
    decodedResponse(uid, {
      hasMore: undefined,
      data: [
        { uid, publishTime: "2026-08-25 08:30:00" },
        { uid, publishTime: "2026-08-24 08:30:00" },
      ],
    }),
    "business-return",
  );

  const [result] = runtime.resultEvents();
  assert.equal(result.outcome, "PARTIAL");
  assert.equal(result.latest_page_proven, false);
  assert.deepEqual(result.publications, []);
});

test("a first page with unknown publication ordering remains PARTIAL", async () => {
  const runtime = createPageRuntime();
  const uid = "runtime-uid-42";
  runtime.arm("capture-ordering-unknown");
  runtime.observeRequest(
    `https://dyapi.huitun.com/user/awemeList?uid=${uid}&from=1&sortMod=desc&sortField=`,
  );

  await runtime.fulfill(
    decodedResponse(uid, {
      hasMore: undefined,
      data: [
        { uid, publishTime: "2026-08-24 08:30:00" },
        { uid, publishTime: "2026-08-25 08:30:00" },
      ],
    }),
    "business-return",
  );

  const [result] = runtime.resultEvents();
  assert.equal(result.outcome, "PARTIAL");
  assert.equal(result.latest_page_proven, false);
  assert.deepEqual(result.publications, []);
});

test("a decoded total-zero first page with explicit current coverage forms terminal EMPTY", async () => {
  const runtime = createPageRuntime();
  const uid = "runtime-empty-uid";
  runtime.arm("capture-empty");
  runtime.observeRequest(emptyRequestUrl(uid));

  await runtime.fulfill(
    { code: 0, total: 0, data: [], dataList: { videoCount: 0 } },
    "business-return",
  );

  const [result] = runtime.resultEvents();
  assert.equal(result.outcome, "EMPTY");
  assert.equal(result.actual_uid, uid);
  assert.equal(result.semantic_uid, uid);
  assert.equal(result.pagination_terminal, true);
  assert.equal(result.latest_page_proven, false);
  assert.deepEqual(result.publications, []);
  assert.equal(result.coverage_start_at, "2025-08-28T16:00:00.000Z");
  assert.match(result.coverage_end_at, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
});

test("an empty array without decoded total zero remains PARTIAL", async () => {
  const runtime = createPageRuntime();
  const uid = "runtime-empty-no-total";
  runtime.arm("capture-empty-no-total");
  runtime.observeRequest(emptyRequestUrl(uid));

  await runtime.fulfill({ code: 0, data: [], dataList: { videoCount: 0 } }, "business-return");

  const [result] = runtime.resultEvents();
  assert.equal(result.outcome, "PARTIAL");
  assert.equal(result.pagination_terminal, false);
  assert.deepEqual(result.publications, []);
});

test("an empty array with nonzero decoded total remains PARTIAL", async () => {
  const runtime = createPageRuntime();
  const uid = "runtime-empty-nonzero-total";
  runtime.arm("capture-empty-nonzero-total");
  runtime.observeRequest(emptyRequestUrl(uid));

  await runtime.fulfill(
    { code: 0, total: 1, data: [], dataList: { videoCount: 0 } },
    "business-return",
  );

  const [result] = runtime.resultEvents();
  assert.equal(result.outcome, "PARTIAL");
  assert.equal(result.pagination_terminal, false);
  assert.deepEqual(result.publications, []);
});

test("a total-zero empty array without explicit query coverage remains PARTIAL", async () => {
  const runtime = createPageRuntime();
  const uid = "runtime-empty-no-coverage";
  runtime.arm("capture-empty-no-coverage");
  runtime.observeRequest(`https://dyapi.huitun.com/user/awemeList?uid=${uid}&from=1`);

  await runtime.fulfill(
    { code: 0, total: 0, data: [], dataList: { videoCount: 0 } },
    "business-return",
  );

  const [result] = runtime.resultEvents();
  assert.equal(result.outcome, "PARTIAL");
  assert.equal(result.pagination_terminal, false);
  assert.deepEqual(result.publications, []);
});

test("an encrypted-looking fulfillment is ignored rather than decoded", async () => {
  const runtime = createPageRuntime();
  runtime.arm("capture-encrypted");
  runtime.observeRequest("https://dyapi.huitun.com/user/awemeList?uid=runtime-uid-42");

  assert.equal(
    await runtime.fulfill(
      { code: 0, total: 0, data: "encrypted-envelope", dataList: [] },
      "business-return",
    ),
    "business-return",
  );
  assert.deepEqual(runtime.resultEvents(), []);
});

test("an observer-side access failure never changes the page callback result", async () => {
  const runtime = createPageRuntime();
  runtime.arm("capture-safe");
  runtime.observeRequest("https://dyapi.huitun.com/user/awemeList?uid=runtime-uid-42");
  const throwingValue = {};
  Object.defineProperty(throwingValue, "code", {
    get() {
      throw new Error("observer must not affect the page");
    },
  });

  assert.equal(await runtime.fulfill(throwingValue, "business-return"), "business-return");
  assert.deepEqual(runtime.resultEvents(), []);
});
