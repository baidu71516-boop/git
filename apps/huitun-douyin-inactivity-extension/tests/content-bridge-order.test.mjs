import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const contentBridgeSource = readFileSync(new URL("../content-bridge.js", import.meta.url), "utf8");
const REQUEST_EVENT = "huitun-douyin-v1:aweme-request";
const RESULT_EVENT = "huitun-douyin-v1:semantic-result";

function deferred() {
  let resolve;
  const promise = new Promise((accept) => {
    resolve = accept;
  });
  return { promise, resolve };
}

function createBridgeRuntime() {
  const listeners = new Map();
  const sent = [];
  const firstRequest = deferred();
  let requestCount = 0;
  const runtime = {
    window: {
      addEventListener(type, listener) {
        const handlers = listeners.get(type) || [];
        handlers.push(listener);
        listeners.set(type, handlers);
      },
      dispatchEvent(event) {
        for (const listener of listeners.get(event.type) || []) listener(event);
      },
    },
    chrome: {
      runtime: {
        onMessage: { addListener() {} },
        sendMessage(message) {
          sent.push(message);
          if (message.type === "huitun-douyin-v1:request" && requestCount++ === 0) {
            return firstRequest.promise;
          }
          return Promise.resolve({ ok: true });
        },
      },
    },
  };
  vm.runInNewContext(contentBridgeSource, runtime, { filename: "content-bridge.js" });
  return {
    emit(type, detail) {
      runtime.window.dispatchEvent({ type, detail });
    },
    sent,
    firstRequest,
  };
}

async function drainMicrotasks() {
  await Promise.resolve();
  await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
}

test("sends a semantic result only after its real request binding resolves", async () => {
  const runtime = createBridgeRuntime();
  runtime.emit(REQUEST_EVENT, { runtime_request_id: "runtime-1", actual_uid: "runtime-uid-42" });
  runtime.emit(RESULT_EVENT, { runtime_request_id: "runtime-1", outcome: "SUCCESS" });

  await drainMicrotasks();
  assert.deepEqual(runtime.sent.map((message) => message.type), ["huitun-douyin-v1:request"]);

  runtime.firstRequest.resolve({ ok: true });
  await drainMicrotasks();
  assert.deepEqual(runtime.sent.map((message) => message.type), [
    "huitun-douyin-v1:request",
    "huitun-douyin-v1:semantic-result",
  ]);
});
