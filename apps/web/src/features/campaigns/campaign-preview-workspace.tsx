"use client";

import {
  Alert,
  Button,
  Descriptions,
  Form,
  Input,
  Modal,
  Select,
  Tag,
} from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";

import { AppEmpty } from "@/components/ui/app-empty";
import { PageHeader } from "@/components/ui/page-header";
import { StatusBadge } from "@/components/ui/status-badge";
import {
  campaignStatusPresentation,
  formatCampaignDateTime,
  isDisabledCampaignOwner,
} from "./formatters";
import { CampaignTable } from "./components/campaign-table";
import type { Campaign, CampaignOperator } from "./types";

const PREVIEW_OPERATORS: CampaignOperator[] = [
  {
    id: "op-a1",
    department_id: "preview-department",
    name: "王小明",
    role: "manager",
    status: "active",
  },
  {
    id: "op-b2",
    department_id: "preview-department",
    name: "李超",
    role: "operator",
    status: "active",
  },
  {
    id: "op-c3",
    department_id: "preview-department",
    name: "高敏",
    role: "operator",
    status: "disabled",
  },
];

const PREVIEW_CAMPAIGNS: Campaign[] = [
  {
    id: "campaign-draft",
    department_id: "preview-department",
    owner_operator_id: "op-a1",
    owner: PREVIEW_OPERATORS[0]!,
    created_by_operator_id: "op-a1",
    name: "新客拓展（春季）",
    status: "DRAFT",
    review_mode: "FIRST_N",
    review_count: 50,
    duplicate_history_policy: "ALLOW_WITH_WARNING",
    duplicate_window_days: 14,
    version: 2,
    created_at: "2026-08-20T00:00:00Z",
    updated_at: "2026-08-20T01:00:00Z",
  },
  {
    id: "campaign-active",
    department_id: "preview-department",
    owner_operator_id: "op-b2",
    owner: PREVIEW_OPERATORS[1]!,
    created_by_operator_id: "op-b2",
    name: "大客户拓展（北区）",
    status: "ACTIVE",
    review_mode: "FIRST_N",
    review_count: 50,
    duplicate_history_policy: "ALLOW_WITH_WARNING",
    duplicate_window_days: 30,
    version: 6,
    created_at: "2026-08-20T02:30:00Z",
    updated_at: "2026-08-20T11:10:00Z",
  },
  {
    id: "campaign-paused-disabled",
    department_id: "preview-department",
    owner_operator_id: "op-c3",
    owner: PREVIEW_OPERATORS[2]!,
    created_by_operator_id: "op-b2",
    name: "重点跟进（高敏）",
    status: "PAUSED",
    review_mode: "MANUAL",
    review_count: 20,
    duplicate_history_policy: "ALLOW_WITH_WARNING",
    duplicate_window_days: 30,
    version: 3,
    created_at: "2026-08-19T10:30:00Z",
    updated_at: "2026-08-19T18:40:00Z",
  },
  {
    id: "campaign-closed",
    department_id: "preview-department",
    owner_operator_id: "op-a1",
    owner: PREVIEW_OPERATORS[0]!,
    created_by_operator_id: "op-a1",
    name: "历史活动（测试）",
    status: "CLOSED",
    review_mode: "FIRST_N",
    review_count: 50,
    duplicate_history_policy: "ALLOW_WITH_WARNING",
    duplicate_window_days: null,
    version: 1,
    created_at: "2026-08-18T00:00:00Z",
    updated_at: "2026-08-18T12:00:00Z",
  },
];

const PREVIEW_ACTIVE_OPERATORS = PREVIEW_OPERATORS.filter(
  (operator) => operator.status === "active",
);

type SceneKey =
  | "list"
  | "list-empty"
  | "list-load-failed"
  | "create"
  | "detail"
  | "detail-disabled-owner"
  | "edit"
  | "edit-conflict"
  | "closed"
  | "owner-options-failed";

type SceneOption = { key: SceneKey; label: string };

