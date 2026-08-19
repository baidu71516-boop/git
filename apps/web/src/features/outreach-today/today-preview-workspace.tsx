"use client";

import { Select, Typography } from "antd";
import { useMemo, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { PageHeader } from "@/components/ui/page-header";

import { TodayFilterBar } from "./components/today-filter-bar";
import { formatAsOf, formatBusinessDate } from "./formatters";
import {
  todayPreviewOptions,
  todayPreviewSceneByKey,
  todayPreviewScenes,
  type TodayPreviewSceneKey,
} from "./preview-fixtures";
import { TodayResultsPanel, isActiveTodayFilter } from "./today-view";

const { Text } = Typography;
const initialSceneKey: TodayPreviewSceneKey = "default";

function noOperation() {
  return undefined;
}

export function TodayPreviewWorkspace() {
  const [sceneKey, setSceneKey] =
    useState<TodayPreviewSceneKey>(initialSceneKey);
  const scene = todayPreviewSceneByKey.get(sceneKey) ?? todayPreviewScenes[0];
  const [filters, setFilters] = useState(scene.filters);
  const [moreFiltersOpen, setMoreFiltersOpen] = useState(
    scene.moreFiltersOpen ?? false,
  );
  const filterOptions = useMemo(() => todayPreviewOptions, []);

  function selectScene(nextKey: TodayPreviewSceneKey) {
    const nextScene = todayPreviewSceneByKey.get(nextKey);
    if (!nextScene) return;
    setSceneKey(nextKey);
    setFilters(nextScene.filters);
    setMoreFiltersOpen(nextScene.moreFiltersOpen ?? false);
  }

  function resetFilters() {
    setFilters({ work_kind: "ALL" });
    setMoreFiltersOpen(false);
  }

  return (
    <AppShell
      title="今日触达"
      department="开发预览"
      operator="内存 fixture"
      role="仅供开发"
      onLogout={noOperation}
    >
      <section
        className="today-workspace today-preview-workspace"
        aria-label="今日触达开发预览"
      >
        <PageHeader
          title="今日触达"
          description="查看今天需要推进的触达任务。"
          extra={
            <div className="today-as-of" aria-label="数据更新时间">
              日期：{formatBusinessDate(scene.page.business_date)} · 更新于{" "}
              {formatAsOf(scene.page.as_of)}
            </div>
          }
        />
        <div className="today-preview-toolbar">
          <Text className="today-preview-toolbar-label">开发预览状态</Text>
          <label className="today-preview-scene-select">
            <span>预览场景</span>
            <Select
              aria-label="预览场景"
              value={scene.key}
              options={todayPreviewScenes.map((option) => ({
                label: option.label,
                value: option.key,
              }))}
              onChange={(value: TodayPreviewSceneKey) => selectScene(value)}
            />
          </label>
        </div>
        <TodayFilterBar
          filters={filters}
          operators={filterOptions.operators}
          campaigns={filterOptions.campaigns}
          tracks={filterOptions.tracks}
          onChange={(changes) =>
            setFilters((current) => ({ ...current, ...changes }))
          }
          onReset={resetFilters}
          moreFiltersOpen={moreFiltersOpen}
          onMoreFiltersOpenChange={setMoreFiltersOpen}
        />
        <TodayResultsPanel
          state={scene.state}
          items={scene.page.items}
          businessDate={scene.page.business_date}
          asOf={scene.page.as_of}
          operators={filterOptions.operators}
          activeFilters={isActiveTodayFilter(filters)}
          hasNextPage={scene.page.next_cursor !== null}
          onLoadMore={noOperation}
          onReset={resetFilters}
          onRetry={noOperation}
        />
      </section>
    </AppShell>
  );
}
