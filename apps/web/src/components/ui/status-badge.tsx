import { Tag } from "antd";
import type { ReactNode } from "react";

type StatusBadgeProps = {
  children: ReactNode;
  tone?: "default" | "success" | "warning" | "danger" | "processing";
  className?: string;
};

export function StatusBadge({
  children,
  tone = "default",
  className,
}: StatusBadgeProps) {
  return (
    <Tag color={tone === "default" ? undefined : tone} className={className}>
      {children}
    </Tag>
  );
}
