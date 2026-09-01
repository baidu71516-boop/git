/* global chrome, importScripts */
"use strict";

if (typeof importScripts === "function") importScripts("runtime-contract.js");

const contract = globalThis.HuitunDouyinRuntimeContract;
const SESSION_KEY = "huitunDouyinInactivityV1.capture";
const STATUS_KEY = "huitunDouyinInactivityV1.status";
const INGEST_PATH = "/api/v1/admin/content-activity/douyin/runtime-captures/ingest";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const MAX_OBSERVED_REQUESTS = 32;
const MAX_PUBLIC_CHROME_ERROR_LENGTH = 240;

class ExtensionArmError extends Error {
  constructor(code, detail) {
    super(`${code}: ${detail}`);
    this.code = code;
  }
}

function safeChromeErrorDetail(error, chromeApi) {
  const lastError = chromeApi && chromeApi.runtime && chromeApi.runtime.lastError;
  const candidates = [
    lastError && typeof lastError.message === "string" ? lastError.message : null,
    typeof error === "string" ? error : error && typeof error.message === "string" ? error.message : null,
  ];
  const detail = [...new Set(candidates.filter(Boolean))].join(" | ") || "unknown Chrome error";
  return detail
    .replace(/[\r\n\t]+/g, " ")
    .replace(/(capture[_ -]?token|authorization|cookie)\s*[:=]\s*[^,\s]+/gi, "$1=[redacted]")
    .slice(0, MAX_PUBLIC_CHROME_ERROR_LENGTH);
}

async function injectMainWorldBridge(chromeApi, target) {
  try {
    await chromeApi.scripting.executeScript({
      target,
      world: "MAIN",
      files: ["runtime-contract.js", "page-main.js"],
    });
  } catch (error) {
    throw new ExtensionArmError(
      "MAIN_WORLD_INJECTION_FAILED",
      safeChromeErrorDetail(error, chromeApi),
    );
  }
}

async function ensureContentBridgeAndArm(chromeApi, target, captureRequestId) {
  const message = {
    type: "huitun-douyin-v1:arm",
    captureRequestId,
  };
  const options = { frameId: target.frameIds[0] };
  try {
    await chromeApi.tabs.sendMessage(target.tabId, message, options);
    return;
  } catch {
    // Static content scripts only exist for documents opened after the
    // extension loaded. Inject the isolated bridge on this already-open SPA
    // tab, then retry the arm message once.
  }
  try {
    await chromeApi.scripting.executeScript({
      target,
      world: "ISOLATED",
      files: ["content-bridge.js"],
    });
  } catch (error) {
    throw new ExtensionArmError(
      "CONTENT_BRIDGE_INJECTION_FAILED",
      safeChromeErrorDetail(error, chromeApi),
    );
  }
  try {
    await chromeApi.tabs.sendMessage(target.tabId, message, options);
  } catch (error) {
    throw new ExtensionArmError(
      "CONTENT_BRIDGE_MESSAGE_FAILED",
      safeChromeErrorDetail(error, chromeApi),
    );
  }
}

async function injectBridgeAndArm(chromeApi, tabId, captureRequestId) {
  // The current Huitun SPA is the top-level dy.huitun.com document. Do not
  // inject into arbitrary child frames, where the page contract is unknown.
  const target = { tabId, frameIds: [0] };
  await injectMainWorldBridge(chromeApi, target);
  await ensureContentBridgeAndArm(chromeApi, target, captureRequestId);
}

function isHuitunPage(urlText) {
  try {
    const url = new URL(urlText);
    return url.protocol === "https:" && (url.hostname === "huitun.com" || url.hostname.endsWith(".huitun.com"));
  } catch {
    return false;
  }
}

function parseIngestUrl(value) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error("INGEST_URL_INVALID");
  }
  const localHttp =
    url.protocol === "http:" && (url.hostname === "localhost" || url.hostname === "127.0.0.1");
  if ((!localHttp && url.protocol !== "https:") || url.pathname !== INGEST_PATH) {
    throw new Error("INGEST_URL_INVALID");
  }
  if (url.username || url.password || url.search || url.hash) throw new Error("INGEST_URL_INVALID");
  return url;
}

function publicStatus(state, detail) {
  return {
    state,
    detail: detail || null,
    updatedAt: new Date().toISOString(),
  };
}

