"use client";

import { Alert, Button, Card, Empty, Space, Spin, Typography } from "antd";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMemo } from "react";

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

const { Paragraph, Title } = Typography;

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
  return "达人列表加载失败";
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

  return (
    <section
      className="influencer-workspace"
      aria-labelledby="influencer-title"
    >
      <Card variant="borderless" className="influencer-heading-card">
        <Title level={3} id="influencer-title">
          达人库
        </Title>
        <Paragraph type="secondary">
          公司级共享达人资源；筛选状态保存在当前 URL 中。
        </Paragraph>
      </Card>

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
            <Spin size="large" />
            <span>正在加载达人列表</span>
          </div>
        ) : listQuery.isError ? (
          <Alert
            type="error"
            showIcon
            message={listErrorMessage(listQuery.error)}
            description="网络错误不会被当作空列表。"
            action={
              <Space>
                <Button onClick={() => void listQuery.refetch()}>
                  重试达人列表
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
          <Empty description="没有符合条件的达人" />
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
