"use client";

import { ClearOutlined, SearchOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Col,
  Input,
  Row,
  Select,
  Space,
  Spin,
  Typography,
} from "antd";
import { useState } from "react";

import type {
  FreshnessStatus,
  InfluencerFilterOptions,
  InfluencerListQueryParams,
} from "../types";

const { Text } = Typography;

const freshnessOptions: Array<{ value: FreshnessStatus; label: string }> = [
  { value: "fresh", label: "新鲜" },
  { value: "aging", label: "较旧" },
  { value: "stale", label: "陈旧" },
  { value: "very_stale", label: "严重陈旧" },
  { value: "unknown", label: "未知" },
];

const refreshNeedOptions: Array<{ value: string; label: string }> = [
  { value: "all", label: "全部" },
  { value: "true", label: "需要更新" },
  { value: "false", label: "暂不需要" },
];

type InfluencerFilterBarProps = {
  query: InfluencerListQueryParams;
  options: InfluencerFilterOptions | undefined;
  optionsLoading: boolean;
  optionsError: boolean;
  validationMessage: string | null;
  onRetryOptions: () => void;
  onChange: (changes: Partial<InfluencerListQueryParams>) => void;
  onClear: () => void;
};

export function validateFollowerRange(
  minimum: string,
  maximum: string,
): string | null {
  const isInteger = (value: string) => value === "" || /^\d+$/.test(value);
  if (!isInteger(minimum) || !isInteger(maximum)) return "粉丝数必须为非负整数";
  if (minimum !== "" && maximum !== "" && BigInt(minimum) > BigInt(maximum)) {
    return "粉丝下限不能大于上限";
  }
  return null;
}

