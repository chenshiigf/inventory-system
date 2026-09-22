"use client";

import {
  DownloadOutlined,
  FileExcelOutlined,
  InboxOutlined,
  PictureOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Image,
  Space,
  Steps,
  Table,
  Tag,
  Tooltip,
  Typography,
  Upload,
  message,
} from "antd";
import type { TableColumnsType, UploadProps } from "antd";
import { useMemo, useState } from "react";
import {
  getImportPreviewImageUrl,
  getProductImportTemplateUrl,
  previewProductImport,
} from "@/lib/api/product-import";
import type {
  ProductImportPreviewResponse,
  ProductImportPreviewRow,
  ProductImportRowStatus,
} from "@/lib/api/product-import";

function formatFileSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatUploadTime(timestamp: number): string {
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(timestamp));
}

function displayValue(value: string | number | null): string {
  if (value === null || value === "") {
    return "—";
  }
  return String(value);
}

function statusLabel(status: ProductImportRowStatus): string {
  if (status === "valid") {
    return "可导入";
  }
  if (status === "warning") {
    return "有警告";
  }
  return "有错误";
}

function statusColor(status: ProductImportRowStatus): string {
  if (status === "valid") {
    return "success";
  }
  if (status === "warning") {
    return "warning";
  }
  return "error";
}

interface UploadedFileInfo {
  name: string;
  size: number;
  lastModified: number;
}

