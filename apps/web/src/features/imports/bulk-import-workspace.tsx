"use client";

import {
  Alert,
  Button,
  Card,
  Col,
  Form,
  Input,
  InputNumber,
  Modal,
  Row,
  Space,
  Typography,
} from "antd";
import { useMemo, useRef, useState } from "react";

import { AppLoading } from "@/components/ui/app-loading";

import { BulkImportWorkspaceView } from "./bulk-import-workspace-view";
import type { BulkUploadItem } from "./components/bulk-file-uploader";
import {
  IMPORT_CANONICAL_FIELDS,
  ImportFieldMappingEditor,
} from "./components/import-field-mapping-editor";
import {
  AMBIGUOUS_BULK_CREATE_MESSAGE,
  getBulkErrorMessage,
  isAmbiguousBulkCreateError,
} from "./formatters";
import {
  useBulkImportFiles,
  useBulkImportJob,
  useCollectionJob,
  useCreateBulkImportJobMutation,
  useCreateCollectionJobMutation,
  useExcludeBulkImportFileMutation,
  useRequestBulkPreviewMutation,
  useRetryBulkImportFileMutation,
  useRetryBulkImportJobMutation,
  useUpdateBulkImportFileMappingMutation,
  useUpdateBulkImportFileSourceAcquiredAtMutation,
  useUploadBulkImportFileMutation,
} from "./queries";
import type {
  CollectionJobCreateInput,
  CollectionJobPublic,
  ImportJobFilePublic,
  ImportJobPublic,
} from "./types";

const { Paragraph, Text } = Typography;

const MAX_PARALLEL_UPLOADS = 2;

