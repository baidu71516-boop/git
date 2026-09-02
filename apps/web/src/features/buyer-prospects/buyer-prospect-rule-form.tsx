"use client";

import {
  Alert,
  Button,
  Checkbox,
  Form,
  Input,
  InputNumber,
  Radio,
  Select,
  Space,
} from "antd";
import { useEffect, useRef } from "react";

import type { BuyerLeadTier } from "@/features/candidate-pools/types";

import type {
  BuyerProspectOwnerFilter,
  BuyerProspectRecentCollectionWindow,
  BuyerProspectRule,
  BuyerProspectRuleCreateRequest,
  BuyerProspectRuleOptions,
} from "./types";

const tierOptions: Array<{ value: BuyerLeadTier; label: string }> = [
  { value: "HIGH", label: "强潜客" },
  { value: "CHANGED", label: "变化潜客" },
  { value: "RELATED", label: "相关潜客" },
  { value: "SAME_CATEGORY", label: "同类潜客" },
  { value: "UNKNOWN", label: "待判断" },
];

const windowOptions: Array<{
  value: BuyerProspectRecentCollectionWindow;
  label: string;
}> = [
  { value: "7", label: "近 7 天" },
  { value: "30", label: "近 30 天" },
  { value: "60", label: "近 60 天" },
  { value: "90", label: "近 90 天" },
  { value: "ALL", label: "全部历史" },
];

type RuleFormValues = BuyerProspectRuleCreateRequest;
export type BuyerProspectRuleSubmitIntent = "save" | "save_and_run";

function initialValues(rule?: BuyerProspectRule): RuleFormValues {
  return {
    name: rule?.name ?? "",
    owner_operator_id: rule?.owner_operator_id,
    category_ids: rule?.category_ids ?? [],
    follower_min: rule?.follower_min ?? null,
    follower_max: rule?.follower_max ?? null,
    buyer_lead_tiers: rule?.buyer_lead_tiers ?? [],
    source_collection_job_id: rule?.source_collection_job_id ?? "",
    recent_collection_window: rule?.recent_collection_window ?? "30",
    prospect_owner_filter: rule?.prospect_owner_filter ?? "ANY",
    prospect_owner_operator_id: rule?.prospect_owner_operator_id ?? undefined,
    exclude_contacted: rule?.exclude_contacted ?? false,
  };
}

