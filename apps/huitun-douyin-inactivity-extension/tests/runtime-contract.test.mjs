import { createRequire } from "node:module";
import test from "node:test";
import assert from "node:assert/strict";

const require = createRequire(import.meta.url);
const contract = require("../runtime-contract.js");

const context = {
  captureRequestId: "5c0f037b-4b26-47f8-8fbb-302e51b6e204",
  runtimeRequestId: "runtime-7f6a3a",
  actualUid: "huitun-uid-42",
};

test("extracts uid only from the exact real Huitun awemeList request", () => {
  assert.deepEqual(
    contract.extractAwemeListRequest(
      "https://dyapi.huitun.com/user/awemeList?uid=huitun-uid-42&from=1&sortMod=desc&sortField=",
    ),
    {
      actualUid: "huitun-uid-42",
      firstPage: true,
      coverageStartAt: null,
      coverageEndDate: null,
      firstPageDescending: true,
    },
  );
  assert.equal(
    contract.extractAwemeListRequest("https://dyapi.huitun.com/user/other?uid=huitun-uid-42"),
    null,
  );
  assert.equal(
    contract.extractAwemeListRequest("https://other.example/user/awemeList?uid=huitun-uid-42"),
    null,
  );
});

test("parses Huitun timezone-free publishTime as Asia/Shanghai deterministically", () => {
  assert.equal(contract.parseHuitunPublishTime("2026-08-13 17:57:52"), "2026-08-13T09:57:52.000Z");
  assert.equal(contract.parseHuitunPublishTime("2026-08-13 01:02:03"), "2026-08-12T17:02:03.000Z");
  assert.equal(contract.parseHuitunPublishTime("2026-02-30 01:02:03"), null);
});

test("keeps every semantic publication instead of assuming the first is latest", () => {
  const payload = contract.normalizeSemanticReport(
    {
      outcome: "SUCCESS",
      semanticUid: "huitun-uid-42",
      paginationTerminal: true,
      coverageEndAt: "2026-08-14T00:00:00+08:00",
      publications: [
        { publishTime: "2026-08-03 22:54:37" },
        { publishTime: "2026-08-13 17:57:52" },
        { publishTime: "2026-08-03 22:56:34" },
      ],
    },
    context,
  );
  assert.equal(payload.outcome, "SUCCESS");
  assert.deepEqual(
    payload.publications.map((item) => item.published_at), [
      "2026-08-03T14:54:37.000Z",
      "2026-08-13T09:57:52.000Z",
      "2026-08-03T14:56:34.000Z",
    ]);
});

test("accepts a proven newest first page without terminal pagination", () => {
  const payload = contract.normalizeSemanticReport(
    {
      outcome: "SUCCESS",
      semanticUid: "huitun-uid-42",
      paginationTerminal: false,
      latestPageProven: true,
      coverageEndAt: "2026-08-14T00:00:00Z",
      publications: [{ publishTime: "2026-08-13 17:57:52" }],
    },
    context,
  );
  assert.equal(payload.outcome, "SUCCESS");
  assert.equal(payload.pagination_terminal, false);
  assert.equal(payload.latest_page_proven, true);
});

test("rejects a semantic uid mismatch as UNKNOWN-safe invalid payload", () => {
  const payload = contract.normalizeSemanticReport(
    {
      outcome: "SUCCESS",
      semanticUid: "different-uid",
      paginationTerminal: true,
      coverageEndAt: "2026-08-14T00:00:00Z",
      publications: [{ publishTime: "2026-08-13 17:57:52" }],
    },
    context,
  );
  assert.equal(payload.outcome, "INVALID_PAYLOAD");
  assert.equal(payload.actual_uid, "huitun-uid-42");
  assert.equal(payload.rejection_reason, "UID_MISMATCH");
  assert.deepEqual(payload.publications, []);
});

test("requires the outbound result to bind to the previously observed request and uid", () => {
  const payload = contract.normalizeSemanticReport(
    {
      outcome: "SUCCESS",
      semanticUid: "huitun-uid-42",
      paginationTerminal: true,
      coverageEndAt: "2026-08-14T00:00:00Z",
      publications: [{ publishTime: "2026-08-13 17:57:52" }],
    },
    context,
  );
  const observed = {
    capture_request_id: context.captureRequestId,
    runtime_request_id: context.runtimeRequestId,
    actual_uid: context.actualUid,
  };
  assert.equal(contract.matchesObservedAwemeRequest(payload, observed), true);
  assert.equal(
    contract.matchesObservedAwemeRequest(payload, { ...observed, actual_uid: "other-uid" }),
    false,
  );
  assert.equal(
    contract.matchesObservedAwemeRequest(payload, { ...observed, runtime_request_id: "other" }),
    false,
  );
});

test("never turns an encrypted envelope or partial response into an empty result", () => {
  const encrypted = contract.normalizeSemanticReport({ outcome: "RAW_ENCRYPTED" }, context);
  assert.equal(encrypted.outcome, "RAW_ENCRYPTED");
  assert.deepEqual(encrypted.publications, []);
  assert.equal(encrypted.pagination_terminal, null);

  const partial = contract.normalizeSemanticReport(
    {
      outcome: "PARTIAL",
      semanticUid: "huitun-uid-42",
      paginationTerminal: false,
      publications: [{ publishTime: "2026-08-13 17:57:52" }],
    },
    context,
  );
  assert.equal(partial.outcome, "PARTIAL");
  assert.deepEqual(partial.publications, []);
  assert.equal(partial.coverage_start_at, null);
  assert.equal(partial.coverage_end_at, null);
});

test("outbound payload is allowlisted and has no cookie, authorization, or capture token field", () => {
  const clean = contract.sanitizeOutboundPayload({
    capture_request_id: context.captureRequestId,
    runtime_request_id: context.runtimeRequestId,
    outcome: "RAW_ENCRYPTED",
    actual_uid: context.actualUid,
    semantic_uid: null,
    publications: [],
    pagination_terminal: null,
    latest_page_proven: false,
    coverage_start_at: null,
    coverage_end_at: null,
  });
  assert.deepEqual(Object.keys(clean).sort(), [
    "actual_uid",
    "capture_request_id",
    "coverage_end_at",
    "coverage_start_at",
    "latest_page_proven",
    "outcome",
    "pagination_terminal",
    "publications",
    "runtime_request_id",
    "semantic_uid",
  ]);
  assert.equal(
    contract.sanitizeOutboundPayload({ ...clean, cookie: "never" }),
    null,
  );
});

test("allows only the closed INVALID_PAYLOAD rejection diagnostic", () => {
  const rejected = contract.normalizeSemanticReport(
    { outcome: "INVALID_PAYLOAD", rejectionReason: "PUBLICATION_UID_CONFLICT" },
    context,
  );
  assert.equal(rejected.rejection_reason, "PUBLICATION_UID_CONFLICT");
  assert.equal(
    contract.sanitizeOutboundPayload({ ...rejected, rejection_reason: "cookie=never" }),
    null,
  );
});
