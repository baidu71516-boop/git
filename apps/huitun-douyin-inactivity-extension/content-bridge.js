/* Bridge only normalized, token-free page events into the extension worker. */
(function installHuitunDouyinContentBridge() {
  "use strict";

  const armEvent = "huitun-douyin-v1:arm";
  const requestEvent = "huitun-douyin-v1:aweme-request";
  const resultEvent = "huitun-douyin-v1:semantic-result";
  let requestDelivery = Promise.resolve();

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (!message || message.type !== "huitun-douyin-v1:arm") return undefined;
    // The page receives only a capture request ID. The one-time backend token
    // remains in chrome.storage.session and never crosses this boundary.
    window.dispatchEvent(
      new CustomEvent(armEvent, {
        detail: { captureRequestId: message.captureRequestId },
      }),
    );
    sendResponse({ armed: true });
    return undefined;
  });

  window.addEventListener(requestEvent, (event) => {
    const detail = event.detail;
    if (!detail || typeof detail !== "object") return;
    // A semantic result may arrive in the same fulfillment turn as the
    // request event. Persist the real request/uid binding first so the worker
    // never accepts an unbound result due to message scheduling.
    requestDelivery = requestDelivery
      .catch(() => undefined)
      .then(() => chrome.runtime.sendMessage({ type: "huitun-douyin-v1:request", detail }))
      .catch(() => undefined);
  });

  window.addEventListener(resultEvent, (event) => {
    const detail = event.detail;
    if (!detail || typeof detail !== "object") return;
    void requestDelivery
      .then(() => chrome.runtime.sendMessage({ type: "huitun-douyin-v1:semantic-result", payload: detail }))
      .catch(() => undefined);
  });
})();