export default function ProductImportWorkspace() {
  const [preview, setPreview] = useState<ProductImportPreviewResponse | null>(
    null,
  );
  const [fileInfo, setFileInfo] = useState<UploadedFileInfo | null>(null);
  const [uploading, setUploading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [messageApi, messageContextHolder] = message.useMessage();

  async function handleFile(file: File) {
    if (!file.name.toLowerCase().endsWith(".xlsx")) {
      setLoadError("只支持 .xlsx 文件，请先按标准模板整理并保存。");
      return;
    }

    setUploading(true);
    setLoadError(null);
    setFileInfo({
      name: file.name,
      size: file.size,
      lastModified: file.lastModified,
    });
    try {
      const result = await previewProductImport(file);
      setPreview(result);
      messageApi.success("Excel 已上传，数据预览完成");
    } catch (error) {
      setPreview(null);
      setLoadError(
        error instanceof Error ? error.message : "Excel 预览失败，请重试。",
      );
    } finally {
      setUploading(false);
    }
  }

  function resetPreview() {
    setPreview(null);
    setFileInfo(null);
    setLoadError(null);
  }

  const uploadProps: UploadProps = {
    accept: ".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    multiple: false,
    showUploadList: false,
    beforeUpload: (file) => {
      void handleFile(file);
      return Upload.LIST_IGNORE;
    },
  };

  const columns = useMemo<TableColumnsType<ProductImportPreviewRow>>(
    () => [
      {
        title: "Excel行",
        dataIndex: "excel_row",
        key: "excel_row",
        width: 78,
        fixed: "left",
        render: (value: number) => <span className="import-row-number">{value}</span>,
      },
      {
        title: "图片",
        dataIndex: "image_preview_url",
        key: "image_preview_url",
        width: 82,
        render: (value: string | null, row) => {
          const imageUrl = getImportPreviewImageUrl(value);
          if (!imageUrl) {
            return (
              <span className="import-image-empty">
                <PictureOutlined aria-hidden="true" />
                <span>无图</span>
              </span>
            );
          }
          return (
            <span className="import-image-frame">
              <Image
                src={imageUrl}
                alt={`第${row.excel_row}行商品图片`}
                width={56}
                height={56}
                preview
              />
            </span>
          );
        },
      },
      {
        title: "仓库",
        dataIndex: "warehouse",
        key: "warehouse",
        width: 110,
      },
      {
        title: "一级分类",
        dataIndex: "category_level_1",
        key: "category_level_1",
        width: 120,
      },
      {
        title: "二级分类",
        dataIndex: "category_level_2",
        key: "category_level_2",
        width: 120,
      },
      {
        title: "产品尺寸",
        dataIndex: "size",
        key: "size",
        width: 125,
        render: displayValue,
      },
      {
        title: "装箱数",
        dataIndex: "packing_qty",
        key: "packing_qty",
        width: 90,
        align: "right",
        render: displayValue,
      },
      {
        title: "单位",
        dataIndex: "unit",
        key: "unit",
        width: 80,
        render: displayValue,
      },
      {
        title: "单价",
        dataIndex: "price",
        key: "price",
        width: 95,
        align: "right",
        render: displayValue,
      },
      {
        title: "当前箱数",
        dataIndex: "carton_count",
        key: "carton_count",
        width: 100,
        align: "right",
        render: displayValue,
      },
      {
        title: "备注",
        dataIndex: "remark",
        key: "remark",
        width: 160,
        render: displayValue,
      },
      {
        title: "原系统编号",
        dataIndex: "source_code",
        key: "source_code",
        width: 120,
        render: displayValue,
      },
      {
        title: "状态",
        dataIndex: "status",
        key: "status",
        width: 92,
        fixed: "right",
        render: (value: ProductImportRowStatus) => (
          <Tag color={statusColor(value)}>{statusLabel(value)}</Tag>
        ),
      },
      {
        title: "错误信息",
        dataIndex: "messages",
        key: "messages",
        width: 250,
        fixed: "right",
        render: (messages: string[]) => (
          <div className="import-message-list">
            {messages.length > 0 ? messages.join("；") : "—"}
          </div>
        ),
      },
    ],
    [],
  );

  return (
    <>
      {messageContextHolder}
      <div className="product-import-page">
        <div className="page-heading product-import-heading">
          <div>
            <Typography.Title level={1}>批量导入商品</Typography.Title>
            <p className="product-import-subtitle">
              按照标准模板上传 Excel，预览并检查数据。
            </p>
          </div>
          <Button
            className="product-import-template-button"
            type="primary"
            icon={<DownloadOutlined />}
            href={getProductImportTemplateUrl()}
          >
            下载标准模板
          </Button>
        </div>

        <div className="product-import-steps">
          <Steps
            current={preview ? 1 : 0}
            items={[
              { title: "上传文件" },
              { title: "数据预览" },
              { title: "确认导入", disabled: true },
            ]}
          />
        </div>

        {loadError && (
          <Alert
            className="product-import-alert"
            type="error"
            showIcon
            title="Excel 预览失败"
            description={loadError}
          />
        )}

        {!preview ? (
          <section className="product-import-upload-panel" aria-label="上传商品导入 Excel">
            <div className="product-import-section-label">第一步</div>
            <Typography.Title level={2}>上传整理好的 Excel</Typography.Title>
            <p className="product-import-panel-description">
              只读取工作表“商品导入”的前 20 条非空商品行；本阶段不会修改库存数据库。
            </p>
            <Upload.Dragger {...uploadProps} disabled={uploading}>
              <p className="ant-upload-drag-icon">
                <InboxOutlined />
              </p>
              <p className="ant-upload-text">
                {uploading ? "正在读取 Excel…" : "点击选择或将 .xlsx 文件拖到这里"}
              </p>
              <p className="ant-upload-hint">
                文件大小不超过 100MB，图片请直接插入对应商品行的 D 列附近。
              </p>
            </Upload.Dragger>
            <div className="product-import-upload-notes">
              <span>固定工作表：商品导入</span>
              <span>固定列数：11 列</span>
              <span>图片：每行最多 1 张</span>
            </div>
          </section>
        ) : (
          <>
            <section className="product-import-file-card" aria-label="已上传文件">
              <div className="product-import-file-icon">
                <FileExcelOutlined />
              </div>
              <div className="product-import-file-meta">
                <strong>{fileInfo?.name ?? preview.file_name}</strong>
                <span>
                  {fileInfo ? formatFileSize(fileInfo.size) : "Excel 文件"}
                  {fileInfo ? ` · 上传于 ${formatUploadTime(fileInfo.lastModified)}` : ""}
                </span>
              </div>
              <Button icon={<ReloadOutlined />} onClick={resetPreview}>
                重新上传
              </Button>
            </section>

            <div className="product-import-stat-grid" aria-label="导入预览统计">
              <div className="product-import-stat-card">
                <span>总行数</span>
                <strong>{preview.total_rows}</strong>
              </div>
              <div className="product-import-stat-card is-valid">
                <span>可导入</span>
                <strong>{preview.valid_count}</strong>
              </div>
              <div className="product-import-stat-card is-warning">
                <span>有警告</span>
                <strong>{preview.warning_count}</strong>
              </div>
              <div className="product-import-stat-card is-error">
                <span>有错误</span>
                <strong>{preview.error_count}</strong>
              </div>
            </div>

            <section className="product-import-table-panel" aria-label="商品导入预览表">
              <div className="product-import-table-heading">
                <div>
                  <Typography.Title level={2}>数据预览</Typography.Title>
                  <p>“可导入”仅表示通过当前校验，并不代表已经写入数据库。</p>
                </div>
                <span className="product-import-limit-note">最多预览前 20 条非空商品行</span>
              </div>
              <Table<ProductImportPreviewRow>
                className="product-import-table"
                rowKey="excel_row"
                columns={columns}
                dataSource={preview.rows}
                pagination={false}
                scroll={{ x: 1650 }}
                size="middle"
              />
            </section>

            <div className="product-import-bottom-bar">
              <Button onClick={resetPreview}>重新上传</Button>
              <Space size={14}>
                <span className="product-import-readonly-note">
                  当前仅进行数据预览，本阶段不会修改库存数据库。
                </span>
                <Tooltip title="正式导入将在下一阶段开放">
                  <span>
                    <Button
                      className="product-import-confirm-button"
                      type="primary"
                      disabled
                    >
                      确认导入 {preview.valid_count} 条
                    </Button>
                  </span>
                </Tooltip>
              </Space>
            </div>
          </>
        )}
      </div>
    </>
  );
}
