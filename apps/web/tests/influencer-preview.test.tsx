import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import DevInfluencerDetailDrawerPreviewPage from "../src/app/dev-ui-preview/influencers/drawer/[id]/page";
import InfluencerVisualPreviewPage from "../src/app/dev-ui-preview/influencers/page";
import { InfluencerPreviewWorkspace } from "../src/features/influencers/influencer-preview-workspace";
import { DevInfluencerDetailDrawerPreview } from "../src/features/influencers/influencer-detail-drawer-preview";
import {
  createInfluencerPreviewDetailItems,
  createInfluencerPreviewItems,
  getInfluencerPreviewDetailById,
} from "../src/features/influencers/preview-fixtures";

const navigation = vi.hoisted(() => ({
  notFound: vi.fn(() => {
    throw new Error("NEXT_NOT_FOUND");
  }),
}));

vi.mock("next/navigation", () => ({
  notFound: navigation.notFound,
  usePathname: () => "/dev-ui-preview/influencers",
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
  }),
}));

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  navigation.notFound.mockClear();
});

describe("development influencer visual preview", () => {
  it("keeps the route unavailable outside development", () => {
    vi.stubEnv("NODE_ENV", "production");

    expect(() => InfluencerVisualPreviewPage()).toThrow("NEXT_NOT_FOUND");
    expect(navigation.notFound).toHaveBeenCalledOnce();
  });

  it("allows the route in development", () => {
    vi.stubEnv("NODE_ENV", "development");

    expect(InfluencerVisualPreviewPage()).toBeTruthy();
    expect(navigation.notFound).not.toHaveBeenCalled();
  });

  it("provides eight representative in-memory records", () => {
    const now = new Date("2026-08-13T08:00:00.000Z");
    const items = createInfluencerPreviewItems(now);

    expect(items).toHaveLength(8);
    expect(items.map((item) => item.display_name)).toEqual([
      "星河杂货铺",
      "白桃汽水",
      "月面电台",
      "南风研究所",
      "小岛日记",
      "橘子宇宙",
      "蓝莓星期五",
      "云朵放映室",
    ]);
    expect(
      items.map((item) => item.current_metrics[0]?.followers_count),
    ).toEqual([8_532, 206_572, 482_400, 1_286_000, 0, null, 12_500, 98_700]);
    expect(
      items.some((item) => item.platform_accounts[0]?.source_tags.length === 0),
    ).toBe(true);
    expect(
      items.some((item) => item.platform_accounts[0]?.source_tags.length >= 4),
    ).toBe(true);
    expect(items.some((item) => item.owner === null)).toBe(true);
    expect(
      items.some((item) => item.current_metrics[0]?.source_updated_at === null),
    ).toBe(true);
    expect(new Set(items.map((item) => item.freshness_status))).toEqual(
      new Set(["fresh", "aging", "stale", "very_stale", "unknown"]),
    );
    expect(
      items.some(
        (item) =>
          item.freshness_status === "unknown" && item.requires_refresh,
      ),
    ).toBe(true);
    expect(
      items.some(
        (item) =>
          item.freshness_status === "unknown" && !item.requires_refresh,
      ),
    ).toBe(true);
  });

  it("reuses the UI-2 table and formatters without requesting any API", () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");

    render(<InfluencerPreviewWorkspace />);

    expect(document.querySelectorAll(".influencer-row")).toHaveLength(8);
    expect(
      screen.getByText(
        (content) =>
          content.includes("仅开发环境可见") ||
          content.includes("仅用于界面预览"),
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("8,532")).toBeInTheDocument();
    expect(screen.getByText("20.66万")).toBeInTheDocument();
    expect(screen.getByText("48.24万")).toBeInTheDocument();
    expect(screen.getByText("128.6万")).toBeInTheDocument();

    const zeroRow = screen
      .getByRole("link", { name: "小岛日记" })
      .closest("tr");
    expect(zeroRow).not.toBeNull();
    expect(
      within(zeroRow as HTMLTableRowElement).getByText("0"),
    ).toBeInTheDocument();

    const nullRow = screen
      .getByRole("link", { name: "橘子宇宙" })
      .closest("tr");
    expect(nullRow).not.toBeNull();
    expect(
      within(nullRow as HTMLTableRowElement).getAllByText("—").length,
    ).toBeGreaterThan(0);

    expect(screen.getByText("今天", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("昨天", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("3天前")).toBeInTheDocument();
    expect(screen.getAllByText("+3")).toHaveLength(2);
    expect(screen.getByText("共 8 位达人")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("keeps drawer detail preview route development-only and in-memory", async () => {
    vi.stubEnv("NODE_ENV", "production");

    await expect(
      DevInfluencerDetailDrawerPreviewPage({
        params: Promise.resolve({ id: "00000000-0000-0000-0000-000000000001" }),
      }),
    ).rejects.toThrow("NEXT_NOT_FOUND");
    expect(navigation.notFound).toHaveBeenCalledOnce();

    vi.stubEnv("NODE_ENV", "development");
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const previewPage = await DevInfluencerDetailDrawerPreviewPage({
      params: Promise.resolve({ id: "00000000-0000-0000-0000-000000000002" }),
    });

    render(previewPage);
    expect(
      screen.getByRole("heading", {
        level: 2,
        name: /^达人详情预览 · /,
      }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("仅开发环境可见").length).toBeGreaterThan(0);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("reuses the same detail presentation for development drawer preview", () => {
    const detailItems = createInfluencerPreviewDetailItems();
    const fixture = detailItems[2];
    const fixtureById = getInfluencerPreviewDetailById(fixture?.id ?? "");
    const displayFixture = fixtureById ?? fixture;

    render(
      <DevInfluencerDetailDrawerPreview
        detail={displayFixture}
        backHref="/dev-ui-preview/influencers/drawer"
      />,
    );

    expect(
      screen.getByRole("heading", { name: "基本资料", level: 5 }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "平台与指标", level: 5 }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "联系方式", level: 5 }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "管理信息", level: 5 }),
    ).toBeInTheDocument();
    expect(screen.getByText(displayFixture.display_name)).toBeInTheDocument();
    expect(screen.getAllByText("粉丝").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("48.24万")).toBeInTheDocument();
    expect(screen.getByText("24.12万")).toBeInTheDocument();
    expect(screen.getByText("月面电台账号1")).toBeInTheDocument();
    expect(screen.getAllByText("粉丝").length).toBeGreaterThanOrEqual(2);
  });
});
