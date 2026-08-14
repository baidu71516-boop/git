import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { InfluencerDetailWorkspace } from "../src/features/influencers/influencer-detail-workspace";
import { InfluencerDetailDrawer } from "../src/features/influencers/influencer-detail-drawer";
import {
  displayValue,
  renderJsonValue,
} from "../src/features/influencers/formatters";

const navigation = vi.hoisted(() => ({ back: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ back: navigation.back }),
}));

const influencerId = "00000000-0000-0000-0000-000000000101";

const detailData = {
  id: influencerId,
  display_name: "多账号达人",
  status: "active",
  crm_stage: "高意向",
  freshness_status: "stale",
  requires_refresh: true,
  owner: {
    id: "00000000-0000-0000-0000-000000000001",
    name: "已停用负责人",
    status: "disabled",
  },
  created_at: "2026-08-10T16:00:00Z",
  updated_at: "2026-08-11T01:30:00Z",
  platform_accounts: [
    {
      id: "00000000-0000-0000-0000-000000000201",
      platform: "xiaohongshu",
      platform_account_id: "xhs-1",
      account_name: "账号甲",
      account_handle: "handle-a",
      profile_url: "https://example.invalid/profile-a",
      source: "huitun",
      is_active: true,
      source_tags: ["美妆", "动画"],
      last_huitun_observed_at: "2026-08-08T08:00:00Z",
      last_huitun_imported_at: "2026-08-09T08:00:00Z",
      freshness_status: "stale",
      freshness_age_days: 3,
      requires_refresh: true,
      bio: "真实简介 <b>不能作为 HTML</b>",
      gender: "女",
      region_raw: "上海",
      verification_info: "认证信息",
      mcn_name: null,
      creator_level: "腰部",
      is_brand_partner: false,
    },
    {
      id: "00000000-0000-0000-0000-000000000202",
      platform: "xiaohongshu",
      platform_account_id: null,
      account_name: "账号乙",
      account_handle: null,
      profile_url: null,
      source: "generic",
      is_active: true,
      source_tags: [],
      last_huitun_observed_at: null,
      last_huitun_imported_at: null,
      freshness_status: null,
      freshness_age_days: null,
      requires_refresh: false,
      bio: null,
      gender: null,
      region_raw: null,
      verification_info: null,
      mcn_name: null,
      creator_level: null,
      is_brand_partner: null,
    },
  ],
  contacts: [
    {
      id: "contact-viewer",
      platform_account_id: null,
      type: "email",
      display_value: "***",
      source: "manual",
      validation_status: "valid",
      is_current: true,
      possible_duplicate_contact: true,
      first_seen_at: "2026-08-10T00:00:00Z",
      last_seen_at: "2026-08-11T00:00:00Z",
      source_updated_at: null,
      first_import_job_id: null,
      first_import_row_id: null,
      last_import_job_id: null,
      last_import_row_id: null,
    },
    {
      id: "contact-complete",
      platform_account_id: "00000000-0000-0000-0000-000000000201",
      type: "phone",
      display_value: "13800138000",
      source: "huitun",
      validation_status: "unverified",
      is_current: true,
      possible_duplicate_contact: false,
      first_seen_at: "2026-08-10T00:00:00Z",
      last_seen_at: "2026-08-11T00:00:00Z",
      source_updated_at: "2026-08-10T08:00:00Z",
      first_import_job_id: "job-first",
      first_import_row_id: "row-first",
      last_import_job_id: "job-last",
      last_import_row_id: "row-last",
    },
  ],
  source_states: [
    {
      platform_account_id: "00000000-0000-0000-0000-000000000201",
      source: "huitun",
      source_updated_at: "2026-08-10T08:00:00Z",
      state_version: 2,
      creator_tags: ["来源赛道"],
      last_import_job_id: "state-job",
      last_import_row_id: "state-row",
    },
  ],
  source_identities: [
    {
      id: "identity-1",
      platform_account_id: "00000000-0000-0000-0000-000000000201",
      platform: "xiaohongshu",
      source: "huitun",
      external_account_id: "external-1",
      first_import_job_id: "identity-first-job",
      first_import_row_id: "identity-first-row",
      last_import_job_id: "identity-last-job",
      last_import_row_id: "identity-last-row",
    },
  ],
  current_metrics: [
    {
      platform_account_id: "00000000-0000-0000-0000-000000000201",
      source: "huitun",
      source_updated_at: "2026-08-10T08:00:00Z",
      metrics: {
        followers_count: 0,
        huitun_score: "98.123456789",
        image_note_price: null,
        fan_region_distribution: [
          { label: "上海", rate: "0.3500" },
          { label: "北京", rate: "0.2000" },
        ],
      },
      last_import_job_id: "metric-job",
      last_import_row_id: "metric-row",
    },
    {
      platform_account_id: "00000000-0000-0000-0000-000000000202",
      source: "generic",
      source_updated_at: "2026-01-01T08:00:00Z",
      metrics: { followers_count: null },
      last_import_job_id: "metric-generic-job",
      last_import_row_id: "metric-generic-row",
    },
  ],
};