export function InfluencerFilterBar({
  query,
  options,
  optionsLoading,
  optionsError,
  validationMessage,
  onRetryOptions,
  onChange,
  onClear,
}: InfluencerFilterBarProps) {
  const [searchDraft, setSearchDraft] = useState(query.q ?? "");
  const [minimumDraft, setMinimumDraft] = useState(query.followers_min ?? "");
  const [maximumDraft, setMaximumDraft] = useState(query.followers_max ?? "");
  const [draftError, setDraftError] = useState<string | null>(null);

  function submitSearch() {
    onChange({ q: searchDraft.trim() || undefined });
  }

  function applyFollowers() {
    const error = validateFollowerRange(minimumDraft, maximumDraft);
    setDraftError(error);
    if (error) return;
    onChange({
      followers_min: minimumDraft || undefined,
      followers_max: maximumDraft || undefined,
    });
  }

  const filterControlsDisabled = optionsLoading || optionsError;

  return (
    <Card className="influencer-filter-card" variant="borderless">
      <Space orientation="vertical" size="small" className="full-width">
        <Row gutter={[12, 12]}>
          <Col xs={24} lg={8}>
            <Space.Compact className="full-width">
              <Input
                aria-label="昵称搜索"
                value={searchDraft}
                maxLength={160}
                placeholder="搜索达人昵称或账号名"
                onChange={(event) => setSearchDraft(event.target.value)}
                onPressEnter={submitSearch}
              />
              <Button
                type="primary"
                icon={<SearchOutlined aria-hidden="true" />}
                onClick={submitSearch}
              >
                搜索
              </Button>
            </Space.Compact>
          </Col>
          <Col xs={24} sm={12} lg={4}>
            <Select
              aria-label="标签筛选"
              className="full-width"
              allowClear
              showSearch
              disabled={filterControlsDisabled}
              loading={optionsLoading}
              placeholder="选择标签"
              value={query.tag}
              onChange={(value: string | undefined) => onChange({ tag: value })}
              options={(options?.tags ?? []).map((tag) => ({
                value: tag,
                disabled: tag.length > 160,
                label: (
                  <span className="filter-option-ellipsis" title={tag}>
                    {tag}
                  </span>
                ),
                title: tag,
              }))}
            />
          </Col>
          <Col xs={24} sm={12} lg={4}>
            <Select
              aria-label="CRM 阶段筛选"
              className="full-width"
              allowClear
              disabled={filterControlsDisabled}
              loading={optionsLoading}
              placeholder="选择 CRM 阶段"
              value={query.crm_stage}
              onChange={(value: string | undefined) =>
                onChange({ crm_stage: value })
              }
              options={(options?.crm_stages ?? []).map((stage) => ({
                value: stage,
                label: stage,
              }))}
            />
          </Col>
          <Col xs={24} sm={12} lg={5}>
            <Select
              aria-label="负责人筛选"
              className="full-width"
              allowClear
              disabled={filterControlsDisabled}
              loading={optionsLoading}
              placeholder="选择负责人"
              value={query.owner_operator_id}
              onChange={(value: string | undefined) =>
                onChange({ owner_operator_id: value })
              }
              options={(options?.owners ?? []).map((owner) => ({
                value: owner.id,
                label: `${owner.name}${owner.status === "disabled" ? "（已停用）" : ""}`,
              }))}
            />
          </Col>
          <Col xs={24} sm={12} lg={3}>
            <Button
              type="text"
              className="influencer-clear-filter-button"
              block
              icon={<ClearOutlined aria-hidden="true" />}
              onClick={onClear}
            >
              清除筛选
            </Button>
          </Col>
        </Row>

        <Row gutter={[12, 12]} className="follower-filter-row">
          <Col xs={24} lg={8}>
            <div className="influencer-follower-filter-group">
              <span className="influencer-follower-filter-label">粉丝区间</span>
              <div className="influencer-follower-filter-compact">
                <Input
                  aria-label="粉丝下限"
                  className="influencer-follower-input"
                  inputMode="numeric"
                  placeholder="最低粉丝"
                  value={minimumDraft}
                  onChange={(event) => setMinimumDraft(event.target.value)}
                />
                <span
                  className="influencer-follower-filter-separator"
                  aria-hidden="true"
                >
                  —
                </span>
                <Input
                  aria-label="粉丝上限"
                  className="influencer-follower-input"
                  inputMode="numeric"
                  placeholder="最高粉丝"
                  value={maximumDraft}
                  onChange={(event) => setMaximumDraft(event.target.value)}
                />
                <Button
                  className="influencer-follower-apply-button"
                  aria-label="应用粉丝范围"
                  onClick={applyFollowers}
                >
                  应用
                </Button>
              </div>
            </div>
          </Col>
          <Col xs={24} sm={8} lg={8}>
            <Select
              aria-label="数据时效筛选"
              className="full-width"
              allowClear
              placeholder="选择数据时效"
              value={query.freshness_status as FreshnessStatus | undefined}
              onChange={(value: FreshnessStatus | undefined) =>
                onChange({ freshness_status: value })
              }
              options={freshnessOptions}
            />
          </Col>
          <Col xs={24} sm={8} lg={8}>
            <div className="influencer-filter-with-label">
              <span className="influencer-filter-inline-label">更新需求</span>
              <Select
                aria-label="更新需求筛选"
                className="full-width"
                allowClear
                placeholder="全部"
                value={query.requires_refresh ?? "all"}
                onChange={(value: string | undefined) =>
                  onChange({
                    requires_refresh:
                      value === "true" || value === "false" ? value : undefined,
                  })
                }
                options={refreshNeedOptions}
              />
            </div>
          </Col>
        </Row>

        {optionsLoading ? (
          <Text type="secondary">
            <Spin size="small" /> 正在加载筛选选项
          </Text>
        ) : null}
        {optionsError ? (
          <Alert
            type="warning"
            showIcon
            message="筛选选项加载失败"
            description="达人列表仍可浏览；筛选选项暂不可用。"
            action={<Button onClick={onRetryOptions}>重试筛选选项</Button>}
          />
        ) : null}
        {draftError || validationMessage ? (
          <Alert
            type="error"
            showIcon
            message={draftError ?? validationMessage}
          />
        ) : null}
      </Space>
    </Card>
  );
}
