"use client";

import { LockOutlined, TeamOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Spin,
  Typography,
} from "antd";
import { useCallback, useEffect, useState } from "react";

import { InfluencerWorkspace } from "@/features/influencers/influencer-workspace";
import { InfluencerDetailWorkspace } from "@/features/influencers/influencer-detail-workspace";
import { DataCollectionWorkspace } from "@/features/imports/data-collection-workspace";
import { ImportJobHistory } from "@/features/imports/import-job-history";
import { RefreshQueueDetail } from "@/features/refresh-queues/refresh-queue-detail";
import { RefreshQueueList } from "@/features/refresh-queues/refresh-queue-list";
import { TodayWorkspace } from "@/features/outreach-today/today-workspace";
import { CampaignDetailView } from "@/features/campaigns/campaign-detail-view";
import { CampaignListView } from "@/features/campaigns/campaign-list-view";
import { CandidatePoolDetailView } from "@/features/candidate-pools/candidate-pool-detail-view";
import { CandidatePoolListView } from "@/features/candidate-pools/candidate-pool-list-view";
import { CandidateRunDetailView } from "@/features/candidate-pools/candidate-run-detail-view";
import { AppShell } from "@/components/app-shell";
import { ApiClientError, apiRequest } from "@/lib/api/client";

const { Paragraph, Title } = Typography;

type Role = "super_admin" | "manager" | "operator" | "viewer";

type Department = {
  id: string;
  name: string;
  status: "active" | "disabled";
};

type Operator = {
  id: string;
  department_id: string;
  name: string;
  role: Role;
  status: "active" | "disabled";
};

type AuthMe = {
  department: Department;
  operator: Operator | null;
  role: Role;
  expires_at: string;
};

type LoginValues = {
  department_id: string;
  password: string;
  remember_me: boolean;
};

const roleLabels: Record<Role, string> = {
  super_admin: "超级管理员",
  manager: "管理员",
  operator: "操作员",
  viewer: "只读成员",
};

type AuthWorkspace =
  | "imports"
  | "import-jobs"
  | "influencers"
  | "refresh-queues"
  | "outreach-today"
  | "campaigns"
  | "candidate-pools";

function workspaceRequiresOperator(
  workspace: AuthWorkspace,
  role: Role,
): boolean {
  return (
    (workspace === "imports" && role !== "viewer") ||
    (workspace === "refresh-queues" && role !== "viewer")
  );
}

