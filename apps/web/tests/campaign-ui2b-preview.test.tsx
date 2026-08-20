import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import CampaignVisualPreviewPage from "../src/app/dev-ui-preview/campaigns/page";
import { CampaignPreviewWorkspace } from "../src/features/campaigns/campaign-preview-workspace";

const navigation = vi.hoisted(() => ({
  scene: "member-list",
  notFound: vi.fn(() => {
    throw new Error("NEXT_NOT_FOUND");
  }),
}));

vi.mock("next/navigation", () => ({
  notFound: navigation.notFound,
  useSearchParams: () => ({
    get: (name: string) => (name === "scene" ? navigation.scene : null),
  }),
  usePathname: () => "/dev-ui-preview/campaigns",
  useRouter: () => ({ push: vi.fn() }),
}));

afterEach(() => {
  cleanup();
  navigation.scene = "member-list";
  navigation.notFound.mockClear();
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});

function renderPreview() {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
          },
        })
      }
    >
      <CampaignPreviewWorkspace />
    </QueryClientProvider>,
  );
}

describe("Campaign UI-2B development preview", () => {
  it("keeps the preview development-only", () => {
    vi.stubEnv("NODE_ENV", "production");

    expect(() => CampaignVisualPreviewPage()).toThrow("NEXT_NOT_FOUND");
    expect(navigation.notFound).toHaveBeenCalledTimes(1);
  });

  it("renders the member table with zero network requests", () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");

    renderPreview();

    expect(screen.getAllByText("活动达人").length).toBeGreaterThanOrEqual(2);
    expect(
      screen.getByText("管理当前活动中的达人及其使用的平台账号。"),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "科技小王" })).toHaveAttribute(
      "href",
      "/influencers/influencer-tech",
    );
    expect(screen.getByText("小红书 · 数码老李")).toBeInTheDocument();
    expect(screen.getByText("@shumaolaoli")).toBeInTheDocument();
    expect(screen.getAllByText("首次加入时间").length).toBeGreaterThanOrEqual(
      1,
    );
    expect(
      screen.getByRole("button", { name: "添加达人" }),
    ).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("opens the add drawer and preserves selected-only local state", async () => {
    navigation.scene = "member-selected";
    renderPreview();

    const dialog = await waitFor(() => screen.getByRole("dialog"));
    expect(within(dialog).getByText("已选择 2 位达人")).toBeInTheDocument();
    expect(
      within(dialog).getByPlaceholderText("搜索达人昵称或平台账号名称"),
    ).toBeDisabled();
    expect(
      within(dialog).getByRole("button", { name: "返回搜索结果" }),
    ).toBeInTheDocument();
  });

  it("distinguishes multiple platform accounts in the drawer", async () => {
    navigation.scene = "member-multi-account";
    renderPreview();

    const dialog = await waitFor(() => screen.getByRole("dialog"));
    expect(within(dialog).getByText("小红书 · 科技小王")).toBeInTheDocument();
    expect(within(dialog).getByText("@techwang")).toBeInTheDocument();
    expect(within(dialog).getByText("抖音 · 科技小王")).toBeInTheDocument();
    expect(within(dialog).getByText("@techwang_dy")).toBeInTheDocument();
  });

  it("keeps CLOSED read-only and shows the member conflict modal locally", async () => {
    navigation.scene = "member-closed";
    renderPreview();

    expect(
      screen.getByText("活动已关闭，无法调整活动达人。"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "添加达人" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("columnheader", { name: "操作" }),
    ).not.toBeInTheDocument();

    cleanup();
    navigation.scene = "member-conflict";
    renderPreview();
    expect(await waitFor(() => screen.getByRole("dialog"))).toHaveTextContent(
      "将达人移出活动？",
    );
  });

  it("renders empty and loading error scenes with the frozen Chinese copy", () => {
    navigation.scene = "member-empty";
    renderPreview();
    expect(screen.getByText("暂无活动达人")).toBeInTheDocument();
    expect(
      screen.getByText("当前还没有达人加入这个拓客活动。"),
    ).toBeInTheDocument();

    cleanup();
    navigation.scene = "member-error";
    renderPreview();
    expect(screen.getByText("活动达人加载失败")).toBeInTheDocument();
    expect(screen.getByText("请稍后重试。")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "重新加载" }),
    ).toBeInTheDocument();
  });
});
