import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, vi } from "vitest";

import { ImportWorkspace } from "../src/components/import-workspace";
import { ImportFieldMappingEditor } from "../src/features/imports/components/import-field-mapping-editor";

const collection = {
  id: "collection-1",
  name: "脱敏任务",
  industry: "测试行业",
  target_count: 1,
  status: "active",
};

const baseJob = {
  id: "job-1",
  collection_job_id: collection.id,
  original_filename: "sanitized.csv",
  mime_type: "text/csv",
  file_size: 2048,
  sha256: "a".repeat(64),
  status: "mapping_required",
  detected_fields: ["达人名称"],
  field_mapping: null,
  preview_revision: 1,
  preview_summary: null,
  result: null,
  error_code: null,
  error_message: null,
};

function job(overrides: Record<string, unknown> = {}) {
  return { ...baseJob, ...overrides };
}

function response(data: unknown, status = 200) {
  return new Response(
    JSON.stringify({
      success: status < 400,
      data,
      error: null,
      request_id: "test",
    }),
    { status, headers: { "Content-Type": "application/json" } },
  );
}

function failureResponse(code: string, message: string, status: number) {
  return new Response(
    JSON.stringify({
      success: false,
      data: null,
      error: { code, message, details: null },
      request_id: "test",
    }),
    { status, headers: { "Content-Type": "application/json" } },
  );
}

function fileInput(): HTMLInputElement {
  const input = document.querySelector<HTMLInputElement>('input[type="file"]');
  expect(input).not.toBeNull();
  return input as HTMLInputElement;
}