async function setStatus(state, detail) {
  await chrome.storage.session.set({ [STATUS_KEY]: publicStatus(state, detail) });
}

async function activeCapture() {
  const stored = await chrome.storage.session.get(SESSION_KEY);
  return stored[SESSION_KEY] || null;
}

async function clearCapture() {
  await chrome.storage.session.remove(SESSION_KEY);
}

async function armCapture(config) {
  const ingestUrl = parseIngestUrl(config && config.ingestUrl);
  if (!config || !UUID.test(config.captureRequestId || "") || typeof config.captureToken !== "string") {
    throw new Error("CAPTURE_CONFIG_INVALID");
  }
  if (config.captureToken.length < 32 || config.captureToken.length > 256) {
    throw new Error("CAPTURE_CONFIG_INVALID");
  }
  const tab = await chrome.tabs.get(config.tabId);
  if (!tab.id || !isHuitunPage(tab.url || "")) throw new Error("HUITUN_TAB_REQUIRED");
  const originPattern = `${ingestUrl.origin}/*`;
  const permission = await chrome.permissions.contains({ origins: [originPattern] });
  if (!permission) throw new Error("INGEST_ORIGIN_PERMISSION_REQUIRED");

  // session storage is intentionally browser-session only. No raw Huitun
  // response, browser cookie, Authorization value, or operator cookie enters it.
  await chrome.storage.session.set({
    [SESSION_KEY]: {
      tabId: tab.id,
      captureRequestId: config.captureRequestId,
      captureToken: config.captureToken,
      ingestUrl: ingestUrl.toString(),
      pendingPayload: null,
      observedRequests: {},
    },
  });
  await setStatus("ARMING", null);
  try {
    await injectBridgeAndArm(chrome, tab.id, config.captureRequestId);
  } catch (error) {
    const armError = error instanceof ExtensionArmError
      ? error
      : new ExtensionArmError("ARM_FAILED", safeChromeErrorDetail(error, chrome));
    await setStatus("ARM_FAILED", armError.message);
    throw armError;
  }
  await setStatus("ARMED", "等待真实 awemeList 请求及页面已解密语义结果。");
}

async function deliverPayload(capture, payload) {
  await setStatus("DELIVERING", null);
  let response;
  try {
    response = await fetch(capture.ingestUrl, {
      method: "POST",
      credentials: "omit",
      cache: "no-store",
      referrerPolicy: "no-referrer",
      headers: {
        "Content-Type": "application/json",
        "X-Content-Activity-Capture-Token": capture.captureToken,
      },
      body: JSON.stringify(payload),
    });
  } catch {
    await chrome.storage.session.set({
      [SESSION_KEY]: { ...capture, pendingPayload: payload },
    });
    await setStatus("DELIVERY_FAILED_RETRYABLE", "本地保留了无敏感语义结果，尚未送达后端。");
    return;
  }

  if (response.ok) {
    await clearCapture();
    await setStatus("DELIVERED", "后端已记录 ACCEPTED 或 UNKNOWN 终态。");
    return;
  }
  if ([401, 409, 410].includes(response.status)) {
    await clearCapture();
    await setStatus("TERMINAL_REJECTED", "采集能力已失效或已结算；未重试。");
    return;
  }
  await chrome.storage.session.set({
    [SESSION_KEY]: { ...capture, pendingPayload: payload },
  });
  await setStatus("DELIVERY_FAILED_RETRYABLE", "后端暂未接受结果；可在本次浏览器会话内重试。");
}

async function handleSemanticResult(payload, sender) {
  const capture = await activeCapture();
  if (!capture || !sender.tab || sender.tab.id !== capture.tabId) return;
  const normalized = contract.sanitizeOutboundPayload(payload);
  if (!normalized || normalized.capture_request_id !== capture.captureRequestId) {
    await rejectSemanticResult(
      capture,
      payload,
      "请求标识不匹配；已按 UNKNOWN 终态拒绝。",
      "REQUEST_BINDING_MISMATCH",
    );
    return;
  }
  const observedUid = (capture.observedRequests || {})[normalized.runtime_request_id];
  if (
    !contract.matchesObservedAwemeRequest(normalized, {
      capture_request_id: capture.captureRequestId,
      runtime_request_id: normalized.runtime_request_id,
      actual_uid: observedUid,
    })
  ) {
    await rejectSemanticResult(
      capture,
      normalized,
      "未绑定同一真实 awemeList 请求；已按 UNKNOWN 拒绝。",
      "REQUEST_BINDING_MISMATCH",
    );
    return;
  }
  await deliverPayload(capture, normalized);
}

