import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import type { Key, ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { InfluencerWorkspace } from "../src/features/influencers/influencer-workspace";

type TestTableColumn = {
  key?: Key;
  render?: (value: unknown, record: unknown) => ReactNode;
};

const influencerTableMode = vi.hoisted(() => ({ compact: false }));

vi.mock("antd", async (importOriginal) => {
  const actual = await importOriginal<typeof import("antd")>();
  return {
    ...actual,
    Table: ({
      className,
      columns = [],
      dataSource = [],
      onRow,
    }: {
      className?: string;
      columns?: TestTableColumn[];
      dataSource?: unknown[];
      onRow?: (record: unknown, index: number) => { className?: string };
    }) => (
      <table className={className}>
        <tbody>
          {dataSource.map((record, rowIndex) => {
            const row = onRow?.(record, rowIndex);
            return (
              <tr
                className={row?.className}
                key={(record as { id?: Key }).id ?? rowIndex}
              >
                {columns.map((column, columnIndex) => (
                  <td key={column.key ?? columnIndex}>
                    {column.render?.(undefined, record)}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    ),
  };
});

const navigation = vi.hoisted(() => ({
  search: "",
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/influencers",
  useRouter: () => ({ replace: navigation.replace }),
  useSearchParams: () => new URLSearchParams(navigation.search),
}));

vi.mock(
  "../src/features/influencers/components/influencer-table",
  async (importOriginal) => {
    const actual =
      await importOriginal<
        typeof import("../src/features/influencers/components/influencer-table")
      >();
    return {
      ...actual,
      InfluencerTable: (props: {
        items: Array<{ id: string; display_name: string }>;
      }) =>
        influencerTableMode.compact ? (
          <table>
            <tbody>
              {props.items.map((item) => (
                <tr className="influencer-row" key={item.id}>
                  <td>
                    <a href={`/influencers/${item.id}`}>{item.display_name}</a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          actual.InfluencerTable(props as never)
        ),
    };
  },
);

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
          content_activity_state: "current",
          content_activity_trusted_observed_at: "2026-08-10T08:00:00Z",
          content_activity_trusted_observation_status: "COMPLETE",
          content_activity_trusted_coverage_status: "FULL_CURRENT_PUBLIC_SET",
          content_activity_trusted_result: "PUBLICATION_FOUND",
          content_activity_last_publication_at: "2026-06-11T08:00:00Z",
          content_activity_inactive_days: 61,
          content_activity_latest_attempt_observed_at: "2026-08-10T08:00:00Z",
          content_activity_latest_attempt_observation_status: "COMPLETE",
          content_activity_latest_attempt_coverage_status:
            "FULL_CURRENT_PUBLIC_SET",
          content_activity_latest_attempt_result: "PUBLICATION_FOUND",
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

const queryClients: QueryClient[] = [];
const renderedWorkspaces: ReturnType<typeof render>[] = [];

function renderWorkspace() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  queryClients.push(client);
  const view = render(
    <QueryClientProvider client={client}>
      <InfluencerWorkspace />
    </QueryClientProvider>,
  );
  renderedWorkspaces.push(view);
  return view;
}

function filterCard(container: HTMLElement) {
  const card = container.querySelector<HTMLElement>(".influencer-filter-card");
  if (!card) throw new Error("Influencer filter card was not rendered");
  return card;
}

function filterCombobox(container: HTMLElement, name: string) {
  const combobox = Array.from(
    filterCard(container).querySelectorAll<HTMLElement>('[role="combobox"]'),
  ).find((element) => element.getAttribute("aria-label") === name);
  if (!combobox) throw new Error(`Filter combobox was not rendered: ${name}`);
  return combobox;
}

async function enabledFilterCombobox(container: HTMLElement, name: string) {
  return await waitFor(() => {
    const combobox = filterCombobox(container, name);
    expect(combobox).toBeEnabled();
    return combobox;
  });
}

async function openFilterSelect(combobox: HTMLElement, optionName: string) {
  fireEvent.mouseDown(combobox);
  return await waitFor(() => {
    const dropdown = Array.from(
      document.querySelectorAll<HTMLElement>(".ant-select-dropdown"),
    ).find((candidate) =>
      Array.from(
        candidate.querySelectorAll<HTMLElement>(".ant-select-item-option"),
      ).some((option) => option.textContent === optionName),
    );
    if (!dropdown) {
      throw new Error(`Select dropdown was not rendered: ${optionName}`);
    }
    return dropdown;
  });
}

function visibleSelectOption(dropdown: HTMLElement, name: string) {
  const option = Array.from(
    dropdown.querySelectorAll<HTMLElement>(".ant-select-item-option"),
  ).find((item) => item.textContent === name);
  if (!option) throw new Error(`Visible option was not rendered: ${name}`);
  return option;
}

beforeEach(() => {
  influencerTableMode.compact = false;
  navigation.search = "";
  navigation.replace.mockReset();
});

afterEach(async () => {
  try {
    for (const view of renderedWorkspaces.splice(0).reverse()) view.unmount();
    const clients = queryClients.splice(0);
    await Promise.all(clients.map((client) => client.cancelQueries()));
    for (const client of clients) {
      expect(client.isFetching()).toBe(0);
      expect(client.isMutating()).toBe(0);
      client.clear();
    }
  } finally {
    influencerTableMode.compact = false;
    vi.restoreAllMocks();
  }
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
    expect(within(row).queryByText("距上次采集 41 天")).toBeNull();
    expect(row).toHaveTextContent("多个小红书账号");
    expect(row).toHaveTextContent("请查看详情");
    expect(within(row).queryByText("61 天未更新")).toBeNull();
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
    expect(within(row).getByRole("link", { name: "详情" })).toHaveAttribute(
      "href",
      "/influencers/00000000-0000-0000-0000-000000000101",
    );
    expect(within(row).getByRole("link", { name: "主页 ↗" })).toHaveAttribute(
      "href",
      "https://example.invalid/a",
    );
    expect(within(row).getByRole("link", { name: "主页 ↗" })).toHaveAttribute(
      "target",
      "_blank",
    );
    expect(within(row).getByRole("link", { name: "主页 ↗" })).toHaveAttribute(
      "rel",
      "noopener noreferrer",
    );
  });

  it("does not render a homepage link when no active account has a profile URL", async () => {
    const withoutProfile = {
      ...listData,
      items: [
        {
          ...listData.items[0],
          platform_accounts: listData.items[0].platform_accounts.map(
            (account) => ({
              ...account,
              profile_url: null,
            }),
          ),
        },
      ],
    };
    mockApi(withoutProfile);
    renderWorkspace();

    const row = await screen.findByRole("row", { name: /零粉多账号达人/ });
    expect(within(row).queryByRole("link", { name: "主页 ↗" })).toBeNull();
  });

  it("renders trusted Content Activity only for one unambiguous XHS account", async () => {
    const singleXhsAccount = {
      ...listData,
      items: [
        {
          ...listData.items[0],
          platform_accounts: [listData.items[0].platform_accounts[0]],
        },
      ],
    };
    mockApi(singleXhsAccount);
    renderWorkspace();

    const row = await screen.findByRole("row", { name: /零粉多账号达人/ });
    expect(within(row).getByText("可信")).toBeInTheDocument();
    expect(within(row).getByText("61 天未更新")).toBeInTheDocument();
    expect(within(row).queryByText("多个小红书账号")).toBeNull();
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

  it("syncs contact and 60-day activity filters to the URL and resets pagination", async () => {
    influencerTableMode.compact = true;
    navigation.search =
      "contact_filter=has_contact&notes_60d_filter=zero&page=3&page_size=50";
    mockApi();
    const view = renderWorkspace();

    const contactSelect = await enabledFilterCombobox(
      view.container,
      "联系方式筛选",
    );
    const contactDropdown = await openFilterSelect(contactSelect, "有邮箱");
    fireEvent.click(visibleSelectOption(contactDropdown, "有邮箱"));
    expect(navigation.replace).toHaveBeenLastCalledWith(
      "/influencers?contact_filter=has_email&notes_60d_filter=zero&page_size=50",
    );

    const notes60dSelect = await enabledFilterCombobox(
      view.container,
      "近60天笔记筛选",
    );
    const notes60dDropdown = await openFilterSelect(notes60dSelect, "1-2篇");
    fireEvent.click(visibleSelectOption(notes60dDropdown, "1-2篇"));
    expect(navigation.replace).toHaveBeenLastCalledWith(
      "/influencers?contact_filter=has_contact&notes_60d_filter=one_to_two&page_size=50",
    );
  });

  it("renders the 7-day activity filter", async () => {
    mockApi();
    renderWorkspace();

    expect(
      await screen.findByRole("combobox", { name: "近7天笔记筛选" }),
    ).toBeInTheDocument();
  });

  it("syncs the strict Content Activity filter to the URL and resets pagination", async () => {
    navigation.search =
      "content_activity_filter=inactive_30d&page=3&page_size=50";
    mockApi();
    renderWorkspace();
    await screen.findByRole("link", { name: "零粉多账号达人" });

    fireEvent.mouseDown(
      screen.getByRole("combobox", { name: "内容活跃度筛选" }),
    );
    await screen.findByRole("option", { name: "断更 ≥ 60 天" });
    const inactiveSixtyDays = Array.from(
      document.querySelectorAll<HTMLElement>(".ant-select-item-option"),
    ).find((item) => item.textContent === "断更 ≥ 60 天");
    expect(inactiveSixtyDays).toBeDefined();
    fireEvent.click(inactiveSixtyDays as HTMLElement);
    await waitFor(() =>
      expect(navigation.replace).toHaveBeenLastCalledWith(
        "/influencers?content_activity_filter=inactive_60d&page_size=50",
      ),
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
    influencerTableMode.compact = true;
    navigation.search = "page=3&page_size=50";
    mockApi();
    const view = renderWorkspace();

    const tagSelect = await enabledFilterCombobox(view.container, "标签筛选");
    const tagDropdown = await openFilterSelect(tagSelect, "动画");
    const longTagOption = visibleSelectOption(
      tagDropdown,
      "超长标签".repeat(41),
    );
    expect(longTagOption).toHaveAttribute("aria-disabled", "true");
    expect(
      within(tagDropdown).getByRole("option", {
        name: "超长标签".repeat(41),
      }),
    ).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(visibleSelectOption(tagDropdown, "动画"));
    expect(navigation.replace).toHaveBeenLastCalledWith(
      "/influencers?page_size=50&tag=%E5%8A%A8%E7%94%BB",
    );

    const ownerSelect = await enabledFilterCombobox(
      view.container,
      "负责人筛选",
    );
    const ownerDropdown = await openFilterSelect(
      ownerSelect,
      "已停用负责人（已停用）",
    );
    fireEvent.click(
      visibleSelectOption(ownerDropdown, "已停用负责人（已停用）"),
    );
    expect(navigation.replace).toHaveBeenLastCalledWith(
      "/influencers?page_size=50&owner_operator_id=00000000-0000-0000-0000-000000000001",
    );

    const crmSelect = await enabledFilterCombobox(
      view.container,
      "CRM 阶段筛选",
    );
    const crmDropdown = await openFilterSelect(crmSelect, "高意向");
    fireEvent.click(visibleSelectOption(crmDropdown, "高意向"));
    expect(navigation.replace).toHaveBeenLastCalledWith(
      "/influencers?page_size=50&crm_stage=%E9%AB%98%E6%84%8F%E5%90%91",
    );
  });

  it("writes backend freshness filters to URL without adding a sort", async () => {
    navigation.search =
      "freshness_status=stale&requires_refresh=true&page=3&page_size=50";
    mockApi();
    renderWorkspace();
    await screen.findByRole("link", { name: "零粉多账号达人" });

    expect(screen.getAllByText("陈旧").length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText("距上次采集 41 天")).toBeNull();
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
