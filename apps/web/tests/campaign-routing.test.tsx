import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import CampaignsPage from "../src/app/campaigns/page";
import CampaignDetailPage from "../src/app/campaigns/[id]/page";
import { SidebarNav } from "../src/components/navigation/sidebar-nav";

vi.mock("@/components/auth-shell", () => ({
  AuthShell: ({
    workspace,
    campaignId,
  }: {
    workspace: string;
    campaignId?: string;
  }) => <div>{campaignId ? `${workspace}:${campaignId}` : workspace}</div>,
}));

vi.mock("next/navigation", () => ({ usePathname: () => "/campaigns" }));

describe("Campaign routing and navigation", () => {
  it("mounts the formal routes through AuthShell", async () => {
    render(<CampaignsPage />);
    expect(await screen.findByText("campaigns")).toBeInTheDocument();
    render(
      await CampaignDetailPage({
        params: Promise.resolve({ id: "campaign-1" }),
      }),
    );
    expect(await screen.findByText("campaigns:campaign-1")).toBeInTheDocument();
  });

  it("places 拓客活动 between 今日触达 and 达人库", () => {
    render(
      <SidebarNav
        department="测试部门"
        operator="王小明"
        effectiveRole="操作员"
        onLogout={() => undefined}
      />,
    );
    const labels = screen.getAllByRole("link").map((link) => link.textContent);
    expect(labels.indexOf("今日触达")).toBeLessThan(labels.indexOf("拓客活动"));
    expect(labels.indexOf("拓客活动")).toBeLessThan(labels.indexOf("达人库"));
    expect(
      screen.getAllByRole("link", { name: "拓客活动" })[0],
    ).toHaveAttribute("href", "/campaigns");
  });
});
