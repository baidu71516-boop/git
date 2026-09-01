# Huitun Douyin Inactivity Capture V1

This unpacked Chrome extension is deliberately a narrow capture bridge for one
endpoint: `https://dyapi.huitun.com/user/awemeList`. It is not a browser
automation framework and it never decrypts a Huitun response.

The extension observes the actual request URL in Chrome's MAIN world to obtain
the `uid`, then waits for an already-decoded page-runtime semantic hook. It
does not read `fetch`/XHR response bodies, cookies, Authorization headers, or
raw encrypted envelopes. The service worker sends only a normalized payload to
the configured backend ingest endpoint with `credentials: "omit"`; its
one-time backend capability token exists only in `chrome.storage.session`.

## Current runtime hook contract

`page-main.js` installs this page-world function after the operator arms an
existing Huitun tab:

```js
window.__huitunDouyinInactivityV1Bridge.reportSemanticAwemeList({
  runtimeRequestId, // emitted after the exact /user/awemeList request
  semanticUid,      // uid from the decoded request/result context; must match
  outcome: "SUCCESS", // SUCCESS | EMPTY | PARTIAL
  publications: [{ publishTime: "2026-08-13 17:57:52" }],
  paginationTerminal: true,
  coverageStartAt: "2026-07-15T00:00:00+08:00",
  coverageEndAt: "2026-08-14T00:00:00+08:00",
});
```

The bridge converts `publishTime` using the explicit `Asia/Shanghai` source
contract, validates `semanticUid === actual uid from the intercepted request`,
and the service worker separately requires that same runtime request ID and
UID to have been observed before it forwards anything. It sends only timestamp
values. A decoded hook that cannot prove the payload must instead call:

```js
window.__huitunDouyinInactivityV1Bridge.reportAwemeListFailure({
  runtimeRequestId,
  outcome: "RAW_ENCRYPTED", // or AUTH_FAILURE | RUNTIME_ERROR | INVALID_PAYLOAD
  // Required only for INVALID_PAYLOAD. This closed enum carries no raw data:
  // UID_MISMATCH | REQUEST_BINDING_MISMATCH | AMBIGUOUS_REQUEST |
  // PUBLICATION_UID_CONFLICT | SCHEMA_REJECTED
  rejectionReason: "UID_MISMATCH",
});
```

`EMPTY` must include a terminal pagination proof and an explicit range. A
`PARTIAL` result strips all publications and range claims before backend ingest,
so it cannot create a lower bound.

## One real logged-in Huitun interop still required

The generic bridge is implemented, but this repository has no existing Huitun
runtime hook. A real logged-in Huitun session is still needed to locate the
stable decoded consumer that owns `e.data` and correlate it with the emitted
`runtimeRequestId`. The minimum live validation is:

1. Load this directory with Chrome's **Load unpacked** flow.
2. Use the authenticated internal backend to create
   `POST /api/v1/admin/content-activity/douyin/runtime-captures`, then paste its
   `capture_request_id`, one-time `capture_token`, and the exact backend ingest
   URL into the popup. Do not paste any Huitun session material.
3. Open only the selected account's real Huitun page, arm the extension, and
   use the verified flow: **视频分析页 → arm → 数据概览 → 视频分析**. This
   intentionally triggers one fresh actual `awemeList` request after arming;
   the popup must first show `REQUEST_OBSERVED`. Do not treat historic page
   resources, a popup `DELIVERED` state, or an automatic navigation as proof.
4. At the verified decoded consumer, invoke the narrow function above with the
   matching `runtimeRequestId`. Do not hook raw fetch/XHR response handling.
5. Confirm the backend returns `ACCEPTED` for a matching semantic result, or
   `UNKNOWN` for encrypted, auth, partial, malformed, expired, or uid-mismatch
   cases. Then run the Candidate Pool threshold check against the persisted
   immutable observation.

The bridge must remain fail-closed if that live hook cannot prove the same real
request's `uid`, semantic result, and terminal pagination state.

The current runtime state machine has no evidence-backed safe automatic
navigation/retry action, so V1 preserves this operator flow rather than
changing page navigation timing. For `INVALID_PAYLOAD`, the backend persists
only the closed sanitized reason code in the existing observation/request error
fields; it never persists cookies, tokens, request URLs, raw responses, or
publication payloads.
