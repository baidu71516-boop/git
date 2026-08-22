import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CampaignListView } from "../src/features/campaigns/campaign-list-view";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

function response(data: unknown) {
  return new Response(
    JSON.stringify({ success: true, data, error: null, request_id: "test" }),
    {
      status: 200,
      headers: { "Content-Type": "application/json" },
    },
  );
}

function renderList(
  role: "operator" | "viewer" = "operator",
  hasSelectedOperator = true,
) {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <CampaignListView role={role} hasSelectedOperator={hasSelectedOperator} />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("Campaign list", () => {
  it("renders fixed backend order, owner projection, and only the detail action", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      response({
        items: [
          {
            id: "campaign-2",
            department_id: "dept",
            owner_operator_id: "op-2",
            owner: { id: "op-2", name: "停用负责人", status: "disabled" },
            created_by_operator_id: "op-2",
            name: "后更新的活动",
            status: "ACTIVE",
            review_mode: "FIRST_N",
            review_count: 50,
            duplicate_history_policy: "ALLOW_WITH_WARNING",
            duplicate_window_days: null,
            version: 2,
            created_at: "2026-08-20T01:00:00Z",
            updated_at: "2026-08-20T02:00:00Z",
          },
          {
            id: "campaign-1",
            department_id: "dept",
            owner_operator_id: "op-1",
            owner: { id: "op-1", name: "王小明", status: "active" },
            created_by_operator_id: "op-1",
            name: "先更新的活动",
            status: "DRAFT",
            review_mode: "FIRST_N",
            review_count: 50,
            duplicate_history_policy: "ALLOW_WITH_WARNING",
            duplicate_window_days: null,
            version: 1,
            created_at: "2026-08-20T00:00:00Z",
            updated_at: "2026-08-20T01:00:00Z",
          },
        ],
        next_cursor: null,
      }),
    );

    renderList();

    expect(await screen.findByText("后更新的活动")).toBeInTheDocument();
    expect(screen.getByText("停用负责人")).toBeInTheDocument();
    expect(screen.getByText("已停用")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "查看详情" })).toHaveLength(2);
    expect(screen.queryByText(/共 .*活动/)).not.toBeInTheDocument();
    expect(screen.queryByText("成员数量")).not.toBeInTheDocument();
  });

  it("keeps Viewer read-only and shows no create affordance", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      response({ items: [], next_cursor: null }),
    );
    renderList("viewer", false);
    expect(await screen.findByText("暂无拓客活动")).toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "新建活动" }),
      ).not.toBeInTheDocument(),
    );
  });
});
