"use client";

import {
  DatabaseOutlined,
  FlagOutlined,
  HistoryOutlined,
  LogoutOutlined,
  MenuOutlined,
  ProfileOutlined,
  SyncOutlined,
  ThunderboltOutlined,
  UsergroupAddOutlined,
} from "@ant-design/icons";
import { Button, Drawer, Typography } from "antd";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { StatusBadge } from "@/components/ui/status-badge";

const { Text } = Typography;

type SidebarNavProps = {
  department: string;
  operator: string | null;
  effectiveRole: string | null;
  showPermissionsNav?: boolean;
  onLogout: () => void;
  logoutLoading?: boolean;
};

const navGroups = [
  {
    label: "工作区",
    items: [
      {
        label: "今日触达",
        href: "/outreach/today",
        icon: <ThunderboltOutlined aria-hidden="true" />,
      },
      {
        label: "拓客活动",
        href: "/campaigns",
        icon: <FlagOutlined aria-hidden="true" />,
      },
      {
        label: "候选池",
        href: "/candidate-pools",
        icon: <ProfileOutlined aria-hidden="true" />,
      },
      {
        label: "潜在客户",
        href: "/buyer-prospects",
        icon: <UsergroupAddOutlined aria-hidden="true" />,
      },
      {
        label: "达人库",
        href: "/influencers",
        icon: <UsergroupAddOutlined aria-hidden="true" />,
      },
    ],
  },
  {
    label: "数据",
    items: [
      {
        label: "数据采集",
        href: "/",
        icon: <DatabaseOutlined aria-hidden="true" />,
      },
      {
        label: "导入记录",
        href: "/import-jobs",
        icon: <HistoryOutlined aria-hidden="true" />,
      },
      {
        label: "数据更新",
        href: "/refresh-queues",
        icon: <SyncOutlined aria-hidden="true" />,
      },
    ],
  },
] as const;

function NavigationLinks({
  showPermissionsNav = false,
  onNavigate,
}: {
  showPermissionsNav?: boolean;
  onNavigate?: () => void;
}) {
  const pathname = usePathname();
  return (
    <nav className="sidebar-links" aria-label="主导航">
      {navGroups.map((group) => (
        <div className="sidebar-group" key={group.label}>
          <Text className="sidebar-group-label">{group.label}</Text>
          {group.items.map((item) => {
            const active =
              item.href === "/"
                ? pathname === "/"
                : pathname.startsWith(item.href);
            return (
              <Link
                className={`sidebar-link${active ? " is-active" : ""}`}
                href={item.href}
                aria-current={active ? "page" : undefined}
                key={item.href}
                onClick={onNavigate}
              >
                {item.icon}
                <span>{item.label}</span>
              </Link>
            );
          })}
        </div>
      ))}
      {showPermissionsNav ? (
        <div className="sidebar-group">
          <Text className="sidebar-group-label">管理</Text>
          <Link
            className={`sidebar-link${
              pathname.startsWith("/admin/permissions") ? " is-active" : ""
            }`}
            href="/admin/permissions"
            aria-current={
              pathname.startsWith("/admin/permissions") ? "page" : undefined
            }
            onClick={onNavigate}
          >
            <ProfileOutlined aria-hidden="true" />
            <span>权限管理</span>
          </Link>
        </div>
      ) : null}
    </nav>
  );
}

export function SidebarNav({
  department,
  operator,
  effectiveRole,
  showPermissionsNav,
  onLogout,
  logoutLoading,
}: SidebarNavProps) {
  const [open, setOpen] = useState(false);
  const content = (
    <>
      <div className="sidebar-brand">
        <div className="brand-mark">达</div>
        <div>
          <div className="brand-name">达人智能触达</div>
          <div className="brand-caption">内部工作台</div>
        </div>
      </div>
      <NavigationLinks
        showPermissionsNav={showPermissionsNav}
        onNavigate={() => setOpen(false)}
      />
      <div className="sidebar-identity">
        <div className="sidebar-identity-label">当前身份</div>
        <div className="sidebar-identity-value">{department}</div>
        <div className="sidebar-identity-meta">{operator ?? "待选择"}</div>
        {effectiveRole ? (
          <StatusBadge tone="processing">{effectiveRole}</StatusBadge>
        ) : null}
        <Button
          className="sidebar-logout"
          type="text"
          danger
          icon={<LogoutOutlined aria-hidden="true" />}
          loading={logoutLoading}
          onClick={onLogout}
        >
          退出登录
        </Button>
      </div>
    </>
  );

  return (
    <>
      <aside className="app-sidebar">{content}</aside>
      <Button
        className="mobile-nav-trigger"
        type="text"
        icon={<MenuOutlined aria-hidden="true" />}
        aria-label="打开导航"
        onClick={() => setOpen(true)}
      />
      <Drawer
        className="mobile-nav-drawer"
        placement="left"
        open={open}
        onClose={() => setOpen(false)}
        size="small"
        closable
        title={null}
      >
        <div className="mobile-sidebar-content">{content}</div>
      </Drawer>
    </>
  );
}
