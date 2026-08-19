"use client";

import { Alert } from "antd";
import { useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { PageHeader } from "@/components/ui/page-header";
import { ApiClientError } from "@/lib/api/client";

import { todayQueryKey } from "./api";
import { formatAsOf, formatBusinessDate } from "./formatters";
import { TodayFilterBar } from "./components/today-filter-bar";
import {
  useToday,
  useTodayCampaigns,
  useTodayOperators,
  useTodayTracks,
} from "./queries";
import type { TodayFilters } from "./types";
import { TodayResultsPanel, isActiveTodayFilter } from "./today-view";

const initialFilters: TodayFilters = { work_kind: "ALL" };

export function TodayWorkspace() {
  const [filters, setFilters] = useState<TodayFilters>(initialFilters);
  const [notice, setNotice] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const todayQuery = useToday(filters);
  const operatorsQuery = useTodayOperators();
  const campaignsQuery = useTodayCampaigns();
  const tracksQuery = useTodayTracks();

  const pages = todayQuery.data?.pages;
  const items = useMemo(
    () => pages?.flatMap((page) => page.items) ?? [],
    [pages],
  );
  const firstPage = pages?.[0];
  const activeFilters = isActiveTodayFilter(filters);
  const operatorOptions = operatorsQuery.isSuccess
    ? operatorsQuery.data
    : undefined;
  const campaignOptions = campaignsQuery.isSuccess
    ? campaignsQuery.data
    : undefined;
  const trackOptions = tracksQuery.isSuccess ? tracksQuery.data : undefined;

  function changeFilters(changes: Partial<TodayFilters>) {
    if (pages?.length)
      setNotice("列表条件已发生变化，已重新从第一批任务加载。");
    setFilters((current) => ({ ...current, ...changes }));
  }

  function resetFilters() {
    if (pages?.length)
      setNotice("列表条件已发生变化，已重新从第一批任务加载。");
    setFilters(initialFilters);
  }

  async function reloadAfterCursorMismatch() {
    if (
      todayQuery.error instanceof ApiClientError &&
      todayQuery.error.code === "CURSOR_MISMATCH"
    ) {
      await queryClient.resetQueries({ queryKey: todayQueryKey(filters) });
      setNotice("列表条件已发生变化，已重新从第一批任务加载。");
      return;
    }
    await todayQuery.refetch();
  }

  async function loadNextPage() {
    try {
      await todayQuery.fetchNextPage();
    } catch (error) {
      if (error instanceof ApiClientError && error.code === "CURSOR_MISMATCH") {
        setNotice("列表条件已发生变化，已重新从第一批任务加载。");
        await queryClient.resetQueries({ queryKey: todayQueryKey(filters) });
      }
    }
  }

  const headingExtra = firstPage ? (
    <div className="today-as-of" aria-label="数据更新时间">
      日期：{formatBusinessDate(firstPage.business_date)} · 更新于{" "}
      {formatAsOf(firstPage.as_of)}
    </div>
  ) : null;

  return (
    <section className="today-workspace" aria-label="今日触达">
      <PageHeader
        title="今日触达"
        description="查看今天需要推进的触达任务。"
        extra={headingExtra}
      />
      <TodayFilterBar
        filters={filters}
        operators={operatorOptions}
        campaigns={campaignOptions}
        tracks={trackOptions}
        onChange={changeFilters}
        onReset={resetFilters}
      />
      {notice ? (
        <Alert
          className="today-query-notice"
          type="info"
          showIcon
          closable
          message={notice}
          onClose={() => setNotice(null)}
        />
      ) : null}
      <TodayResultsPanel
        state={
          todayQuery.isPending
            ? "loading"
            : todayQuery.isError
              ? "error"
              : items.length === 0
                ? "empty"
                : "ready"
        }
        items={items}
        businessDate={firstPage?.business_date}
        asOf={firstPage?.as_of}
        operators={operatorOptions}
        activeFilters={activeFilters}
        hasNextPage={todayQuery.hasNextPage}
        loadingMore={todayQuery.isFetchingNextPage}
        onLoadMore={() => void loadNextPage()}
        onReset={resetFilters}
        onRetry={() => void reloadAfterCursorMismatch()}
      />
    </section>
  );
}
