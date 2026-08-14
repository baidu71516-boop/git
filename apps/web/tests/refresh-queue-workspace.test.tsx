import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RefreshQueueDetail } from "../src/features/refresh-queues/refresh-queue-detail";
import { RefreshQueueList } from "../src/features/refresh-queues/refresh-queue-list";
import type { RefreshQueueStatus } from "../src/features/refresh-queues/types";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn() }),
}));

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

function queue(status: RefreshQueueStatus = "open") {
  return {
    id: `queue-${status}`,
    department_id: "department-1",
    created_by_operator_id: "operator-1",
    status,
    as_of: "2026-08-14T00:00:00Z",
    requested_limit: 100,
    today_total_limit: 150,
    refresh_limit: 120,
    policy_version: 1,
    criteria_snapshot: {},
    created_at: "2026-08-14T01:02:00Z",
    updated_at: "2026-08-14T01:02:00Z",
    exported_at: status === "exported" ? "2026-08-14T02:00:00Z" : null,
    completed_at: status === "completed" ? "2026-08-14T03:00:00Z" : null,
    cancelled_at: status === "cancelled" ? "2026-08-14T03:00:00Z" : null,
  };
}

function summary(status: RefreshQueueStatus = "open") {
  const itemStatus =
    status === "completed"
      ? "fulfilled_changed"
      : status === "cancelled"
        ? "cancelled"
        : "pending";
  return {
    requested: 100,
    selected: 1,
    unique_influencers: 1,
    freshness_breakdown: { very_stale: 1 },
    priority_breakdown: { "2": 1 },
    status_breakdown: { [itemStatus]: 1 },
  };
}

function detail(status: RefreshQueueStatus = "open") {
  return { queue: queue(status), summary: summary(status) };
}

const item = {
  id: "item-1",
  department_id: "department-1",
  queue_id: "queue-open",
  influencer_id: "influencer-1",
  platform_account_id: "account-uuid-1",
  source: "huitun",
  priority_tier: 2,
  priority_reasons: ["VERY_STALE", "FOLLOWERS_MISSING"],
  identity_snapshot: {
    schema_version: 1,
    platform: "xiaohongshu",
    account_name: "冻住的账号名",
    platform_account_id: "redbook-123",
    account_handle: "frozen_handle",
    profile_url: null,
    external_source_id: null,
    followers_count: null,
  },
  baseline_last_observed_at: "2026-05-01T00:00:00Z",
  baseline_source_updated_at: "2026-05-01T00:00:00Z",
  status: "pending",
  fulfilled_import_job_id: null,
  fulfilled_import_row_id: null,
  fulfilled_at: null,
  last_return_import_job_id: null,
  last_return_import_row_id: null,
  created_at: "2026-08-14T01:02:00Z",
  updated_at: "2026-08-14T01:02:00Z",
};

function renderWithQueryClient(ui: React.ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

function mockDetailBackend(status: RefreshQueueStatus = "open", total = 1) {
  return vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith(`/refresh-queues/queue-${status}`) && !init?.method) {
        return envelope(detail(status));
      }
      if (url.includes(`/refresh-queues/queue-${status}/items?`)) {
        const offset = Number(
          new URL(url, "http://test").searchParams.get("offset"),
        );
        return envelope({
          items:
            offset >= total ? [] : [{ ...item, queue_id: `queue-${status}` }],
          total,
          offset,
          limit: 50,
        });
      }
      if (
        url.endsWith(`/refresh-queues/queue-${status}/cancel`) &&
        init?.method === "POST"
      ) {
        return envelope(detail("cancelled"));
      }
      throw new Error(`Unexpected request: ${url} ${init?.method ?? "GET"}`);
    });
}

afterEach(() => {
  vi.restoreAllMocks();
  push.mockReset();
  document.cookie = "outreach_csrf=; Max-Age=0; path=/";
});

