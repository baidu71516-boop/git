"use client";

import {
  Alert,
  Button,
  Card,
  Descriptions,
  Form,
  Input,
  Modal,
  Select,
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
    updated_at: "2026-08-20T03:10:00Z",
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

type CampaignFormValues = {
  name: string;
  owner_operator_id?: string;
};

type CampaignEditableValues = {
  name: string;
  owner_operator_id: string;
};

const PREVIEW_SCENES: Array<{ key: SceneKey; label: string }> = [
  { key: "list", label: "拓客活动 · 列表" },
  { key: "list-empty", label: "拓客活动 · 空状态" },
  { key: "list-load-failed", label: "拓客活动 · 加载失败" },
  { key: "create", label: "拓客活动 · 新建活动" },
  { key: "detail", label: "拓客活动 · 详情" },
  { key: "detail-disabled-owner", label: "拓客活动 · 停用负责人" },
  { key: "edit", label: "拓客活动 · 编辑活动" },
  { key: "edit-conflict", label: "拓客活动 · 编辑冲突" },
  { key: "closed", label: "拓客活动 · 已关闭" },
  { key: "owner-options-failed", label: "拓客活动 · 负责人选项加载失败" },
];

function parsePreviewScene(raw: string | null): SceneKey {
  if (raw === null) return "list";
  return PREVIEW_SCENES.some((candidate) => candidate.key === raw)
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

export function CampaignPreviewWorkspace() {
  const searchParams = useSearchParams();
  const initialScene = parsePreviewScene(searchParams.get("scene"));
  const [scene, setScene] = useState<SceneKey>(initialScene);
  const [detailCampaignId, setDetailCampaignId] = useState(
    sceneCampaignId(initialScene),
  );
  const [createForm] = Form.useForm<CampaignFormValues>();
  const [editForm] = Form.useForm<CampaignEditableValues>();

  const [sceneCreateName, setSceneCreateName] = useState("");
  const [sceneCreateOwner, setSceneCreateOwner] = useState<string | undefined>(
    undefined,
  );

  const [sceneEditName, setSceneEditName] = useState("");
  const [sceneEditOwner, setSceneEditOwner] = useState("");

  const currentCampaign = useMemo(
    () =>
      PREVIEW_CAMPAIGNS.find((campaign) => campaign.id === detailCampaignId) ??
      PREVIEW_CAMPAIGNS[0]!,
    [detailCampaignId],
  );
  const status = campaignStatusPresentation(currentCampaign.status);
  const ownerLabel = currentCampaign.owner.name;
  const closedScene = scene === "closed";
  const editScene = scene === "edit" || scene === "edit-conflict";
  const createScene = scene === "create" || scene === "owner-options-failed";
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
  const ownerOptions = operatorsWithCurrent.map((operator) => ({
    label: operator.name,
    value: operator.id,
  }));

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
        name: sceneCreateName || "新建拓客活动",
        owner_operator_id: sceneCreateOwner,
      });
    }
  }, [createScene, createForm, sceneCreateName, sceneCreateOwner]);

  useEffect(() => {
    if (editScene) {
      editForm.setFieldsValue({
        name: sceneEditName || currentCampaign.name,
        owner_operator_id: sceneEditOwner || currentCampaign.owner_operator_id,
      });
    }
  }, [editScene, editForm, sceneEditName, sceneEditOwner, currentCampaign]);

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
          <Button type="primary" onClick={() => setScene("create")}>
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
          <Button type="default" disabled>
            加载更多
          </Button>
          <span className="campaign-preview-pagination-end">已经到底了</span>
        </div>
      </div>
    );
  }

  const sceneIsOwnerOptionFailed = scene === "owner-options-failed";

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
          <span className="campaign-preview-toolbar-label">开发预览状态</span>
          <label className="campaign-preview-scene-select">
            <span>预览场景</span>
            <Select
              aria-label="预览场景"
              value={scene}
              options={PREVIEW_SCENES}
              onChange={onSceneChange}
            />
          </label>
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
            <div className="campaign-preview-descriptions">
              <StatusBadge
                tone={status.tone}
                className="campaign-table-status-badge"
              >
                {status.label}
              </StatusBadge>
              <span>活动名称：{currentCampaign.name}</span>
              <span>负责人：{ownerLabel}</span>
              <span>
                更新时间：{formatCampaignDateTime(currentCampaign.updated_at)}
              </span>
              {isDisabledCampaignOwner(currentCampaign.owner) ? (
                <span className="campaign-owner-disabled">负责人：已停用</span>
              ) : null}
            </div>
            <Card
              className="campaign-preview-detail-card campaign-detail-card"
              variant="borderless"
              title="基本信息"
            >
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
                  {isDisabledCampaignOwner(currentCampaign.owner)
                    ? `${currentCampaign.owner.name}（已停用）`
                    : currentCampaign.owner.name}
                </Descriptions.Item>
                <Descriptions.Item label="创建时间">
                  {formatCampaignDateTime(currentCampaign.created_at)}
                </Descriptions.Item>
                <Descriptions.Item label="更新时间">
                  {formatCampaignDateTime(currentCampaign.updated_at)}
                </Descriptions.Item>
              </Descriptions>
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
            </Card>
          </section>
        ) : null}
        {scene === "list" ||
        scene === "list-empty" ||
        scene === "list-load-failed" ? (
          <section className="campaign-preview-list-workspace">
            <Card
              className="campaign-preview-list-card campaign-list-card"
              variant="borderless"
            >
              {renderListContent()}
            </Card>
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
          onFinish={(values) => {
            setSceneCreateName(values.name);
            setSceneCreateOwner(values.owner_operator_id);
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
              options={scene === "owner-options-failed" ? [] : ownerOptions}
              loading={false}
            />
          </Form.Item>
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
            message="活动信息已被其他人更新"
            description="请重新加载最新版本后再继续编辑。"
            className="campaign-modal-alert"
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
          onFinish={(values) => {
            setSceneEditName(values.name);
            setSceneEditOwner(values.owner_operator_id);
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
                <div>
                  {isDisabledCampaignOwner(currentCampaign.owner)
                    ? `${currentCampaign.owner.name}（已停用）`
                    : currentCampaign.owner.name}
                </div>
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
