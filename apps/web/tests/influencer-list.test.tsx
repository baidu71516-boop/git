import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { InfluencerWorkspace } from "../src/features/influencers/influencer-workspace";

const navigation = vi.hoisted(() => ({
  search: "",
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/influencers",
  useRouter: () => ({ replace: navigation.replace }),
  useSearchParams: () => new URLSearchParams(navigation.search),
}));

const listData = {
  items: [
    {
      id: "00000000-0000-0000-0000-000000000101",
      display_name: "零粉多账号达人",
      status: "active",
      crm_stage: "高意向",
      freshness_status: "stale",
      requires_refresh: true,
      owner: {
        id: "00000000-0000-0000-0000-000000000001",
        name: "已停用负责人",
        status: "disabled",
      },
      platform_accounts: [
        {
          id: "account-1",
          platform: "xiaohongshu",
          platform_account_id: "xhs-1",
          account_name: "账号甲",
          account_handle: "handle-a",
          profile_url: "https://example.invalid/a",
          source: "huitun",
          is_active: true,
          source_tags: ["动画", "二次元", "生活方式", "超长标签".repeat(41)],
          last_huitun_observed_at: "2026-07-01T08:00:00Z",
          last_huitun_imported_at: "2026-08-02T08:00:00Z",
          freshness_status: "stale",
          freshness_age_days: 41,
          requires_refresh: true,
        },
        {
          id: "account-2",
          platform: "xiaohongshu",
          platform_account_id: "xhs-2",
          account_name: "账号乙",
          account_handle: null,
          profile_url: null,
          source: "huitun",
          is_active: true,
          source_tags: [],
          last_huitun_observed_at: null,
          last_huitun_imported_at: null,
          freshness_status: "unknown",
          freshness_age_days: null,
          requires_refresh: true,
        },
      ],
      current_metrics: [
        {
          platform_account_id: "account-1",
          source: "huitun",
          source_updated_at: "2026-08-03T06:28:00Z",
          followers_count: 206572,
        },
        {
          platform_account_id: "account-2",
          source: "huitun",
          source_updated_at: null,
          followers_count: null,
        },
      ],
      current_contacts: [
        {
          id: "contact-1",
          type: "email",
          display_value: "***",
          source: "manual",
          validation_status: "unverified",
          possible_duplicate_contact: true,
        },
        {
          id: "contact-2",
          type: "phone",
          display_value: "12345678901",
          source: "manual",
          validation_status: "unverified",
          possible_duplicate_contact: false,
        },
      ],
      possible_duplicate_contact: true,
      created_at: "2026-08-11T00:00:00Z",
      updated_at: "2026-08-11T00:00:00Z",
    },
  ],
  page: 2,
  page_size: 50,
  total: 120,
};

const filterOptions = {
  owners: [
    {
      id: "00000000-0000-0000-0000-000000000001",
      name: "已停用负责人",
      status: "disabled",
    },
  ],
  tags: ["动画", "超长标签".repeat(41)],
  crm_stages: ["待开发", "高意向"],
};

function envelope(data: unknown, status = 200) {
  return new Response(
    JSON.stringify({
      success: status < 400,
      data: status < 400 ? data : null,
      error:
        status < 400
          ? null
          : {
              code: status === 422 ? "VALIDATION_ERROR" : "INTERNAL_ERROR",
              message: "Request failed",
              details: null,
            },
      request_id: "request-test",
    }),
    { status, headers: { "Content-Type": "application/json" } },
  );
}

function mockApi(list: unknown = listData, options: unknown = filterOptions) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes("/influencers/filter-options")) {
      return options instanceof Response ? options.clone() : envelope(options);
    }
    if (url.includes("/influencers")) {
      return list instanceof Response ? list.clone() : envelope(list);
    }
    throw new Error(`Unexpected request: ${url}`);
  });
}

