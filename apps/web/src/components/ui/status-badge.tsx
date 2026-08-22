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
  const color = tone === "danger" ? "red" : tone;
  return (
    <Tag color={color === "default" ? undefined : color} className={className}>
      {children}
    </Tag>
  );
}
