import { render, screen } from "@testing-library/react";
import { vi } from "vitest";

import { AppShell } from "../src/components/app-shell";

vi.mock("../src/components/navigation/sidebar-nav", () => ({
  SidebarNav: () => <aside>导航</aside>,
}));

vi.mock("../src/components/ui/page-header", () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

vi.mock("../src/components/ui/user-menu", () => ({
  UserMenu: () => <div>用户菜单</div>,
}));

describe("AppShell", () => {
  it("renders the ICP footer on authenticated application pages", () => {
    render(
      <AppShell
        title="数据采集"
        department="商务部"
        operator="操作人"
        effectiveRole="操作员"
        onLogout={vi.fn()}
      >
        <div>页面内容</div>
      </AppShell>,
    );

    expect(
      screen.getByRole("link", { name: "鄂ICP备2026044999号" }),
    ).toBeInTheDocument();
  });
});