const snapshotData = {
  items: [
    {
      id: "snapshot-1",
      platform_account_id: "00000000-0000-0000-0000-000000000201",
      source: "huitun",
      source_updated_at: "2026-08-09T08:00:00Z",
      captured_at: "2026-08-09T09:00:00Z",
      metrics: { followers_count: 0, video_cpm: "12.3400" },
      import_job_id: "snapshot-job",
      import_row_id: "snapshot-row",
    },
  ],
  page: 1,
  page_size: 50,
  total: 51,
};

function envelope(data: unknown, status = 200, code = "INTERNAL_ERROR") {
  return new Response(
    JSON.stringify({
      success: status < 400,
      data: status < 400 ? data : null,
      error:
        status < 400
          ? null
          : { code, message: "Request failed", details: null },
      request_id: "request-detail",
    }),
    { status, headers: { "Content-Type": "application/json" } },
  );
}

function mockDetailApi(
  detail: unknown = detailData,
  snapshots: unknown = snapshotData,
) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes("/metric-snapshots")) {
      return snapshots instanceof Response
        ? snapshots.clone()
        : envelope(snapshots);
    }
    if (url.endsWith(`/influencers/${influencerId}`)) {
      return detail instanceof Response ? detail.clone() : envelope(detail);
    }
    throw new Error(`Unexpected request: ${url}`);
  });
}

function renderDetail() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <InfluencerDetailWorkspace influencerId={influencerId} />
    </QueryClientProvider>,
  );
}

function renderDrawer() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <InfluencerDetailDrawer influencerId={influencerId} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  navigation.back.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("influencer value formatters", () => {
  it("preserves frozen null, empty string, zero, and false semantics", () => {
    expect(displayValue(null)).toBe("—");
    expect(displayValue("")).toBe("");
    expect(displayValue(0)).toBe("0");
    expect(displayValue(false)).toBe("false");

    expect(renderJsonValue(null)).toBe("—");
    expect(renderJsonValue("")).toBe("");
    expect(renderJsonValue(0)).toBe("0");
    expect(renderJsonValue(false)).toBe("false");
    expect(renderJsonValue("98.123456789")).toBe("98.123456789");
  });
});