describe("Refresh Queue list and create", () => {
  it("renders the compact Queue list with API fields", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      envelope({ items: [queue()], total: 1, offset: 0, limit: 50 }),
    );

    renderWithQueryClient(
      <RefreshQueueList
        role="operator"
        departments={[]}
        currentDepartmentId="department-1"
      />,
    );

    expect(
      screen.getByText("集中管理需要重新采集的达人数据名单。"),
    ).toBeInTheDocument();
    expect(await screen.findByText("待导出")).toBeInTheDocument();
    expect(
      screen.getByRole("columnheader", { name: "创建时间" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("columnheader", { name: "状态" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("columnheader", { name: "目标数量" }),
    ).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/搜索/)).not.toBeInTheDocument();
    expect(screen.queryByText("回流进度")).not.toBeInTheDocument();
  });

  it("requests the next Queue list page from the server", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input) => {
        const url = new URL(String(input), "http://test");
        const offset = Number(url.searchParams.get("offset"));
        return envelope({
          items: [queue()],
          total: 51,
          offset,
          limit: 50,
        });
      });

    const { container } = renderWithQueryClient(
      <RefreshQueueList
        role="operator"
        departments={[]}
        currentDepartmentId="department-1"
      />,
    );
    await screen.findByText("待导出");
    const nextButton = container.querySelector<HTMLButtonElement>(
      ".refresh-queue-list-card .ant-pagination-next button",
    );
    expect(nextButton).not.toBeNull();
    fireEvent.click(nextButton as HTMLButtonElement);

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) =>
          String(input).includes("offset=50&limit=50"),
        ),
      ).toBe(true),
    );
  });

  it("creates with the exact three-field body and does not expose a department for Operators", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        envelope({ items: [], total: 0, offset: 0, limit: 50 }),
      )
      .mockResolvedValueOnce(envelope(detail(), 201));

    renderWithQueryClient(
      <RefreshQueueList
        role="operator"
        departments={[]}
        currentDepartmentId="department-1"
      />,
    );
    await screen.findByText("暂无数据更新名单。");
    fireEvent.click(screen.getByRole("button", { name: /创建更新名单/ }));

    expect(screen.queryByLabelText("目标部门")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("目标更新数量"), {
      target: { value: "10" },
    });
    fireEvent.change(screen.getByLabelText("本次更新上限"), {
      target: { value: "20" },
    });
    fireEvent.change(screen.getByLabelText("今日计划总上限"), {
      target: { value: "30" },
    });
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "创建更新名单",
      }),
    );

    await waitFor(() =>
      expect(push).toHaveBeenCalledWith("/refresh-queues/queue-open"),
    );
    const createCall = fetchMock.mock.calls.find(
      ([input, init]) =>
        String(input).endsWith("/refresh-queues") && init?.method === "POST",
    );
    expect(JSON.parse(String(createCall?.[1]?.body))).toEqual({
      requested_limit: 10,
      refresh_limit: 20,
      today_total_limit: 30,
    });
  });

  it("adds target department only for Super Admin", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      envelope({ items: [], total: 0, offset: 0, limit: 50 }),
    );

    renderWithQueryClient(
      <RefreshQueueList
        role="super_admin"
        departments={[
          { id: "department-1", name: "当前部门" },
          { id: "department-2", name: "目标部门" },
        ]}
        currentDepartmentId="department-1"
      />,
    );
    await screen.findByText("暂无数据更新名单。");
    fireEvent.click(screen.getByRole("button", { name: /创建更新名单/ }));

    expect(screen.getByLabelText("目标部门")).toBeInTheDocument();
  });

  it("shows the safe ambiguous outcome and performs no automatic create retry", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        envelope({ items: [], total: 0, offset: 0, limit: 50 }),
      )
      .mockRejectedValueOnce(new TypeError("network failed"));

    renderWithQueryClient(
      <RefreshQueueList
        role="operator"
        departments={[]}
        currentDepartmentId="department-1"
      />,
    );
    await screen.findByText("暂无数据更新名单。");
    fireEvent.click(screen.getByRole("button", { name: /创建更新名单/ }));
    fireEvent.change(screen.getByLabelText("目标更新数量"), {
      target: { value: "10" },
    });
    fireEvent.change(screen.getByLabelText("本次更新上限"), {
      target: { value: "20" },
    });
    fireEvent.change(screen.getByLabelText("今日计划总上限"), {
      target: { value: "30" },
    });
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "创建更新名单",
      }),
    );

    expect(
      await screen.findByText(
        "无法确认更新名单是否创建成功，请先刷新数据更新列表确认，避免重复创建。",
      ),
    ).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.filter(([, init]) => init?.method === "POST"),
    ).toHaveLength(1);
  });

  it("keeps Viewer read-only", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      envelope({ items: [queue()], total: 1, offset: 0, limit: 50 }),
    );

    renderWithQueryClient(
      <RefreshQueueList
        role="viewer"
        departments={[]}
        currentDepartmentId="department-1"
      />,
    );

    await screen.findByText("待导出");
    expect(
      screen.queryByRole("button", { name: /创建更新名单/ }),
    ).not.toBeInTheDocument();
  });
});

