import { describe, expect, it } from "vitest";

import { criterionObservedValue } from "../src/features/candidate-pools/candidate-run-detail-view";

describe("Grey Dolphin Long Inactivity wording", () => {
  it("labels the aggregate as coarse note-publication evidence", () => {
    expect(
      criterionObservedValue(
        { source: "GREY_DOLPHIN", notes_7d: 0, notes_60d: 0 },
        "long_inactivity",
      ),
    ).toBe("灰豚粗略判断：近60天未检测到笔记");
  });

  it("does not convert recent aggregate evidence into a generic account update claim", () => {
    expect(
      criterionObservedValue(
        { source: "GREY_DOLPHIN", notes_7d: 1, notes_60d: 1 },
        "long_inactivity",
      ),
    ).toBe("灰豚粗略判断：近7天检测到笔记");
  });

  it("distinguishes Huitun runtime exact, bounded, and unknown evidence", () => {
    expect(
      criterionObservedValue(
        { source: "HUITUN_DOUYIN_AWEME_LIST", precision: "EXACT", inactive_days: 60 },
        "long_inactivity",
      ),
    ).toBe("灰豚运行时观测：断更 60 天");
    expect(
      criterionObservedValue(
        {
          source: "HUITUN_DOUYIN_AWEME_LIST",
          precision: "LOWER_BOUND",
          lower_bound_inactive_days: 30,
        },
        "long_inactivity",
      ),
    ).toBe("灰豚运行时观测：至少断更 30 天（已验证范围）");
    expect(
      criterionObservedValue(
        { source: "HUITUN_DOUYIN_AWEME_LIST", precision: "UNKNOWN" },
        "long_inactivity",
      ),
    ).toBe("断更状态：未知 · 灰豚运行时观测未形成可信结果");
  });
});