function renderWorkspace() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <InfluencerWorkspace />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  navigation.search = "";
  navigation.replace.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("InfluencerWorkspace", () => {
  it("shows independent loading, list error retry, and empty states", async () => {
    let resolveList: ((value: Response) => void) | undefined;
    let resolveOptions: ((value: Response) => void) | undefined;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input).includes("filter-options")) {
        return new Promise<Response>((resolve) => {
          resolveOptions = resolve;
        });
      }
      return new Promise<Response>((resolve) => {
        resolveList = resolve;
      });
    });
    const first = renderWorkspace();
    expect(
      screen.getByText("公司共享达人资源 · 统一查看、筛选和管理"),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /导入达人/ })).toHaveAttribute(
      "href",
      "/",
    );
    expect(screen.getByText("正在加载达人列表")).toBeInTheDocument();
    expect(screen.getByText("正在加载筛选选项")).toBeInTheDocument();
    resolveOptions?.(envelope(filterOptions));
    resolveList?.(envelope({ items: [], page: 1, page_size: 50, total: 0 }));
    expect(
      await screen.findByText("达人库暂无数据，可以先从数据采集导入达人。"),
    ).toBeInTheDocument();
    first.unmount();

    const fetchMock = mockApi(envelope(null, 500));
    renderWorkspace();
    expect(
      await screen.findByText("达人数据加载失败", {}, { timeout: 3000 }),
    ).toBeInTheDocument();
    const retry = screen.getByRole("button", { name: "重新加载" });
    const callsBeforeRetry = fetchMock.mock.calls.length;
    fireEvent.click(retry);
    await waitFor(() =>
      expect(fetchMock.mock.calls.length).toBeGreaterThan(callsBeforeRetry),
    );
  });

  it("renders one influencer row with multiple accounts and exact API values", async () => {
    navigation.search = "page=2&page_size=50";
    mockApi();
    renderWorkspace();

    await screen.findByRole("link", { name: "零粉多账号达人" });
    const rows =
      document.querySelectorAll<HTMLTableRowElement>(".influencer-row");
    expect(rows).toHaveLength(1);
    const row = rows[0] as HTMLTableRowElement;
    expect(within(row).getByText(/账号甲/)).toBeInTheDocument();
    expect(within(row).getByText("小红书")).toBeInTheDocument();
    expect(within(row).getByText("动画")).toBeInTheDocument();
    expect(within(row).getByText("二次元")).toBeInTheDocument();
    expect(within(row).getByText("+2")).toBeInTheDocument();
    expect(within(row).getByText("20.66万")).toBeInTheDocument();
    expect(within(row).getByText("高意向")).toBeInTheDocument();
    expect(within(row).getByText("陈旧")).toBeInTheDocument();
    expect(within(row).getByText("距上次采集 41 天")).toBeInTheDocument();
    expect(within(row).getByText("邮箱 · 手机")).toBeInTheDocument();
    expect(within(row).getByText("需核对")).toBeInTheDocument();
    expect(within(row).queryByText("***")).not.toBeInTheDocument();
    expect(within(row).queryByText("12345678901")).not.toBeInTheDocument();
    expect(within(row).getByText("08-03")).toBeInTheDocument();
    fireEvent.mouseOver(within(row).getByText("20.66万"));
    expect(await screen.findByText("206,572")).toBeInTheDocument();
    expect(screen.getByText("共 120 位达人")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下一页" })).toBeEnabled();
    expect(
      screen.getByRole("link", { name: "零粉多账号达人" }),
    ).toHaveAttribute(
      "href",
      "/influencers/00000000-0000-0000-0000-000000000101",
    );
    expect(within(row).getByRole("link", { name: "查看" })).toHaveAttribute(
      "href",
      "/influencers/00000000-0000-0000-0000-000000000101",
    );
  });

  it("restores URL filters and resets the page for search and filters", async () => {
    navigation.search =
      "q=%E6%97%A7%E6%90%9C%E7%B4%A2&tag=%E5%8A%A8%E7%94%BB&followers_min=0&followers_max=100&owner_operator_id=00000000-0000-0000-0000-000000000001&crm_stage=%E9%AB%98%E6%84%8F%E5%90%91&freshness_status=very_stale&requires_refresh=false&page=3&page_size=50";
    mockApi();
    renderWorkspace();

    const search = await screen.findByRole("textbox", { name: "昵称搜索" });
    expect(search).toHaveValue("旧搜索");
    expect(search).toHaveAttribute("placeholder", "搜索达人昵称或账号名");
    expect(search).toHaveAttribute("maxlength", "160");
    expect(screen.getByText("严重陈旧")).toBeInTheDocument();
    expect(screen.getByText("暂不需要")).toBeInTheDocument();
    fireEvent.change(search, { target: { value: "  新搜索  " } });
    fireEvent.click(screen.getByRole("button", { name: /搜.*索/ }));
    expect(navigation.replace).toHaveBeenLastCalledWith(
      expect.stringContaining("q=%E6%96%B0%E6%90%9C%E7%B4%A2"),
    );
    expect(navigation.replace).toHaveBeenLastCalledWith(
      expect.not.stringContaining("page=3"),
    );

    fireEvent.change(search, { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: /搜.*索/ }));
    expect(navigation.replace).toHaveBeenLastCalledWith(
      expect.not.stringContaining("q="),
    );
  });

  it("validates follower ranges locally and supports clearing filters", async () => {
    navigation.search = "followers_min=100&followers_max=10&page=4";
    mockApi();
    renderWorkspace();

    expect(await screen.findByText("粉丝下限不能大于上限")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "清除筛选" }));
    expect(navigation.replace).toHaveBeenLastCalledWith("/influencers");
  });

  it("explains an empty result when filters are active", async () => {
    navigation.search = "q=%E5%9B%BD%E9%A3%8E";
    mockApi({ items: [], page: 1, page_size: 50, total: 0 });
    renderWorkspace();

    expect(
      await screen.findByText("没有符合条件的达人。试试调整或清除筛选条件。"),
    ).toBeInTheDocument();
  });

  it("keeps list browsing available when filter options fail", async () => {
    mockApi(listData, envelope(null, 500));
    renderWorkspace();

    expect(
      await screen.findByRole("link", { name: "零粉多账号达人" }),
    ).toBeInTheDocument();
    expect(
      await screen.findByText("筛选选项加载失败", {}, { timeout: 3000 }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "重试筛选选项" }),
    ).toBeInTheDocument();
  });

  it("writes follower ranges and pagination to URL with page resets", async () => {
    navigation.search = "page=2&page_size=50";
    mockApi();
    renderWorkspace();

    await screen.findByRole("link", { name: "零粉多账号达人" });
    const minimum = await screen.findByRole("textbox", { name: "粉丝下限" });
    const maximum = screen.getByRole("textbox", { name: "粉丝上限" });
    fireEvent.change(minimum, { target: { value: "0" } });
    fireEvent.change(maximum, { target: { value: "100" } });
    fireEvent.click(screen.getByRole("button", { name: /应用/ }));
    expect(navigation.replace).toHaveBeenLastCalledWith(
      expect.stringContaining("followers_min=0&followers_max=100"),
    );
    expect(navigation.replace).toHaveBeenLastCalledWith(
      expect.not.stringContaining("page=2"),
    );

    navigation.replace.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    expect(navigation.replace).toHaveBeenCalledWith(
      "/influencers?page=3&page_size=50",
    );
    navigation.replace.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "上一页" }));
    expect(navigation.replace).toHaveBeenCalledWith(
      "/influencers?page_size=50",
    );
  });

  it("uses API tag, owner, and CRM options and disables overlong tags", async () => {
    navigation.search = "page=3&page_size=50";
    mockApi();
    renderWorkspace();
    await screen.findByRole("link", { name: "零粉多账号达人" });

    fireEvent.mouseDown(screen.getByRole("combobox", { name: "标签筛选" }));
    await screen.findByRole("option", { name: "动画" });
    const longTagOption = screen.getByRole("option", {
      name: "超长标签".repeat(41),
    });
    expect(longTagOption).toHaveAttribute("aria-disabled", "true");
    const visibleTagOption = Array.from(
      document.querySelectorAll<HTMLElement>(".ant-select-item-option"),
    ).find((item) => item.textContent === "动画");
    expect(visibleTagOption).toBeDefined();
    fireEvent.click(visibleTagOption as HTMLElement);
    await waitFor(() =>
      expect(navigation.replace).toHaveBeenLastCalledWith(
        "/influencers?page_size=50&tag=%E5%8A%A8%E7%94%BB",
      ),
    );

    fireEvent.mouseDown(screen.getByRole("combobox", { name: "负责人筛选" }));
    await screen.findByRole("option", { name: "已停用负责人（已停用）" });
    const visibleOwnerOption = Array.from(
      document.querySelectorAll<HTMLElement>(".ant-select-item-option"),
    ).find((item) => item.textContent === "已停用负责人（已停用）");
    expect(visibleOwnerOption).toBeDefined();
    fireEvent.click(visibleOwnerOption as HTMLElement);
    await waitFor(() =>
      expect(navigation.replace).toHaveBeenLastCalledWith(
        "/influencers?page_size=50&owner_operator_id=00000000-0000-0000-0000-000000000001",
      ),
    );

    fireEvent.mouseDown(screen.getByRole("combobox", { name: "CRM 阶段筛选" }));
    await screen.findByRole("option", { name: "高意向" });
    const visibleCrmOption = Array.from(
      document.querySelectorAll<HTMLElement>(".ant-select-item-option"),
    ).find((item) => item.textContent === "高意向");
    expect(visibleCrmOption).toBeDefined();
    fireEvent.click(visibleCrmOption as HTMLElement);
    await waitFor(() =>
      expect(navigation.replace).toHaveBeenLastCalledWith(
        "/influencers?page_size=50&crm_stage=%E9%AB%98%E6%84%8F%E5%90%91",
      ),
    );
  });

  it("writes backend freshness filters to URL without adding a sort", async () => {
    navigation.search =
      "freshness_status=stale&requires_refresh=true&page=3&page_size=50";
    mockApi();
    renderWorkspace();
    await screen.findByRole("link", { name: "零粉多账号达人" });

    expect(screen.getAllByText("陈旧").length).toBeGreaterThanOrEqual(2);
    expect(
      screen.getAllByText("距上次采集 41 天").length,
    ).toBeGreaterThanOrEqual(1);
    const search = screen.getByRole("textbox", { name: "昵称搜索" });
    fireEvent.change(search, { target: { value: "国风" } });
    fireEvent.click(screen.getByRole("button", { name: /搜.*索/ }));
    expect(navigation.replace).toHaveBeenLastCalledWith(
      "/influencers?freshness_status=stale&requires_refresh=true&page_size=50&q=%E5%9B%BD%E9%A3%8E",
    );

    expect(navigation.replace.mock.calls.flat().join(" ")).not.toContain(
      "sort",
    );
    expect(screen.queryByRole("button", { name: /刷新此达人/ })).toBeNull();
    expect(screen.queryByText(/Refresh Queue|刷新队列/)).toBeNull();
  });

  it("shows empty account, metric, tag, and contact collections as missing", async () => {
    const emptyItem = {
      ...listData.items[0],
      id: "00000000-0000-0000-0000-000000000102",
      display_name: "缺失集合达人",
      owner: null,
      platform_accounts: [],
      current_metrics: [],
      current_contacts: [],
      possible_duplicate_contact: false,
    };
    mockApi({ items: [emptyItem], page: 1, page_size: 50, total: 1 });
    renderWorkspace();

    const link = await screen.findByRole("link", { name: "缺失集合达人" });
    const row = link.closest("tr");
    expect(row).not.toBeNull();
    expect(
      within(row as HTMLTableRowElement).getAllByText("—").length,
    ).toBeGreaterThan(3);
    expect(
      within(row as HTMLTableRowElement).getByText("未分配"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();
  });
});
