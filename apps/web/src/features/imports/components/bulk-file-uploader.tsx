"use client";

import { FileAddOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Typography } from "antd";
import { useRef } from "react";

const { Text } = Typography;

export type BulkUploadItem = {
  id: string;
  importJobId: string;
  clientFileId: string;
  file: File;
  status: "queued" | "uploading" | "failed";
  error: string | null;
};

export function BulkFileUploader({
  items,
  disabled,
  onSelectFiles,
  onRetry,
}: {
  items: readonly BulkUploadItem[];
  disabled: boolean;
  onSelectFiles: (files: File[]) => void;
  onRetry: (item: BulkUploadItem) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <div className="bulk-file-uploader">
      <input
        ref={inputRef}
        className="bulk-file-input"
        type="file"
        accept=".csv,.xlsx"
        multiple
        aria-label="选择批量文件"
        disabled={disabled}
        onChange={(event) => {
          const files = Array.from(event.target.files ?? []);
          event.target.value = "";
          if (files.length > 0) onSelectFiles(files);
        }}
      />
      <Button
        type="primary"
        icon={<FileAddOutlined aria-hidden="true" />}
        disabled={disabled}
        onClick={() => inputRef.current?.click()}
      >
        添加文件
      </Button>
      <Text type="secondary">支持 CSV / XLSX，可一次添加多个文件。</Text>

      {items.length > 0 ? (
        <div className="bulk-upload-queue" aria-label="文件上传队列">
          {items.map((item) => (
            <div className="bulk-upload-queue-item" key={item.id}>
              <div>
                <Text>{item.file.name}</Text>
                <Text type="secondary">
                  {item.status === "queued"
                    ? "等待上传"
                    : item.status === "uploading"
                      ? "正在上传"
                      : (item.error ?? "文件上传失败")}
                </Text>
              </div>
              {item.status === "failed" ? (
                <Button
                  size="small"
                  icon={<ReloadOutlined aria-hidden="true" />}
                  onClick={() => onRetry(item)}
                  disabled={disabled}
                >
                  重新上传
                </Button>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