async function rejectSemanticResult(capture, candidate, detail, rejectionReason) {
  const requestedRuntimeId = candidate && typeof candidate.runtime_request_id === "string"
    ? candidate.runtime_request_id
    : null;
  const observedUid = requestedRuntimeId
    ? (capture.observedRequests || {})[requestedRuntimeId] || null
    : null;
  // No decoded data or range survives a request/result binding failure.  The
  // backend records a terminal UNKNOWN observation under the one-time
  // capability, so this cannot silently turn into a future accepted result.
  const rejected = {
    capture_request_id: capture.captureRequestId,
    runtime_request_id: requestedRuntimeId || `rejected-${Date.now()}`,
    outcome: "INVALID_PAYLOAD",
    actual_uid: observedUid,
    semantic_uid: null,
    publications: [],
    pagination_terminal: null,
    coverage_start_at: null,
    coverage_end_at: null,
    rejection_reason: rejectionReason,
  };
  await setStatus("RESULT_REJECTED", detail);
  await deliverPayload(capture, rejected);
}

async function recordObservedRequest(capture, detail) {
  if (!detail || typeof detail !== "object" || detail.capture_request_id !== capture.captureRequestId) {
    await setStatus("REQUEST_REJECTED", "未接收匹配的 awemeList 请求绑定。");
    return false;
  }
  const runtimeRequestId = detail.runtime_request_id;
  const actualUid = detail.actual_uid;
  if (
    typeof runtimeRequestId !== "string" ||
    typeof actualUid !== "string" ||
    runtimeRequestId.length < 1 ||
    runtimeRequestId.length > 80 ||
    actualUid.length < 1 ||
    actualUid.length > 160
  ) {
    await setStatus("REQUEST_REJECTED", "awemeList 请求绑定无效。");
    return false;
  }
  const observedRequests = { ...(capture.observedRequests || {}) };
  if (!Object.hasOwn(observedRequests, runtimeRequestId)) {
    const ids = Object.keys(observedRequests);
    if (ids.length >= MAX_OBSERVED_REQUESTS) delete observedRequests[ids[0]];
  }
  observedRequests[runtimeRequestId] = actualUid;
  await chrome.storage.session.set({
    [SESSION_KEY]: { ...capture, observedRequests },
  });
  await setStatus("REQUEST_OBSERVED", "已绑定真实 awemeList 请求，等待同一请求的语义结果。");
  return true;
}

const serviceWorkerApi = {
  ExtensionArmError,
  ensureContentBridgeAndArm,
  injectBridgeAndArm,
  injectMainWorldBridge,
  safeChromeErrorDetail,
};

if (typeof module !== "undefined" && module.exports) module.exports = serviceWorkerApi;

if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.onMessage) {
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    if (!message || typeof message.type !== "string") return { ok: false };
    if (message.type === "huitun-douyin-v1:arm-capture") {
      await armCapture(message.config);
      return { ok: true };
    }
    if (message.type === "huitun-douyin-v1:semantic-result") {
      await handleSemanticResult(message.payload, sender);
      return { ok: true };
    }
    if (message.type === "huitun-douyin-v1:request") {
      const capture = await activeCapture();
      if (capture && sender.tab && sender.tab.id === capture.tabId) {
        await recordObservedRequest(capture, message.detail);
      }
      return { ok: true };
    }
    if (message.type === "huitun-douyin-v1:get-status") {
      const stored = await chrome.storage.session.get(STATUS_KEY);
      return { ok: true, status: stored[STATUS_KEY] || null };
    }
    if (message.type === "huitun-douyin-v1:retry") {
      const capture = await activeCapture();
      if (!capture || !capture.pendingPayload) throw new Error("RETRY_UNAVAILABLE");
      await deliverPayload(capture, capture.pendingPayload);
      return { ok: true };
    }
    return { ok: false };
  })()
    .then(sendResponse)
    .catch(async (error) => {
      const message = error instanceof ExtensionArmError
        ? error.message
        : `EXTENSION_ERROR: ${safeChromeErrorDetail(error, chrome)}`;
      if (!(error instanceof ExtensionArmError)) await setStatus("ERROR", message);
      sendResponse({ ok: false, error: message });
    });
  return true;
});
}
