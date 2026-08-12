import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, vi } from "vitest";

import { ImportWorkspace } from "../src/components/import-workspace";

afterEach(() => {
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
    const job = {
      id: "job-1",
      collection_job_id: "collection-1",
      original_filename: "sanitized.csv",
      mime_type: "text/csv",
      file_size: 2048,
      sha256: "a".repeat(64),
      status: "preview_ready",
      detected_fields: ["达人名称"],
      field_mapping: { 达人名称: "nickname" },
      preview_revision: 1,
      preview_summary: {
        file_type: "csv",
        total_rows: 1,
        valid_rows: 0,
        invalid_email_rows: 1,
        error_rows: 1,
      },
      result: null,
      error_code: null,
      error_message: null,
    };
    const response = (data: unknown) =>
      new Response(
        JSON.stringify({
          success: true,
          data,
          error: null,
          request_id: "test",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
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
      if (url.endsWith("/import-jobs/job-1")) return response(job);
      if (url.endsWith("/import-jobs") && init?.method === "POST") {
        return response(job);
      }
      if (url.endsWith("/collection-jobs")) {
        return response([
          {
            id: "collection-1",
            name: "脱敏任务",
            industry: "测试行业",
            target_count: 1,
            status: "active",
          },
        ]);
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    render(<ImportWorkspace role="operator" />);
    const input =
      document.querySelector<HTMLInputElement>('input[type="file"]');
    expect(input).not.toBeNull();
    fireEvent.change(input as HTMLInputElement, {
      target: {
        files: [new File(["fixture"], "sanitized.csv", { type: "text/csv" })],
      },
    });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /上传并预览/ })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: /上传并预览/ }));

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
});
