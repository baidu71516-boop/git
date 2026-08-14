import { Tag } from "antd";
import type { ReactNode } from "react";

type StatusBadgeProps = {
  children: ReactNode;
  tone?: "default" | "success" | "warning" | "danger" | "processing";
};

export function StatusBadge({ children, tone = "default" }: StatusBadgeProps) {
  return <Tag color={tone === "default" ? undefined : tone}>{children}</Tag>;
}