const PREVIEW_SCENES: SceneOption[] = [
  { key: "list", label: "拓客活动 · 列表" },
  { key: "create", label: "拓客活动 · 新建活动" },
  { key: "detail", label: "拓客活动 · 详情" },
  { key: "detail-disabled-owner", label: "拓客活动 · 停用负责人" },
  { key: "edit", label: "拓客活动 · 编辑活动" },
  { key: "edit-conflict", label: "拓客活动 · 编辑冲突" },
  { key: "list-empty", label: "拓客活动 · 空状态" },
  { key: "list-load-failed", label: "拓客活动 · 加载失败" },
];

const PREVIEW_SCENE_KEYS: SceneKey[] = [
  "list",
  "list-empty",
  "list-load-failed",
  "create",
  "detail",
  "detail-disabled-owner",
  "edit",
  "edit-conflict",
  "closed",
  "owner-options-failed",
];

const PREVIEW_SCENE_LABELS = new Map(
  PREVIEW_SCENE_KEYS.map((key) => {
    const option = PREVIEW_SCENES.find((item) => item.key === key);
    return [key, option?.label ?? `拓客活动 · ${key}`] as const;
  }),
);

function parsePreviewScene(raw: string | null): SceneKey {
  if (raw === null) {
    return "list";
  }
  return PREVIEW_SCENE_KEYS.includes(raw as SceneKey)
    ? (raw as SceneKey)
    : "list";
}

function sceneCampaignId(scene: SceneKey): string {
  switch (scene) {
    case "detail":
      return "campaign-active";
    case "detail-disabled-owner":
      return "campaign-paused-disabled";
    case "closed":
      return "campaign-closed";
    case "owner-options-failed":
      return "campaign-paused-disabled";
    case "edit":
    case "edit-conflict":
      return "campaign-active";
    default:
      return "campaign-draft";
  }
}

type CampaignFormValues = {
  name: string;
  owner_operator_id?: string;
};

type CampaignEditableValues = {
  name: string;
  owner_operator_id: string;
};

