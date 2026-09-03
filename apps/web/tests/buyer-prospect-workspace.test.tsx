import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Key, ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BuyerProspectWorkspace } from "../src/features/buyer-prospects/buyer-prospect-workspace";
import type { BuyerProspectRuleCreateRequest } from "../src/features/buyer-prospects/types";

type TestTableColumn = {
  key?: Key;
  title?: ReactNode;
  render?: (value: unknown, record: Record<string, unknown>) => ReactNode;
};

const router = { push: vi.fn() };

vi.mock("next/navigation", () => ({ useRouter: () => router }));

vi.mock("antd", async (importOriginal) => {
  const actual = await importOriginal<typeof import("antd")>();
  return {
    ...actual,
    Table: ({
      columns = [],
      dataSource = [],
    }: {
      columns?: TestTableColumn[];
      dataSource?: Record<string, unknown>[];
    }) => (
      <table>
        <thead>
          <tr>
            {columns.map((column, index) => (
              <th key={column.key ?? index}>{column.title}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {dataSource.map((record) => (
            <tr key={String(record.id)}>
              {columns.map((column, index) => (
                <td key={column.key ?? index}>
                  {column.render?.(undefined, record)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    ),
  };
});

const submittedValues: BuyerProspectRuleCreateRequest = {
  name: "新建潜客规则",
  category_ids: ["BEAUTY"],
  follower_min: 1000,
  follower_max: 9000,
  buyer_lead_tiers: ["HIGH"],
  source_type: "manual_huitun_export",
  recent_collection_window: "30",
  prospect_owner_filter: "ANY",
  exclude_contacted: true,
};

vi.mock("../src/features/buyer-prospects/buyer-prospect-rule-form", () => ({
  BuyerProspectRuleForm: ({
    onSubmit,
    submitLabel,
    saveAndRunLabel,
  }: {
    onSubmit: (
      values: BuyerProspectRuleCreateRequest,
      intent: "save" | "save_and_run",
    ) => void;
    submitLabel: string;
    saveAndRunLabel?: string;
  }) => (
    <div>
      <button onClick={() => onSubmit(submittedValues, "save")}>
        {submitLabel}
      </button>
      {saveAndRunLabel ? (
        <button onClick={() => onSubmit(submittedValues, "save_and_run")}>
          {saveAndRunLabel}
        </button>
      ) : null}
    </div>
  ),
}));

function response(data: unknown) {
  return new Response(
    JSON.stringify({ success: true, data, error: null, request_id: "test" }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

const options = {
  taxonomy_category_ids: ["BEAUTY", "PARENTING"],
  taxonomy_categories: [
    { id: "BEAUTY", label: "美妆" },
    { id: "PARENTING", label: "亲子" },
  ],
  sources: [{ value: "manual_huitun_export", label: "灰豚" }],
  operators: [{ id: "operator-2", name: "李四" }],
};

const rule = {
  id: "buyer-rule-1",
  department_id: "department-1",
  owner_operator_id: "operator-1",
  owner: { id: "operator-1", name: "王小明", status: "active" },
  name: "美妆跨类目潜客",
  status: "ACTIVE" as const,
  version: 3,
  current_policy_id: "policy-1",
  current_policy_version: 3,
  category_ids: ["BEAUTY", "PARENTING"],
  follower_min: 1000,
  follower_max: 10000,
  buyer_lead_tiers: ["HIGH", "RELATED"] as const,
  source_type: "manual_huitun_export",
  recent_collection_window: "30" as const,
  prospect_owner_filter: "OPERATOR" as const,
  prospect_owner_operator_id: "operator-2",
  exclude_contacted: true,
  latest_run: {
    id: "run-1",
    pool_id: "buyer-rule-1",
    policy_id: "policy-1",
    as_of: "2026-09-01T00:00:00Z",
    input_watermark: null,
    status: "COMPLETED",
    match_count: 6,
    unknown_count: 2,
    not_match_count: 10,
    error_code: null,
    error_message: null,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    idempotent_replay: false,
  },
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

function renderWorkspace(role: "operator" | "viewer" = "operator") {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <BuyerProspectWorkspace
        role={role}
        hasSelectedOperator={role !== "viewer"}
      />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  router.push.mockReset();
  vi.restoreAllMocks();
});

describe("Buyer prospect workspace", () => {
  it("shows saved rule criteria, latest result, and rule lifecycle actions", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (url.includes("/buyer-prospects/options")) return response(options);
        if (url.includes("/buyer-prospects?") && !init?.method) {
          return response({ items: [rule], next_cursor: null });
        }
        if (url.endsWith("/buyer-prospects/buyer-rule-1/lifecycle")) {
          return response({ ...rule, status: "DISABLED", version: 4 });
        }
        throw new Error(`Unexpected request: ${url}`);
      });

    renderWorkspace();

    expect(await screen.findByText("美妆跨类目潜客")).toBeInTheDocument();
    expect(screen.getByText("当前达人类目：美妆、亲子")).toBeInTheDocument();
    expect(screen.getByText("粉丝数：1000 - 10000")).toBeInTheDocument();
    expect(screen.getByText("潜客等级：强潜客、相关潜客")).toBeInTheDocument();
    expect(screen.getByText("数据来源：灰豚")).toBeInTheDocument();
    expect(screen.queryByText("manual_huitun_export")).not.toBeInTheDocument();
    expect(screen.getByText("最近采集：近 30 天")).toBeInTheDocument();
    expect(
      screen.getByText("负责人筛选：指定负责人：李四"),
    ).toBeInTheDocument();
    expect(screen.getByText("排除已触达")).toBeInTheDocument();
    expect(
      screen.getByText(/已完成 · 符合 6 · 信息不足 2 · 不符合 10/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "潜客等级仅用于销售线索排序，不代表已发生账号购买或交易。",
      ),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "查看客户" }));
    expect(router.push).toHaveBeenCalledWith(
      "/candidate-pools/buyer-rule-1/runs/run-1",
    );

    fireEvent.click(screen.getByRole("button", { name: "停用" }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([input, init]) =>
            String(input).endsWith("/buyer-prospects/buyer-rule-1/lifecycle") &&
            init?.method === "POST" &&
            init.body ===
              JSON.stringify({ expected_pool_version: 3, status: "DISABLED" }),
        ),
      ).toBe(true),
    );
  });

  it("saves and immediately runs a new rule through the existing API contract", async () => {
    const created = { ...rule, id: "buyer-rule-2", name: submittedValues.name };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (url.includes("/buyer-prospects/options")) return response(options);
        if (url.includes("/buyer-prospects?") && !init?.method) {
          return response({ items: [], next_cursor: null });
        }
        if (url.endsWith("/buyer-prospects") && init?.method === "POST")
          return response(created);
        if (
          url.endsWith("/buyer-prospects/buyer-rule-2/runs") &&
          init?.method === "POST"
        ) {
          return response({
            ...created.latest_run,
            id: "run-2",
            pool_id: "buyer-rule-2",
          });
        }
        throw new Error(`Unexpected request: ${url}`);
      });

    renderWorkspace();
    fireEvent.click(
      await screen.findByRole("button", { name: "新建筛选规则" }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "保存并运行" }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([input, init]) =>
            String(input).endsWith("/buyer-prospects") &&
            init?.method === "POST" &&
            init.body === JSON.stringify(submittedValues),
        ),
      ).toBe(true),
    );
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([input, init]) =>
            String(input).endsWith("/buyer-prospects/buyer-rule-2/runs") &&
            init?.method === "POST" &&
            init.body === "{}",
        ),
      ).toBe(true),
    );
  });

  it("keeps Viewer users read-only while preserving customer access", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/buyer-prospects/options")) return response(options);
      if (url.includes("/buyer-prospects?") && !init?.method) {
        return response({ items: [rule], next_cursor: null });
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    renderWorkspace("viewer");

    expect(await screen.findByText("美妆跨类目潜客")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看客户" })).toBeEnabled();
    expect(
      screen.queryByRole("button", { name: "新建筛选规则" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "停用" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "归档" }),
    ).not.toBeInTheDocument();
  });
});
