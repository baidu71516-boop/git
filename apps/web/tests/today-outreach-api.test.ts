import { afterEach, describe, expect, it, vi } from "vitest";

import {
  buildTodayPath,
  fetchTodayPage,
  TODAY_PAGE_LIMIT,
} from "../src/features/outreach-today/api";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("today outreach API", () => {
  const filters = {
    work_kind: "FIRST_TOUCH" as const,
    channel: "EMAIL" as const,
    campaign_id: "campaign-1",
    owner_operator_id: "operator-1",
    track: "数码",
    followers_min: 1000,
    followers_max: 10_000,
    contact_filter: "has_email" as const,
    priority: "HIGH" as const,
  };

  it("serializes only the frozen Today fields with the contract default limit", () => {
    const path = buildTodayPath({
      ...filters,
      unknown: "never",
    } as typeof filters & { unknown: string });
    const url = new URL(path, "https://example.invalid");

    expect(url.pathname).toBe("/outreach-tasks/today");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      ...Object.fromEntries(
        Object.entries(filters).map(([key, value]) => [key, String(value)]),
      ),
      limit: String(TODAY_PAGE_LIMIT),
    });
    expect(path).not.toContain("unknown");
  });

  it("passes the cursor through as an opaque continuation token", async () => {
    const cursor = "v1.opaque.hmac.cursor";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          success: true,
          data: {
            business_date: "2026-08-19",
            timezone: "Asia/Shanghai",
            as_of: "2026-08-19T01:46:00Z",
            items: [],
            next_cursor: null,
          },
          error: null,
          request_id: "today-test",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    await fetchTodayPage({ work_kind: "ALL" }, cursor);

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      `/api/v1/outreach-tasks/today?work_kind=ALL&limit=50&cursor=${encodeURIComponent(cursor)}`,
    );
  });
});
