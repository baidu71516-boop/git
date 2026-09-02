import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, vi } from "vitest";

import { ImportJobHistory } from "@/features/imports/import-job-history";
import type {
  ImportConfirmResult,
  ImportJobPublic,
  ImportJobStatus,
} from "@/features/imports/types";

const navigation = vi.hoisted(() => ({
  replace: vi.fn(),
  search: "",
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: navigation.replace }),
  useSearchParams: () => new URLSearchParams(navigation.search),
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

const result: ImportConfirmResult = {
  import_job_id: "job-completed",
  preview_revision: 2,
  created_rows: 7,
  updated_rows: 5,
  no_change_rows: 3,
  skipped_rows: 2,
  error_rows: 1,
  manual_review_rows: 4,
};

function job(
  status: ImportJobStatus,
  overrides: Partial<ImportJobPublic> = {},
): ImportJobPublic {
  return {
    id: `job-${status}`,
    collection_job_id: "collection-1",
    refresh_queue_id: null,
    department_id: "department-1",
    operator_id: "operator-1",
    stored_file_id: null,
    original_filename: null,
    mime_type: null,
    file_size: null,
    sha256: null,
    source_type: "manual_huitun_export",
    status,
    detected_fields: null,
    field_mapping: null,
    mapping_hash: null,
    preview_revision: 2,
    preview_summary: null,
    result: status === "completed" ? result : null,
    total_rows: 22,
    valid_rows: 21,
    warning_rows: 0,
    error_rows: 1,
    created_rows: 999,
    updated_rows: 999,
    no_change_rows: 999,
    skipped_rows: 999,
    manual_review_rows: 999,
    confirmed_revision: status === "completed" ? 2 : null,
    confirmed_at: status === "completed" ? "2026-08-15T02:20:00Z" : null,
    completed_at: status === "completed" ? "2026-08-15T02:30:00Z" : null,
    error_code: null,
    error_message: null,
    created_at: "2026-08-15T01:00:00Z",
    updated_at: "2026-08-15T02:30:00Z",
    ...overrides,
  };
}

function renderHistory(role: "operator" | "viewer" = "viewer") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
  }
  return render(<ImportJobHistory role={role} />, { wrapper: Wrapper });
}

afterEach(() => {
  vi.restoreAllMocks();
  navigation.replace.mockReset();
  navigation.search = "";
});

