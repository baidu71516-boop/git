import {
  Alert,
  Button,
  Checkbox,
  Form,
  Input,
  InputNumber,
  Modal,
  Row,
  Col,
  Space,
  Typography,
} from "antd";
import { useEffect } from "react";

import type {
  CollectionJobPublic,
  CollectionJobScreeningRulesUpdatePayload,
} from "../types";

const { Text } = Typography;

type ScreeningRuleFormValues = {
  platforms: "xiaohongshu"[];
  sourceTags: string;
  follower_min?: number | null;
  follower_max?: number | null;
};

function sourceTagsFromText(value: string): string[] {
  return value
    .split("\n")
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
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

  useEffect(() => {
    if (!open) return;
    form.setFieldsValue({
      platforms: collection.screening_rules.platforms,
      sourceTags: collection.screening_rules.source_tags_exact_any.join("\n"),
      follower_min: collection.follower_min,
      follower_max: collection.follower_max,
    });
  }, [collection, form, open]);

  function submit(values: ScreeningRuleFormValues) {
    const followerMin = values.follower_min ?? null;
    const followerMax = values.follower_max ?? null;
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
        platforms: values.platforms ?? [],
        source_tags_exact_any: sourceTagsFromText(values.sourceTags ?? ""),
      },
      follower_min: followerMin,
      follower_max: followerMax,
      expected_revision: collection.screening_rules_revision,
    });
  }

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
    >
      <Space orientation="vertical" size="middle" className="full-width">
        {previewReady && !readOnly ? (
          <Alert
            type="warning"
            showIcon
            title="保存后，当前数据预览将失效，需要重新生成。"
          />
        ) : null}
        {conflict ? (
          <Alert
            type="error"
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

        <Form<ScreeningRuleFormValues>
          form={form}
          layout="vertical"
          onFinish={submit}
          disabled={readOnly || saving || reloading}
        >
          <Form.Item name="platforms" label="平台">
            <Checkbox.Group
              options={[{ label: "小红书", value: "xiaohongshu" }]}
            />
          </Form.Item>

          <Form.Item
            name="sourceTags"
            label="来源标签"
            extra="每行一个标签，按完整原值精确匹配；区分大小写。"
            rules={[
              {
                validator: async (_, value: string | undefined) => {
                  const tags = sourceTagsFromText(value ?? "");
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
            <Input.TextArea
              rows={4}
              placeholder={readOnly ? "未设置" : "例如：美妆\n护肤"}
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
                    placeholder="最低粉丝（不限）"
                  />
                </Form.Item>
              </Col>
              <Col span={2} className="screening-rule-range-separator">
                <Text type="secondary">至</Text>
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
                    placeholder="最高粉丝（不限）"
                  />
                </Form.Item>
              </Col>
            </Row>
          </Form.Item>

          <Form.Item label="规则版本">
            <Input
              value={String(collection.screening_rules_revision)}
              disabled
            />
          </Form.Item>
        </Form>
      </Space>
    </Modal>
  );
}