type CollectionFormValues = {
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

type OwnedImportFile = {
  importJobId: string;
  file: ImportJobFilePublic;
};

function isBulkJob(job: ImportJobPublic): boolean {
  return (
    job.stored_file_id === null &&
    job.original_filename === null &&
    job.mime_type === null &&
    job.file_size === null &&
    job.sha256 === null
  );
}

function clientFileId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `web-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function localDateTimeInput(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    })
      .formatToParts(date)
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

function collectionPayload(
  values: CollectionFormValues,
): CollectionJobCreateInput {
  return {
    ...values,
    subdirection: values.subdirection || null,
    follower_min: values.follower_min ?? null,
    follower_max: values.follower_max ?? null,
    notes: values.notes || null,
    source_type: "manual_huitun_export",
  };
}

export function BulkImportWorkspace({
  role,
  jobId,
  onSelectJob,
  onClearJob,
}: {
  role: "super_admin" | "manager" | "operator" | "viewer";
  jobId: string | null;
  onSelectJob: (jobId: string) => void;
  onClearJob: () => void;
}) {
  const readOnly = role === "viewer";
  const [collectionForm] = Form.useForm<CollectionFormValues>();
  const [collectionModalOpen, setCollectionModalOpen] = useState(false);
  const [pendingCollection, setPendingCollection] =
    useState<CollectionJobPublic | null>(null);
  const [creationError, setCreationError] = useState<string | null>(null);
  const [ambiguousBulkCreate, setAmbiguousBulkCreate] = useState(false);
  const [uploadItems, setUploadItems] = useState<BulkUploadItem[]>([]);
  const [busyFileId, setBusyFileId] = useState<string | null>(null);
  const [mappingFile, setMappingFile] = useState<OwnedImportFile | null>(null);
  const [mappingDraft, setMappingDraft] = useState<Record<string, string>>({});
  const [acquisitionFile, setAcquisitionFile] =
    useState<OwnedImportFile | null>(null);
  const [acquisitionValue, setAcquisitionValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const creationInFlightRef = useRef(false);
  const currentJobIdRef = useRef(jobId);
  const uploadQueueRef = useRef<BulkUploadItem[]>([]);
  const activeUploadCountRef = useRef(0);
  const scheduledUploadIdsRef = useRef(new Set<string>());
  currentJobIdRef.current = jobId;

  const importJobQuery = useBulkImportJob(jobId ?? "", Boolean(jobId));
  const recoveredJob = importJobQuery.data ?? null;
  const validBulkJob = recoveredJob ? isBulkJob(recoveredJob) : false;
  const collectionQuery = useCollectionJob(
    validBulkJob ? (recoveredJob?.collection_job_id ?? "") : "",
    validBulkJob,
  );
  const filesQuery = useBulkImportFiles(jobId ?? "", validBulkJob);

  const createCollectionMutation = useCreateCollectionJobMutation();
  const createBulkJobMutation = useCreateBulkImportJobMutation();
  const uploadMutation = useUploadBulkImportFileMutation();
  const updateTimeMutation = useUpdateBulkImportFileSourceAcquiredAtMutation();
  const updateMappingMutation = useUpdateBulkImportFileMappingMutation();
  const retryFileMutation = useRetryBulkImportFileMutation();
  const excludeFileMutation = useExcludeBulkImportFileMutation();
  const previewMutation = useRequestBulkPreviewMutation();
  const retryJobMutation = useRetryBulkImportJobMutation();

  const files = useMemo(() => filesQuery.data ?? [], [filesQuery.data]);
  const currentUploadItems = useMemo(
    () => uploadItems.filter((item) => item.importJobId === jobId),
    [jobId, uploadItems],
  );
  const uploadBusy = currentUploadItems.some(
    (item) => item.status === "queued" || item.status === "uploading",
  );

  function openNewCollection() {
    if (
      uploadBusy ||
      busyFileId !== null ||
      previewMutation.isPending ||
      retryJobMutation.isPending
    ) {
      setError("当前任务仍有操作正在进行，请等待完成后再新建采集任务。");
      return;
    }
    if (!pendingCollection) {
      setCreationError(null);
      setAmbiguousBulkCreate(false);
    }
    setCollectionModalOpen(true);
  }

  async function submitCollection(values: CollectionFormValues) {
    if (creationInFlightRef.current) return;
    creationInFlightRef.current = true;
    setCreationError(null);
    setAmbiguousBulkCreate(false);
    let collection = pendingCollection;

    try {
      if (!collection) {
        collection = await createCollectionMutation.mutateAsync(
          collectionPayload(values),
        );
        setPendingCollection(collection);
      }
      const job = await createBulkJobMutation.mutateAsync({
        collection_job_id: collection.id,
      });
      setPendingCollection(null);
      setCreationError(null);
      collectionForm.resetFields();
      setCollectionModalOpen(false);
      setNotice("批量采集任务已创建，可以开始添加文件。");
      onSelectJob(job.id);
    } catch (caught) {
      if (!collection) {
        setCreationError(
          getBulkErrorMessage(caught, "采集任务创建失败，请检查后重试。"),
        );
        return;
      }
      const ambiguous = isAmbiguousBulkCreateError(caught);
      setAmbiguousBulkCreate(ambiguous);
      setCreationError(
        ambiguous
          ? AMBIGUOUS_BULK_CREATE_MESSAGE
          : getBulkErrorMessage(
              caught,
              "批量文件处理任务创建失败，可继续使用已创建的采集任务。",
            ),
      );
    } finally {
      creationInFlightRef.current = false;
    }
  }

  function updateUploadItem(
    id: string,
    changes: Partial<Pick<BulkUploadItem, "status" | "error">>,
  ) {
    setUploadItems((current) =>
      current.map((item) => (item.id === id ? { ...item, ...changes } : item)),
    );
  }

  async function uploadOne(item: BulkUploadItem) {
    updateUploadItem(item.id, { status: "uploading", error: null });
    try {
      await uploadMutation.mutateAsync({
        importJobId: item.importJobId,
        clientFileId: item.clientFileId,
        file: item.file,
      });
      setUploadItems((current) =>
        current.filter((candidate) => candidate.id !== item.id),
      );
      if (currentJobIdRef.current === item.importJobId) {
        setNotice(`${item.file.name} 已上传，后台正在处理。`);
      }
    } catch (caught) {
      updateUploadItem(item.id, {
        status: "failed",
        error: getBulkErrorMessage(caught, "文件上传失败，请检查后重试。"),
      });
    }
  }

  function drainUploadQueue() {
    while (
      activeUploadCountRef.current < MAX_PARALLEL_UPLOADS &&
      uploadQueueRef.current.length > 0
    ) {
      const item = uploadQueueRef.current.shift();
      if (!item) return;
      activeUploadCountRef.current += 1;
      void uploadOne(item).finally(() => {
        activeUploadCountRef.current -= 1;
        scheduledUploadIdsRef.current.delete(item.id);
        drainUploadQueue();
      });
    }
  }

  function scheduleUploads(items: BulkUploadItem[]) {
    const unscheduled = items.filter((item) => {
      if (scheduledUploadIdsRef.current.has(item.id)) return false;
      scheduledUploadIdsRef.current.add(item.id);
      return true;
    });
    uploadQueueRef.current.push(...unscheduled);
    drainUploadQueue();
  }

  function selectFiles(selected: File[]) {
    if (!jobId) return;
    const items: BulkUploadItem[] = selected.map((file) => {
      const id = clientFileId();
      return {
        id,
        importJobId: jobId,
        clientFileId: id,
        file,
        status: "queued",
        error: null,
      };
    });
    setError(null);
    setUploadItems((current) => [...current, ...items]);
    scheduleUploads(items);
  }

  function retryUpload(item: BulkUploadItem) {
    if (item.importJobId !== jobId) return;
    updateUploadItem(item.id, { status: "queued", error: null });
    scheduleUploads([item]);
  }

  async function runFileAction(
    ownerJobId: string,
    file: ImportJobFilePublic,
    action: () => Promise<unknown>,
    successMessage: string,
  ): Promise<boolean> {
    setBusyFileId(file.id);
    setError(null);
    try {
      await action();
      if (currentJobIdRef.current === ownerJobId) setNotice(successMessage);
      return true;
    } catch (caught) {
      if (currentJobIdRef.current === ownerJobId) {
        setError(getBulkErrorMessage(caught));
      }
      return false;
    } finally {
      if (currentJobIdRef.current === ownerJobId) setBusyFileId(null);
    }
  }

  function openMapping(file: ImportJobFilePublic) {
    if (!jobId) return;
    setMappingFile({ importJobId: jobId, file });
    setMappingDraft(file.field_mapping ?? {});
  }

  async function saveMapping() {
    if (!mappingFile) return;
    const succeeded = await runFileAction(
      mappingFile.importJobId,
      mappingFile.file,
      () =>
        updateMappingMutation.mutateAsync({
          importJobId: mappingFile.importJobId,
          importJobFileId: mappingFile.file.id,
          mapping: mappingDraft,
        }),
      "字段映射已保存，文件正在重新处理。",
    );
    if (succeeded) setMappingFile(null);
  }

  function openAcquisitionTime(file: ImportJobFilePublic) {
    if (!jobId) return;
    setAcquisitionFile({ importJobId: jobId, file });
    setAcquisitionValue(localDateTimeInput(file.source_acquired_at));
  }

  async function saveAcquisitionTime() {
    if (!acquisitionFile || !acquisitionValue) return;
    const unchanged =
      acquisitionValue ===
      localDateTimeInput(acquisitionFile.file.source_acquired_at);
    const parsed = unchanged ? null : new Date(`${acquisitionValue}:00+08:00`);
    if (!unchanged && parsed && Number.isNaN(parsed.getTime())) {
      setError("请输入有效的数据取得时间。");
      return;
    }
    const sourceAcquiredAt =
      unchanged && acquisitionFile.file.source_acquired_at
        ? acquisitionFile.file.source_acquired_at
        : parsed?.toISOString();
    if (!sourceAcquiredAt) {
      setError("请输入有效的数据取得时间。");
      return;
    }
    const succeeded = await runFileAction(
      acquisitionFile.importJobId,
      acquisitionFile.file,
      () =>
        updateTimeMutation.mutateAsync({
          importJobId: acquisitionFile.importJobId,
          importJobFileId: acquisitionFile.file.id,
          sourceAcquiredAt,
        }),
      "数据取得时间已保存。",
    );
    if (succeeded) setAcquisitionFile(null);
  }

  async function retryFile(file: ImportJobFilePublic) {
    if (!jobId) return;
    await runFileAction(
      jobId,
      file,
      () =>
        retryFileMutation.mutateAsync({
          importJobId: jobId,
          importJobFileId: file.id,
        }),
      "文件已进入重新处理队列。",
    );
  }

  async function excludeFile(file: ImportJobFilePublic) {
    if (!jobId) return;
    await runFileAction(
      jobId,
      file,
      () =>
        excludeFileMutation.mutateAsync({
          importJobId: jobId,
          importJobFileId: file.id,
        }),
      "文件已排除，不会计入本次数据预览。",
    );
  }

  async function requestPreview(rebuild: boolean) {
    if (!jobId) return;
    const ownerJobId = jobId;
    setError(null);
    try {
      await previewMutation.mutateAsync({ importJobId: jobId, rebuild });
      if (currentJobIdRef.current === ownerJobId) {
        setNotice(rebuild ? "正在重新生成数据预览。" : "正在生成数据预览。");
      }
    } catch (caught) {
      if (currentJobIdRef.current === ownerJobId) {
        setError(getBulkErrorMessage(caught, "数据预览请求失败，请重试。"));
      }
    }
  }

  async function retryJob() {
    if (!jobId) return;
    const ownerJobId = jobId;
    setError(null);
    try {
      await retryJobMutation.mutateAsync(jobId);
      if (currentJobIdRef.current === ownerJobId) {
        setNotice("任务已重新进入处理队列。");
      }
    } catch (caught) {
      if (currentJobIdRef.current === ownerJobId) {
        setError(getBulkErrorMessage(caught, "任务重试失败，请稍后再试。"));
      }
    }
  }

  if (jobId && importJobQuery.isPending) {
    return (
      <Card variant="borderless" className="bulk-workspace-card">
        <AppLoading label="正在恢复批量采集任务" />
      </Card>
    );
  }

  if (jobId && importJobQuery.isError) {
    return (
      <Card variant="borderless" className="bulk-workspace-card">
        <Alert
          type="error"
          showIcon
          title={getBulkErrorMessage(
            importJobQuery.error,
            "批量采集任务无法加载。",
          )}
          description="请检查链接或权限。当前系统不会自动查找其他批量任务。"
          action={
            <Space>
              <Button onClick={() => void importJobQuery.refetch()}>
                重新加载
              </Button>
              <Button onClick={onClearJob}>清除当前任务</Button>
            </Space>
          }
        />
      </Card>
    );
  }

  if (recoveredJob && !validBulkJob) {
    return (
      <Card variant="borderless" className="bulk-workspace-card">
        <Alert
          type="error"
          showIcon
          title="当前链接不是批量文件处理任务。"
          description="系统不会把单文件任务转换为批量任务，也不会自动查找其他任务。"
          action={<Button onClick={onClearJob}>清除当前任务</Button>}
        />
      </Card>
    );
  }

  if (validBulkJob && (filesQuery.isPending || collectionQuery.isPending)) {
    return (
      <Card variant="borderless" className="bulk-workspace-card">
        <AppLoading label="正在加载批量文件" />
      </Card>
    );
  }

  const readError = filesQuery.error ?? collectionQuery.error;
  if (validBulkJob && readError) {
    return (
      <Card variant="borderless" className="bulk-workspace-card">
        <Alert
          type="error"
          showIcon
          title={getBulkErrorMessage(readError, "批量任务数据加载失败。")}
          description="请检查网络连接后重新加载。"
          action={
            <Space>
              <Button
                onClick={() => {
                  void filesQuery.refetch();
                  void collectionQuery.refetch();
                }}
              >
                重新加载
              </Button>
              <Button onClick={onClearJob}>清除当前任务</Button>
            </Space>
          }
        />
      </Card>
    );
  }

  return (
    <>
      <BulkImportWorkspaceView
        job={validBulkJob ? recoveredJob : null}
        collection={collectionQuery.data ?? null}
        files={files}
        readOnly={readOnly}
        uploadItems={currentUploadItems}
        busyFileId={busyFileId}
        previewBusy={previewMutation.isPending || retryJobMutation.isPending}
        error={error}
        notice={notice}
        onNewCollection={openNewCollection}
        onSelectFiles={selectFiles}
        onRetryUpload={retryUpload}
        onEditAcquisitionTime={openAcquisitionTime}
        onEditMapping={openMapping}
        onRetryFile={(file) => void retryFile(file)}
        onExcludeFile={(file) => void excludeFile(file)}
        onRequestPreview={() => void requestPreview(false)}
        onRebuildPreview={() => void requestPreview(true)}
        onRetryJob={() => void retryJob()}
      />

      <Modal
        title={pendingCollection ? "继续创建批量采集任务" : "新建采集任务"}
        open={collectionModalOpen}
        footer={null}
        destroyOnHidden={false}
        onCancel={() => setCollectionModalOpen(false)}
      >
        {creationError ? (
          <Alert
            className="modal-error"
            type="error"
            showIcon
            title={creationError}
          />
        ) : null}
        {pendingCollection ? (
          <Alert
            type="info"
            showIcon
            title={`采集任务“${pendingCollection.name}”已创建`}
            description="后续操作只会继续创建批量文件处理任务，不会重复创建采集任务。"
          />
        ) : null}
        <Form<CollectionFormValues>
          form={collectionForm}
          layout="vertical"
          initialValues={{ target_count: 50 }}
          onFinish={(values) => void submitCollection(values)}
        >
          <Form.Item name="name" label="任务名称" rules={[{ required: true }]}>
            <Input maxLength={200} disabled={Boolean(pendingCollection)} />
          </Form.Item>
          <Form.Item name="industry" label="行业" rules={[{ required: true }]}>
            <Input maxLength={160} disabled={Boolean(pendingCollection)} />
          </Form.Item>
          <Form.Item name="subdirection" label="细分方向">
            <Input maxLength={200} disabled={Boolean(pendingCollection)} />
          </Form.Item>
          <Form.Item
            name="purpose"
            label="采集目的"
            rules={[{ required: true }]}
          >
            <Input.TextArea
              maxLength={2000}
              rows={2}
              disabled={Boolean(pendingCollection)}
            />
          </Form.Item>
          <Form.Item
            name="target_action"
            label="目标动作"
            rules={[{ required: true }]}
          >
            <Input
              maxLength={160}
              placeholder="例如：商务邮件触达"
              disabled={Boolean(pendingCollection)}
            />
          </Form.Item>
          <Row gutter={12}>
            <Col span={8}>
              <Form.Item name="follower_min" label="最低粉丝">
                <InputNumber
                  min={0}
                  className="full-width"
                  disabled={Boolean(pendingCollection)}
                />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="follower_max" label="最高粉丝">
                <InputNumber
                  min={0}
                  className="full-width"
                  disabled={Boolean(pendingCollection)}
                />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item
                name="target_count"
                label="目标数量"
                rules={[{ required: true }]}
              >
                <InputNumber
                  min={1}
                  className="full-width"
                  disabled={Boolean(pendingCollection)}
                />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="notes" label="备注">
            <Input.TextArea
              maxLength={4000}
              rows={2}
              disabled={Boolean(pendingCollection)}
            />
          </Form.Item>
          {ambiguousBulkCreate ? null : (
            <Button
              type="primary"
              htmlType="submit"
              block
              loading={
                createCollectionMutation.isPending ||
                createBulkJobMutation.isPending
              }
            >
              {pendingCollection ? "继续创建批量任务" : "创建并开始批量处理"}
            </Button>
          )}
        </Form>
      </Modal>

      <Modal
        title="处理字段映射"
        open={mappingFile?.importJobId === jobId}
        okText="保存字段映射"
        cancelText="取消"
        confirmLoading={busyFileId === mappingFile?.file.id}
        onOk={() => void saveMapping()}
        onCancel={() => setMappingFile(null)}
        width={680}
      >
        <Paragraph type="secondary">
          每个源字段只能映射一个目标字段；必须包含达人昵称与至少一个身份字段。
        </Paragraph>
        <ImportFieldMappingEditor
          sourceFields={mappingFile?.file.detected_fields ?? []}
          canonicalFields={IMPORT_CANONICAL_FIELDS}
          value={mappingDraft}
          onChange={setMappingDraft}
          disabled={busyFileId !== null}
        />
      </Modal>

      <Modal
        title="修改数据取得时间"
        open={acquisitionFile?.importJobId === jobId}
        okText="保存时间"
        cancelText="取消"
        okButtonProps={{ disabled: acquisitionValue.length === 0 }}
        confirmLoading={busyFileId === acquisitionFile?.file.id}
        onOk={() => void saveAcquisitionTime()}
        onCancel={() => setAcquisitionFile(null)}
      >
        <Space orientation="vertical" size="middle" className="full-width">
          <Text type="secondary">
            请输入该文件中的数据实际从来源取得的大致时间。系统不会从文件名或修改时间推断。
          </Text>
          <Input
            type="datetime-local"
            aria-label="数据取得时间"
            value={acquisitionValue}
            onChange={(event) => setAcquisitionValue(event.target.value)}
          />
        </Space>
      </Modal>
    </>
  );
}
