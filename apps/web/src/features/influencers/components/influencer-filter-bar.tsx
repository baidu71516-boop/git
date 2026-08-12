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
  InfluencerFilterOptions,
  InfluencerListQueryParams,
} from "../types";

const { Text } = Typography;

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
              className="full-width"
              icon={<ClearOutlined aria-hidden="true" />}
              onClick={onClear}
            >
              清除筛选
            </Button>
          </Col>
        </Row>

        <Row gutter={[12, 12]} align="middle" className="follower-filter-row">
          <Col xs={24} sm={12} lg={5}>
            <Input
              aria-label="粉丝下限"
              inputMode="numeric"
              placeholder="粉丝下限"
              value={minimumDraft}
              onChange={(event) => setMinimumDraft(event.target.value)}
            />
          </Col>
          <Col xs={24} sm={12} lg={5}>
            <Input
              aria-label="粉丝上限"
              inputMode="numeric"
              placeholder="粉丝上限"
              value={maximumDraft}
              onChange={(event) => setMaximumDraft(event.target.value)}
            />
          </Col>
          <Col xs={24} sm={12} lg={5}>
            <Button className="full-width" onClick={applyFollowers}>
              应用粉丝范围
            </Button>
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
