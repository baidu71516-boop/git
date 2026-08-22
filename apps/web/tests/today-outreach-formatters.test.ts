import { describe, expect, it } from "vitest";

import {
  contactSummary,
  formatBusinessDate,
  formatTodayDueAt,
  priorityLabel,
} from "../src/features/outreach-today/formatters";

describe("Today outreach presenters", () => {
  it("uses the response business date and Asia/Shanghai due-time semantics", () => {
    expect(formatBusinessDate("2026-08-19")).toBe("2026年8月19日");
    expect(
      formatTodayDueAt(
        "2026-08-19T02:30:00Z",
        "2026-08-19",
        "2026-08-19T01:46:00Z",
      ),
    ).toMatchObject({ label: "今天 10:30", overdue: false });
    expect(
      formatTodayDueAt(
        "2026-08-18T01:30:00Z",
        "2026-08-19",
        "2026-08-19T01:46:00Z",
      ),
    ).toMatchObject({ overdue: true });
  });

  it("only presents the frozen contact and priority meanings", () => {
    expect(contactSummary({ has_email: true, has_contact: true })).toBe(
      "有邮箱",
    );
    expect(contactSummary({ has_email: false, has_contact: true })).toBe(
      "有联系方式",
    );
    expect(contactSummary({ has_email: false, has_contact: false })).toBe(
      "无联系方式",
    );
    expect(priorityLabel("HIGH")).toBe("高");
    expect(priorityLabel("NORMAL")).toBe("普通");
  });
});
