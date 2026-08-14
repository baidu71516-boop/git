import { Typography } from "antd";

import { AppTooltip } from "@/components/ui/app-tooltip";

import { freshnessDisplay, freshnessExplanation } from "../formatters";
import type { FreshnessStatus } from "../types";

const { Text } = Typography;

export function FreshnessStatusText({
  status,
  requiresRefresh,
  showRefreshNeed = false,
}: {
  status: FreshnessStatus | null;
  requiresRefresh: boolean;
  showRefreshNeed?: boolean;
}) {
  const presentation = freshnessDisplay(status);
  const explanation = freshnessExplanation(status, requiresRefresh);
  const content = (
    <span className="freshness-status-stack">
      <Text
        className={`freshness-status freshness-status-${presentation.tone}`}
      >
        {presentation.label}
      </Text>
      {showRefreshNeed && requiresRefresh ? (
        <Text className="freshness-refresh-needed">需要更新</Text>
      ) : null}
    </span>
  );

  return explanation ? (
    <AppTooltip title={explanation}>{content}</AppTooltip>
  ) : (
    content
  );
}
