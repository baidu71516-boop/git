import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TodayWorkspace } from "../src/features/outreach-today/today-workspace";

const firstItem = {
  task_id: "task-1",
  target_id: "target-1",
  campaign_id: "campaign-1",
  campaign_name: "春季数码合作项目",
  member_id: "member-1",
  influencer_id: "influencer-1",
  crm_stage: "待开发",
  preferred_platform_account: {
    id: "account-1",
    platform: "xiaohongshu",
    account_name: "数码老李",
    account_handle: "@shumaolaoli",
    source_tags: ["数码"],
    followers_count: 100000,
  },
  assigned_operator_id: "operator-1",
  kind: "FIRST_TOUCH" as const,
  state: "READY" as const,
  channel: "EMAIL" as const,
  priority: "HIGH" as const,
  due_at: "2026-08-19T00:30:00Z",
  version: 1,
  has_contact: true,
  has_email: true,
  masked_target_display: "***",
  history_warning: {
    channel: "EMAIL" as const,
    last_sent_at: "2026-08-18T01:00:00Z",
  },
};

function envelope(data: unknown, status = 200) {
  return new Response(
    JSON.stringify({
      success: status < 400,
      data: status < 400 ? data : null,
      error:
        status < 400
          ? null
          : { code: "TEST_ERROR", message: "failed", details: null },
      request_id: "today-workspace-test",
    }),
    { status, headers: { "Content-Type": "application/json" } },
  );
}

function renderWorkspace() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <TodayWorkspace />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("TodayWorkspace", () => {
  it("renders the frozen read-only queue and appends the next cursor page in backend order", async () => {
    let todayCalls = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/outreach-tasks/today")) {
        todayCalls += 1;
        return envelope({
          business_date: "2026-08-19",
          timezone: "Asia/Shanghai",
          as_of: "2026-08-19T01:46:00Z",
          items:
            todayCalls === 1
              ? [firstItem]
              : [
                  {
                    ...firstItem,
                    task_id: "task-2",
                    preferred_platform_account: {
                      ...firstItem.preferred_platform_account,
                      account_name: "摄影小周",
                    },
                  },
                ],
          next_cursor: todayCalls === 1 ? "opaque-next" : null,
        });
      }
      if (url.includes("/operators"))
        return envelope([
          { id: "operator-1", name: "王小明", status: "active" },
        ]);
      if (url.includes("/campaigns"))
        return envelope({
          items: [
            { id: "campaign-1", name: "春季数码合作项目", status: "ACTIVE" },
          ],
          next_cursor: null,
        });
      if (url.includes("/influencers/filter-options"))
        return envelope({ tags: ["数码"] });
      throw new Error(`Unexpected URL: ${url}`);
    });

    renderWorkspace();
    expect(await screen.findByText("数码老李")).toBeInTheDocument();
    expect(
      screen.getByText("日期：2026年8月19日 · 更新于 09:46"),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /数码老李/ })).toHaveAttribute(
      "href",
      "/influencers/influencer-1",
    );
    for (const column of [
      "达人账号",
      "Campaign",
      "当前任务",
      "渠道",
      "到期时间",
      "联系方式",
      "优先级",
      "负责人",
    ]) {
      expect(
        screen.getByRole("columnheader", { name: column }),
      ).toBeInTheDocument();
    }
    expect(
      screen.queryByRole("columnheader", { name: "操作" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/Review|发送|AI|Inbox|Sequence/),
    ).not.toBeInTheDocument();
    expect(screen.getByText("曾有历史触达")).toBeInTheDocument();
    expect(screen.getByText("有邮箱")).toBeInTheDocument();
    expect(screen.getByText("高")).toBeInTheDocument();
    expect(screen.getByText("王小明")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "加载更多" }));
    expect(await screen.findByText("摄影小周")).toBeInTheDocument();
    expect(
      Array.from(document.querySelectorAll(".today-account-name")).map(
        (node) => node.textContent,
      ),
    ).toEqual(["数码老李", "摄影小周"]);
  });

  it("hides unavailable canonical selectors without blocking the Today list", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/outreach-tasks/today"))
        return envelope({
          business_date: "2026-08-19",
          timezone: "Asia/Shanghai",
          as_of: "2026-08-19T01:46:00Z",
          items: [],
          next_cursor: null,
        });
      return envelope(null, 403);
    });

    renderWorkspace();
    expect(
      await screen.findByText("今天没有待处理的触达任务"),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Campaign")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("负责人")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("赛道")).not.toBeInTheDocument();
  });

  it("resets the list from the first page when task type changes", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input) => {
        const url = String(input);
        if (url.includes("/outreach-tasks/today"))
          return envelope({
            business_date: "2026-08-19",
            timezone: "Asia/Shanghai",
            as_of: "2026-08-19T01:46:00Z",
            items: [],
            next_cursor: null,
          });
        if (url.includes("/operators")) return envelope([]);
        if (url.includes("/campaigns"))
          return envelope({ items: [], next_cursor: null });
        return envelope({ tags: [] });
      });

    renderWorkspace();
    await screen.findByText("今天没有待处理的触达任务");
    fireEvent.click(screen.getByRole("tab", { name: "首次触达" }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) =>
          String(url).includes("work_kind=FIRST_TOUCH"),
        ),
      ).toBe(true),
    );
  });
});
