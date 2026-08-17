"use client";

import {
  CloudUploadOutlined,
  FileSearchOutlined,
  PlusOutlined,
  ReloadOutlined,
  StopOutlined,
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Pagination,
  Popconfirm,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  IMPORT_CANONICAL_FIELDS,
  ImportFieldMappingEditor,
} from "@/features/imports/components/import-field-mapping-editor";
import { getBulkErrorMessage } from "@/features/imports/formatters";
import { apiRequest } from "@/lib/api/client";

const { Paragraph, Text, Title } = Typography;

type Role = "super_admin" | "manager" | "operator" | "viewer";
type ImportStatus =
  | "uploaded"
  | "parsing"
  | "mapping_required"
  | "previewing"
  | "preview_ready"
  | "preview_stale"
  | "confirm_queued"
  | "importing"
  | "completed"
  | "failed"
  | "cancelled";
type ImportAction =
  "create" | "update" | "no_change" | "skip" | "error" | "manual_review";

type CollectionJob = {
  id: string;
  name: string;
  industry: string;
  target_count: number;
  status: string;
};

type ImportJob = {
  id: string;
  collection_job_id: string;
  original_filename: string;
  mime_type: string;
  file_size: number;
  sha256: string;
  status: ImportStatus;
  detected_fields: string[] | null;
  field_mapping: Record<string, string> | null;
  preview_revision: number;
  preview_summary: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
  error_code: string | null;
  error_message: string | null;
};

type ImportRow = {
  id: string;
  row_number: number;
  raw_data: Record<string, unknown>;
  normalized_data: Record<string, unknown> | null;
  match_type: string;
  action: ImportAction;
  warnings: Array<Record<string, unknown>>;
  errors: Array<Record<string, unknown>>;
};

type ImportRowsPage = {
  items: ImportRow[];
  total: number;
  offset: number;
  limit: number;
};

type CollectionValues = {
  name: string;
  industry: string;
  subdirection?: string;
  purpose: string;
  target_action: string;
  follower_min?: number;
  follower_max?: number;
  target_count: number;
  notes?: string;
};

const POLLING_STATUSES = new Set<ImportStatus>([
  "uploaded",
  "parsing",
  "previewing",
  "confirm_queued",
  "importing",
]);
const CANCELLABLE_STATUSES = new Set<ImportStatus>([
  "uploaded",
  "parsing",
  "mapping_required",
  "previewing",
  "preview_ready",
  "preview_stale",
  "confirm_queued",
]);

const statusLabels: Record<ImportStatus, string> = {
  uploaded: "已上传",
  parsing: "解析中",
  mapping_required: "需要字段映射",
  previewing: "生成预览中",
  preview_ready: "预览待确认",
  preview_stale: "预览已失效",
  confirm_queued: "确认已排队",
  importing: "导入中",
  completed: "导入完成",
  failed: "处理失败",
  cancelled: "已取消",
};

function summaryNumber(
  summary: Record<string, unknown> | null,
  key: string,
): number {
  const value = summary?.[key];
  return typeof value === "number" ? value : 0;
}

function statusColor(status: ImportStatus): string {
  if (status === "completed") return "success";
  if (status === "failed" || status === "preview_stale") return "error";
  if (status === "cancelled") return "default";
  if (POLLING_STATUSES.has(status)) return "processing";
  return "blue";
}

function displayName(row: ImportRow): string {
  const value = row.normalized_data?.display_name;
  return typeof value === "string" && value ? value : "—";
}

function summaryText(
  summary: Record<string, unknown> | null,
  key: string,
): string {
  const value = summary?.[key];
  return typeof value === "string" && value ? value : "未知";
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
}

function issueText(issue: Record<string, unknown>): string {
  const code = typeof issue.code === "string" ? issue.code : "IMPORT_ROW_ISSUE";
  const message =
    typeof issue.message === "string" ? issue.message : "未提供详情";
  const field = typeof issue.field === "string" ? ` · 字段 ${issue.field}` : "";
  return `${code}${field}：${message}`;
}

function legacyFailureText(errorCode: string | null): string {
  switch (errorCode) {
    case "INVALID_FILE_EXTENSION":
      return "仅支持 CSV / XLSX 文件，请重新选择文件。";
    case "MIME_MISMATCH":
      return "文件内容与格式不匹配，请检查后重新上传。";
    case "INVALID_CSV_ENCODING":
      return "CSV 编码无法识别，请转换编码后重新上传。";
    case "INVALID_CSV":
      return "CSV 文件格式不正确或包含不安全内容，请检查后重新上传。";
    case "EMPTY_FILE":
      return "文件内容为空，请选择包含达人数据的文件。";
    case "FILE_TOO_LARGE":
      return "文件过大，请缩小后重新上传。";
    case "FILE_INTEGRITY_FAILED":
      return "文件完整性校验失败，请重新上传。";
    default:
      return "文件处理失败，请重新上传文件或新建采集任务。";
  }
}