describe("InfluencerDetailWorkspace", () => {
  it("renders frozen detail sections with exact values and safe provenance", async () => {
    mockDetailApi();
    renderDetail();

    expect(screen.getByText("正在加载达人详情")).toBeInTheDocument();
    expect(
      await screen.findByRole("heading", { name: "多账号达人" }),
    ).toBeInTheDocument();
    const basicSection = screen
      .getByRole("heading", { name: "基本资料", level: 5 })
      .closest("section");
    expect(basicSection).not.toBeNull();
    expect(
      within(basicSection as HTMLElement).getByText("已停用负责人"),
    ).toBeInTheDocument();
    const ownerSection = screen
      .getByRole("heading", { name: "管理信息", level: 5 })
      .closest("section");
    expect(ownerSection).not.toBeNull();
    expect(
      within(ownerSection as HTMLElement).getByText("资料更新时间"),
    ).toBeInTheDocument();
    expect(
      within(ownerSection as HTMLElement).getByText("创建时间"),
    ).toBeInTheDocument();
    expect(screen.getAllByText(/账号甲/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/账号乙/).length).toBeGreaterThan(0);
    expect(screen.getByText("美妆")).toBeInTheDocument();
    expect(screen.getByText("动画")).toBeInTheDocument();
    expect(
      within(ownerSection as HTMLElement).getByText("2026-08-11 09:30"),
    ).toBeInTheDocument();

    const contactSection = screen
      .getByRole("heading", { name: "联系方式", level: 5 })
      .closest("section");
    expect(contactSection).not.toBeNull();
    expect(
      within(contactSection as HTMLElement).getByText("邮箱"),
    ).toBeInTheDocument();
    expect(
      within(contactSection as HTMLElement).getByText("***"),
    ).toBeInTheDocument();
    expect(
      within(contactSection as HTMLElement).getByText("手机"),
    ).toBeInTheDocument();
    expect(
      within(contactSection as HTMLElement).getByText("13800138000"),
    ).toBeInTheDocument();
    expect(screen.queryByText("normalized_value")).not.toBeInTheDocument();
    expect(screen.queryByText("重复达人")).not.toBeInTheDocument();
    expect(screen.queryByText("已自动合并")).not.toBeInTheDocument();

    expect(
      within(ownerSection as HTMLElement).getByText("数据来源"),
    ).toBeInTheDocument();
    expect(
      within(ownerSection as HTMLElement).getByText(/灰豚/),
    ).toBeInTheDocument();
    expect(screen.queryByText("最后观察")).not.toBeInTheDocument();
    expect(screen.queryByText("source_data")).not.toBeInTheDocument();

    const platformMetrics = screen
      .getByRole("heading", { name: "平台与指标", level: 5 })
      .closest("section");
    expect(platformMetrics).not.toBeNull();
    expect(
      within(platformMetrics as HTMLElement).getAllByText("粉丝").length,
    ).toBeGreaterThan(0);
    expect(
      within(platformMetrics as HTMLElement).getAllByText("0").length,
    ).toBeGreaterThan(0);
    expect(
      within(platformMetrics as HTMLElement).getAllByText("数据时效"),
    ).toHaveLength(2);
    expect(
      within(platformMetrics as HTMLElement).getByText("陈旧"),
    ).toBeInTheDocument();
    expect(
      within(platformMetrics as HTMLElement).getByText("不适用"),
    ).toBeInTheDocument();
    expect(
      within(platformMetrics as HTMLElement).getAllByText("最近可靠采集时间"),
    ).toHaveLength(2);
    expect(
      within(platformMetrics as HTMLElement).getAllByText("最近导入时间"),
    ).toHaveLength(2);
    expect(
      within(platformMetrics as HTMLElement).getByText("2026-08-08 16:00"),
    ).toBeInTheDocument();
    expect(
      within(platformMetrics as HTMLElement).getByText("2026-08-09 16:00"),
    ).toBeInTheDocument();
    expect(
      within(platformMetrics as HTMLElement).getByText("3 天"),
    ).toBeInTheDocument();
    expect(
      within(platformMetrics as HTMLElement).getAllByText("是否需要更新"),
    ).toHaveLength(2);
    expect(
      within(platformMetrics as HTMLElement).getAllByText("指标更新时间"),
    ).toHaveLength(2);
    const genericAccount = within(platformMetrics as HTMLElement)
      .getByText("小红书 · 账号乙")
      .closest(".detail-platform-item");
    expect(genericAccount).not.toBeNull();
    expect(
      within(genericAccount as HTMLElement).getAllByText("—").length,
    ).toBeGreaterThanOrEqual(3);
    expect(
      within(genericAccount as HTMLElement).getByText("01-01"),
    ).toBeInTheDocument();
    expect(screen.queryByText("AI Score")).not.toBeInTheDocument();
    expect(screen.queryByText(/¥|RMB|人民币/)).not.toBeInTheDocument();
    expect(screen.queryByText("notes_count")).not.toBeInTheDocument();

    expect(
      screen.getByText("历史快照记录的是当次输入，不等于当前合并指标。"),
    ).toBeInTheDocument();
    expect(await screen.findByText("12.3400")).toBeInTheDocument();
    expect(screen.getByText("共 51 条历史快照")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "下一页历史快照" }),
    ).toBeEnabled();
    expect(
      screen.getByRole("button", { name: "上一页历史快照" }),
    ).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "返回上一页" }));
    expect(navigation.back).toHaveBeenCalledOnce();
    expect(screen.getByRole("link", { name: "返回达人库" })).toHaveAttribute(
      "href",
      "/influencers",
    );
    expect(
      screen.queryByText(/Campaign|Follow-up|Notes|Analytics/),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /编辑|修改|删除/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /刷新此达人|刷新/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/Refresh Queue|刷新队列/)).not.toBeInTheDocument();
  });

  it("renders an unassigned Owner and missing fields without inventing values", async () => {
    mockDetailApi(
      {
        ...detailData,
        display_name: "缺失值达人",
        owner: null,
        contacts: [],
        source_states: [],
        source_identities: [],
        current_metrics: [],
      },
      { items: [], page: 1, page_size: 50, total: 0 },
    );
    renderDetail();

    expect(
      await screen.findByRole("heading", { name: "缺失值达人", level: 2 }),
    ).toBeInTheDocument();
    const missingBasicSection = screen
      .getByRole("heading", { name: "基本资料", level: 5 })
      .closest("section");
    expect(missingBasicSection).not.toBeNull();
    expect(
      within(missingBasicSection as HTMLElement).getByText("未分配"),
    ).toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThan(2);
    expect(await screen.findByText("暂无指标历史")).toBeInTheDocument();
  });

  it("keeps detail errors, 404, and retry behavior distinct", async () => {
    const fetchMock = mockDetailApi(envelope(null, 500));
    const first = renderDetail();
    expect(
      await screen.findByText("达人资料加载失败", {}, { timeout: 3000 }),
    ).toBeInTheDocument();
    const callsBeforeRetry = fetchMock.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.length).toBeGreaterThan(callsBeforeRetry),
    );
    first.unmount();

    mockDetailApi(envelope(null, 404, "INFLUENCER_NOT_FOUND"));
    renderDetail();
    expect(await screen.findByText("达人不存在或不可见")).toBeInTheDocument();
    expect(screen.queryByText("达人资料加载失败")).not.toBeInTheDocument();
  });

  it("keeps snapshot loading and errors independent from readable detail", async () => {
    let resolveSnapshots: ((value: Response) => void) | undefined;
    let snapshotRequests = 0;
    const pendingFetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input) => {
        const url = String(input);
        if (url.includes("/metric-snapshots")) {
          snapshotRequests += 1;
          if (snapshotRequests > 1) return envelope(null, 500);
          return new Promise<Response>((resolve) => {
            resolveSnapshots = resolve;
          });
        }
        return envelope(detailData);
      });
    const first = renderDetail();
    expect(
      await screen.findByRole("heading", { name: "多账号达人", level: 2 }),
    ).toBeInTheDocument();
    expect(screen.getByText("正在加载指标历史")).toBeInTheDocument();
    resolveSnapshots?.(envelope(null, 500));
    expect(
      await screen.findByText("指标历史加载失败", {}, { timeout: 3000 }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("账号甲").length).toBeGreaterThan(0);
    const callsBeforeRetry = pendingFetchMock.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "重试指标历史" }));
    await waitFor(() =>
      expect(pendingFetchMock.mock.calls.length).toBeGreaterThan(
        callsBeforeRetry,
      ),
    );
    first.unmount();

    const fetchMock = mockDetailApi(detailData, snapshotData);
    renderDetail();
    await screen.findByText("12.3400");
    fireEvent.click(screen.getByRole("button", { name: "下一页历史快照" }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) =>
          String(input).includes("metric-snapshots?page=2&page_size=50"),
        ),
      ).toBe(true),
    );
    expect(await screen.findByText("第 2 页")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "下一页历史快照" }),
    ).toBeDisabled();
  });

  it("keeps total and recovery pagination when an over-page response is empty", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith(`/influencers/${influencerId}`)) {
        return envelope(detailData);
      }
      if (url.includes("page=1&page_size=50")) {
        return envelope({ ...snapshotData, total: 101 });
      }
      if (url.includes("page=2&page_size=50")) {
        return envelope({ items: [], page: 2, page_size: 50, total: 101 });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    renderDetail();

    await screen.findByText("12.3400");
    fireEvent.click(screen.getByRole("button", { name: "下一页历史快照" }));
    expect(await screen.findByText("暂无指标历史")).toBeInTheDocument();
    expect(screen.getByText("共 101 条历史快照")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "上一页历史快照" }),
    ).toBeEnabled();
  });

  it("reuses the protected detail view inside a canonical URL drawer", async () => {
    mockDetailApi();
    renderDrawer();

    expect(screen.getByText("正在加载达人详情")).toBeInTheDocument();
    expect(
      await screen.findByText("多账号达人", { exact: false }),
    ).toBeInTheDocument();
    const drawerHeader = document.querySelector(
      ".influencer-detail-drawer-title",
    );
    expect(drawerHeader).not.toBeNull();
    expect(
      within(drawerHeader as HTMLElement).getByText("小红书 · 账号甲"),
    ).toBeInTheDocument();
    expect(screen.getAllByText("高意向").length).toBeGreaterThan(0);
    const drawerContactSection = within(screen.getByRole("dialog"))
      .getByRole("heading", { name: "联系方式", level: 5 })
      .closest("section");
    expect(drawerContactSection).not.toBeNull();
    expect(
      within(drawerContactSection as HTMLElement).getByText("邮箱"),
    ).toBeInTheDocument();
    expect(
      within(drawerContactSection as HTMLElement).getByText("***"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "完整资料" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "关闭达人详情" }));
    expect(navigation.back).toHaveBeenCalledOnce();
  });

  it("keeps drawer errors in Chinese and closes with browser history", async () => {
    mockDetailApi(envelope(null, 500));
    renderDrawer();

    expect(
      await screen.findByText("达人资料加载失败", {}, { timeout: 3000 }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新加载" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: /^关\s*闭$/ }));
    expect(navigation.back).toHaveBeenCalledOnce();
  });
});
