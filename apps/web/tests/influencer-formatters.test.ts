import { describe, expect, it } from "vitest";

import {
  contactTypeLabel,
  contentActivityDisplay,
  contentActivityCoverageLabel,
  contentActivityLatestAttemptLabel,
  contentActivityTrustedResultLabel,
  crmStageDisplay,
  formatExactFollowers,
  formatFollowers,
  freshnessSupportText,
  freshnessDisplay,
  freshnessExplanation,
  formatMetricsTimestamp,
  formatMetricsTimestampTooltip,
  latestMetricsTimestamp,
  platformLabel,
} from "../src/features/influencers/formatters";

describe("influencer list formatters", () => {
  it.each([
    [null, "—"],
    [0, "0"],
    [9999, "9,999"],
    [10000, "1万"],
    [206572, "20.66万"],
  ])("formats follower value %s", (value, expected) => {
    expect(formatFollowers(value)).toBe(expected);
  });

  it("keeps an exact follower value for tooltips", () => {
    expect(formatExactFollowers(206572)).toBe("206,572");
  });

  it("uses centralized platform, contact, and CRM labels", () => {
    expect(platformLabel("xiaohongshu")).toBe("小红书");
    expect(contactTypeLabel("email")).toBe("邮箱");
    expect(contactTypeLabel("phone")).toBe("手机");
    expect(crmStageDisplay("高意向")).toEqual({
      label: "高意向",
      color: "orange",
    });
    expect(crmStageDisplay(undefined)).toEqual({ label: "未设置" });
  });

  it.each([
    ["fresh", "新鲜"],
    ["aging", "较旧"],
    ["stale", "陈旧"],
    ["very_stale", "严重陈旧"],
    ["unknown", "未知"],
  ] as const)("maps backend freshness status %s", (status, label) => {
    expect(freshnessDisplay(status).label).toBe(label);
  });

  it("keeps both backend-defined unknown semantics as explanations", () => {
    expect(freshnessSupportText("unknown", true)).toBe("暂无可靠采集记录");
    expect(freshnessSupportText("unknown", false)).toBe("暂无可更新数据");
    expect(freshnessExplanation("unknown", true)).toBe(
      "暂无可靠采集记录，建议加入数据更新名单。",
    );
    expect(freshnessExplanation("unknown", false)).toBe(
      "暂无可参与当前灰豚更新流程的数据。",
    );
    expect(freshnessExplanation("stale", true)).toBeNull();
  });

  it("keeps trusted, no-public, and uncertain Content Activity states distinct", () => {
    expect(contentActivityDisplay("current", "PUBLICATION_FOUND")).toEqual({
      label: "可信",
      tone: "success",
    });
    expect(contentActivityDisplay("current", "NO_PUBLIC_CONTENT")).toEqual({
      label: "当前无公开作品",
      tone: "success",
    });
    expect(contentActivityDisplay("unknown", null)).toEqual({
      label: "当前未知",
      tone: "warning",
    });
    expect(contentActivityDisplay("last_known", "PUBLICATION_FOUND")).toEqual({
      label: "最后可信结果",
      tone: "warning",
    });
    expect(
      contentActivityLatestAttemptLabel("RESULT_INCOMPLETE", "UNDETERMINED"),
    ).toBe("结果不完整");
    expect(
      contentActivityLatestAttemptLabel("COMPLETE", "NO_PUBLIC_CONTENT"),
    ).toBe("已完成：无公开作品");
    expect(contentActivityTrustedResultLabel("PUBLICATION_FOUND")).toBe(
      "发现公开作品",
    );
    expect(contentActivityTrustedResultLabel("NO_PUBLIC_CONTENT")).toBe(
      "无公开作品",
    );
    expect(contentActivityCoverageLabel("FULL_CURRENT_PUBLIC_SET")).toBe(
      "完整当前公开作品集",
    );
    expect(contentActivityCoverageLabel("INCOMPLETE")).toBe("覆盖不完整");
    expect(contentActivityCoverageLabel("UNKNOWN")).toBe("覆盖范围未知");
  });

  it("selects the latest real metrics source timestamp", () => {
    expect(
      latestMetricsTimestamp([
        {
          platform_account_id: "account-1",
          source: "huitun",
          source_updated_at: "2026-08-03T06:28:00Z",
          followers_count: 1,
        },
        {
          platform_account_id: "account-2",
          source: "huitun",
          source_updated_at: "2026-08-09T06:28:00Z",
          followers_count: 2,
        },
        {
          platform_account_id: "account-3",
          source: "huitun",
          source_updated_at: null,
          followers_count: 3,
        },
      ]),
    ).toBe("2026-08-09T06:28:00Z");
    expect(latestMetricsTimestamp([])).toBeNull();
  });

  it("formats metrics timestamps in the business timezone", () => {
    const now = new Date("2026-08-12T10:00:00+08:00");
    expect(formatMetricsTimestamp(null, now)).toBe("—");
    expect(formatMetricsTimestamp("2026-08-12T14:20:00+08:00", now)).toBe(
      "今天 14:20",
    );
    expect(formatMetricsTimestamp("2026-08-11T16:42:00+08:00", now)).toBe(
      "昨天 16:42",
    );
    expect(formatMetricsTimestamp("2026-08-09T14:28:00+08:00", now)).toBe(
      "3天前",
    );
    expect(formatMetricsTimestamp("2026-08-03T14:28:00+08:00", now)).toBe(
      "08-03",
    );
    expect(formatMetricsTimestampTooltip("2026-08-03T14:28:00+08:00")).toBe(
      "2026-08-03 14:28",
    );
  });
});
