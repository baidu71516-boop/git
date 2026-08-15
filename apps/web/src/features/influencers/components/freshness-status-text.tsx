import { Typography } from "antd";

import { AppTooltip } from "@/components/ui/app-tooltip";

import {
  freshnessSupportText,
  freshnessDisplay,
  freshnessExplanation,
} from "../formatters";
import type { FreshnessStatus } from "../types";

const { Text } = Typography;

export function FreshnessStatusText({
  status,
  requiresRefresh,
  freshnessAgeDays,
  showRefreshNeed = false,
}: {
  status: FreshnessStatus | null;
  requiresRefresh: boolean;
  freshnessAgeDays?: number | null;
  showRefreshNeed?: boolean;
}) {
  const presentation = freshnessDisplay(status);
  const freshnessAge =
    freshnessAgeDays === null || freshnessAgeDays === undefined
      ? null
      : `距上次采集 ${freshnessAgeDays} 天`;
  const auxiliaryText =
    freshnessAge ?? freshnessSupportText(status, requiresRefresh);
  const explanation = freshnessExplanation(status, requiresRefresh);
  const content = (
    <span className="freshness-status-stack">
      <Text
        className={`freshness-status freshness-status-${presentation.tone}`}
      >
        {presentation.label}
      </Text>
      {auxiliaryText ? (
        <Text className="freshness-status-note">{auxiliaryText}</Text>
      ) : null}
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