export function CampaignPreviewWorkspace() {
  const searchParams = useSearchParams();
  const initialScene = parsePreviewScene(searchParams.get("scene"));
  const [scene, setScene] = useState<SceneKey>(initialScene);
  const [detailCampaignId, setDetailCampaignId] = useState(
    sceneCampaignId(initialScene),
  );
  const [createForm] = Form.useForm<CampaignFormValues>();
  const [editForm] = Form.useForm<CampaignEditableValues>();

  const currentCampaign = useMemo(
    () =>
      PREVIEW_CAMPAIGNS.find((campaign) => campaign.id === detailCampaignId) ??
      PREVIEW_CAMPAIGNS[0]!,
    [detailCampaignId],
  );

  const status = campaignStatusPresentation(currentCampaign.status);
  const ownerLabel = currentCampaign.owner.name;
  const isOwnerDisabled = isDisabledCampaignOwner(currentCampaign.owner);
  const closedScene = scene === "closed";
  const editScene = scene === "edit" || scene === "edit-conflict";
  const createScene = scene === "create" || scene === "owner-options-failed";
  const sceneIsOwnerOptionFailed = scene === "owner-options-failed";
  const hasNextCursor = scene === "list";

  const operatorsWithCurrent = useMemo(() => {
    const currentOwner = currentCampaign.owner;
    if (currentOwner.status === "active") return PREVIEW_ACTIVE_OPERATORS;
    return [
      currentOwner,
      ...PREVIEW_ACTIVE_OPERATORS.filter(
        (operator) => operator.id !== currentOwner.id,
      ),
    ];
  }, [currentCampaign.owner]);

  function onSceneChange(nextScene: SceneKey) {
    setScene(nextScene);
    const nextCampaignId = sceneCampaignId(nextScene);
    setDetailCampaignId(nextCampaignId);
  }

  function openDetail(campaign: Campaign) {
    setDetailCampaignId(campaign.id);
    setScene(campaign.status === "CLOSED" ? "closed" : "detail");
  }

  useEffect(() => {
    if (createScene) {
      createForm.setFieldsValue({
        name: "新建拓客活动",
        owner_operator_id: undefined,
      });
    }

    if (editScene) {
      editForm.setFieldsValue({
        name: currentCampaign.name,
        owner_operator_id: currentCampaign.owner_operator_id,
      });
    }
  }, [createScene, editScene, createForm, editForm, currentCampaign]);

  function renderListContent() {
    if (scene === "list-load-failed") {
      return (
        <Alert
          type="error"
          showIcon
          title="拓客活动加载失败"
          description="请稍后重试。"
        />
      );
    }

    if (scene === "list-empty") {
      return (
        <div className="campaign-empty-state">
          <AppEmpty description="暂无拓客活动" />
          <p className="campaign-preview-empty-tip">还没有创建销售拓展活动。</p>
          <Button type="primary" onClick={() => onSceneChange("create")}>
            新建活动
          </Button>
        </div>
      );
    }

    return (
      <div className="campaign-preview-table-shell">
        <CampaignTable
          items={PREVIEW_CAMPAIGNS}
          onViewCampaign={(campaign) => openDetail(campaign)}
        />
        <div className="campaign-preview-pagination">
          {hasNextCursor ? (
            <Button type="default">加载更多</Button>
          ) : (
            <span className="campaign-preview-pagination-end">已经到底了</span>
          )}
        </div>
      </div>
    );
  }

  return (
    <main className="campaign-preview-page">
      <section
        className="campaign-preview-workspace"
        aria-label="拓客活动开发预览"
      >
        <PageHeader
          title="拓客活动"
          description="仅开发环境可见 · 使用内存 fixture，无 Auth/API/DB 依赖。"
          extra={
            <Button
              type="primary"
              icon={<PlusOutlined aria-hidden="true" />}
              onClick={() => onSceneChange("create")}
            >
              新建活动
            </Button>
          }
        />
        <div className="campaign-preview-toolbar">
          <span className="campaign-preview-toolbar-label">开发预览场景</span>
          <label className="campaign-preview-scene-select">
            <span>场景切换</span>
            <Select
              aria-label="预览场景"
              value={scene}
              labelInValue={false}
              options={PREVIEW_SCENES}
              onChange={onSceneChange}
              style={{ width: 240 }}
            />
          </label>
          <span className="campaign-preview-scene-tip">
            当前场景：{PREVIEW_SCENE_LABELS.get(scene) ?? `拓客活动 · ${scene}`}
          </span>
        </div>

        {scene.includes("detail") ||
        scene === "edit" ||
        scene === "edit-conflict" ||
        scene === "closed" ? (
          <section className="campaign-preview-detail-workspace">
            {closedScene ? (
              <Alert
                type="warning"
                showIcon
                message="该拓客活动已关闭，无法继续编辑。"
              />
            ) : null}
            <section className="campaign-preview-detail-head">
              <div className="campaign-preview-head-main">
                <h2>{currentCampaign.name}</h2>
                <StatusBadge
                  tone={status.tone}
                  className="campaign-preview-detail-status"
                >
                  {status.label}
                </StatusBadge>
              </div>
              <div className="campaign-preview-meta">
                <span>负责人：{ownerLabel}</span>
                <span>·</span>
                <span>
                  更新于：{formatCampaignDateTime(currentCampaign.updated_at)}
                </span>
                {isOwnerDisabled ? (
                  <Tag className="campaign-owner-status-tag">已停用</Tag>
                ) : null}
              </div>
              {!closedScene ? (
                <Button
                  type="primary"
                  className="campaign-preview-edit-button"
                  onClick={() =>
                    setScene(
                      currentCampaign.status === "CLOSED" ? "closed" : "edit",
                    )
                  }
                >
                  编辑活动
                </Button>
              ) : null}
            </section>
            <section className="campaign-preview-detail-basic">
              <Descriptions column={1} size="middle">
                <Descriptions.Item label="活动名称">
                  {currentCampaign.name}
                </Descriptions.Item>
                <Descriptions.Item label="状态">
                  <StatusBadge
                    tone={status.tone}
                    className="campaign-table-status-badge"
                  >
                    {status.label}
                  </StatusBadge>
                </Descriptions.Item>
                <Descriptions.Item label="负责人">
                  {ownerLabel}
                  {isOwnerDisabled ? (
                    <Tag className="campaign-owner-status-tag">已停用</Tag>
                  ) : null}
                </Descriptions.Item>
                <Descriptions.Item label="创建时间">
                  {formatCampaignDateTime(currentCampaign.created_at)}
                </Descriptions.Item>
                <Descriptions.Item label="更新时间">
                  {formatCampaignDateTime(currentCampaign.updated_at)}
                </Descriptions.Item>
              </Descriptions>
            </section>
          </section>
        ) : null}

        {scene === "list" ||
        scene === "list-empty" ||
        scene === "list-load-failed" ? (
          <section className="campaign-preview-list-workspace">
            {renderListContent()}
          </section>
        ) : null}
      </section>

      <Modal
        title="新建拓客活动"
        width={560}
        open={createScene}
        footer={null}
        destroyOnHidden
        onCancel={() => setScene("list")}
      >
        {scene === "owner-options-failed" ? (
          <Alert
            type="warning"
            showIcon
            message="负责人选项加载失败"
            description="可以不指定负责人创建，或稍后重新加载。"
            className="campaign-modal-alert"
            action={
              <Button size="small" onClick={() => setScene("create")}>
                重新加载
              </Button>
            }
          />
        ) : null}
        <Form
          form={createForm}
          layout="vertical"
          onFinish={() => {
            setDetailCampaignId("campaign-draft");
            setScene("detail");
          }}
        >
          <Form.Item
            label="活动名称"
            name="name"
            rules={[
              { required: true, message: "请输入活动名称" },
              { max: 200, message: "活动名称不能超过 200 个字符" },
            ]}
          >
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item label="负责人" name="owner_operator_id">
            <Select
              allowClear
              placeholder="未指定"
              options={
                scene === "owner-options-failed"
                  ? []
                  : PREVIEW_ACTIVE_OPERATORS.map((operator) => ({
                      label: operator.name,
                      value: operator.id,
                    }))
              }
              loading={false}
            />
          </Form.Item>
          <div className="campaign-preview-helper">
            未指定负责人时，将由系统按当前操作人设置。
          </div>
          <div className="campaign-modal-footer">
            <Button onClick={() => setScene("list")}>取消</Button>
            <Button type="primary" htmlType="submit">
              创建活动
            </Button>
          </div>
        </Form>
      </Modal>

      <Modal
        title="编辑拓客活动"
        width={560}
        open={editScene}
        footer={null}
        destroyOnHidden
        onCancel={() => setScene(closedScene ? "closed" : "detail")}
      >
        {scene === "edit-conflict" ? (
          <Alert
            type="warning"
            showIcon
            message="活动信息已被其他人更新。"
            description="请重新加载最新信息后再继续编辑。"
            className="campaign-modal-alert"
            action={
              <Button size="small" onClick={() => setScene("detail")}>
                重新加载最新信息
              </Button>
            }
          />
        ) : null}
        {sceneIsOwnerOptionFailed ? (
          <Alert
            type="warning"
            showIcon
            message="负责人选项加载失败"
            description="当前负责人仍可保留，暂时无法变更负责人。"
            className="campaign-modal-alert"
          />
        ) : null}

        <Form
          form={editForm}
          layout="vertical"
          onFinish={() => {
            setScene(closedScene ? "closed" : "detail");
          }}
        >
          <Form.Item
            label="活动名称"
            name="name"
            rules={[
              { required: true, message: "请输入活动名称" },
              { max: 200, message: "活动名称不能超过 200 个字符" },
            ]}
          >
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item label="负责人" name="owner_operator_id">
            {sceneIsOwnerOptionFailed ? (
              <div>
                {ownerLabel}
                {isOwnerDisabled ? (
                  <Tag className="campaign-owner-status-tag">已停用</Tag>
                ) : null}
              </div>
            ) : (
              <Select
                options={operatorsWithCurrent.map((operator) => ({
                  label: operator.name,
                  value: operator.id,
                  disabled:
                    operator.id === currentCampaign.owner_operator_id &&
                    operator.status === "disabled",
                }))}
              />
            )}
          </Form.Item>
          <div className="campaign-modal-footer">
            <Button onClick={() => setScene("detail")}>取消</Button>
            <Button
              type="primary"
              htmlType="submit"
              disabled={scene === "edit-conflict"}
            >
              保存
            </Button>
          </div>
        </Form>
      </Modal>
    </main>
  );
}