async function selectAndUpload(name = "sanitized.csv") {
  fireEvent.change(fileInput(), {
    target: {
      files: [new File(["fixture"], name, { type: "text/csv" })],
    },
  });
  const upload = await screen.findByRole("button", { name: /上传并预览/ });
  await waitFor(() => expect(upload).toBeEnabled());
  fireEvent.click(upload);
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("ImportWorkspace", () => {
  it("keeps Viewer imports read-only and never parses files in the browser", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          success: true,
          data: [],
          error: null,
          request_id: "test",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    render(<ImportWorkspace role="viewer" />);

    await waitFor(() =>
      expect(
        screen.getByText("只读角色不能上传或确认导入。"),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByText(
        "上传灰豚导出的 CSV / XLSX 文件，系统会先生成数据预览，确认无误后再写入达人库。",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /新建采集任务/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /上传并预览/ })).toBeDisabled();
  });

  it("shows file metadata, invalid-email totals, and expandable row details", async () => {
    const uploadedJob = job({
      status: "preview_ready",
      detected_fields: ["达人名称"],
      field_mapping: { 达人名称: "nickname" },
      preview_summary: {
        file_type: "csv",
        total_rows: 1,
        valid_rows: 0,
        invalid_email_rows: 1,
        error_rows: 1,
      },
    });
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/import-jobs/job-1/rows")) {
        return response({
          items: [
            {
              id: "row-1",
              row_number: 2,
              raw_data: { 达人名称: "脱敏行" },
              normalized_data: { display_name: "脱敏行" },
              match_type: "none",
              action: "error",
              warnings: [
                {
                  code: "INVALID_EMAIL",
                  field: "email",
                  message: "Email is invalid",
                },
              ],
              errors: [
                {
                  code: "MISSING_PLATFORM_IDENTITY",
                  message: "A stable platform identity is required",
                },
              ],
            },
          ],
          total: 1,
          offset: 0,
          limit: 20,
        });
      }
      if (url.endsWith("/import-jobs/job-1")) return response(uploadedJob);
      if (url.endsWith("/import-jobs") && init?.method === "POST") {
        return response(uploadedJob);
      }
      if (url.endsWith("/collection-jobs")) {
        return response([collection]);
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    render(<ImportWorkspace role="operator" />);
    await selectAndUpload();

    expect(await screen.findByText("sanitized.csv")).toBeInTheDocument();
    expect(
      screen.getByText(/文件类型：csv · MIME：text\/csv/),
    ).toBeInTheDocument();
    expect(screen.getByText("无效邮箱")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /刷新/ }));
    expect(await screen.findByText("脱敏行")).toBeInTheDocument();

    const expand = document.querySelector<HTMLButtonElement>(
      ".ant-table-row-expand-icon",
    );
    expect(expand).not.toBeNull();
    fireEvent.click(expand as HTMLButtonElement);
    expect(await screen.findByText("标准化数据")).toBeInTheDocument();
    expect(screen.getByText("原始行（审计）")).toBeInTheDocument();
    expect(screen.getByText(/INVALID_EMAIL · 字段 email/)).toBeInTheDocument();
    expect(screen.getByText(/MISSING_PLATFORM_IDENTITY/)).toBeInTheDocument();
  });

  it("creates a collection with the current Legacy contract", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (url.endsWith("/collection-jobs") && !init?.method) {
          return response([]);
        }
        if (url.endsWith("/collection-jobs") && init?.method === "POST") {
          return response(collection, 201);
        }
        throw new Error(`Unexpected request: ${url}`);
      });

    render(<ImportWorkspace role="operator" />);
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: /新建采集任务/ }));

    fireEvent.change(screen.getByLabelText("任务名称"), {
      target: { value: "脱敏任务" },
    });
    fireEvent.change(screen.getByLabelText("行业"), {
      target: { value: "测试行业" },
    });
    fireEvent.change(screen.getByLabelText("采集目的"), {
      target: { value: "回归测试" },
    });
    fireEvent.change(screen.getByLabelText("目标动作"), {
      target: { value: "商务邮件触达" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建任务" }));

    expect(await screen.findByText("采集任务已创建")).toBeInTheDocument();
    const createCall = fetchSpy.mock.calls.find(
      ([input, init]) =>
        String(input).endsWith("/collection-jobs") && init?.method === "POST",
    );
    expect(createCall).toBeDefined();
    expect(JSON.parse(String(createCall?.[1]?.body))).toMatchObject({
      name: "脱敏任务",
      industry: "测试行业",
      purpose: "回归测试",
      target_action: "商务邮件触达",
      target_count: 50,
      source_type: "manual_huitun_export",
    });
  });

  it("uploads one file with FormData and preserves the Legacy request order", async () => {
    const uploadedJob = job();
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (url.endsWith("/collection-jobs")) return response([collection]);
        if (url.endsWith("/import-jobs") && init?.method === "POST") {
          return response(uploadedJob, 201);
        }
        throw new Error(`Unexpected request: ${url}`);
      });

    render(<ImportWorkspace role="operator" />);
    await selectAndUpload("legacy.csv");
    expect(
      await screen.findByText("文件已安全保存，后台正在解析"),
    ).toBeInTheDocument();

    const uploadCall = fetchSpy.mock.calls.find(
      ([input, init]) =>
        String(input).endsWith("/import-jobs") && init?.method === "POST",
    );
    expect(uploadCall).toBeDefined();
    const body = uploadCall?.[1]?.body;
    expect(body).toBeInstanceOf(FormData);
    expect((body as FormData).get("collection_job_id")).toBe(collection.id);
    expect(((body as FormData).get("file") as File).name).toBe("legacy.csv");
    expect(fetchSpy.mock.calls[0]?.[0]).toBe("/api/v1/collection-jobs");
  });

  it("keeps the shared Mapping Editor presentational and submits through Legacy state", async () => {
    const mappingJob = job({ detected_fields: ["达人名称", "主页链接"] });
    const previewJob = job({
      status: "preview_ready",
      field_mapping: { 达人名称: "nickname" },
      preview_summary: { total_rows: 0 },
    });
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (url.endsWith("/collection-jobs")) return response([collection]);
        if (url.endsWith("/import-jobs") && init?.method === "POST") {
          return response(mappingJob, 201);
        }
        if (url.endsWith("/import-jobs/job-1/mapping")) {
          return response(mappingJob, 202);
        }
        if (url.endsWith("/import-jobs/job-1/rows?offset=0&limit=20")) {
          return response({ items: [], total: 0, offset: 0, limit: 20 });
        }
        if (url.endsWith("/import-jobs/job-1")) return response(previewJob);
        throw new Error(`Unexpected request: ${url}`);
      });

    render(<ImportWorkspace role="operator" />);
    await selectAndUpload();
    expect(await screen.findByText("字段映射")).toBeInTheDocument();

    const select = screen.getByRole("combobox", {
      name: "将 达人名称 映射到",
    });
    fireEvent.mouseDown(select);
    fireEvent.click(await screen.findByTitle("nickname"));
    fireEvent.click(
      screen.getByRole("button", { name: "保存字段映射并生成预览" }),
    );

    expect(
      await screen.findByText("字段映射已保存，正在重新生成预览"),
    ).toBeInTheDocument();
    const mappingCall = fetchSpy.mock.calls.find(([input]) =>
      String(input).endsWith("/import-jobs/job-1/mapping"),
    );
    expect(mappingCall?.[1]?.method).toBe("PUT");
    expect(JSON.parse(String(mappingCall?.[1]?.body))).toEqual({
      mapping: { 达人名称: "nickname" },
    });
  });

  it("regenerates a stale Legacy preview through its real endpoint", async () => {
    const staleJob = job({ status: "preview_stale" });
    const readyJob = job({
      status: "preview_ready",
      preview_summary: { total_rows: 0 },
    });
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (url.endsWith("/collection-jobs")) return response([collection]);
        if (url.endsWith("/import-jobs") && init?.method === "POST") {
          return response(staleJob, 201);
        }
        if (url.endsWith("/import-jobs/job-1/preview"))
          return response(null, 202);
        if (url.includes("/import-jobs/job-1/rows")) {
          return response({ items: [], total: 0, offset: 0, limit: 20 });
        }
        if (url.endsWith("/import-jobs/job-1")) return response(readyJob);
        throw new Error(`Unexpected request: ${url}`);
      });

    render(<ImportWorkspace role="operator" />);
    await selectAndUpload();
    fireEvent.click(
      await screen.findByRole("button", { name: "重新生成预览" }),
    );
    expect(
      await screen.findByText("正在按当前数据状态重新生成预览"),
    ).toBeInTheDocument();
    const previewCall = fetchSpy.mock.calls.find(([input]) =>
      String(input).endsWith("/import-jobs/job-1/preview"),
    );
    expect(previewCall?.[1]?.method).toBe("POST");
    expect(previewCall?.[1]?.body).toBeUndefined();
  });

  it("confirms the current Legacy preview revision", async () => {
    const readyJob = job({
      status: "preview_ready",
      preview_revision: 7,
      preview_summary: { total_rows: 1 },
    });
    const completedJob = job({
      status: "completed",
      preview_revision: 7,
      preview_summary: { total_rows: 1 },
      result: { created: 1 },
    });
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (url.endsWith("/collection-jobs")) return response([collection]);
        if (url.endsWith("/import-jobs") && init?.method === "POST") {
          return response(readyJob, 201);
        }
        if (url.endsWith("/import-jobs/job-1/confirm"))
          return response(null, 202);
        if (url.includes("/import-jobs/job-1/rows")) {
          return response({ items: [], total: 0, offset: 0, limit: 20 });
        }
        if (url.endsWith("/import-jobs/job-1")) return response(completedJob);
        throw new Error(`Unexpected request: ${url}`);
      });

    render(<ImportWorkspace role="operator" />);
    await selectAndUpload();
    fireEvent.click(
      await screen.findByRole("button", { name: "确认当前数据预览" }),
    );
    const confirmPopup = await screen.findByText("确认导入第 7 版数据？");
    const popover = confirmPopup.closest(".ant-popover");
    const confirmButton = popover?.querySelector<HTMLButtonElement>(
      ".ant-popconfirm-buttons .ant-btn-primary",
    );
    expect(confirmButton).not.toBeNull();
    fireEvent.click(confirmButton as HTMLButtonElement);

    expect(
      await screen.findByText("确认请求已排队，重复确认不会重复导入"),
    ).toBeInTheDocument();
    const confirmCall = fetchSpy.mock.calls.find(([input]) =>
      String(input).endsWith("/import-jobs/job-1/confirm"),
    );
    expect(confirmCall?.[1]?.method).toBe("POST");
    expect(JSON.parse(String(confirmCall?.[1]?.body))).toEqual({
      preview_revision: 7,
    });
    expect(await screen.findByText("created")).toBeInTheDocument();
  });

  it("cancels a cancellable Legacy job through the current endpoint", async () => {
    const cancellableJob = job();
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (url.endsWith("/collection-jobs")) return response([collection]);
        if (url.endsWith("/import-jobs") && init?.method === "POST") {
          return response(cancellableJob, 201);
        }
        if (url.endsWith("/import-jobs/job-1/cancel")) {
          return response(job({ status: "cancelled" }));
        }
        throw new Error(`Unexpected request: ${url}`);
      });

    render(<ImportWorkspace role="operator" />);
    await selectAndUpload();
    fireEvent.click(await screen.findByRole("button", { name: /取消/ }));
    const prompt = await screen.findByText("确认取消本次导入？");
    const popover = prompt.closest(".ant-popover");
    const confirmButton = popover?.querySelector<HTMLButtonElement>(
      ".ant-popconfirm-buttons .ant-btn-primary",
    );
    expect(confirmButton).not.toBeNull();
    fireEvent.click(confirmButton as HTMLButtonElement);

    expect(
      await screen.findByText("本次导入已取消，未写入达人库"),
    ).toBeInTheDocument();
    const cancelCall = fetchSpy.mock.calls.find(([input]) =>
      String(input).endsWith("/import-jobs/job-1/cancel"),
    );
    expect(cancelCall?.[1]?.method).toBe("POST");
  });

  it("polls only while a Legacy task has a real active status", async () => {
    const parsingJob = job({ status: "parsing" });
    const mappedJob = job({ status: "mapping_required" });
    let readCount = 0;
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (url.endsWith("/collection-jobs")) return response([collection]);
        if (url.endsWith("/import-jobs") && init?.method === "POST") {
          return response(parsingJob, 201);
        }
        if (url.endsWith("/import-jobs/job-1")) {
          readCount += 1;
          return response(mappedJob);
        }
        throw new Error(`Unexpected request: ${url}`);
      });

    render(<ImportWorkspace role="operator" />);
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
    await selectAndUpload();
    await waitFor(() => expect(readCount).toBe(1), { timeout: 2500 });
    await act(() => new Promise((resolve) => window.setTimeout(resolve, 1400)));
    expect(readCount).toBe(1);
  });

  it("shows safe Chinese failure handling, no Legacy Retry, and allows a new upload", async () => {
    const failedJob = job({
      status: "failed",
      error_code: "INTERNAL_DATABASE_FAILURE",
      error_message: "postgresql://secret@internal:5432 traceback",
    });
    let uploadCount = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/collection-jobs")) return response([collection]);
      if (url.endsWith("/import-jobs") && init?.method === "POST") {
        uploadCount += 1;
        return response(
          uploadCount === 1
            ? failedJob
            : job({ original_filename: "replacement.csv" }),
          201,
        );
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    render(<ImportWorkspace role="operator" />);
    await selectAndUpload("broken.csv");
    expect(
      await screen.findByText("文件处理失败，请重新上传文件或新建采集任务。"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "当前流程不支持重试此任务；请使用上方真实入口重新上传文件，或新建采集任务后继续。",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("postgresql://secret@internal:5432 traceback"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "重试" }),
    ).not.toBeInTheDocument();

    await selectAndUpload("replacement.csv");
    expect(await screen.findByText("replacement.csv")).toBeInTheDocument();
    expect(uploadCount).toBe(2);
  });

  it("never renders a raw Legacy API error message", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      failureResponse(
        "INTERNAL_DATABASE_FAILURE",
        "postgresql://secret@internal:5432 worker traceback",
        500,
      ),
    );

    render(<ImportWorkspace role="operator" />);

    expect(
      await screen.findByText("系统暂时无法完成操作，请稍后重试。"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("postgresql://secret@internal:5432 worker traceback"),
    ).not.toBeInTheDocument();
  });
});

describe("ImportFieldMappingEditor", () => {
  it("emits mapping changes without owning an API request or workflow state", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const onChange = vi.fn();
    render(
      <ImportFieldMappingEditor
        sourceFields={["达人名称"]}
        canonicalFields={["nickname", "profile_url"]}
        value={{}}
        onChange={onChange}
      />,
    );

    const select = screen.getByRole("combobox", {
      name: "将 达人名称 映射到",
    });
    fireEvent.mouseDown(select);
    fireEvent.click(await screen.findByTitle("nickname"));

    expect(onChange).toHaveBeenCalledWith({ 达人名称: "nickname" });
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
