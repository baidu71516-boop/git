/*
 * Injected into Chrome's MAIN world only while an operator arms a capture.
 *
 * It observes the real awemeList URL and uid, but never reads fetch/XHR
 * response text, JSON, body, headers, cookies, or Authorization. A separately
 * located decoded-runtime hook must call reportSemanticAwemeList() with the
 * semantic fields that Huitun already decrypted in the page.
 */
(function installHuitunDouyinMainBridge() {
  "use strict";

  const contract = globalThis.HuitunDouyinRuntimeContract;
  if (!contract) return;
  const bridgeKey = "__huitunDouyinInactivityV1Bridge";
  const armEvent = "huitun-douyin-v1:arm";
  const requestEvent = "huitun-douyin-v1:aweme-request";
  const resultEvent = "huitun-douyin-v1:semantic-result";

  if (globalThis[bridgeKey]) return;

  const state = {
    captureRequestId: null,
    settled: false,
    requests: new Map(),
    requestIdsByUrl: new Map(),
  };
  let resourceObserver = null;
  let semanticPromiseThen = null;

  function runtimeRequestId() {
    if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
      return globalThis.crypto.randomUUID();
    }
    return `runtime-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }

  function dispatch(name, detail) {
    globalThis.dispatchEvent(new CustomEvent(name, { detail }));
  }

  function arm(detail) {
    if (!detail || typeof detail.captureRequestId !== "string") return;
    state.captureRequestId = detail.captureRequestId;
    state.settled = false;
    state.requests.clear();
    state.requestIdsByUrl.clear();
    installSemanticPromiseObserver();
    observeExistingResourceEntries();
  }

  function observeAwemeRequest(rawUrl) {
    if (!state.captureRequestId || state.settled) return null;
    const request = contract.extractAwemeListRequest(rawUrl);
    if (!request) return null;
    // fetch/XHR hooks and Resource Timing can report the same request. Keep a
    // single runtime ID so the semantic hook has one stable request binding.
    const requestKey = `${request.actualUid}\u0000${rawUrl}`;
    const existingRequestId = state.requestIdsByUrl.get(requestKey);
    if (existingRequestId) return state.requests.get(existingRequestId) || null;
    const id = runtimeRequestId();
    const context = {
      captureRequestId: state.captureRequestId,
      runtimeRequestId: id,
      actualUid: request.actualUid,
      requestUrl: rawUrl,
      firstPage: request.firstPage === true,
      firstPageDescending: request.firstPageDescending === true,
      coverageStartAt: request.coverageStartAt,
      coverageEndDate: request.coverageEndDate,
    };
    state.requests.set(id, context);
    state.requestIdsByUrl.set(requestKey, id);
    dispatch(requestEvent, {
      capture_request_id: context.captureRequestId,
      runtime_request_id: context.runtimeRequestId,
      actual_uid: context.actualUid,
    });
    return context;
  }

  function observeResourceEntry(entry) {
    if (
      !entry ||
      (entry.initiatorType !== "fetch" && entry.initiatorType !== "xmlhttprequest") ||
      typeof entry.name !== "string"
    ) {
      return null;
    }
    return observeAwemeRequest(entry.name);
  }

  function observeExistingResourceEntries() {
    const performanceApi = globalThis.performance;
    if (!performanceApi || typeof performanceApi.getEntriesByType !== "function") return;
    let entries;
    try {
      entries = performanceApi.getEntriesByType("resource");
    } catch {
      return;
    }
    for (const entry of entries) observeResourceEntry(entry);
  }

  function installResourceObserver() {
    if (resourceObserver || typeof globalThis.PerformanceObserver !== "function") return;
    const onEntries = (list) => {
      for (const entry of list.getEntries()) observeResourceEntry(entry);
    };
    try {
      resourceObserver = new globalThis.PerformanceObserver(onEntries);
      resourceObserver.observe({ type: "resource", buffered: true });
    } catch {
      // Current Chrome supports the buffered form. Keep a non-buffered form
      // for an implementation that only supports the older entryTypes API;
      // arm() still performs the explicit historical scan above.
      try {
        resourceObserver = new globalThis.PerformanceObserver(onEntries);
        resourceObserver.observe({ entryTypes: ["resource"] });
      } catch {
        resourceObserver = null;
      }
    }
  }

  function isRecord(value) {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
  }

  function hasOwn(record, key) {
    return Object.prototype.hasOwnProperty.call(record, key);
  }

  function semanticUidFromEntry(value) {
    if (typeof value === "string" && /^[\x21-\x7e]{1,160}$/.test(value)) return value;
    if (typeof value === "number" && Number.isSafeInteger(value) && value >= 0) {
      return String(value);
    }
    return null;
  }

  function responseRecords(value) {
    const records = [value];
    if (isRecord(value.dataList)) records.push(value.dataList);
    return records;
  }

  function hasTerminalPagination(value) {
    for (const record of responseRecords(value)) {
      for (const name of ["paginationTerminal", "pagination_terminal", "isLastPage", "is_last_page"]) {
        if (hasOwn(record, name) && record[name] === true) return true;
      }
      for (const name of ["hasMore", "has_more"]) {
        if (hasOwn(record, name) && record[name] === false) return true;
      }
    }
    // Never infer an empty terminal page from page length or total.  Only an
    // explicit decoded pagination signal can authorize an EMPTY lower bound.
    return false;
  }

  function currentShanghaiDate() {
    const wallClock = new Date(Date.now() + 8 * 60 * 60 * 1000);
    return [
      wallClock.getUTCFullYear(),
      String(wallClock.getUTCMonth() + 1).padStart(2, "0"),
      String(wallClock.getUTCDate()).padStart(2, "0"),
    ].join("-");
  }

  function currentCoverageFromRequest(context) {
    if (
      !context ||
      context.firstPage !== true ||
      typeof context.coverageStartAt !== "string" ||
      context.coverageEndDate !== currentShanghaiDate()
    ) {
      return null;
    }
    const coverageEndAt = new Date().toISOString();
    if (Date.parse(context.coverageStartAt) >= Date.parse(coverageEndAt)) return null;
    // queryTimeEnd is date-only. Once it is proven to be today's Shanghai
    // date, the fulfillment instant is the only non-future current-ending
    // bound accepted by the backend contract.
    return { coverageStartAt: context.coverageStartAt, coverageEndAt };
  }

  function decodedAwemeListCandidate(value) {
    // The identified consumer fulfills with a narrow decoded envelope. Reject
    // everything else before traversing it, including encrypted envelopes.
    if (
      !isRecord(value) ||
      (value.code !== 0 && value.code !== "0") ||
      !hasOwn(value, "dataList") ||
      !Array.isArray(value.data) ||
      value.data.length > 1000
    ) {
      return null;
    }
    if (value.data.length === 0) {
      return {
        kind: value.total === 0 ? "EMPTY_CANDIDATE" : "EMPTY_UNPROVEN",
        publications: [],
      };
    }
    let semanticUid = null;
    const publications = [];
    let previousPublishedAt = null;
    let newestFirst = true;
    for (const entry of value.data) {
      if (!isRecord(entry) || typeof entry.publishTime !== "string" || !entry.publishTime) {
        return null;
      }
      const entryUid = semanticUidFromEntry(entry.uid);
      if (!entryUid) return null;
      if (semanticUid !== null && semanticUid !== entryUid) {
        return { kind: "UID_CONFLICT" };
      }
      const publishedAt = contract.parseHuitunPublishTime(entry.publishTime);
      if (!publishedAt) return null;
      const publishedAtMillis = Date.parse(publishedAt);
      if (!Number.isFinite(publishedAtMillis)) return null;
      if (previousPublishedAt !== null && previousPublishedAt < publishedAtMillis) {
        newestFirst = false;
      }
      previousPublishedAt = publishedAtMillis;
      semanticUid = entryUid;
      publications.push({ publishTime: entry.publishTime });
    }
    return {
      kind: "CANDIDATE",
      semanticUid,
      publications,
      newestFirst,
    };
  }

  function matchingRequestContext(semanticUid) {
    const matches = Array.from(state.requests.values()).filter(
      (context) => context.actualUid === semanticUid,
    );
    return matches.length === 1 ? matches[0] : null;
  }

  function onlyObservedRequestContext() {
    const contexts = Array.from(state.requests.values());
    return contexts.length === 1 ? contexts[0] : null;
  }

  function rejectDecodedSemanticCandidate(context, rejectionReason) {
    reportAwemeListFailure({
      runtimeRequestId: context ? context.runtimeRequestId : null,
      outcome: "INVALID_PAYLOAD",
      rejectionReason,
    });
  }

  function observeDecodedSemanticFulfillment(value) {
    if (!state.captureRequestId || state.settled || state.requests.size === 0) return;
    const candidate = decodedAwemeListCandidate(value);
    if (!candidate) return;
    if (candidate.kind === "EMPTY_CANDIDATE" || candidate.kind === "EMPTY_UNPROVEN") {
      const context = onlyObservedRequestContext();
      if (!context) {
        rejectDecodedSemanticCandidate(null, "AMBIGUOUS_REQUEST");
        return;
      }
      const coverage = candidate.kind === "EMPTY_CANDIDATE"
        ? currentCoverageFromRequest(context)
        : null;
      reportSemanticAwemeList({
        runtimeRequestId: context.runtimeRequestId,
        // An empty semantic array carries no entry uid. This value is the
        // already-bound real awemeList uid, never a guessed provider identity.
        semanticUid: context.actualUid,
        outcome: coverage ? "EMPTY" : "PARTIAL",
        publications: [],
        paginationTerminal: coverage !== null,
        latestPageProven: false,
        coverageStartAt: coverage ? coverage.coverageStartAt : undefined,
        coverageEndAt: coverage ? coverage.coverageEndAt : undefined,
      });
      return;
    }
    if (candidate.kind !== "CANDIDATE") {
      rejectDecodedSemanticCandidate(onlyObservedRequestContext(), "PUBLICATION_UID_CONFLICT");
      return;
    }
    const context = matchingRequestContext(candidate.semanticUid);
    if (!context) {
      // A matching shape with a different or ambiguous uid cannot prove the
      // same awemeList request. Preserve no decoded values in the rejection.
      const requestCount = state.requests.size;
      rejectDecodedSemanticCandidate(
        onlyObservedRequestContext(),
        requestCount > 1 ? "AMBIGUOUS_REQUEST" : "UID_MISMATCH",
      );
      return;
    }
    const terminal = hasTerminalPagination(value);
    // A non-terminal response can still establish the exact latest timestamp
    // only under the verified Huitun first-page request contract plus direct
    // decoded publishTime descending order.  No uid, date, or total is
    // hard-coded; a changed/missing signal falls back to PARTIAL.
    const latestPageProven =
      !terminal && context.firstPageDescending && candidate.newestFirst;
    const accepted = terminal || latestPageProven;
    reportSemanticAwemeList({
      runtimeRequestId: context.runtimeRequestId,
      semanticUid: candidate.semanticUid,
      outcome: accepted ? "SUCCESS" : "PARTIAL",
      publications: accepted ? candidate.publications : [],
      paginationTerminal: terminal,
      latestPageProven,
      // This is the local fulfillment observation instant, not a provider
      // timestamp. The backend independently bounds it against server time.
      coverageEndAt: accepted ? new Date().toISOString() : undefined,
    });
  }

  function installSemanticPromiseObserver() {
    if (semanticPromiseThen || !globalThis.Promise || !globalThis.Promise.prototype) return;
    const originalThen = globalThis.Promise.prototype.then;
    if (typeof originalThen !== "function") return;
    const wrappedThen = function huitunDouyinSemanticThen(onFulfilled, onRejected) {
      if (typeof onFulfilled !== "function") {
        return originalThen.call(this, onFulfilled, onRejected);
      }
      return originalThen.call(
        this,
        function huitunDouyinObserveFulfillment(value) {
          // Observation must never change a Huitun fulfillment value, return
          // value, or exception path. It does not inspect response bodies.
          try {
            observeDecodedSemanticFulfillment(value);
          } catch {
            // The page consumer continues even if this narrow observer fails.
          }
          return onFulfilled.apply(this, arguments);
        },
        onRejected,
      );
    };
    try {
      globalThis.Promise.prototype.then = wrappedThen;
      if (globalThis.Promise.prototype.then === wrappedThen) semanticPromiseThen = wrappedThen;
    } catch {
      // A locked Promise prototype leaves the page untouched and fail-closed.
    }
  }

  function emitFailureForUnknownRequest(report) {
    if (!state.captureRequestId || state.settled) return;
    const payload = contract.normalizeSemanticReport(
      { outcome: "INVALID_PAYLOAD", rejectionReason: "REQUEST_BINDING_MISMATCH" },
      {
        captureRequestId: state.captureRequestId,
        runtimeRequestId:
          report && typeof report.runtimeRequestId === "string"
            ? report.runtimeRequestId
            : runtimeRequestId(),
        actualUid: null,
      },
    );
    if (!payload) return;
    state.settled = true;
    dispatch(resultEvent, payload);
  }

  function reportSemanticAwemeList(report) {
    if (!report || typeof report.runtimeRequestId !== "string") {
      emitFailureForUnknownRequest(report);
      return false;
    }
    const context = state.requests.get(report.runtimeRequestId);
    if (!context || state.settled) {
      emitFailureForUnknownRequest(report);
      return false;
    }
    const payload = contract.normalizeSemanticReport(report, context);
    if (!payload) return false;
    state.settled = true;
    dispatch(resultEvent, payload);
    return payload.outcome === "SUCCESS" || payload.outcome === "EMPTY";
  }

  function reportAwemeListFailure(report) {
    const runtimeId = report && typeof report.runtimeRequestId === "string"
      ? report.runtimeRequestId
      : null;
    const context = runtimeId ? state.requests.get(runtimeId) : null;
    if (!context || state.settled) {
      emitFailureForUnknownRequest(report);
      return false;
    }
    const outcome = report && typeof report.outcome === "string" ? report.outcome : "RUNTIME_ERROR";
    const payload = contract.normalizeSemanticReport(
      { outcome, rejectionReason: report && report.rejectionReason },
      context,
    );
    if (!payload) return false;
    state.settled = true;
    dispatch(resultEvent, payload);
    return true;
  }

  // Record only the request URL metadata. Deliberately do not attach to the
  // Promise or response object, so encrypted response envelopes are never
  // parsed or mistaken for an empty semantic list.
  const originalFetch = globalThis.fetch;
  if (typeof originalFetch === "function") {
    globalThis.fetch = function huitunDouyinCaptureFetch(input, init) {
      const url = typeof input === "string" ? input : input && input.url;
      observeAwemeRequest(url);
      return originalFetch.call(this, input, init);
    };
  }

  const xhrRequestUrl = Symbol("huitunDouyinCaptureRequestUrl");
  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function huitunDouyinCaptureOpen(method, url, ...rest) {
    this[xhrRequestUrl] = typeof url === "string" ? url : null;
    return originalOpen.call(this, method, url, ...rest);
  };
  XMLHttpRequest.prototype.send = function huitunDouyinCaptureSend(...args) {
    observeAwemeRequest(this[xhrRequestUrl]);
    return originalSend.apply(this, args);
  };

  globalThis.addEventListener(armEvent, (event) => arm(event.detail));
  // Some Umi bundles retain the original fetch before this bridge is injected.
  // Resource Timing observes the resulting real network URL without touching
  // response bodies, headers, cookies, or Authorization.
  installResourceObserver();
  globalThis[bridgeKey] = Object.freeze({
    reportAwemeListFailure,
    reportSemanticAwemeList,
  });
})();
