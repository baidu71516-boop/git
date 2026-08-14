import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import DataCollectionVisualPreviewPage from "../src/app/dev-ui-preview/data-collection/page";
import { BulkImportPreviewWorkspace } from "../src/features/imports/bulk-import-preview-workspace";

const navigation = vi.hoisted(() => ({
  notFound: vi.fn(() => {
    throw new Error("NEXT_NOT_FOUND");
  }),
}));

vi.mock("next/navigation", () => ({
  notFound: navigation.notFound,
  usePathname: () => "/dev-ui-preview/data-collection",
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  navigation.notFound.mockClear();
});

function previewRow(name: string): HTMLElement {
  const row = screen.getByText(name).closest("tr");
  expect(row).not.toBeNull();
  return row as HTMLElement;
}

describe("development data collection visual preview", () => {
  it("returns notFound outside development", () => {
    vi.stubEnv("NODE_ENV", "production");

    expect(() => DataCollectionVisualPreviewPage()).toThrow("NEXT_NOT_FOUND");
    expect(navigation.notFound).toHaveBeenCalledOnce();
  });

  it("uses typed in-memory Preview fixtures with zero Auth, API, or DB requests", () => {
    vi.stubEnv("NODE_ENV", "development");
    const fetchMock = vi.spyOn(globalThis, "fetch");

    render(<BulkImportPreviewWorkspace />);

    expect(screen.getByText("开发预览状态")).toBeInTheDocument();
    expect(screen.getByText("核心结果")).toBeInTheDocument();
    expect(screen.getByText("夏日防晒小美")).toBeInTheDocument();
    expect(screen.getByText("共 51 条")).toBeInTheDocument();
    expect(screen.getByText("符合条件 46")).toBeInTheDocument();

    fireEvent.click(screen.getByText("多文件处理中"));
    expect(screen.getByText("灰豚_美妆达人_第一批.csv")).toBeInTheDocument();
    expect(screen.getByText("需要字段映射")).toBeInTheDocument();
    expect(screen.getByText("处理失败")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("opens and completes the local confirm Modal without submitting a request", async () => {
    vi.stubEnv("NODE_ENV", "development");
    const fetchMock = vi.spyOn(globalThis, "fetch");

    render(<BulkImportPreviewWorkspace />);

    fireEvent.click(screen.getByRole("button", { name: "确认导入" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("确认导入这批数据？")).toBeInTheDocument();
    expect(within(dialog).getByText("新增")).toBeInTheDocument();
    expect(within(dialog).getByText("需人工处理")).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "确认导入" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(fetchMock).not.toHaveBeenCalled();
  }, 20_000);

  it("handles local category, pagination, and row Drawer interactions", async () => {
    vi.stubEnv("NODE_ENV", "development");
    const fetchMock = vi.spyOn(globalThis, "fetch");

    render(<BulkImportPreviewWorkspace />);

    fireEvent.click(
      within(previewRow("夏日防晒小美")).getByRole("button", {
        name: "查看",
      }),
    );
    expect(screen.getByText("基本信息")).toBeInTheDocument();
    expect(screen.getByText("本次会更新")).toBeInTheDocument();
    expect(screen.getByText("不会更新")).toBeInTheDocument();
    expect(screen.getByText("数据更新时间变化")).toBeInTheDocument();
    expect(screen.getByText("历史观察")).toBeInTheDocument();
    expect(
      screen.getByText("新增 1 条，其中包含疑似重复联系方式"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "关闭预览数据详情" }));
    await waitFor(() => {
      expect(screen.queryByText("本次会更新")).not.toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("radio", { name: /人工处理/ }));
    expect(screen.getByText("身份冲突达人")).toBeInTheDocument();
    expect(screen.queryByText("格式异常达人")).not.toBeInTheDocument();
    expect(screen.getByText("共 1 条")).toBeInTheDocument();

    fireEvent.click(
      within(previewRow("身份冲突达人")).getByRole("button", {
        name: "查看",
      }),
    );
    await waitFor(() => {
      expect(
        screen.getAllByText("同批次达人数据对应到多个现有账号，需要人工处理。")
          .length,
      ).toBeGreaterThan(0);
    });
    fireEvent.click(screen.getByRole("button", { name: "关闭预览数据详情" }));

    fireEvent.click(screen.getByRole("radio", { name: /全部/ }));
    const secondPage = screen.getByTitle("2");
    fireEvent.click(secondPage);
    await waitFor(() => {
      expect(screen.getByText("美妆达人 52")).toBeInTheDocument();
    });
    expect(screen.queryByText("夏日防晒小美")).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  }, 20_000);

  it("reuses formal stale, failed, Task 6, and completed result states", () => {
    vi.stubEnv("NODE_ENV", "development");
    const fetchMock = vi.spyOn(globalThis, "fetch");

    render(<BulkImportPreviewWorkspace />);
    fireEvent.click(screen.getByRole("radio", { name: /^错误/ }));

    fireEvent.click(screen.getByText("数据预览需重建"));
    expect(screen.getByText("数据预览需要重新生成")).toBeInTheDocument();
    expect(
      screen.getByLabelText("已过期的数据预览，只读参考"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "确认导入" }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("任务失败"));
    expect(screen.getByText("任务处理失败")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /重\s*试/ })).toBeInTheDocument();

    fireEvent.click(screen.getByText("确认任务排队中"));
    expect(screen.getByText("正在准备导入")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "确认导入" }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("导入中"));
    expect(screen.getByText("正在导入")).toBeInTheDocument();

    fireEvent.click(screen.getByText("导入完成"));
    const resultSection = document.querySelector(".bulk-preview-completed-result");
    expect(resultSection).not.toBeNull();
    expect(within(resultSection as HTMLElement).getByText("46")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  }, 20_000);
});
