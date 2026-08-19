"use client";

import { FilterOutlined } from "@ant-design/icons";
import { Button, InputNumber, Popover, Select, Space } from "antd";

import { channelLabel, contactFilterLabel, priorityLabel } from "../formatters";
import type {
  CampaignOption,
  OperatorOption,
  TodayChannel,
  TodayContactFilter,
  TodayFilters,
  TodayPriority,
  TodayWorkKind,
} from "../types";

const channelOptions: TodayChannel[] = [
  "EMAIL",
  "XIAOHONGSHU_PRIVATE_MESSAGE",
  "DOUYIN_PRIVATE_MESSAGE",
  "WECHAT",
  "MANUAL",
];
const contactOptions: TodayContactFilter[] = [
  "has_contact",
  "has_email",
  "no_contact",
];
const priorityOptions: TodayPriority[] = ["HIGH", "NORMAL"];

export function TodayFilterBar({
  filters,
  operators,
  campaigns,
  tracks,
  onChange,
  onReset,
  showReset = true,
  moreFiltersOpen,
  onMoreFiltersOpenChange,
}: {
  filters: TodayFilters;
  operators?: OperatorOption[];
  campaigns?: CampaignOption[];
  tracks?: string[];
  onChange: (changes: Partial<TodayFilters>) => void;
  onReset: () => void;
  showReset?: boolean;
  moreFiltersOpen?: boolean;
  onMoreFiltersOpenChange?: (open: boolean) => void;
}) {
  const hasFilters = Object.entries(filters).some(
    ([key, value]) =>
      key !== "work_kind" && value !== undefined && value !== "",
  );
  const moreFilters = (
    <div className="today-more-filter-panel">
      <span className="today-more-filter-label">粉丝区间</span>
      <Space.Compact>
        <InputNumber
          aria-label="粉丝数下限"
          placeholder="最小值"
          min={0}
          value={filters.followers_min}
          onChange={(value) => onChange({ followers_min: value ?? undefined })}
        />
        <InputNumber
          aria-label="粉丝数上限"
          placeholder="最大值"
          min={0}
          value={filters.followers_max}
          onChange={(value) => onChange({ followers_max: value ?? undefined })}
        />
      </Space.Compact>
      <Select
        allowClear
        aria-label="联系方式"
        placeholder="联系方式"
        value={filters.contact_filter}
        options={contactOptions.map((value) => ({
          label: contactFilterLabel(value),
          value,
        }))}
        onChange={(value: TodayContactFilter | undefined) =>
          onChange({ contact_filter: value })
        }
      />
      <Select
        allowClear
        aria-label="优先级"
        placeholder="优先级"
        value={filters.priority}
        options={priorityOptions.map((value) => ({
          label: priorityLabel(value),
          value,
        }))}
        onChange={(value: TodayPriority | undefined) =>
          onChange({ priority: value })
        }
      />
    </div>
  );
  return (
    <div className="today-filter-bar" aria-label="今日触达筛选">
      <div className="today-task-kind" role="tablist" aria-label="任务类型">
        {(["ALL", "FIRST_TOUCH", "FOLLOW_UP"] as TodayWorkKind[]).map(
          (kind) => (
            <button
              className={`today-kind-tab${filters.work_kind === kind ? " is-active" : ""}`}
              key={kind}
              type="button"
              role="tab"
              aria-selected={filters.work_kind === kind}
              onClick={() => onChange({ work_kind: kind })}
            >
              {kind === "ALL"
                ? "全部"
                : kind === "FIRST_TOUCH"
                  ? "首次触达"
                  : "待跟进"}
            </button>
          ),
        )}
      </div>
      <div className="today-filter-controls">
        <Select
          allowClear
          aria-label="渠道"
          placeholder="渠道"
          value={filters.channel}
          options={channelOptions.map((value) => ({
            label: channelLabel(value),
            value,
          }))}
          onChange={(value: TodayChannel | undefined) =>
            onChange({ channel: value })
          }
        />
        {campaigns ? (
          <Select
            allowClear
            showSearch
            aria-label="Campaign"
            placeholder="Campaign"
            value={filters.campaign_id}
            options={campaigns.map((campaign) => ({
              label: campaign.name,
              value: campaign.id,
            }))}
            onChange={(value: string | undefined) =>
              onChange({ campaign_id: value })
            }
          />
        ) : null}
        {operators ? (
          <Select
            allowClear
            showSearch
            aria-label="负责人"
            placeholder="负责人"
            value={filters.owner_operator_id}
            options={operators.map((operator) => ({
              label: operator.name,
              value: operator.id,
            }))}
            onChange={(value: string | undefined) =>
              onChange({ owner_operator_id: value })
            }
          />
        ) : null}
        {tracks ? (
          <Select
            allowClear
            showSearch
            aria-label="赛道"
            placeholder="赛道"
            value={filters.track}
            options={tracks.map((track) => ({ label: track, value: track }))}
            onChange={(value: string | undefined) => onChange({ track: value })}
          />
        ) : null}
        <Popover
          content={moreFilters}
          trigger="click"
          placement="bottomLeft"
          open={moreFiltersOpen}
          onOpenChange={onMoreFiltersOpenChange}
        >
          <Button icon={<FilterOutlined aria-hidden="true" />}>更多筛选</Button>
        </Popover>
        {hasFilters && showReset ? (
          <Button type="text" onClick={onReset} aria-label="重置筛选">
            重置
          </Button>
        ) : null}
      </div>
    </div>
  );
}
