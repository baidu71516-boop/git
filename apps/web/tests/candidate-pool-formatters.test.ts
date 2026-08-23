import { describe, expect, it } from "vitest";

import {
  candidatePoolKindLabel,
  candidatePoolStatus,
  candidateReasonLabel,
  candidateResultPresentation,
  candidateRunFailureMessage,
  candidateRunStatus,
} from "../src/features/candidate-pools/formatters";

describe("Candidate Pool presenters", () => {
  it.each([
    ["POTENTIAL_SELLER", "潜在卖家"],
    ["POTENTIAL_BUYER", "潜在买家"],
    ["FUTURE", "未知类型"],
  ])("presents pool kind %s safely", (kind, label) =>
    expect(candidatePoolKindLabel(kind)).toBe(label),
  );
  it.each([
    ["ACTIVE", "启用中"],
    ["ARCHIVED", "已归档"],
    ["FUTURE", "未知状态"],
  ])("presents pool status %s safely", (status, label) =>
    expect(candidatePoolStatus(status).label).toBe(label),
  );
  it.each([
    ["PENDING", "等待生成"],
    ["RUNNING", "正在生成"],
    ["COMPLETED", "已完成"],
    ["FAILED", "生成失败"],
    ["FUTURE", "未知状态"],
  ])("presents run status %s safely", (status, label) =>
    expect(candidateRunStatus(status).label).toBe(label),
  );
  it("keeps MATCH and UNKNOWN distinct and never maps a future value to NOT_MATCH", () => {
    expect(candidateResultPresentation("MATCH").label).toBe("符合条件");
    expect(candidateResultPresentation("UNKNOWN").label).toBe("信息不足");
    expect(candidateResultPresentation("FUTURE").label).toBe("未知结果");
  });
  it("covers all frozen reason codes and treats CATEGORY_MISMATCH as a reason, not a result", () => {
    const codes = [
      "ACTIVITY_MISSING",
      "AMBIGUOUS_CLASSIFICATION",
      "CATEGORY_ALIGNED",
      "CATEGORY_MISMATCH",
      "CLASSIFICATION_STALE",
      "COLLECTION_CATEGORY_UNMAPPED",
      "COLLECTION_CONTEXT_MISSING",
      "CONTACT_AVAILABLE",
      "CONTACT_EVIDENCE_REDACTED",
      "CONTENT_ACTIVITY_INCOMPLETE",
      "CONTENT_ACTIVITY_MATCH",
      "CONTENT_ACTIVITY_MISSING",
      "CONTENT_ACTIVITY_NO_PUBLIC_CONTENT",
      "CONTENT_ACTIVITY_RECENT",
      "CONTENT_ACTIVITY_STALE",
      "CONTENT_ACTIVITY_UNKNOWN",
      "CONTENT_ACTIVITY_UNTRUSTED",
      "CREATOR_CATEGORY_UNMAPPED",
      "CREATOR_CLASSIFICATION_MISSING",
      "EMAIL_AVAILABLE",
      "FOLLOWERS_IN_RANGE",
      "FOLLOWERS_MISSING",
      "FOLLOWERS_OUT_OF_RANGE",
      "FRESHNESS_MATCH",
      "FRESHNESS_MISSING",
      "FRESHNESS_NOT_MATCH",
      "NO_COMPARISON_RULE",
      "NO_CURRENT_CONTACT",
      "NO_CURRENT_EMAIL",
      "NOTES_60D_IN_RANGE",
      "NOTES_60D_OUT_OF_RANGE",
      "NOTES_7D_IN_RANGE",
      "NOTES_7D_OUT_OF_RANGE",
      "PLATFORM_MATCH",
      "PLATFORM_MISSING",
      "PLATFORM_NOT_MATCH",
      "SOURCE_MATCH",
      "SOURCE_MISSING",
      "SOURCE_NOT_MATCH",
      "TRACK_MATCH",
      "TRACK_MISSING",
      "TRACK_NOT_MATCH",
    ];
    for (const code of codes)
      expect(candidateReasonLabel(code)).not.toBe("未知判断原因");
    expect(candidateReasonLabel("FUTURE_REASON")).toBe("未知判断原因");
  });
  it("uses an allowlisted failed-run presenter", () => {
    expect(candidateRunFailureMessage("TARGETING_POLICY_MISSING")).toContain(
      "当前规则不可用",
    );
    expect(candidateRunFailureMessage("FUTURE")).toBe(
      "候选结果生成失败，请稍后查看或联系管理员。",
    );
  });
});