describe("Refresh Queue detail", () => {
  it("renders real summary and frozen Item snapshot fields", async () => {
    mockDetailBackend();
    renderWithQueryClient(
      <RefreshQueueDetail queueId="queue-open" role="operator" />,
    );

    expect(
      await screen.findByRole("heading", { name: "数据更新名单" }),
    ).toBeInTheDocument();
    expect(screen.getByText("请求数量")).toBeInTheDocument();
    expect(screen.getByText("实际选中")).toBeInTheDocument();
    expect(screen.getByText("涉及达人")).toBeInTheDocument();
    expect(await screen.findByText("冻住的账号名")).toBeInTheDocument();
    expect(screen.getByText(/frozen_handle/)).toBeInTheDocument();
    expect(
      screen.getByRole("columnheader", { name: "创建时数据时效" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("严重陈旧").length).toBeGreaterThan(0);
    expect(screen.getAllByText("缺少粉丝数").length).toBeGreaterThan(0);
    expect(screen.getAllByText("待回流").length).toBeGreaterThan(0);
    expect(screen.queryByText("上传回流文件")).not.toBeInTheDocument();
    expect(screen.queryByText("查看回流证据")).not.toBeInTheDocument();
    expect(screen.queryByText("回流处理")).not.toBeInTheDocument();
  });

  it("requests the next Item page from the server", async () => {
    const fetchMock = mockDetailBackend("open", 51);
    const { container } = renderWithQueryClient(
      <RefreshQueueDetail queueId="queue-open" role="operator" />,
    );
    await screen.findByText("冻住的账号名");
    const nextButton = container.querySelector<HTMLButtonElement>(
      ".refresh-queue-items-card .ant-pagination-next button",
    );
    expect(nextButton).not.toBeNull();
    fireEvent.click(nextButton as HTMLButtonElement);

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) =>
          String(input).includes("/items?offset=50&limit=50"),
        ),
      ).toBe(true),
    );
  });

  it("requires the exact cancellation confirmation and cancels only an active Queue", async () => {
    document.cookie = "outreach_csrf=csrf-cancel; path=/";
    const fetchMock = mockDetailBackend();
    renderWithQueryClient(
      <RefreshQueueDetail queueId="queue-open" role="operator" />,
    );
    await screen.findByText("冻住的账号名");
    fireEvent.click(screen.getByRole("button", { name: /取消名单/ }));

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("取消这个更新名单？")).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        "尚未完成的条目将标记为已取消，已经完成的回流结果不会被撤销。",
      ),
    ).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "确认取消" }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([input, init]) =>
            String(input).endsWith("/queue-open/cancel") &&
            init?.method === "POST",
        ),
      ).toBe(true),
    );
  });

  it.each([
    ["exported", "重新导出 CSV", true],
    ["completed", "导出 CSV", false],
    ["cancelled", "导出 CSV", false],
  ] as const)(
    "shows only state-allowed actions for %s",
    async (status, exportLabel, shouldShow) => {
      mockDetailBackend(status);
      renderWithQueryClient(
        <RefreshQueueDetail queueId={`queue-${status}`} role="operator" />,
      );
      await screen.findByText("冻住的账号名");
      const exportButton = screen.queryByRole("button", {
        name: new RegExp(exportLabel),
      });
      expect(Boolean(exportButton)).toBe(shouldShow);
      expect(Boolean(screen.queryByRole("button", { name: /取消名单/ }))).toBe(
        shouldShow,
      );
    },
  );

  it("shows no mutation actions to Viewer", async () => {
    mockDetailBackend();
    renderWithQueryClient(
      <RefreshQueueDetail queueId="queue-open" role="viewer" />,
    );
    await screen.findByText("冻住的账号名");
    expect(
      screen.queryByRole("button", { name: /导出 CSV/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /取消名单/ }),
    ).not.toBeInTheDocument();
  });
});
