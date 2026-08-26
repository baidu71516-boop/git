import { afterEach, describe, expect, it, vi } from "vitest";

import {
  cancelRefreshQueue,
  createRefreshQueue,
  exportRefreshQueue,
  getRefreshQueue,
  listRefreshQueueItems,
  listRefreshQueues,
} from "../src/features/refresh-queues/api";

function envelope(data: unknown, status = 200) {
  return new Response(
    JSON.stringify({
      success: status < 400,
      data: status < 400 ? data : null,
      error:
        status < 400
          ? null
          : { code: "TEST_ERROR", message: "failed", details: null },
      request_id: "request-test",
    }),
    { status, headers: { "Content-Type": "application/json" } },
  );
}

const detail = {
  queue: { id: "queue-1", status: "open" },
  summary: {
    requested: 10,
    selected: 8,
    unique_influencers: 7,
    freshness_breakdown: { stale: 8 },
    priority_breakdown: { "3": 8 },
    status_breakdown: { pending: 8 },
  },
};

afterEach(() => {
  vi.restoreAllMocks();
  document.cookie = "outreach_csrf=; Max-Age=0; path=/";
});

describe("refresh queue API", () => {
  it("uses exact Queue list pagination parameters", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        envelope({ items: [], total: 0, offset: 50, limit: 50 }),
      );

    await listRefreshQueues(50, 50);

    expect(fetchMock).toHaveBeenCalledOnce();
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/v1/refresh-queues?offset=50&limit=50",
    );
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      credentials: "include",
    });
  });

  it("sends only the exact create body and includes CSRF", async () => {
    document.cookie = "outreach_csrf=csrf-refresh; path=/";
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(envelope(detail, 201));
    const input = {
      department_id: "department-2",
      requested_limit: 10,
      refresh_limit: 20,
      today_total_limit: 30,
    };

    await createRefreshQueue(input);

    const init = fetchMock.mock.calls[0]?.[1];
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual(input);
    expect(new Headers(init?.headers).get("X-CSRF-Token")).toBe("csrf-refresh");
  });

  it("uses the detail and server-paginated Item endpoints", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(envelope(detail))
      .mockResolvedValueOnce(
        envelope({ items: [], total: 201, offset: 100, limit: 100 }),
      );

    await getRefreshQueue("queue/unsafe");
    await listRefreshQueueItems("queue/unsafe", 100, 100);

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/v1/refresh-queues/queue%2Funsafe",
    );
    expect(String(fetchMock.mock.calls[1]?.[0])).toBe(
      "/api/v1/refresh-queues/queue%2Funsafe/items?offset=100&limit=100",
    );
  });

  it("downloads CSV as a blob and preserves Content-Disposition", async () => {
    document.cookie = "outreach_csrf=csrf-export; path=/";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("account_name\nfixture", {
        status: 200,
        headers: {
          "Content-Type": "text/csv",
          "Content-Disposition": 'attachment; filename="frozen-queue.csv"',
        },
      }),
    );

    const result = await exportRefreshQueue("queue-1");

    expect(typeof result.blob.text).toBe("function");
    expect(result.blob.type).toBe("text/csv");
    expect(result.blob.size).toBeGreaterThan(0);
    expect(await result.blob.text()).toBe("account_name\nfixture");
    expect(result.contentDisposition).toBe(
      'attachment; filename="frozen-queue.csv"',
    );
    const init = fetchMock.mock.calls[0]?.[1];
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("X-CSRF-Token")).toBe("csrf-export");
  });

  it("uses POST and CSRF for cancellation", async () => {
    document.cookie = "outreach_csrf=csrf-cancel; path=/";
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        envelope({ ...detail, queue: { id: "queue-1", status: "cancelled" } }),
      );

    await cancelRefreshQueue("queue-1");

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/v1/refresh-queues/queue-1/cancel",
    );
    const init = fetchMock.mock.calls[0]?.[1];
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("X-CSRF-Token")).toBe("csrf-cancel");
  });
});
