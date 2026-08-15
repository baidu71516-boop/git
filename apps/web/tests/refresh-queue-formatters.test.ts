import { describe, expect, it, vi } from "vitest";

import {
  downloadBlob,
  filenameFromContentDisposition,
  formatRefreshDateTime,
  freshnessLabel,
  itemStatusPresentation,
  priorityReasonLabel,
  queueStatusPresentation,
  snapshotFreshnessFromReasons,
} from "../src/features/refresh-queues/formatters";

describe("refresh queue formatters", () => {
  it("maps every Queue and return status to frozen Chinese copy", () => {
    expect(queueStatusPresentation("open").label).toBe("待导出");
    expect(queueStatusPresentation("exported").label).toBe("已导出");
    expect(queueStatusPresentation("completed").label).toBe("已完成");
    expect(queueStatusPresentation("cancelled").label).toBe("已取消");
    expect(itemStatusPresentation("pending").label).toBe("待回流");
    expect(itemStatusPresentation("fulfilled_changed").label).toBe(
      "已回流 · 有变更",
    );
    expect(itemStatusPresentation("fulfilled_no_change").label).toBe(
      "已回流 · 无变更",
    );
    expect(itemStatusPresentation("stale_return").label).toBe("回流数据已过期");
    expect(itemStatusPresentation("unresolved").label).toBe("无法确认");
  });

  it("maps snapshot Freshness and priority reasons without time recalculation", () => {
    expect(snapshotFreshnessFromReasons(["FRESHNESS_UNKNOWN"])).toBe("unknown");
    expect(snapshotFreshnessFromReasons(["VERY_STALE"])).toBe("very_stale");
    expect(snapshotFreshnessFromReasons(["STALE"])).toBe("stale");
    expect(snapshotFreshnessFromReasons(["AGING"])).toBe("aging");
    expect(snapshotFreshnessFromReasons(["FOLLOWERS_MISSING"])).toBe("fresh");
    expect(freshnessLabel("fresh")).toBe("新鲜");
    expect(freshnessLabel("very_stale")).toBe("严重陈旧");
    expect(priorityReasonLabel("FRESHNESS_UNKNOWN")).toBe("数据时效未知");
    expect(priorityReasonLabel("FOLLOWERS_MISSING")).toBe("缺少粉丝数");
  });

  it("uses the existing Asia/Shanghai minute formatter", () => {
    expect(formatRefreshDateTime("2026-08-14T00:05:59Z")).toBe(
      "2026-08-14 08:05",
    );
  });

  it("honors plain and UTF-8 Content-Disposition filenames", () => {
    expect(
      filenameFromContentDisposition(
        'attachment; filename="refresh-queue-1.csv"',
        "fallback.csv",
      ),
    ).toBe("refresh-queue-1.csv");
    expect(
      filenameFromContentDisposition(
        "attachment; filename*=UTF-8''%E6%95%B0%E6%8D%AE.csv",
        "fallback.csv",
      ),
    ).toBe("数据.csv");
    expect(filenameFromContentDisposition(null, "fallback.csv")).toBe(
      "fallback.csv",
    );
  });

  it("creates and revokes an object URL for the CSV", () => {
    const createObjectURL = vi.fn(() => "blob:test");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: createObjectURL,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: revokeObjectURL,
    });
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);

    downloadBlob(new Blob(["csv"]), "queue.csv");

    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(click).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:test");
  });
});
