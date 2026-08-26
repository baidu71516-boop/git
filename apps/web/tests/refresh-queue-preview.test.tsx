import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import RefreshQueueDetailVisualPreviewPage from "../src/app/dev-ui-preview/refresh-queues/[status]/page";
import RefreshQueueVisualPreviewPage from "../src/app/dev-ui-preview/refresh-queues/page";
import {
  RefreshQueueDetailPreviewWorkspace,
  RefreshQueueListPreviewWorkspace,
} from "../src/features/refresh-queues/refresh-queue-preview-workspace";

const navigation = vi.hoisted(() => ({
  notFound: vi.fn(() => {
    throw new Error("NEXT_NOT_FOUND");
  }),
}));

vi.mock("next/navigation", () => ({
  notFound: navigation.notFound,
  usePathname: () => "/dev-ui-preview/refresh-queues",
}));

const queryClients: QueryClient[] = [];
const renderedViews: ReturnType<typeof render>[] = [];

afterEach(async () => {
  try {
    for (const view of renderedViews.splice(0).reverse()) view.unmount();
    const clients = queryClients.splice(0);
    await Promise.all(clients.map((client) => client.cancelQueries()));
    for (const client of clients) {
      expect(client.isFetching()).toBe(0);
      expect(client.isMutating()).toBe(0);
      client.clear();
    }
  } finally {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    navigation.notFound.mockClear();
  }
});

function renderPreview(ui: ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  queryClients.push(queryClient);
  const view = render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
  renderedViews.push(view);
  return view;
}

describe("development Refresh Queue visual preview", () => {
  it("returns notFound outside development", async () => {
    vi.stubEnv("NODE_ENV", "production");

    expect(() => RefreshQueueVisualPreviewPage()).toThrow("NEXT_NOT_FOUND");
    await expect(
      RefreshQueueDetailVisualPreviewPage({
        params: Promise.resolve({ status: "open" }),
      }),
    ).rejects.toThrow("NEXT_NOT_FOUND");
    expect(navigation.notFound).toHaveBeenCalledTimes(2);
  });

  it("renders an in-memory list, status links, and local pagination with zero requests", () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const { container } = renderPreview(<RefreshQueueListPreviewWorkspace />);

    expect(screen.getByText("开发预览状态")).toBeInTheDocument();
    expect(screen.getAllByText("待导出").length).toBeGreaterThan(0);
    expect(screen.getAllByText("已导出").length).toBeGreaterThan(0);
    expect(screen.getAllByText("已完成").length).toBeGreaterThan(0);
    expect(screen.getAllByText("已取消").length).toBeGreaterThan(0);
    expect(screen.getByText("共 54 个名单")).toBeInTheDocument();

    const nextButton = container.querySelector<HTMLButtonElement>(
      ".refresh-queue-list-card .ant-pagination-next button",
    );
    fireEvent.click(nextButton as HTMLButtonElement);
    expect(
      container.querySelectorAll(
        ".refresh-queue-list-card .ant-table-tbody tr.ant-table-row",
      ),
    ).toHaveLength(4);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("runs the real Create Modal presentation entirely in memory", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    renderPreview(<RefreshQueueListPreviewWorkspace />);
    fireEvent.click(screen.getByRole("button", { name: /创建更新名单/ }));

    expect(screen.getByLabelText("目标部门")).toBeInTheDocument();
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
      await screen.findByText("已完成本地创建交互预览"),
    ).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("reuses detail Summary, Item table, status actions, and pagination with zero requests", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const { container } = renderPreview(
      <RefreshQueueDetailPreviewWorkspace initialStatus="open" />,
    );

    expect(screen.getByText("山野生活研究所")).toBeInTheDocument();
    expect(screen.getByText("请求数量")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /导出 CSV/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /取消名单/ }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("共 55 条").length).toBeGreaterThan(0);

    const nextButton = container.querySelector<HTMLButtonElement>(
      ".refresh-queue-items-card .ant-pagination-next button",
    );
    fireEvent.click(nextButton as HTMLButtonElement);
    expect(screen.getByText("预览达人账号 51")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /取消名单/ }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("取消这个更新名单？")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "确认取消" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(
      screen.queryByRole("button", { name: /导出 CSV/ }),
    ).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  }, 20_000);

  it("shows the shared return workflow entry for an unfinished exported Queue", () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const { container } = renderPreview(
      <RefreshQueueDetailPreviewWorkspace initialStatus="exported" />,
    );

    const actions = container.querySelector<HTMLElement>(
      ".app-page-header-extra",
    );
    expect(actions).not.toBeNull();
    expect(
      within(actions as HTMLElement).getByRole("link", {
        name: "继续处理回流数据",
      }),
    ).toHaveAttribute("href", "/?workspace=bulk&refresh_queue_id=exported");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("supports each valid detail preview route and rejects unknown states", async () => {
    vi.stubEnv("NODE_ENV", "development");

    await expect(
      RefreshQueueDetailVisualPreviewPage({
        params: Promise.resolve({ status: "exported" }),
      }),
    ).resolves.toBeTruthy();
    await expect(
      RefreshQueueDetailVisualPreviewPage({
        params: Promise.resolve({ status: "unknown" }),
      }),
    ).rejects.toThrow("NEXT_NOT_FOUND");
  });
});
