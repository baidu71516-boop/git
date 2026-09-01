import { createRequire } from "node:module";
import assert from "node:assert/strict";
import test from "node:test";

const require = createRequire(import.meta.url);
const popup = require("../popup.js");

function fields(values = {}) {
  return {
    ingestUrl: { value: values.ingestUrl || "" },
    captureRequestId: { value: values.captureRequestId || "" },
    captureToken: { value: values.captureToken || "" },
  };
}

function storage(seed = {}) {
  const local = { ...(seed.local || {}) };
  const session = { ...(seed.session || {}) };
  return {
    local: {
      async get(key) { return { [key]: local[key] }; },
      async set(value) { Object.assign(local, value); },
      data: local,
    },
    session: {
      async get(key) { return { [key]: session[key] }; },
      async set(value) { Object.assign(session, value); },
      data: session,
    },
  };
}

test("restores ingest URL from local and capture secrets only from session", async () => {
  const chromeStorage = storage({
    local: { [popup.POPUP_INGEST_URL_KEY]: "http://localhost:8080/api/ingest" },
    session: {
      [popup.POPUP_CAPTURE_STATE_KEY]: {
        captureRequestId: "request-123",
        captureToken: "one-time-token",
      },
    },
  });
  const restored = fields();

  await popup.restorePopupInputs(chromeStorage, restored);

  assert.deepEqual(restored, fields({
    ingestUrl: "http://localhost:8080/api/ingest",
    captureRequestId: "request-123",
    captureToken: "one-time-token",
  }));
  assert.equal(chromeStorage.local.data[popup.POPUP_CAPTURE_STATE_KEY], undefined);
});

test("failed arm preserves visible and session capture inputs", async () => {
  const chromeStorage = storage();
  const popupFields = fields({
    ingestUrl: "http://localhost:8080/api/ingest",
    captureRequestId: "request-123",
    captureToken: "one-time-token",
  });
  const chromeApi = {
    storage: chromeStorage,
    permissions: { request: async () => true },
    tabs: { query: async () => [{ id: 17 }] },
    runtime: { sendMessage: async () => ({ ok: false, error: "ARM_FAILED" }) },
  };

  await assert.rejects(
    popup.armCapture(chromeApi, popupFields, async () => {}),
    /ARM_FAILED/,
  );

  assert.equal(popupFields.captureToken.value, "one-time-token");
  assert.deepEqual(chromeStorage.session.data[popup.POPUP_CAPTURE_STATE_KEY], {
    captureRequestId: "request-123",
    captureToken: "one-time-token",
  });
});

test("successful arm clears only the visible token after worker handoff", async () => {
  const chromeStorage = storage();
  const popupFields = fields({
    ingestUrl: "http://localhost:8080/api/ingest",
    captureRequestId: "request-123",
    captureToken: "one-time-token",
  });
  const sent = [];
  const chromeApi = {
    storage: chromeStorage,
    permissions: { request: async () => true },
    tabs: { query: async () => [{ id: 17 }] },
    runtime: {
      sendMessage: async (message) => {
        sent.push(message);
        return { ok: true };
      },
    },
  };

  await popup.armCapture(chromeApi, popupFields, async () => {});

  assert.equal(popupFields.captureToken.value, "");
  assert.equal(sent[0].config.captureToken, "one-time-token");
  assert.deepEqual(chromeStorage.session.data[popup.POPUP_CAPTURE_STATE_KEY], {
    captureRequestId: "request-123",
    captureToken: "one-time-token",
  });
});
