import { describe, expect, it } from "vitest";

import {
  contactTypeLabel,
  crmStageDisplay,
  formatExactFollowers,
  formatFollowers,
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
    expect(freshnessExplanation("unknown", true)).toBe(
      "暂无可靠采集记录，需要更新",
    );
    expect(freshnessExplanation("unknown", false)).toBe(
      "暂无可参与灰豚更新的数据",
    );
    expect(freshnessExplanation("stale", true)).toBeNull();
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
