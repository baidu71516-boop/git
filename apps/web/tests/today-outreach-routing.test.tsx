import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import TodayOutreachPage from "../src/app/outreach/today/page";
import { SidebarNav } from "../src/components/navigation/sidebar-nav";

vi.mock("@/components/auth-shell", () => ({
  AuthShell: ({ workspace }: { workspace: string }) => <div>{workspace}</div>,
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/outreach/today",
}));

describe("today outreach route and navigation", () => {
  it("mounts the formal /outreach/today route through the existing AuthShell", () => {
    render(<TodayOutreachPage />);
    expect(screen.getByText("outreach-today")).toBeInTheDocument();
  });

  it("puts 今日触达 ahead of 达人库 in the workspace navigation", () => {
    render(
      <SidebarNav
        department="测试部门"
        operator="王小明"
        effectiveRole="操作员"
        onLogout={() => undefined}
      />,
    );
    const links = screen.getAllByRole("link");
    const labels = links.map((link) => link.textContent);
    expect(labels.indexOf("今日触达")).toBeLessThan(labels.indexOf("达人库"));
    expect(
      screen.getAllByRole("link", { name: "今日触达" })[0],
    ).toHaveAttribute("href", "/outreach/today");
  });
});
