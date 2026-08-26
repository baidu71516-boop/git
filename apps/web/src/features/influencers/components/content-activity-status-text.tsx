import { Typography } from "antd";

import {
  contentActivityDisplay,
  contentActivityLatestAttemptLabel,
  formatMetricsTimestamp,
} from "../formatters";
import type { PlatformAccountSummary } from "../types";

const { Text } = Typography;

/** Render only server-derived, platform-account-scoped Content Activity facts. */
export function ContentActivityStatusText({
  account,
  showLastPublication = true,
}: {
  account: PlatformAccountSummary;
  showLastPublication?: boolean;
}) {
  const presentation = contentActivityDisplay(
    account.content_activity_state,
    account.content_activity_trusted_result,
  );
  const inactiveDays = account.content_activity_inactive_days;
  const lastPublication = account.content_activity_last_publication_at;
  const latestAttempt = contentActivityLatestAttemptLabel(
    account.content_activity_latest_attempt_observation_status,
    account.content_activity_latest_attempt_result,
  );
  const note =
    account.content_activity_state === "current" &&
    account.content_activity_trusted_result === "PUBLICATION_FOUND" &&
    inactiveDays !== null &&
    inactiveDays !== undefined
      ? `${inactiveDays} 天未更新`
      : (account.content_activity_state === "unknown" ||
            account.content_activity_state === "last_known") &&
          account.content_activity_latest_attempt_observation_status
        ? latestAttempt
        : null;

  return (
    <span className="content-activity-status-stack">
      <Text
        className={`content-activity-status content-activity-status-${presentation.tone}`}
      >
        {presentation.label}
      </Text>
      {note ? <Text type="secondary">{note}</Text> : null}
      {showLastPublication && lastPublication ? (
        <Text type="secondary">
          最后公开：{formatMetricsTimestamp(lastPublication)}
        </Text>
      ) : null}
    </span>
  );
}
