"use client";

import { ConfigProvider } from "antd";
import type { ReactNode } from "react";

import { ComplianceFooter } from "@/components/compliance-footer";
import { SidebarNav } from "@/components/navigation/sidebar-nav";
import { PageHeader } from "@/components/ui/page-header";
import { UserMenu } from "@/components/ui/user-menu";

const theme = {
  token: {
    colorPrimary: "#1677ff",
    colorInfo: "#1677ff",
    colorSuccess: "#16a34a",
    colorWarning: "#d97706",
    colorError: "#dc2626",
    colorBgLayout: "#f3f6fb",
    colorBgContainer: "#ffffff",
    colorBorder: "#e2e8f0",
    colorText: "#172033",
    colorTextSecondary: "#667085",
    borderRadius: 10,
    borderRadiusLG: 12,
    controlHeight: 38,
    boxShadow: "0 8px 24px rgb(25 42 70 / 6%)",
  },
  components: {
    Card: { borderRadiusLG: 12 },
    Button: { borderRadius: 8 },
    Input: { borderRadius: 8 },
    Select: { borderRadius: 8 },
  },
};

export function AppShell({
  title,
  description,
  department,
  operator,
  role,
  onLogout,
  logoutLoading,
  children,
}: {
  title: string;
  description?: string;
  department: string;
  operator: string | null;
  role: string;
  onLogout: () => void;
  logoutLoading?: boolean;
  children: ReactNode;
}) {
  return (
    <ConfigProvider theme={theme}>
      <div className="app-shell">
        <SidebarNav
          department={department}
          operator={operator}
          role={role}
          onLogout={onLogout}
          logoutLoading={logoutLoading}
        />
        <div className="app-main">
          <header className="app-topbar">
            <PageHeader title={title} />
            <UserMenu
              department={department}
              operator={operator}
              role={role}
              onLogout={onLogout}
              loading={logoutLoading}
            />
          </header>
          <main className="app-content">
            {description ? (
              <div className="app-content-description">{description}</div>
            ) : null}
            {children}
          </main>
          <ComplianceFooter />
        </div>
      </div>
    </ConfigProvider>
  );
}
