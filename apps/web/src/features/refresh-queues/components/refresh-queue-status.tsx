import { Badge } from "antd";

import { itemStatusPresentation, queueStatusPresentation } from "../formatters";
import type { RefreshQueueItemStatus, RefreshQueueStatus } from "../types";

export function RefreshQueueStatusBadge({
  status,
}: {
  status: RefreshQueueStatus;
}) {
  const presentation = queueStatusPresentation(status);
  return <Badge status={presentation.color} text={presentation.label} />;
}

export function RefreshQueueItemStatusBadge({
  status,
}: {
  status: RefreshQueueItemStatus;
}) {
  const presentation = itemStatusPresentation(status);
  return <Badge status={presentation.color} text={presentation.label} />;
}