export function BuyerProspectRuleForm({
  options,
  rule,
  submitLabel,
  saveAndRunLabel,
  loading,
  error,
  onSubmit,
}: {
  options: BuyerProspectRuleOptions;
  rule?: BuyerProspectRule;
  submitLabel: string;
  saveAndRunLabel?: string;
  loading: boolean;
  error: string | null;
  onSubmit: (
    values: BuyerProspectRuleCreateRequest,
    intent: BuyerProspectRuleSubmitIntent,
  ) => void;
}) {
  const [form] = Form.useForm<RuleFormValues>();
  const submitIntent = useRef<BuyerProspectRuleSubmitIntent>("save");
  const ownerFilter = Form.useWatch("prospect_owner_filter", form);

  useEffect(() => {
    form.setFieldsValue(initialValues(rule));
  }, [form, rule]);

  return (
    <Form<RuleFormValues>
      form={form}
      layout="vertical"
      initialValues={initialValues(rule)}
      onFinish={(values) => {
        if (values.prospect_owner_filter !== "OPERATOR") {
          values.prospect_owner_operator_id = undefined;
        }
        onSubmit(values, submitIntent.current);
        submitIntent.current = "save";
      }}
    >
      {error ? (
        <Alert className="campaign-modal-alert" type="error" showIcon title={error} />
      ) : null}
      <Form.Item
        label="规则名称"
        name="name"
        rules={[{ required: true, message: "请输入规则名称" }]}
      >
        <Input maxLength={200} placeholder="例如：影视赛道跨类目潜客" />
      </Form.Item>
      <Form.Item
        label="领域 / 赛道"
        name="category_ids"
        rules={[{ required: true, message: "请选择至少一个规范类目" }]}
      >
        <Select
          mode="multiple"
          allowClear
          placeholder="选择规范类目"
          options={options.taxonomy_category_ids.map((categoryId) => ({
            label: categoryId,
            value: categoryId,
          }))}
        />
      </Form.Item>
      <Space size="middle" className="full-width" wrap>
        <Form.Item label="粉丝数最小值" name="follower_min">
          <InputNumber min={0} precision={0} placeholder="不限" />
        </Form.Item>
        <Form.Item label="粉丝数最大值" name="follower_max">
          <InputNumber min={0} precision={0} placeholder="不限" />
        </Form.Item>
      </Space>
      <Form.Item
        label="潜客等级"
        name="buyer_lead_tiers"
        rules={[{ required: true, message: "请选择至少一个潜客等级" }]}
      >
        <Select mode="multiple" options={tierOptions} placeholder="选择潜客等级" />
      </Form.Item>
      <Form.Item
        label="数据来源"
        name="source_collection_job_id"
        rules={[{ required: true, message: "请选择可用采集任务" }]}
      >
        <Select
          placeholder="选择同部门采集任务"
          options={options.source_collection_jobs.map((job) => ({
            label: `${job.name} · ${job.industry}${
              job.subdirection ? ` / ${job.subdirection}` : ""
            }`,
            value: job.id,
          }))}
        />
      </Form.Item>
      <Form.Item
        label="最近采集范围"
        name="recent_collection_window"
        rules={[{ required: true, message: "请选择最近采集范围" }]}
      >
        <Radio.Group options={windowOptions} optionType="button" buttonStyle="solid" />
      </Form.Item>
      <Form.Item label="潜客负责人筛选" name="prospect_owner_filter">
        <Select<BuyerProspectOwnerFilter>
          options={[
            { value: "ANY", label: "不限负责人" },
            { value: "UNASSIGNED", label: "未分配负责人" },
            { value: "OPERATOR", label: "指定负责人" },
          ]}
        />
      </Form.Item>
      {ownerFilter === "OPERATOR" ? (
        <Form.Item
          label="指定负责人"
          name="prospect_owner_operator_id"
          rules={[{ required: true, message: "请选择负责人" }]}
        >
          <Select
            placeholder="选择负责人"
            options={options.operators.map((operator) => ({
              label: operator.name,
              value: operator.id,
            }))}
          />
        </Form.Item>
      ) : null}
      <Form.Item label="规则负责人" name="owner_operator_id">
        <Select
          allowClear
          placeholder="默认当前操作人"
          options={options.operators.map((operator) => ({
            label: operator.name,
            value: operator.id,
          }))}
        />
      </Form.Item>
      <Form.Item name="exclude_contacted" valuePropName="checked">
        <Checkbox>排除系统内已发送触达的达人</Checkbox>
      </Form.Item>
      <Form.Item
        noStyle
        shouldUpdate={(previous, current) =>
          previous.follower_min !== current.follower_min ||
          previous.follower_max !== current.follower_max
        }
      >
        {() => {
          const minimum = form.getFieldValue("follower_min") as number | null;
          const maximum = form.getFieldValue("follower_max") as number | null;
          const invalid = minimum != null && maximum != null && minimum > maximum;
          return invalid ? (
            <Alert
              className="campaign-modal-alert"
              type="warning"
              showIcon
              title="粉丝数最小值不能大于最大值"
            />
          ) : null;
        }}
      </Form.Item>
      <Form.Item>
        <Space className="full-width" wrap>
          <Button
            type="primary"
            htmlType="submit"
            loading={loading}
            onClick={() => {
              submitIntent.current = "save";
            }}
          >
            {submitLabel}
          </Button>
          {saveAndRunLabel ? (
            <Button
              htmlType="submit"
              loading={loading}
              onClick={() => {
                submitIntent.current = "save_and_run";
              }}
            >
              {saveAndRunLabel}
            </Button>
          ) : null}
        </Space>
      </Form.Item>
    </Form>
  );
}
