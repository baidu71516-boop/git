"use strict";

const POPUP_INGEST_URL_KEY = "huitunDouyinInactivityV1.popup.ingestUrl";
const POPUP_CAPTURE_STATE_KEY = "huitunDouyinInactivityV1.popup.capture";

function ingestOriginPattern(value) {
  const url = new URL(value);
  return `${url.origin}/*`;
}

async function restorePopupInputs(storage, fields) {
  const [local, session] = await Promise.all([
    storage.local.get(POPUP_INGEST_URL_KEY),
    storage.session.get(POPUP_CAPTURE_STATE_KEY),
  ]);
  const ingestUrl = local[POPUP_INGEST_URL_KEY];
  const capture = session[POPUP_CAPTURE_STATE_KEY];
  if (typeof ingestUrl === "string") fields.ingestUrl.value = ingestUrl;
  if (!capture || typeof capture !== "object") return;
  if (typeof capture.captureRequestId === "string") {
    fields.captureRequestId.value = capture.captureRequestId;
  }
  if (typeof capture.captureToken === "string") {
    fields.captureToken.value = capture.captureToken;
  }
}

async function persistIngestUrl(storage, ingestUrl) {
  await storage.local.set({ [POPUP_INGEST_URL_KEY]: ingestUrl });
}

async function persistCaptureState(storage, fields) {
  await storage.session.set({
    [POPUP_CAPTURE_STATE_KEY]: {
      captureRequestId: fields.captureRequestId.value.trim(),
      captureToken: fields.captureToken.value,
    },
  });
}

async function armCapture(chromeApi, fields, refreshStatus) {
  const ingestUrl = fields.ingestUrl.value.trim();
  const captureRequestId = fields.captureRequestId.value.trim();
  const captureToken = fields.captureToken.value;

  // Persist before any permission request or async handoff. Popup teardown then
  // cannot lose a configuration that has not yet reached the service worker.
  await persistIngestUrl(chromeApi.storage, ingestUrl);
  await persistCaptureState(chromeApi.storage, fields);

  const origin = ingestOriginPattern(ingestUrl);
  const granted = await chromeApi.permissions.request({ origins: [origin] });
  if (!granted) throw new Error("INGEST_ORIGIN_PERMISSION_REQUIRED");
  const [tab] = await chromeApi.tabs.query({ active: true, currentWindow: true });
  if (!tab || tab.id === undefined) throw new Error("HUITUN_TAB_REQUIRED");
  const result = await chromeApi.runtime.sendMessage({
    type: "huitun-douyin-v1:arm-capture",
    config: { tabId: tab.id, ingestUrl, captureRequestId, captureToken },
  });
  if (!result || !result.ok) throw new Error((result && result.error) || "ARM_FAILED");

  // The worker has returned success only after it holds this session-scoped
  // configuration. Clear the visible password field, while preserving the
  // session value so a transient popup close cannot strand the active capture.
  fields.captureToken.value = "";
  await refreshStatus();
}

const popupApi = {
  POPUP_CAPTURE_STATE_KEY,
  POPUP_INGEST_URL_KEY,
  armCapture,
  ingestOriginPattern,
  persistCaptureState,
  persistIngestUrl,
  restorePopupInputs,
};

if (typeof module !== "undefined" && module.exports) module.exports = popupApi;

if (typeof document !== "undefined" && typeof chrome !== "undefined") {
  const fields = {
    ingestUrl: document.getElementById("ingest-url"),
    captureRequestId: document.getElementById("capture-request-id"),
    captureToken: document.getElementById("capture-token"),
  };
  const statusElement = document.getElementById("status");

  function show(message) {
    statusElement.textContent = message;
  }

  async function refreshStatus() {
    const result = await chrome.runtime.sendMessage({ type: "huitun-douyin-v1:get-status" });
    if (result && result.status) show(`${result.status.state}\n${result.status.detail || ""}`.trim());
  }

  fields.ingestUrl.addEventListener("input", () => {
    void persistIngestUrl(chrome.storage, fields.ingestUrl.value.trim());
  });
  fields.captureRequestId.addEventListener("input", () => {
    void persistCaptureState(chrome.storage, fields);
  });
  fields.captureToken.addEventListener("input", () => {
    void persistCaptureState(chrome.storage, fields);
  });

  document.getElementById("arm").addEventListener("click", async () => {
    try {
      await armCapture(chrome, fields, refreshStatus);
    } catch (error) {
      show(`未启动：${error && error.message ? error.message : "配置无效"}`);
    }
  });

  document.getElementById("retry").addEventListener("click", async () => {
    const result = await chrome.runtime.sendMessage({ type: "huitun-douyin-v1:retry" });
    if (!result || !result.ok) {
      show(`无法重试：${(result && result.error) || "本会话没有未送达结果"}`);
      return;
    }
    await refreshStatus();
  });

  void restorePopupInputs(chrome.storage, fields);
  void refreshStatus();
}
