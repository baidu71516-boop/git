import { fireEvent, render, screen } from "@testing-library/react";
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
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  navigation.notFound.mockClear();
});

describe("development data collection visual preview", () => {
  it("returns notFound outside development", () => {
    vi.stubEnv("NODE_ENV", "production");

    expect(() => DataCollectionVisualPreviewPage()).toThrow("NEXT_NOT_FOUND");
    expect(navigation.notFound).toHaveBeenCalledOnce();
  });

  it("uses only in-memory fixtures with zero Auth or business API requests", () => {
    vi.stubEnv("NODE_ENV", "development");
    const fetchMock = vi.spyOn(globalThis, "fetch");

    render(<BulkImportPreviewWorkspace />);

    expect(screen.getByText("开发预览状态")).toBeInTheDocument();
    expect(screen.getByText("灰豚_美妆达人_第一批.csv")).toBeInTheDocument();
    expect(screen.getByText("需要字段映射")).toBeInTheDocument();
    expect(screen.getByText("处理失败")).toBeInTheDocument();
    expect(
      screen.queryByText(/真实导航|真实入口|真实摘要|真实操作/),
    ).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("reuses the formal presentation for Preview and Task 6 compatibility states", () => {
    vi.stubEnv("NODE_ENV", "development");
    const fetchMock = vi.spyOn(globalThis, "fetch");

    render(<BulkImportPreviewWorkspace />);

    fireEvent.click(screen.getByText("数据预览已生成"));
    expect(screen.getAllByText("数据预览已生成").length).toBeGreaterThan(0);
    expect(
      screen.queryByRole("button", { name: /确认导入/ }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("确认任务排队中"));
    expect(screen.getByText("正在准备导入")).toBeInTheDocument();
    fireEvent.click(screen.getByText("导入中"));
    expect(screen.getAllByText("正在导入").length).toBeGreaterThan(0);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
