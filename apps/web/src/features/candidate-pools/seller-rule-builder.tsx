"use client";

import { Alert, Button, Form, Input, InputNumber, Select, Space } from "antd";
import { useState } from "react";

import type { SellerTargetingPolicy } from "./types";

type RuleFormValues = {
  contact_availability?: "has_contact" | "has_email" | "no_contact";
  followers?: { minimum?: number | null; maximum?: number | null };
  tags?: string;
  notes_7d?: { minimum?: number | null; maximum?: number | null };
  notes_60d?: { minimum?: number | null; maximum?: number | null };
  freshness?: Array<"fresh" | "aging" | "stale" | "very_stale">;
  platforms?: string[];
  sources?: string[];
};

function definedRange(
  value: { minimum?: number | null; maximum?: number | null } | undefined,
) {
  if (value?.minimum == null && value?.maximum == null) return undefined;
  return { minimum: value?.minimum ?? null, maximum: value?.maximum ?? null };
}

function RangeInputs({
  name,
  label,
}: {
  name: "followers" | "notes_7d" | "notes_60d";
  label: string;
}) {
  return (
    <Form.Item label={label}>
      <Space.Compact block>
        <Form.Item name={[name, "minimum"]} noStyle>
          <InputNumber
            min={0}
            precision={0}
            placeholder="最小值"
            style={{ width: "50%" }}
          />
        </Form.Item>
        <Form.Item name={[name, "maximum"]} noStyle>
          <InputNumber
            min={0}
            precision={0}
            placeholder="最大值"
            style={{ width: "50%" }}
          />
        </Form.Item>
      </Space.Compact>
    </Form.Item>
  );
}

function initialValues(policy?: SellerTargetingPolicy): RuleFormValues {
  return {
    contact_availability:
      typeof policy?.contact_availability === "string"
        ? policy.contact_availability
        : undefined,
    followers: policy?.followers ?? undefined,
    tags: policy?.tags_exact_any?.join(", ") ?? "",
    notes_7d: policy?.notes_7d ?? undefined,
    notes_60d: policy?.notes_60d ?? undefined,
    freshness: (policy?.freshness?.allowed_statuses ?? []).filter(
      (status): status is "fresh" | "aging" | "stale" | "very_stale" =>
        status === "fresh" ||
        status === "aging" ||
        status === "stale" ||
        status === "very_stale",
    ),
    platforms: policy?.platforms ?? [],
    sources: policy?.sources ?? [],
  };
}

export function SellerRuleBuilder({
  initialPolicy,
  submitLabel,
  onSubmit,
  loading = false,
  error,
}: {
  initialPolicy?: SellerTargetingPolicy;
  submitLabel: string;
  onSubmit: (policy: SellerTargetingPolicy) => void;
  loading?: boolean;
  error?: string | null;
}) {
  const [form] = Form.useForm<RuleFormValues>();
  const [validationError, setValidationError] = useState<string | null>(null);
  function submit(values: RuleFormValues) {
    const ranges = [values.followers, values.notes_7d, values.notes_60d];
    if (
      ranges.some(
        (range) =>
          range?.minimum != null &&
          range?.maximum != null &&
          range.minimum > range.maximum,
      )
    ) {
      setValidationError("最小值不能大于最大值。");
      return;
    }
    const tags = Array.from(
      new Set(
        (values.tags ?? "")
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
      ),
    );
    setValidationError(null);
    onSubmit({
      schema_version: 1,
      policy_type: "SELLER_V1",
      ...(values.contact_availability
        ? { contact_availability: values.contact_availability }
        : {}),
      ...(definedRange(values.followers)
        ? { followers: definedRange(values.followers) }
        : {}),
      ...(tags.length ? { tags_exact_any: tags } : {}),
      ...(definedRange(values.notes_7d)
        ? { notes_7d: definedRange(values.notes_7d) }
        : {}),
      ...(definedRange(values.notes_60d)
        ? { notes_60d: definedRange(values.notes_60d) }
        : {}),
      ...(values.freshness?.length
        ? { freshness: { allowed_statuses: values.freshness } }
        : {}),
      ...(values.platforms?.length ? { platforms: values.platforms } : {}),
      ...(values.sources?.length ? { sources: values.sources } : {}),
    });
  }
  return (
    <Form<RuleFormValues>
      form={form}
      layout="vertical"
      initialValues={initialValues(initialPolicy)}
      onFinish={submit}
    >
      {error || validationError ? (
        <Alert
          type="error"
          showIcon
          title={error ?? validationError ?? "规则校验失败"}
          className="campaign-modal-alert"
        />
      ) : null}
      <Form.Item label="联系方式" name="contact_availability">
        <Select
          allowClear
          placeholder="不限制"
          options={[
            { value: "has_contact", label: "有联系方式" },
            { value: "has_email", label: "有邮箱" },
            { value: "no_contact", label: "无联系方式" },
          ]}
        />
      </Form.Item>
      <RangeInputs name="followers" label="粉丝数" />
      <Form.Item label="来源标签（逗号分隔）" name="tags">
        <Input placeholder="例如：美妆, 护肤" />
      </Form.Item>
      <RangeInputs name="notes_7d" label="近 7 天笔记数" />
      <RangeInputs name="notes_60d" label="近 60 天笔记数" />
      <Form.Item label="数据新鲜度" name="freshness">
        <Select
          mode="multiple"
          placeholder="不限制"
          options={[
            { value: "fresh", label: "新鲜" },
            { value: "aging", label: "较早" },
            { value: "stale", label: "陈旧" },
            { value: "very_stale", label: "很陈旧" },
          ]}
        />
      </Form.Item>
      <Form.Item label="平台" name="platforms">
        <Select
          mode="multiple"
          placeholder="不限制"
          options={[{ value: "xiaohongshu", label: "小红书" }]}
        />
      </Form.Item>
      <Form.Item label="数据来源" name="sources">
        <Select
          mode="multiple"
          placeholder="不限制"
          options={[
            { value: "huitun", label: "灰豚" },
            { value: "generic", label: "通用导入" },
            { value: "manual", label: "人工维护" },
          ]}
        />
      </Form.Item>
      <p className="candidate-rule-builder-note">
        已配置条件按 AND 组合；每项判断沿用现有的 MATCH / UNKNOWN / NOT_MATCH
        语义。
      </p>
      <Button type="primary" htmlType="submit" loading={loading} block>
        {submitLabel}
      </Button>
    </Form>
  );
}
