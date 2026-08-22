import {
  Alert,
  Button,
  Descriptions,
  Form,
  InputNumber,
  Modal,
  Row,
  Col,
  Select,
  Space,
  Tag,
  Typography,
} from "antd";
import { useEffect } from "react";

import type {
  CollectionJobPublic,
  CollectionJobScreeningRulesUpdatePayload,
} from "../types";

const { Text } = Typography;

type ScreeningRuleFormValues = {
  sourceTags: string[];
  follower_min?: number | null;
  follower_max?: number | null;
};

function sourceTagsFromValue(value: string[] | undefined): string[] {
  return (
    value?.map((item) => item.trim()).filter((item) => item.length > 0) ?? []
  );
}

function formatFollowerBoundary(value: number | null): string {
  if (value === null) {
    return "不限";
  }
  if (value < 10_000) {
    return value.toLocaleString("zh-CN");
  }
  const scaled = value / 10_000;
  return `${scaled % 1 === 0 ? scaled.toFixed(0) : scaled.toFixed(1).replace(/\.0$/, "")}万`;
}

function formatFollowerRange(min: number | null, max: number | null): string {
  if (min === null && max === null) {
    return "不限";
  }
  return `${formatFollowerBoundary(min)}–${formatFollowerBoundary(max)}`;
}

export function ScreeningRuleEditorModal({
  open,
  collection,
  readOnly,
  previewReady,
  saving,
  reloading,
  conflict,
  error,
  onCancel,
  onSave,
  onReloadLatest,
}: {
  open: boolean;
  collection: CollectionJobPublic;
  readOnly: boolean;
  previewReady: boolean;
  saving: boolean;
  reloading: boolean;
  conflict: boolean;
  error: string | null;
  onCancel: () => void;
  onSave: (payload: CollectionJobScreeningRulesUpdatePayload) => void;
  onReloadLatest: () => void;
}) {
  const [form] = Form.useForm<ScreeningRuleFormValues>();
  const platformLabel = collection.screening_rules.platforms.includes(
    "xiaohongshu",
  )
    ? "小红书"
    : "未设置";

  useEffect(() => {
    if (!open) return;
    form.setFieldsValue({
      sourceTags: collection.screening_rules.source_tags_exact_any,
      follower_min: collection.follower_min,
      follower_max: collection.follower_max,
    });
  }, [collection, form, open]);

  function submit(values: ScreeningRuleFormValues) {
    const followerMin = values.follower_min ?? null;
    const followerMax = values.follower_max ?? null;
    const sourceTags = sourceTagsFromValue(values.sourceTags);
    if (
      followerMin !== null &&
      followerMax !== null &&
      followerMin > followerMax
    ) {
      form.setFields([
        {
          name: "follower_max",
          errors: ["粉丝上限不能小于粉丝下限。"],
        },
      ]);
      return;
    }
    onSave({
      screening_rules: {
        schema_version: 1,
        platforms:
          collection.screening_rules.platforms.length > 0
            ? collection.screening_rules.platforms
            : ["xiaohongshu"],
        source_tags_exact_any: sourceTags,
      },
      follower_min: followerMin,
      follower_max: followerMax,
      expected_revision: collection.screening_rules_revision,
    });
  }

  const followerRange = formatFollowerRange(
    collection.follower_min,
    collection.follower_max,
  );
  const sourceTags = collection.screening_rules.source_tags_exact_any;

  return (
    <Modal
      title={readOnly ? "查看筛选规则" : "编辑筛选规则"}
      open={open}
      width={600}
      destroyOnHidden={false}
      confirmLoading={saving}
      okText="保存筛选规则"
      cancelText={readOnly ? "关闭" : "取消"}
      okButtonProps={{ hidden: readOnly, disabled: conflict }}
      onOk={() => form.submit()}
      onCancel={onCancel}
      className="screening-rule-editor-modal"
    >
      <Space orientation="vertical" size="middle" className="full-width">
        {previewReady && !readOnly ? (
          <Text type="warning" className="screening-rule-preview-hint">
            保存后，当前数据预览将失效，需要重新生成。
          </Text>
        ) : null}
        {conflict ? (
          <Alert
            type="warning"
            showIcon
            title="筛选规则已被其他人更新，请重新加载最新规则后再继续编辑。"
            action={
              <Button size="small" loading={reloading} onClick={onReloadLatest}>
                重新加载最新规则
              </Button>
            }
          />
        ) : null}
        {error ? <Alert type="error" showIcon title={error} /> : null}

        {readOnly ? (
          <Descriptions
            size="small"
            column={1}
            className="screening-rule-readonly-summary"
            bordered={false}
          >
            <Descriptions.Item label="平台">{platformLabel}</Descriptions.Item>
            <Descriptions.Item label="来源标签">
              {sourceTags.length === 0 ? (
                <Text type="secondary">未设置</Text>
              ) : (
                <Space size={[6, 6]} wrap>
                  {sourceTags.map((tag) => (
                    <Tag key={tag} color="blue">
                      {tag}
                    </Tag>
                  ))}
                </Space>
              )}
            </Descriptions.Item>
            <Descriptions.Item label="粉丝范围">
              {followerRange}
            </Descriptions.Item>
            <Descriptions.Item label="规则版本">
              <Text type="secondary">
                {collection.screening_rules_revision}
              </Text>
            </Descriptions.Item>
          </Descriptions>
        ) : (
          <Form<ScreeningRuleFormValues>
            form={form}
            layout="vertical"
            onFinish={submit}
            disabled={saving || reloading}
          >
            <Form.Item label="平台">
              <Text>{platformLabel}</Text>
            </Form.Item>
            <Form.Item
              name="sourceTags"
              label="来源标签"
              extra="至少命中一个来源标签即可，按标签原值精确匹配。"
              rules={[
                {
                  validator: async (_, value: string[] | undefined) => {
                    const tags = sourceTagsFromValue(value);
                    if (tags.some((tag) => tag.length > 160)) {
                      throw new Error("每个来源标签不能超过 160 个字符。");
                    }
                    if (new Set(tags).size !== tags.length) {
                      throw new Error("来源标签不能重复。");
                    }
                  },
                },
              ]}
            >
              <Select
                mode="tags"
                className="screening-rule-source-tags"
                placeholder="例如：美妆、护肤"
                tokenSeparators={[",", "，", "\n", "\t", " "]}
                maxTagCount="responsive"
                disabled={saving || reloading}
              />
            </Form.Item>
            <Form.Item label="粉丝范围">
              <Row gutter={12} align="middle">
                <Col span={11}>
                  <Form.Item
                    name="follower_min"
                    className="screening-rule-range-field"
                  >
                    <InputNumber
                      min={0}
                      precision={0}
                      className="full-width"
                      placeholder="最低粉丝数"
                    />
                  </Form.Item>
                </Col>
                <Col span={2} className="screening-rule-range-separator">
                  <Text type="secondary">—</Text>
                </Col>
                <Col span={11}>
                  <Form.Item
                    name="follower_max"
                    className="screening-rule-range-field"
                  >
                    <InputNumber
                      min={0}
                      precision={0}
                      className="full-width"
                      placeholder="最高粉丝数"
                    />
                  </Form.Item>
                </Col>
              </Row>
            </Form.Item>
            <Form.Item label="规则版本">
              <Text type="secondary">
                {collection.screening_rules_revision}
              </Text>
            </Form.Item>
          </Form>
        )}
      </Space>
    </Modal>
  );
}
