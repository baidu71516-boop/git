"use client";

import { ImportOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Space } from "antd";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMemo } from "react";

import { AppEmpty } from "@/components/ui/app-empty";
import { AppLoading } from "@/components/ui/app-loading";
import { PageHeader } from "@/components/ui/page-header";
import { ApiClientError } from "@/lib/api/client";

import { INFLUENCER_QUERY_PARAMETERS } from "./api";
import {
  InfluencerFilterBar,
  validateFollowerRange,
} from "./components/influencer-filter-bar";
import { InfluencerPagination } from "./components/influencer-pagination";
import { InfluencerTable } from "./components/influencer-table";
import { useInfluencerFilterOptions, useInfluencerList } from "./queries";
import type { InfluencerListQueryParams } from "./types";

function readQuery(search: URLSearchParams): InfluencerListQueryParams {
  const query: InfluencerListQueryParams = {};
  for (const name of INFLUENCER_QUERY_PARAMETERS) {
    const value = search.get(name);
    if (value !== null) query[name] = value;
  }
  query.page ??= "1";
  query.page_size ??= "50";
  return query;
}

function displayInteger(value: string | undefined, fallback: number): number {
  if (!value || !/^\d+$/.test(value)) return fallback;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : fallback;
}

function listErrorMessage(error: Error | null): string {
  if (error instanceof ApiClientError && error.status === 422) {
    return "筛选条件无效，请清除筛选后重试。";
  }
  return "达人数据加载失败";
}

export function InfluencerWorkspace() {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const searchString = searchParams.toString();
  const query = useMemo(
    () => readQuery(new URLSearchParams(searchString)),
    [searchString],
  );
  const validationMessage = validateFollowerRange(
    query.followers_min ?? "",
    query.followers_max ?? "",
  );
  const listQuery = useInfluencerList(query, validationMessage === null);
  const optionsQuery = useInfluencerFilterOptions();

  function currentFrozenSearch(): URLSearchParams {
    const next = new URLSearchParams();
    for (const name of INFLUENCER_QUERY_PARAMETERS) {
      const value = searchParams.get(name);
      if (value !== null) next.set(name, value);
    }
    return next;
  }

  function navigate(next: URLSearchParams) {
    const nextSearch = next.toString();
    router.replace(nextSearch ? `${pathname}?${nextSearch}` : pathname);
  }

  function updateFilters(changes: Partial<InfluencerListQueryParams>) {
    const next = currentFrozenSearch();
    for (const [name, rawValue] of Object.entries(changes)) {
      const value =
        name === "q" || name === "tag" ? rawValue?.trim() : rawValue;
      if (value) next.set(name, value);
      else next.delete(name);
    }
    next.delete("page");
    navigate(next);
  }

  function changePage(page: number) {
    const next = currentFrozenSearch();
    if (page <= 1) next.delete("page");
    else next.set("page", String(page));
    navigate(next);
  }

  const page = displayInteger(query.page, 1);
  const pageSize = displayInteger(query.page_size, 50);
  const hasActiveFilters = Boolean(
    query.q ||
    query.tag ||
    query.followers_min ||
    query.followers_max ||
    query.owner_operator_id ||
    query.crm_stage,
  );

  return (
    <section className="influencer-workspace" aria-label="达人库">
      <div className="influencer-page-heading">
        <PageHeader
          title="达人库"
          description="公司共享达人资源 · 统一查看、筛选和管理"
          extra={
            <Button
              type="primary"
              icon={<ImportOutlined aria-hidden="true" />}
              href="/"
            >
              导入达人
            </Button>
          }
        />
      </div>

      <InfluencerFilterBar
        key={searchString}
        query={query}
        options={optionsQuery.data}
        optionsLoading={optionsQuery.isPending}
        optionsError={optionsQuery.isError}
        validationMessage={validationMessage}
        onRetryOptions={() => void optionsQuery.refetch()}
        onChange={updateFilters}
        onClear={() => router.replace(pathname)}
      />

      <Card variant="borderless" className="influencer-list-card">
        {validationMessage ? null : listQuery.isPending ? (
          <div className="influencer-list-state">
            <AppLoading label="正在加载达人列表" />
          </div>
        ) : listQuery.isError ? (
          <Alert
            type="error"
            showIcon
            message={listErrorMessage(listQuery.error)}
            description="请检查网络连接后重新加载。"
            action={
              <Space>
                <Button onClick={() => void listQuery.refetch()}>
                  重新加载
                </Button>
                {listQuery.error instanceof ApiClientError &&
                listQuery.error.status === 422 ? (
                  <Button onClick={() => router.replace(pathname)}>
                    清除筛选
                  </Button>
                ) : null}
              </Space>
            }
          />
        ) : listQuery.data && listQuery.data.items.length === 0 ? (
          <AppEmpty
            description={
              hasActiveFilters
                ? "没有符合条件的达人。试试调整或清除筛选条件。"
                : "达人库暂无数据，可以先从数据采集导入达人。"
            }
          />
        ) : listQuery.data ? (
          <Space orientation="vertical" size="middle" className="full-width">
            <InfluencerTable items={listQuery.data.items} />
            <InfluencerPagination
              page={listQuery.data.page ?? page}
              pageSize={listQuery.data.page_size ?? pageSize}
              total={listQuery.data.total}
              onPageChange={changePage}
            />
          </Space>
        ) : null}
      </Card>
    </section>
  );
}