describe("Import Job history", () => {
  it("shows preview-ready confirm and cancel actions, then confirms through the existing API", async () => {
    const ready = job("preview_ready", {
      id: "job-ready",
      preview_revision: 5,
    });
    let current = ready;
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (url.endsWith("/import-jobs?offset=0&limit=50")) {
          return envelope({ items: [current], total: 1, offset: 0, limit: 50 });
        }
        if (url.endsWith("/import-jobs/job-ready") && !init?.method) {
          return envelope(current);
        }
        if (
          url.endsWith("/import-jobs/job-ready/confirm") &&
          init?.method === "POST"
        ) {
          current = { ...current, status: "completed" };
          return envelope(
            {
              import_job_id: current.id,
              status: "confirm_queued",
              preview_revision: current.preview_revision,
              task_id: "task-1",
              idempotent: false,
            },
            202,
          );
        }
        throw new Error(`Unexpected request: ${url} ${init?.method ?? "GET"}`);
      });

    renderHistory("operator");
    fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));

    expect(
      await screen.findByRole("button", { name: "确认导入" }),
    ).toBeEnabled();
    expect(screen.getByRole("button", { name: "取消导入" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "确认导入" }));
    expect(await screen.findByText("确认导入这批数据？")).toBeInTheDocument();
    fireEvent.click(
      screen.getAllByRole("button", { name: "确认导入" }).at(-1)!,
    );

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([input, init]) =>
            String(input).endsWith("/import-jobs/job-ready/confirm") &&
            init?.method === "POST" &&
            init.body === JSON.stringify({ preview_revision: 5 }),
        ),
      ).toBe(true),
    );
    expect((await screen.findAllByText("导入完成")).length).toBeGreaterThan(0);
    expect(
      fetchMock.mock.calls.filter(([input]) =>
        String(input).endsWith("/import-jobs?offset=0&limit=50"),
      ).length,
    ).toBeGreaterThanOrEqual(2);
  }, 10_000);

  it("keeps preview-ready actions unavailable to Viewer and terminal states", async () => {
    const ready = job("preview_ready", { id: "job-ready" });
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/import-jobs?offset=0&limit=50")) {
        return envelope({ items: [ready], total: 1, offset: 0, limit: 50 });
      }
      if (url.endsWith("/import-jobs/job-ready")) return envelope(ready);
      throw new Error(`Unexpected request: ${url}`);
    });

    const rendered = renderHistory("viewer");
    fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));
    await screen.findByText("job-ready");
    expect(
      screen.queryByRole("button", { name: "确认导入" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "取消导入" }),
    ).not.toBeInTheDocument();

    rendered.unmount();
    vi.restoreAllMocks();
    const completed = job("completed", { id: "job-completed-terminal" });
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/import-jobs?offset=0&limit=50")) {
        return envelope({ items: [completed], total: 1, offset: 0, limit: 50 });
      }
      if (url.endsWith("/import-jobs/job-completed-terminal"))
        return envelope(completed);
      throw new Error(`Unexpected request: ${url}`);
    });
    renderHistory("operator");
    fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));
    await screen.findByText("job-completed-terminal");
    expect(
      screen.queryByRole("button", { name: "确认导入" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "取消导入" }),
    ).not.toBeInTheDocument();
  });

  it("renders the exact read-only table and only persisted completed results", async () => {
    const completed = job("completed");
    const importing = job("importing");
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (url.endsWith("/import-jobs?offset=0&limit=50")) {
          return envelope({
            items: [completed, importing],
            total: 2,
            offset: 0,
            limit: 50,
          });
        }
        if (url.endsWith("/import-jobs/job-completed")) {
          return envelope(completed);
        }
        throw new Error(`Unexpected request: ${url} ${init?.method ?? "GET"}`);
      });

    renderHistory();

    expect(
      screen.getByText("查看导入任务的当前状态和最终处理结果。"),
    ).toBeInTheDocument();
    expect(await screen.findByText("导入完成")).toBeInTheDocument();
    for (const heading of [
      "创建时间",
      "来源",
      "状态",
      "数据量",
      "处理结果",
      "完成时间",
      "操作",
    ]) {
      expect(
        screen.getByRole("columnheader", { name: heading }),
      ).toBeInTheDocument();
    }
    expect(
      screen.getByText("新增 7，更新 5，无变化 3，跳过 2，错误 1，人工复核 4"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/999/)).not.toBeInTheDocument();
    expect(screen.getByText("正在导入")).toBeInTheDocument();
    expect(screen.getAllByText("—")).toHaveLength(2);
    expect(screen.queryByPlaceholderText(/搜索/)).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: "查看详情" })[0]);

    expect(await screen.findByText("job-completed")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "处理结果" }),
    ).toBeInTheDocument();
    expect(
      within(screen.getByLabelText("处理统计")).getByText("7"),
    ).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).endsWith("/import-jobs/job-completed"),
      ),
    ).toBe(true);
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method)).toBe(true);
    for (const action of ["删除", "重试", "取消", "恢复", "导出"]) {
      expect(
        screen.queryByRole("button", { name: action }),
      ).not.toBeInTheDocument();
    }
  });

  it("restores offset from the URL and pages on the server with limit 50", async () => {
    navigation.search = "offset=50";
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input) => {
        const url = new URL(String(input), "http://test");
        const offset = Number(url.searchParams.get("offset"));
        return envelope({
          items: [job("completed", { id: `job-${offset}` })],
          total: 125,
          offset,
          limit: 50,
        });
      });

    const rendered = renderHistory();

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) =>
          String(input).endsWith("/import-jobs?offset=50&limit=50"),
        ),
      ).toBe(true),
    );
    expect(await screen.findByText("共 125 条记录")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    expect(navigation.replace).toHaveBeenLastCalledWith(
      "/import-jobs?offset=100",
      { scroll: false },
    );
    navigation.search = "offset=100";
    rendered.rerender(<ImportJobHistory />);
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) =>
          String(input).endsWith("/import-jobs?offset=100&limit=50"),
        ),
      ).toBe(true),
    );
    fireEvent.click(await screen.findByRole("button", { name: "上一页" }));
    expect(navigation.replace).toHaveBeenLastCalledWith(
      "/import-jobs?offset=50",
      { scroll: false },
    );
    expect(
      fetchMock.mock.calls.every(([input]) =>
        String(input).includes("limit=50"),
      ),
    ).toBe(true);
  });

  it("shows the required table Skeleton while the GET is pending", () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      () => new Promise<Response>(() => undefined),
    );

    renderHistory();

    expect(
      screen.getByRole("status", { name: "导入记录加载中" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("columnheader", { name: "处理结果" }),
    ).toBeInTheDocument();
  });

  it("shows the required empty state and data collection link", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      envelope({ items: [], total: 0, offset: 0, limit: 50 }),
    );

    renderHistory();

    expect(await screen.findByText("暂无导入记录")).toBeInTheDocument();
    expect(
      screen.getByText("完成数据采集后，相关导入任务会显示在这里。"),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "前往数据采集" })).toHaveAttribute(
      "href",
      "/",
    );
  });

  it("shows the required GET error state and can reload", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(envelope(null, 422))
      .mockResolvedValueOnce(
        envelope({ items: [], total: 0, offset: 0, limit: 50 }),
      );

    renderHistory();

    expect(await screen.findByText("导入记录加载失败")).toBeInTheDocument();
    expect(screen.getByText("请稍后重试。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    expect(await screen.findByText("暂无导入记录")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("shows persisted Backend error information in the read-only Drawer", async () => {
    const failed = job("failed", {
      id: "job-failed",
      error_code: "IMPORT_PREVIEW_FAILED",
      error_message: "数据预览处理失败",
    });
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/import-jobs?")) {
        return envelope({ items: [failed], total: 1, offset: 0, limit: 50 });
      }
      if (url.endsWith("/import-jobs/job-failed")) return envelope(failed);
      throw new Error(`Unexpected request: ${url}`);
    });

    renderHistory();
    fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));

    expect(await screen.findByText("数据预览处理失败")).toBeInTheDocument();
    expect(screen.getByText("IMPORT_PREVIEW_FAILED")).toBeInTheDocument();
    expect(
      within(screen.getByLabelText("处理统计")).getByText("暂无最终处理统计。"),
    ).toBeInTheDocument();
  });
});
