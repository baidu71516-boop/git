import { DownOutlined, LogoutOutlined, UserOutlined } from "@ant-design/icons";
import { Avatar, Dropdown, Typography } from "antd";
import type { MenuProps } from "antd";

const { Text } = Typography;

type UserMenuProps = {
  department: string;
  operator: string | null;
  role: string;
  onLogout: () => void;
  loading?: boolean;
};

export function UserMenu({
  department,
  operator,
  role,
  onLogout,
  loading = false,
}: UserMenuProps) {
  const items: MenuProps["items"] = [
    {
      key: "identity",
      disabled: true,
      label: `${department} · ${operator ?? "待选择"} · ${role}`,
    },
    { type: "divider" },
    {
      key: "logout",
      icon: <LogoutOutlined />,
      label: "退出登录",
      danger: true,
      disabled: loading,
    },
  ];

  return (
    <Dropdown
      trigger={["click"]}
      menu={{ items, onClick: ({ key }) => key === "logout" && onLogout() }}
    >
      <button
        className="user-menu-trigger"
        type="button"
        aria-label="打开用户菜单"
      >
        <Avatar size="small" icon={<UserOutlined />} />
        <span className="user-menu-copy">
          <Text strong>{operator ?? "待选择"}</Text>
        </span>
        <DownOutlined />
      </button>
    </Dropdown>
  );
}