function rowDetails(row: ImportRow) {
  return (
    <div className="row-preview-details">
      <div>
        <Text strong>标准化数据</Text>
        <pre>{JSON.stringify(row.normalized_data ?? {}, null, 2)}</pre>
      </div>
      <div>
        <Text strong>原始行（审计）</Text>
        <pre>{JSON.stringify(row.raw_data, null, 2)}</pre>
      </div>
      {row.warnings.length ? (
        <Alert
          type="warning"
          showIcon
          message="行级警告"
          description={
            <ul className="row-issue-list">
              {row.warnings.map((issue, index) => (
                <li key={`${issueText(issue)}-${index}`}>{issueText(issue)}</li>
              ))}
            </ul>
          }
        />
      ) : null}
      {row.errors.length ? (
        <Alert
          type="error"
          showIcon
          message="行级错误"
          description={
            <ul className="row-issue-list">
              {row.errors.map((issue, index) => (
                <li key={`${issueText(issue)}-${index}`}>{issueText(issue)}</li>
              ))}
            </ul>
          }
        />
      ) : null}
    </div>
  );
}

export function ImportWorkspace({ role }: { role: Role }) {
  const readOnly = role === "viewer";
  const [collectionForm] = Form.useForm<CollectionValues>();
  const [collections, setCollections] = useState<CollectionJob[]>([]);
  const [collectionId, setCollectionId] = useState<string>();
  const [collectionModalOpen, setCollectionModalOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [job, setJob] = useState<ImportJob | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [rows, setRows] = useState<ImportRow[]>([]);
  const [rowTotal, setRowTotal] = useState(0);
  const [rowOffset, setRowOffset] = useState(0);
  const [actionFilter, setActionFilter] = useState<ImportAction | undefined>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const pageSize = 20;

  const loadCollections = useCallback(async () => {
    const response = await apiRequest<CollectionJob[]>("/collection-jobs");
    const items = response.data ?? [];
    setCollections(items);
    setCollectionId((current) => current ?? items[0]?.id);
  }, []);

  const loadRows = useCallback(
    async (jobId: string, offset: number, action?: ImportAction) => {
      const query = new URLSearchParams({
        offset: String(offset),
        limit: String(pageSize),
      });
      if (action) query.set("action", action);
      const response = await apiRequest<ImportRowsPage>(
        `/import-jobs/${jobId}/rows?${query.toString()}`,
      );
      setRows(response.data?.items ?? []);
      setRowTotal(response.data?.total ?? 0);
      setRowOffset(offset);
    },
    [],
  );

  const loadJob = useCallback(
    async (jobId: string) => {
      const response = await apiRequest<ImportJob>(`/import-jobs/${jobId}`);
      const next = response.data;
      if (!next) return;
      setJob(next);
      if (next.field_mapping) setMapping(next.field_mapping);
      if (
        ["preview_ready", "preview_stale", "completed"].includes(next.status)
      ) {
        await loadRows(next.id, rowOffset, actionFilter);
      }
    },
    [actionFilter, loadRows, rowOffset],
  );

  useEffect(() => {
    async function initialize() {
      try {
        await loadCollections();
      } catch (caught) {
        setError(getBulkErrorMessage(caught, "采集任务加载失败，请稍后重试。"));
      }
    }
    void initialize();
  }, [loadCollections]);

  useEffect(() => {
    if (!job || !POLLING_STATUSES.has(job.status)) return;
    const timer = window.setTimeout(() => {
      void loadJob(job.id).catch((caught: unknown) => {
        setError(getBulkErrorMessage(caught, "导入状态刷新失败，请稍后重试。"));
      });
    }, 1200);
    return () => window.clearTimeout(timer);
  }, [job, loadJob]);

  const selectedCollection = useMemo(
    () => collections.find((item) => item.id === collectionId),
    [collectionId, collections],
  );

  async function createCollection(values: CollectionValues) {
    setBusy(true);
    setError(null);
    try {
      const response = await apiRequest<CollectionJob>("/collection-jobs", {
        method: "POST",
        body: JSON.stringify({
          ...values,
          source_type: "manual_huitun_export",
        }),
      });
      if (response.data) {
        setCollections((current) => [
          response.data as CollectionJob,
          ...current,
        ]);
        setCollectionId(response.data.id);
      }
      collectionForm.resetFields();
      setCollectionModalOpen(false);
      setNotice("采集任务已创建");
    } catch (caught) {
      setError(getBulkErrorMessage(caught, "采集任务创建失败，请检查后重试。"));
    } finally {
      setBusy(false);
    }
  }

  async function uploadFile() {
    if (!collectionId || !file) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    const body = new FormData();
    body.append("collection_job_id", collectionId);
    body.append("file", file);
    try {
      const response = await apiRequest<ImportJob>("/import-jobs", {
        method: "POST",
        body,
      });
      if (response.data) setJob(response.data);
      setRows([]);
      setRowTotal(0);
      setRowOffset(0);
      setNotice("文件已安全保存，后台正在解析");
    } catch (caught) {
      setError(getBulkErrorMessage(caught, "文件上传失败，请检查后重试。"));
    } finally {
      setBusy(false);
    }
  }

  async function submitMapping() {
    if (!job) return;
    setBusy(true);
    setError(null);
    try {
      await apiRequest(`/import-jobs/${job.id}/mapping`, {
        method: "PUT",
        body: JSON.stringify({ mapping }),
      });
      await loadJob(job.id);
      setNotice("字段映射已保存，正在重新生成预览");
    } catch (caught) {
      setError(getBulkErrorMessage(caught, "字段映射保存失败，请检查后重试。"));
    } finally {
      setBusy(false);
    }
  }

  async function confirmImport() {
    if (!job) return;
    setBusy(true);
    setError(null);
    try {
      await apiRequest(`/import-jobs/${job.id}/confirm`, {
        method: "POST",
        body: JSON.stringify({ preview_revision: job.preview_revision }),
      });
      await loadJob(job.id);
      setNotice("确认请求已排队，重复确认不会重复导入");
    } catch (caught) {
      setError(getBulkErrorMessage(caught, "确认导入失败，请稍后重试。"));
    } finally {
      setBusy(false);
    }
  }

  async function regeneratePreview() {
    if (!job) return;
    setBusy(true);
    setError(null);
    try {
      await apiRequest(`/import-jobs/${job.id}/preview`, { method: "POST" });
      await loadJob(job.id);
      setNotice("正在按当前数据状态重新生成预览");
    } catch (caught) {
      setError(getBulkErrorMessage(caught, "数据预览重建失败，请稍后重试。"));
    } finally {
      setBusy(false);
    }
  }

  async function cancelImport() {
    if (!job) return;
    setBusy(true);
    setError(null);
    try {
      const response = await apiRequest<ImportJob>(
        `/import-jobs/${job.id}/cancel`,
        {
          method: "POST",
        },
      );
      if (response.data) setJob(response.data);
      setNotice("本次导入已取消，未写入达人库");
    } catch (caught) {
      setError(getBulkErrorMessage(caught, "取消失败，请稍后重试。"));
    } finally {
      setBusy(false);
    }
  }

  const columns: ColumnsType<ImportRow> = [
    { title: "行", dataIndex: "row_number", width: 72 },
    { title: "达人", render: (_, row) => displayName(row) },
    {
      title: "计划动作",
      dataIndex: "action",
      width: 130,
      render: (action: ImportAction) => <Tag>{action}</Tag>,
    },
    { title: "匹配依据", dataIndex: "match_type", width: 180 },
    {
      title: "提示",
      width: 120,
      render: (_, row) => (
        <Space size="small">
          {row.warnings.length ? (
            <Tag color="warning">W {row.warnings.length}</Tag>
          ) : null}
          {row.errors.length ? (
            <Tag color="error">E {row.errors.length}</Tag>
          ) : null}
          {!row.warnings.length && !row.errors.length ? "—" : null}
        </Space>
      ),
    },
  ];

  return (
    <section className="import-workspace">
      <Card bordered={false}>
        <Space direction="vertical" size="middle" className="full-width">
          <div className="workspace-heading">
            <div>
              <Title level={3}>达人采集</Title>
              <Paragraph type="secondary">
                上传灰豚导出的 CSV / XLSX
                文件，系统会先生成数据预览，确认无误后再写入达人库。
              </Paragraph>
            </div>
            <Button
              icon={<PlusOutlined />}
              onClick={() => setCollectionModalOpen(true)}
              disabled={readOnly}
            >
              新建采集任务
            </Button>
          </div>
          {readOnly ? (
            <Alert
              type="info"
              showIcon
              message="只读角色不能上传或确认导入。"
            />
          ) : null}
          {error ? (
            <Alert type="error" showIcon message={error} closable />
          ) : null}
          {notice ? (
            <Alert type="success" showIcon message={notice} closable />
          ) : null}
          <Row gutter={[16, 16]} align="bottom">
            <Col xs={24} lg={10}>
              <Text strong>采集任务</Text>
              <Select
                className="full-width control-top-gap"
                placeholder="选择采集任务"
                value={collectionId}
                onChange={(value) => setCollectionId(value)}
                options={collections.map((item) => ({
                  value: item.id,
                  label: `${item.name} · ${item.industry}`,
                }))}
              />
            </Col>
            <Col xs={24} lg={10}>
              <Text strong>灰豚导出文件</Text>
              <Input
                className="control-top-gap"
                type="file"
                accept=".csv,.xlsx"
                disabled={readOnly}
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
            </Col>
            <Col xs={24} lg={4}>
              <Button
                type="primary"
                block
                icon={<CloudUploadOutlined />}
                disabled={readOnly || !collectionId || !file}
                loading={busy}
                onClick={() => void uploadFile()}
              >
                上传并预览
              </Button>
            </Col>
          </Row>
          {selectedCollection ? (
            <Text type="secondary">
              当前任务目标：{selectedCollection.target_count} 位达人
            </Text>
          ) : null}
        </Space>
      </Card>

      {job ? (
        <Card bordered={false}>
          <Space direction="vertical" size="middle" className="full-width">
            <div className="workspace-heading">
              <div>
                <Space wrap>
                  <Title level={4}>{job.original_filename}</Title>
                  <Tag color={statusColor(job.status)}>
                    {statusLabels[job.status]}
                  </Tag>
                  <Tag>版本 {job.preview_revision}</Tag>
                </Space>
                <Space direction="vertical" size={2}>
                  <Text type="secondary">
                    文件类型：{summaryText(job.preview_summary, "file_type")} ·
                    MIME：
                    {job.mime_type} · 大小：{formatBytes(job.file_size)}
                  </Text>
                  <Text type="secondary">SHA-256：{job.sha256}</Text>
                </Space>
              </div>
              <Space>
                <Button
                  icon={<ReloadOutlined />}
                  onClick={() => void loadJob(job.id)}
                  loading={busy}
                >
                  刷新
                </Button>
                {CANCELLABLE_STATUSES.has(job.status) ? (
                  <Popconfirm
                    title="确认取消本次导入？"
                    onConfirm={() => void cancelImport()}
                  >
                    <Button danger icon={<StopOutlined />} disabled={readOnly}>
                      取消
                    </Button>
                  </Popconfirm>
                ) : null}
              </Space>
            </div>
            {job.status === "failed" ? (
              <Alert
                type="error"
                showIcon
                message={legacyFailureText(job.error_code)}
                description="当前流程不支持重试此任务；请使用上方真实入口重新上传文件，或新建采集任务后继续。"
              />
            ) : null}
            {job.status === "preview_stale" ? (
              <Alert
                type="warning"
                showIcon
                message="达人数据在预览后发生了变化，请重新生成预览后确认。"
                action={
                  <Button
                    size="small"
                    onClick={() => void regeneratePreview()}
                    disabled={readOnly}
                  >
                    重新生成预览
                  </Button>
                }
              />
            ) : null}
            {POLLING_STATUSES.has(job.status) ? (
              <Alert
                type="info"
                showIcon
                message="后台任务执行中，页面会自动刷新。"
              />
            ) : null}
          </Space>
        </Card>
      ) : null}

      {job?.status === "mapping_required" ? (
        <Card title="字段映射" bordered={false}>
          <Paragraph type="secondary">
            需要达人官方地址 / 平台账号ID /
            来源ID之一；小红书号和邮箱不能作为稳定身份字段。
          </Paragraph>
          <ImportFieldMappingEditor
            sourceFields={job.detected_fields ?? []}
            canonicalFields={IMPORT_CANONICAL_FIELDS}
            value={mapping}
            onChange={setMapping}
            disabled={readOnly}
          />
          <Button
            type="primary"
            onClick={() => void submitMapping()}
            disabled={readOnly}
            loading={busy}
          >
            保存字段映射并生成预览
          </Button>
        </Card>
      ) : null}

      {job?.preview_summary ? (
        <Card title="数据预览摘要" bordered={false}>
          <Row gutter={[16, 16]}>
            {[
              ["总行数", "total_rows"],
              ["有效", "valid_rows"],
              ["新建", "created_rows"],
              ["已存在", "existing_rows"],
              ["更新", "updated_rows"],
              ["无变化", "no_change_rows"],
              ["有效邮箱", "valid_email_rows"],
              ["无效邮箱", "invalid_email_rows"],
              ["缺少邮箱", "missing_email_rows"],
              ["疑似联系方式", "possible_duplicate_contact_rows"],
              ["警告", "warning_rows"],
              ["错误", "error_rows"],
            ].map(([label, key]) => (
              <Col xs={12} md={8} xl={4} key={key}>
                <Statistic
                  title={label}
                  value={summaryNumber(job.preview_summary, key)}
                />
              </Col>
            ))}
          </Row>
          {job.status === "preview_ready" ? (
            <div className="preview-actions">
              <Popconfirm
                title={`确认导入第 ${job.preview_revision} 版数据？`}
                description="确认后才会写入达人、平台账号、联系方式与指标快照。"
                onConfirm={() => void confirmImport()}
              >
                <Button type="primary" disabled={readOnly} loading={busy}>
                  确认当前数据预览
                </Button>
              </Popconfirm>
            </div>
          ) : null}
          {job.status === "completed" && job.result ? (
            <Descriptions
              bordered
              size="small"
              column={2}
              className="result-details"
            >
              {Object.entries(job.result).map(([key, value]) => (
                <Descriptions.Item key={key} label={key}>
                  {String(value)}
                </Descriptions.Item>
              ))}
            </Descriptions>
          ) : null}
        </Card>
      ) : null}

      {job && rowTotal > 0 ? (
        <Card
          title={
            <Space>
              <FileSearchOutlined /> 逐行数据预览
            </Space>
          }
          extra={
            <Select
              allowClear
              placeholder="全部动作"
              value={actionFilter}
              onChange={(value?: ImportAction) => {
                setActionFilter(value);
                void loadRows(job.id, 0, value);
              }}
              options={[
                "create",
                "update",
                "no_change",
                "skip",
                "error",
                "manual_review",
              ].map((value) => ({ value, label: value }))}
            />
          }
          bordered={false}
        >
          <Table<ImportRow>
            rowKey="id"
            columns={columns}
            dataSource={rows}
            pagination={false}
            scroll={{ x: 760 }}
            expandable={{ expandedRowRender: rowDetails }}
            locale={{ emptyText: <Empty description="当前筛选没有行" /> }}
          />
          <Pagination
            className="row-pagination"
            current={Math.floor(rowOffset / pageSize) + 1}
            pageSize={pageSize}
            total={rowTotal}
            showSizeChanger={false}
            onChange={(page) =>
              void loadRows(job.id, (page - 1) * pageSize, actionFilter)
            }
          />
        </Card>
      ) : null}

      <Modal
        title="新建采集任务"
        open={collectionModalOpen}
        footer={null}
        onCancel={() => setCollectionModalOpen(false)}
        destroyOnHidden
      >
        <Form<CollectionValues>
          form={collectionForm}
          layout="vertical"
          initialValues={{ target_count: 50 }}
          onFinish={(values) => void createCollection(values)}
        >
          <Form.Item name="name" label="任务名称" rules={[{ required: true }]}>
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item name="industry" label="行业" rules={[{ required: true }]}>
            <Input maxLength={160} />
          </Form.Item>
          <Form.Item name="subdirection" label="细分方向">
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item
            name="purpose"
            label="采集目的"
            rules={[{ required: true }]}
          >
            <Input.TextArea maxLength={2000} rows={2} />
          </Form.Item>
          <Form.Item
            name="target_action"
            label="目标动作"
            rules={[{ required: true }]}
          >
            <Input maxLength={160} placeholder="例如：商务邮件触达" />
          </Form.Item>
          <Row gutter={12}>
            <Col span={8}>
              <Form.Item name="follower_min" label="最低粉丝">
                <InputNumber min={0} className="full-width" />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="follower_max" label="最高粉丝">
                <InputNumber min={0} className="full-width" />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item
                name="target_count"
                label="目标数量"
                rules={[{ required: true }]}
              >
                <InputNumber min={1} className="full-width" />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="notes" label="备注">
            <Input.TextArea maxLength={4000} rows={2} />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={busy}>
            创建任务
          </Button>
        </Form>
      </Modal>
    </section>
  );
}
