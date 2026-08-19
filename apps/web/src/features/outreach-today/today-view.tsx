"use client";

import { ReloadOutlined } from "@ant-design/icons";
import { Button, Card, Space, Typography } from "antd";

import { AppEmpty } from "@/components/ui/app-empty";

import { TodayTable, TodayTableError } from "./components/today-table";
import type { OperatorOption, TodayFilters, TodayItem } from "./types";

const { Text } = Typography;

export type TodayListState = "loading" | "error" | "empty" | "ready";

export function isActiveTodayFilter(filters: TodayFilters): boolean {
  return (
    filters.work_kind !== "ALL" ||
    Object.entries(filters).some(
      ([key, value]) =>
        key !== "work_kind" && value !== undefined && value !== "",
    )
  );
}

export function TodayResultsPanel({
  state,
  items,
  businessDate,
  asOf,
  operators,
  activeFilters,
  hasNextPage,
  loadingMore,
  onLoadMore,
  onReset,
  onRetry,
}: {
  state: TodayListState;
  items?: TodayItem[];
  businessDate?: string;
  asOf?: string;
  operators?: OperatorOption[];
  activeFilters: boolean;
  hasNextPage?: boolean;
  loadingMore?: boolean;
  onLoadMore?: () => void;
  onReset: () => void;
  onRetry: () => void;
}) {
  return (
    <Card className="today-list-card" variant="borderless">
      {state === "loading" ? (
        <TodayTable loading />
      ) : state === "error" ? (
        <TodayTableError onRetry={onRetry} />
      ) : state === "empty" ? (
        <div className="today-empty-state">
          <AppEmpty
            description={
              activeFilters
                ? "没有符合当前筛选条件的任务"
                : "今天没有待处理的触达任务"
            }
          />
          {activeFilters ? (
            <Button onClick={onReset}>重置筛选</Button>
          ) : (
            <Text type="secondary">
              当前没有符合条件、需要在今天推进的任务。
            </Text>
          )}
        </div>
      ) : (
        <Space orientation="vertical" size={0} className="full-width">
          <TodayTable
            items={items}
            businessDate={businessDate}
            asOf={asOf}
            operators={operators}
          />
          <div className="today-pagination-footer">
            {hasNextPage ? (
              <Button
                icon={<ReloadOutlined aria-hidden="true" />}
                loading={loadingMore}
                onClick={onLoadMore}
              >
                加载更多
              </Button>
            ) : (
              <Text type="secondary">已经到底了</Text>
            )}
          </div>
        </Space>
      )}
    </Card>
  );
}
