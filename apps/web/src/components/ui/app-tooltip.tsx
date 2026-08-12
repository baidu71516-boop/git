import { Tooltip } from "antd";
import type { ReactNode } from "react";

export function AppTooltip({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return <Tooltip title={title}>{children}</Tooltip>;
}
