import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { BuyerProspectRuleForm } from "@/features/buyer-prospects/buyer-prospect-rule-form";
import type {
  BuyerProspectRule,
  BuyerProspectRuleCreateRequest,
  BuyerProspectRuleOptions,
} from "@/features/buyer-prospects/types";

const options: BuyerProspectRuleOptions = {
  taxonomy_category_ids: ["AUTO", "BEAUTY", "AI_SHORT_DRAMA"],
  taxonomy_categories: [
    { id: "AUTO", label: "汽车" },
    { id: "BEAUTY", label: "美妆" },
    { id: "AI_SHORT_DRAMA", label: "AI短剧" },
  ],
  source_collection_jobs: [
    {
      id: "collection-film",
      name: "灰豚 2026-09-02 影视批次",
      industry: "影视",
      subdirection: "剧情 / 娱乐",
    },
  ],
  operators: [],
};

function rule(categoryIds: string[]): BuyerProspectRule {
  return {
    id: "rule-1",
    department_id: "department-1",
    owner_operator_id: "operator-1",
    owner: { id: "operator-1", name: "操作人", status: "active" },
    name: "影视跨类目潜客",
    status: "ACTIVE",
    version: 1,
    current_policy_id: "policy-1",
    current_policy_version: 1,
    category_ids: categoryIds,
    follower_min: null,
    follower_max: null,
    buyer_lead_tiers: ["CHANGED"],
    source_collection_job_id: "collection-film",
    recent_collection_window: "30",
    prospect_owner_filter: "ANY",
    prospect_owner_operator_id: null,
    exclude_contacted: false,
    latest_run: null,
    created_at: "2026-09-02T00:00:00Z",
    updated_at: "2026-09-02T00:00:00Z",
  };
}

function renderForm(categoryIds: string[]) {
  const onSubmit = vi.fn<(values: BuyerProspectRuleCreateRequest) => void>();
  render(
    <BuyerProspectRuleForm
      options={options}
      rule={rule(categoryIds)}
      submitLabel="保存"
      loading={false}
      error={null}
      onSubmit={(values) => onSubmit(values)}
    />,
  );
  return onSubmit;
}

describe("Buyer prospect rule form", () => {
  it("keeps the current creator category optional and submits empty canonical IDs", async () => {
    const onSubmit = renderForm([]);

    expect(screen.getByText("当前达人类目（可选）")).toBeInTheDocument();
    expect(screen.queryByText("领域 / 赛道")).not.toBeInTheDocument();
    expect(screen.getByText("不限当前达人类目")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    expect(onSubmit.mock.calls[0]?.[0].category_ids).toEqual([]);
  });

  it("shows source context separately and renders Chinese taxonomy labels while preserving IDs", async () => {
    const onSubmit = renderForm(["AUTO"]);

    expect(screen.getByText("来源上下文")).toBeInTheDocument();
    expect(screen.getByText("来源行业")).toBeInTheDocument();
    expect(screen.getByText("影视")).toBeInTheDocument();
    expect(screen.getByText("来源赛道")).toBeInTheDocument();
    expect(screen.getByText("剧情 / 娱乐")).toBeInTheDocument();
    expect(screen.getByText("汽车")).toBeInTheDocument();
    expect(screen.queryByText("AUTO")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    expect(onSubmit.mock.calls[0]?.[0].category_ids).toEqual(["AUTO"]);
  });
});
