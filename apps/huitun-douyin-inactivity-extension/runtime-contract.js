/*
 * Browser- and Node-testable contract for the V1 semantic bridge.
 *
 * This file intentionally has no HTTP response decoder. It accepts only
 * decoded semantic fields provided by a page-runtime hook, and emits the
 * small normalized payload that the backend contract accepts.
 */
(function attachHuitunDouyinRuntimeContract(globalScope) {
  "use strict";

  const API_HOST = "dyapi.huitun.com";
  const API_PATH = "/user/awemeList";
  const SAFE_IDENTIFIER = /^[\x21-\x7e]{1,160}$/;
  const SEMANTIC_OUTCOMES = new Set(["SUCCESS", "EMPTY", "PARTIAL"]);
  const FAILURE_OUTCOMES = new Set([
    "RAW_ENCRYPTED",
    "AUTH_FAILURE",
    "RUNTIME_ERROR",
    "INVALID_PAYLOAD",
  ]);
  // This is intentionally a closed diagnostic vocabulary.  It identifies a
  // bridge decision without carrying page data, a request URL, or credentials.
  const REJECTION_REASONS = new Set([
    "UID_MISMATCH",
    "REQUEST_BINDING_MISMATCH",
    "AMBIGUOUS_REQUEST",
    "PUBLICATION_UID_CONFLICT",
    "SCHEMA_REJECTED",
  ]);

  function isRecord(value) {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
  }

  function safeIdentifier(value) {
    return typeof value === "string" && SAFE_IDENTIFIER.test(value) ? value : null;
  }

  function parseShanghaiQueryDate(value) {
    if (typeof value !== "string") return null;
    const matched = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
    if (!matched) return null;
    const [, yearText, monthText, dayText] = matched;
    const year = Number(yearText);
    const month = Number(monthText);
    const day = Number(dayText);
    const instant = new Date(Date.UTC(year, month - 1, day, -8, 0, 0));
    const shanghaiWallClock = new Date(instant.getTime() + 8 * 60 * 60 * 1000);
    if (
      shanghaiWallClock.getUTCFullYear() !== year ||
      shanghaiWallClock.getUTCMonth() !== month - 1 ||
      shanghaiWallClock.getUTCDate() !== day
    ) {
      return null;
    }
    return { date: value, startAt: instant.toISOString() };
  }

  function extractAwemeListRequest(rawUrl) {
    if (typeof rawUrl !== "string") return null;
    let url;
    try {
      url = new URL(rawUrl, "https://huitun.invalid/");
    } catch {
      return null;
    }
    if (url.protocol !== "https:" || url.hostname !== API_HOST || url.pathname !== API_PATH) {
      return null;
    }
    const actualUid = safeIdentifier(url.searchParams.get("uid"));
    if (!actualUid) return null;
    const coverageStart = parseShanghaiQueryDate(url.searchParams.get("queryTimeStart"));
    const coverageEnd = parseShanghaiQueryDate(url.searchParams.get("queryTimeEnd"));
    const firstPage = url.searchParams.get("from") === "1";
    const coverageIsOrdered =
      coverageStart !== null && coverageEnd !== null && coverageStart.date <= coverageEnd.date;
    // This is only the request-side half of latest-page proof.  A blank
    // sortField is accepted solely as part of the observed Huitun request
    // contract; page-main must additionally verify decoded publishTime order.
    return {
      actualUid,
      firstPage,
      coverageStartAt: coverageIsOrdered ? coverageStart.startAt : null,
      coverageEndDate: coverageIsOrdered ? coverageEnd.date : null,
      firstPageDescending:
        firstPage &&
        url.searchParams.get("sortMod") === "desc" &&
        url.searchParams.get("sortField") === "",
    };
  }

  function parseHuitunPublishTime(value) {
    if (typeof value !== "string") return null;
    const matched = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$/.exec(value);
    if (!matched) return null;
    const [, yearText, monthText, dayText, hourText, minuteText, secondText] = matched;
    const year = Number(yearText);
    const month = Number(monthText);
    const day = Number(dayText);
    const hour = Number(hourText);
    const minute = Number(minuteText);
    const second = Number(secondText);
    if (
      month < 1 ||
      month > 12 ||
      day < 1 ||
      day > 31 ||
      hour > 23 ||
      minute > 59 ||
      second > 59
    ) {
      return null;
    }
    // Huitun's semantic publishTime has no offset. The V1 source contract
    // explicitly fixes it to Asia/Shanghai (UTC+08:00), never local browser TZ.
    const instant = new Date(Date.UTC(year, month - 1, day, hour - 8, minute, second));
    const shanghaiWallClock = new Date(instant.getTime() + 8 * 60 * 60 * 1000);
    if (
      shanghaiWallClock.getUTCFullYear() !== year ||
      shanghaiWallClock.getUTCMonth() !== month - 1 ||
      shanghaiWallClock.getUTCDate() !== day ||
      shanghaiWallClock.getUTCHours() !== hour ||
      shanghaiWallClock.getUTCMinutes() !== minute ||
      shanghaiWallClock.getUTCSeconds() !== second
    ) {
      return null;
    }
    return instant.toISOString();
  }

  function normalizeIsoInstant(value) {
    if (typeof value !== "string") return null;
    // Do not silently interpret timezone-free coverage times in the extension.
    if (!/(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return null;
    const milliseconds = Date.parse(value);
    return Number.isFinite(milliseconds) ? new Date(milliseconds).toISOString() : null;
  }

  function failurePayload(context, outcome, rejectionReason = null) {
    const payload = {
      capture_request_id: context.captureRequestId,
      runtime_request_id: context.runtimeRequestId,
      outcome,
      actual_uid: context.actualUid || null,
      semantic_uid: null,
      publications: [],
      pagination_terminal: null,
      latest_page_proven: false,
      coverage_start_at: null,
      coverage_end_at: null,
    };
    if (outcome === "INVALID_PAYLOAD") {
      payload.rejection_reason = REJECTION_REASONS.has(rejectionReason)
        ? rejectionReason
        : "SCHEMA_REJECTED";
    }
    return payload;
  }

  function normalizeSemanticReport(report, context) {
    if (!isRecord(context) || !safeIdentifier(context.runtimeRequestId) || !context.captureRequestId) {
      return null;
    }
    if (!isRecord(report)) return failurePayload(context, "INVALID_PAYLOAD", "SCHEMA_REJECTED");
    const outcome = typeof report.outcome === "string" ? report.outcome : "INVALID_PAYLOAD";
    if (FAILURE_OUTCOMES.has(outcome)) {
      return failurePayload(context, outcome, report.rejectionReason);
    }
    if (!SEMANTIC_OUTCOMES.has(outcome) || !safeIdentifier(context.actualUid)) {
      return failurePayload(context, "INVALID_PAYLOAD", "SCHEMA_REJECTED");
    }

    const semanticUid = safeIdentifier(report.semanticUid);
    if (semanticUid !== context.actualUid) {
      return failurePayload(context, "INVALID_PAYLOAD", "UID_MISMATCH");
    }

    const latestPageProven = report.latestPageProven === true;
    if (outcome === "PARTIAL") {
      if (report.paginationTerminal === true || latestPageProven) {
        return failurePayload(context, "INVALID_PAYLOAD", "SCHEMA_REJECTED");
      }
      return {
        capture_request_id: context.captureRequestId,
        runtime_request_id: context.runtimeRequestId,
        outcome,
        actual_uid: context.actualUid,
        semantic_uid: semanticUid,
        publications: [],
        pagination_terminal: false,
        latest_page_proven: false,
        coverage_start_at: null,
        coverage_end_at: null,
      };
    }

    const publicationsInput = Array.isArray(report.publications) ? report.publications : null;
    if (!publicationsInput || publicationsInput.length > 1000) {
      return failurePayload(context, "INVALID_PAYLOAD", "SCHEMA_REJECTED");
    }
    const publications = [];
    for (const publication of publicationsInput) {
      if (!isRecord(publication)) return failurePayload(context, "INVALID_PAYLOAD", "SCHEMA_REJECTED");
      const publishedAt = parseHuitunPublishTime(publication.publishTime);
      if (!publishedAt) return failurePayload(context, "INVALID_PAYLOAD", "SCHEMA_REJECTED");
      publications.push({ published_at: publishedAt });
    }
    const paginationTerminal = report.paginationTerminal;
    const coverageStartAt =
      report.coverageStartAt === undefined ? null : normalizeIsoInstant(report.coverageStartAt);
    const coverageEndAt =
      report.coverageEndAt === undefined ? null : normalizeIsoInstant(report.coverageEndAt);
    if (
      (report.coverageStartAt !== undefined && !coverageStartAt) ||
      (report.coverageEndAt !== undefined && !coverageEndAt) ||
      (coverageStartAt && coverageEndAt && coverageStartAt > coverageEndAt)
    ) {
      return failurePayload(context, "INVALID_PAYLOAD", "SCHEMA_REJECTED");
    }

    if (outcome === "SUCCESS") {
      if (
        publications.length === 0 ||
        !coverageEndAt ||
        (paginationTerminal !== true && !(paginationTerminal === false && latestPageProven))
      ) {
        return failurePayload(context, "INVALID_PAYLOAD", "SCHEMA_REJECTED");
      }
    } else if (outcome === "EMPTY") {
      if (
        publications.length !== 0 ||
        paginationTerminal !== true ||
        latestPageProven ||
        !coverageStartAt ||
        !coverageEndAt
      ) {
        return failurePayload(context, "INVALID_PAYLOAD", "SCHEMA_REJECTED");
      }
    }

    return {
      capture_request_id: context.captureRequestId,
      runtime_request_id: context.runtimeRequestId,
      outcome,
      actual_uid: context.actualUid,
      semantic_uid: semanticUid,
      publications,
      pagination_terminal: typeof paginationTerminal === "boolean" ? paginationTerminal : null,
      latest_page_proven:
        outcome === "SUCCESS" && paginationTerminal === false && latestPageProven,
      coverage_start_at: coverageStartAt,
      coverage_end_at: coverageEndAt,
    };
  }

  function sanitizeOutboundPayload(value) {
    if (!isRecord(value)) return null;
    const allowedKeys = new Set([
      "capture_request_id",
      "runtime_request_id",
      "outcome",
      "actual_uid",
      "semantic_uid",
      "publications",
      "pagination_terminal",
      "latest_page_proven",
      "coverage_start_at",
      "coverage_end_at",
      "rejection_reason",
    ]);
    if (Object.keys(value).some((key) => !allowedKeys.has(key))) return null;
    if (
      typeof value.capture_request_id !== "string" ||
      !safeIdentifier(value.runtime_request_id) ||
      typeof value.outcome !== "string" ||
      !Array.isArray(value.publications)
    ) {
      return null;
    }
    const rejectionReason = value.rejection_reason;
    if (value.outcome === "INVALID_PAYLOAD") {
      if (!REJECTION_REASONS.has(rejectionReason)) return null;
    } else if (rejectionReason !== undefined) {
      return null;
    }
    const payload = {
      capture_request_id: value.capture_request_id,
      runtime_request_id: value.runtime_request_id,
      outcome: value.outcome,
      actual_uid: safeIdentifier(value.actual_uid),
      semantic_uid: safeIdentifier(value.semantic_uid),
      publications: value.publications.map((publication) => ({
        published_at: publication && typeof publication.published_at === "string"
          ? publication.published_at
          : null,
      })),
      pagination_terminal:
        typeof value.pagination_terminal === "boolean" ? value.pagination_terminal : null,
      latest_page_proven: value.latest_page_proven === true,
      coverage_start_at:
        typeof value.coverage_start_at === "string" ? value.coverage_start_at : null,
      coverage_end_at:
        typeof value.coverage_end_at === "string" ? value.coverage_end_at : null,
    };
    if (value.outcome === "INVALID_PAYLOAD") payload.rejection_reason = rejectionReason;
    return payload;
  }

  // The extension service worker retains this tiny binding only for the
  // current browser session.  It refuses to forward a semantic result unless
  // the same runtime id and uid were first observed on the exact real
  // awemeList request.  This is deliberately separate from semantic parsing:
  // a page result can be well-formed yet still belong to another request.
  function matchesObservedAwemeRequest(payload, observed) {
    if (!isRecord(payload) || !isRecord(observed)) return false;
    const observedUid = safeIdentifier(observed.actual_uid);
    return Boolean(
      observedUid &&
        payload.capture_request_id === observed.capture_request_id &&
        payload.runtime_request_id === observed.runtime_request_id &&
        safeIdentifier(payload.actual_uid) === observedUid,
    );
  }

  const api = {
    extractAwemeListRequest,
    matchesObservedAwemeRequest,
    normalizeSemanticReport,
    parseHuitunPublishTime,
    sanitizeOutboundPayload,
  };
  globalScope.HuitunDouyinRuntimeContract = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(globalThis);
