import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import InfluencerVisualPreviewPage from "../src/app/dev-ui-preview/influencers/page";
import { InfluencerPreviewWorkspace } from "../src/features/influencers/influencer-preview-workspace";
import { createInfluencerPreviewItems } from "../src/features/influencers/preview-fixtures";

const navigation = vi.hoisted(() => ({
  notFound: vi.fn(() => {
    throw new Error("NEXT_NOT_FOUND");
  }),
}));

vi.mock("next/navigation", () => ({
  notFound: navigation.notFound,
  usePathname: () => "/dev-ui-preview/influencers",
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
  });

  it("reuses the UI-2 table and formatters without requesting any API", () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");

    render(<InfluencerPreviewWorkspace />);

    expect(document.querySelectorAll(".influencer-row")).toHaveLength(8);
    expect(
      screen.getByText("仅用于界面预览", { exact: false }),
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
});
