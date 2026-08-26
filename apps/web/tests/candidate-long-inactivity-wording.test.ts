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
});