export function AuthShell({
  workspace = "imports",
  influencerId,
  refreshQueueId,
  campaignId,
  candidatePoolId,
  candidateRunId,
}: {
  workspace?: AuthWorkspace;
  influencerId?: string;
  refreshQueueId?: string;
  campaignId?: string;
  candidatePoolId?: string;
  candidateRunId?: string;
}) {
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [operators, setOperators] = useState<Operator[]>([]);
  const [auth, setAuth] = useState<AuthMe | null>(null);
  const [error, setError] = useState<string | null>(null);
  const requiresOperator = auth
    ? workspaceRequiresOperator(workspace, auth.role)
    : false;

  const loadDepartments = useCallback(async () => {
    const response = await apiRequest<Department[]>("/departments");
    setDepartments(response.data ?? []);
  }, []);

  const loadOperators = useCallback(async () => {
    const response = await apiRequest<Operator[]>("/operators");
    setOperators(response.data ?? []);
  }, []);

  useEffect(() => {
    async function initialize() {
      try {
        const response = await apiRequest<AuthMe>("/auth/me");
        setAuth(response.data);
        if (
          response.data &&
          workspaceRequiresOperator(workspace, response.data.role) &&
          !response.data.operator
        ) {
          await loadOperators();
        }
        if (
          workspace === "refresh-queues" &&
          response.data?.role === "super_admin"
        ) {
          await loadDepartments();
        }
      } catch (caught) {
        if (!(caught instanceof ApiClientError) || caught.status !== 401) {
          setError(caught instanceof Error ? caught.message : "系统暂时不可用");
        }
        await loadDepartments();
      } finally {
        setLoading(false);
      }
    }
    void initialize();
  }, [loadDepartments, loadOperators, workspace]);

  async function handleLogin(values: LoginValues) {
    setSubmitting(true);
    setError(null);
    try {
      await apiRequest("/auth/login", {
        method: "POST",
        body: JSON.stringify(values),
      });
      const meResponse = await apiRequest<AuthMe>("/auth/me");
      setAuth(meResponse.data);
      if (
        meResponse.data &&
        workspaceRequiresOperator(workspace, meResponse.data.role) &&
        !meResponse.data.operator
      ) {
        await loadOperators();
      }
      if (
        workspace === "refresh-queues" &&
        meResponse.data?.role === "super_admin"
      ) {
        await loadDepartments();
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "登录失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleOperatorSelect(operatorId: string) {
    setSubmitting(true);
    setError(null);
    try {
      const response = await apiRequest<AuthMe>("/auth/select-operator", {
        method: "POST",
        body: JSON.stringify({ operator_id: operatorId }),
      });
      setAuth(response.data);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "选择操作人失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleLogout() {
    setSubmitting(true);
    try {
      await apiRequest("/auth/logout", { method: "POST" });
      setAuth(null);
      setOperators([]);
      await loadDepartments();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "退出失败");
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <main className="auth-shell auth-loading">
        <Spin size="large" description="正在检查登录状态" />
      </main>
    );
  }

  if (!auth) {
    return (
      <main className="auth-shell">
        <Card className="login-card" bordered={false}>
          <Space direction="vertical" size="large" className="full-width">
            <div>
              <Title level={2}>达人智能触达系统</Title>
              <Paragraph type="secondary">请使用部门密码登录。</Paragraph>
            </div>
            {error ? <Alert type="error" showIcon message={error} /> : null}
            <Form<LoginValues>
              layout="vertical"
              initialValues={{ remember_me: false }}
              onFinish={(values) => void handleLogin(values)}
            >
              <Form.Item
                label="部门"
                name="department_id"
                rules={[{ required: true, message: "请选择部门" }]}
              >
                <Select
                  placeholder="选择部门"
                  options={departments.map((department) => ({
                    label: department.name,
                    value: department.id,
                  }))}
                  suffixIcon={<TeamOutlined />}
                />
              </Form.Item>
              <Form.Item
                label="密码"
                name="password"
                rules={[{ required: true, message: "请输入密码" }]}
              >
                <Input.Password
                  prefix={<LockOutlined />}
                  autoComplete="current-password"
                />
              </Form.Item>
              <Form.Item name="remember_me" valuePropName="checked">
                <Checkbox>30 天内保持登录</Checkbox>
              </Form.Item>
              <Button
                type="primary"
                htmlType="submit"
                block
                loading={submitting}
              >
                登录
              </Button>
            </Form>
          </Space>
        </Card>
      </main>
    );
  }

  const title =
    workspace === "imports"
      ? "数据采集"
      : workspace === "import-jobs"
        ? "导入记录"
        : workspace === "refresh-queues"
          ? refreshQueueId
            ? "数据更新名单"
            : "数据更新"
          : workspace === "outreach-today"
            ? "今日触达"
            : workspace === "campaigns"
              ? campaignId
                ? "拓客活动详情"
                : "拓客活动"
              : workspace === "candidate-pools"
                ? candidateRunId
                  ? "候选结果"
                  : candidatePoolId
                    ? "候选池详情"
                    : "候选池"
                : influencerId
                  ? "达人详情"
                  : "达人库";

  return (
    <AppShell
      title={title}
      description={
        workspace === "imports"
          ? "上传达人数据，完成文件检查后生成数据预览。"
          : undefined
      }
      department={auth.department.name}
      operator={auth.operator?.name ?? null}
      role={roleLabels[auth.role]}
      onLogout={() => void handleLogout()}
      logoutLoading={submitting}
    >
      {error ? <Alert type="error" showIcon message={error} /> : null}
      {workspace === "influencers" ? (
        influencerId ? (
          <InfluencerDetailWorkspace influencerId={influencerId} />
        ) : (
          <InfluencerWorkspace />
        )
      ) : workspace === "import-jobs" ? (
        <ImportJobHistory />
      ) : workspace === "refresh-queues" ? (
        auth.role === "viewer" || auth.operator ? (
          refreshQueueId ? (
            <RefreshQueueDetail queueId={refreshQueueId} role={auth.role} />
          ) : (
            <RefreshQueueList
              role={auth.role}
              departments={departments.filter(
                (department) => department.status === "active",
              )}
              currentDepartmentId={auth.department.id}
            />
          )
        ) : null
      ) : workspace === "outreach-today" ? (
        <TodayWorkspace />
      ) : workspace === "campaigns" ? (
        campaignId ? (
          <CampaignDetailView
            campaignId={campaignId}
            role={auth.role}
            hasSelectedOperator={auth.operator !== null}
          />
        ) : (
          <CampaignListView
            role={auth.role}
            hasSelectedOperator={auth.operator !== null}
          />
        )
      ) : workspace === "candidate-pools" ? (
        candidatePoolId ? (
          candidateRunId ? (
            <CandidateRunDetailView
              poolId={candidatePoolId}
              runId={candidateRunId}
              role={auth.role}
              hasSelectedOperator={auth.operator !== null}
            />
          ) : (
            <CandidatePoolDetailView
              poolId={candidatePoolId}
              role={auth.role}
              hasSelectedOperator={auth.operator !== null}
            />
          )
        ) : (
          <CandidatePoolListView />
        )
      ) : auth.role === "viewer" || auth.operator ? (
        <DataCollectionWorkspace role={auth.role} />
      ) : null}

      <Modal
        title="选择当前操作人"
        open={requiresOperator && !auth.operator}
        footer={null}
        closable={false}
      >
        <Paragraph type="secondary">请选择本次操作的实际执行人。</Paragraph>
        {error ? (
          <Alert
            type="error"
            showIcon
            message={error}
            className="modal-error"
          />
        ) : null}
        <Select
          className="full-width"
          size="large"
          placeholder="选择操作人"
          loading={submitting}
          onChange={(operatorId: string) =>
            void handleOperatorSelect(operatorId)
          }
          options={operators.map((operator) => ({
            label: `${operator.name} · ${roleLabels[operator.role]}`,
            value: operator.id,
          }))}
        />
      </Modal>
    </AppShell>
  );
}
