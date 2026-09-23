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
  Modal,
  Result,
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
  commitProductImport,
  getImportPreviewImageUrl,
  getProductImportTemplateUrl,
  previewProductImport,
} from "@/lib/api/product-import";
import type {
  ProductImportCommitResponse,
  ProductImportPreviewPackaging,
  ProductImportPreviewProduct,
  ProductImportPreviewResponse,
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
    return "待确认";
  }
  return "有错误";
}

function statusColor(status: ProductImportRowStatus): string {
  if (status === "valid") {
    return "success";
  }
  if (status === "warning") {
    return "processing";
  }
  return "error";
}

function formatPackaging(
  packingQty: number | null,
  cartonCount: number | null,
  unit: string | null,
): string {
  const quantity =
    packingQty === null ? "装箱数待确认" : `${packingQty} ${unit || "—"}/箱`;
  const cartons = cartonCount === null ? "箱数待确认" : `${cartonCount}箱`;
  return `${quantity} × ${cartons}`;
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
  const [committing, setCommitting] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [commitResult, setCommitResult] =
    useState<ProductImportCommitResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [commitError, setCommitError] = useState<string | null>(null);
  const [messageApi, messageContextHolder] = message.useMessage();

  async function handleFile(file: File) {
    if (!file.name.toLowerCase().endsWith(".xlsx")) {
      setLoadError("只支持 .xlsx 文件，请先按标准模板整理并保存。");
      return;
    }

    setUploading(true);
    setLoadError(null);
    setCommitError(null);
    setCommitResult(null);
    setFileInfo({
      name: file.name,
      size: file.size,
      lastModified: file.lastModified,
    });
    try {
      const result = await previewProductImport(file);
      setPreview(result);
      messageApi.success("Excel 已上传，商品级预览完成");
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
    setCommitError(null);
    setCommitResult(null);
    setConfirmOpen(false);
  }

  async function handleCommit() {
    if (!preview || committing) {
      return;
    }
    setCommitting(true);
    setCommitError(null);
    try {
      const result = await commitProductImport(preview.preview_session_id);
      setCommitResult(result);
      setConfirmOpen(false);
      messageApi.success("商品已正式导入库存");
    } catch (error) {
      setConfirmOpen(false);
      setCommitError(
        error instanceof Error ? error.message : "正式导入失败，请重试。",
      );
    } finally {
      setCommitting(false);
    }
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

  const columns = useMemo<TableColumnsType<ProductImportPreviewProduct>>(
    () => [
      {
        title: "Excel行",
        dataIndex: "excel_rows",
        key: "excel_rows",
        width: 92,
        fixed: "left",
        render: (rows: number[]) => (
          <span className="import-row-number">{rows.join("、")}</span>
        ),
      },
      {
        title: "图片",
        dataIndex: "image_preview_url",
        key: "image_preview_url",
        width: 122,
        render: (value: string | null, product) => {
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
            <div className="import-image-cell">
              <span className="import-image-frame">
                <Image
                  src={imageUrl}
                  alt={`${product.product_group ?? "商品"}预览图`}
                  width={56}
                  height={56}
                  preview
                />
              </span>
              {product.shared_image && (
                <span className="import-shared-image">共用图片</span>
              )}
            </div>
          );
        },
      },
      {
        title: "商品组",
        dataIndex: "product_group",
        key: "product_group",
        width: 110,
        render: displayValue,
      },
      {
        title: "仓库",
        dataIndex: "warehouse",
        key: "warehouse",
        width: 100,
      },
      {
        title: "分类",
        key: "category",
        width: 160,
        render: (_value, product) => (
          <span>
            {product.category_level_1} / {product.category_level_2}
          </span>
        ),
      },
      {
        title: "产品尺寸",
        dataIndex: "size",
        key: "size",
        width: 125,
        render: displayValue,
      },
      {
        title: "包装规格",
        dataIndex: "packagings",
        key: "packagings",
        width: 225,
        render: (
          packagings: ProductImportPreviewPackaging[],
          product: ProductImportPreviewProduct,
        ) => (
          <div className="import-packaging-list">
            {packagings.map((packaging) => (
              <div
                className="import-packaging-line"
                key={`${packaging.excel_row}-${packaging.packing_qty ?? "blank"}`}
              >
                <span className="import-packaging-row">
                  第{packaging.excel_row}行
                </span>
                <span>
                  {formatPackaging(
                    packaging.packing_qty,
                    packaging.carton_count,
                    product.unit,
                  )}
                </span>
              </div>
            ))}
          </div>
        ),
      },
      {
        title: "总箱数",
        dataIndex: "total_carton_count",
        key: "total_carton_count",
        width: 88,
        align: "right",
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
        title: "备注",
        dataIndex: "remark",
        key: "remark",
        width: 190,
        render: displayValue,
      },
      {
        title: "原系统编号",
        dataIndex: "source_codes",
        key: "source_codes",
        width: 140,
        render: (sourceCodes: string[]) => displayValue(sourceCodes.join("、")),
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
        title: "信息",
        dataIndex: "messages",
        key: "messages",
        width: 280,
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

  const packagingCount =
    preview?.products.reduce(
      (total, product) => total + product.packagings.length,
      0,
    ) ?? 0;
  const canCommit = Boolean(
    preview &&
      preview.product_count > 0 &&
      preview.product_count <= 100 &&
      preview.error_count === 0 &&
      !preview.already_imported,
  );

  function commitDisabledReason(): string {
    if (!preview) {
      return "请先上传 Excel 并完成预览";
    }
    if (preview.already_imported) {
      return "这份 Excel 已经成功导入过";
    }
    if (preview.error_count > 0) {
      return "请先修正 Preview 中的错误";
    }
    if (preview.product_count > 100) {
      return "单次最多支持正式导入 100 个商品，请拆分 Excel 后再导入。";
    }
    return "";
  }

  return (
    <>
      {messageContextHolder}
      <div className="product-import-page">
        <div className="page-heading product-import-heading">
          <div>
            <Typography.Title level={1}>批量导入商品</Typography.Title>
            <p className="product-import-subtitle">
              按表头读取 Excel，先查看合并后的商品与包装结构。
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
            current={commitResult ? 2 : preview ? 1 : 0}
            items={[
              { title: "上传文件" },
              { title: "数据预览" },
              { title: "确认导入" },
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

        {commitError && (
          <Alert
            className="product-import-alert"
            type="error"
            showIcon
            title="正式导入失败"
            description={commitError}
          />
        )}

        {commitResult ? (
          <section className="product-import-success-panel" aria-label="正式导入结果">
            <Result
              status="success"
              title="导入成功"
              subTitle={`批次 ${commitResult.batch_id} · ${commitResult.file_name}`}
            />
            <div className="product-import-success-stats">
              <div>
                <span>商品</span>
                <strong>{commitResult.product_count}</strong>
              </div>
              <div>
                <span>包装规格</span>
                <strong>{commitResult.packaging_count}</strong>
              </div>
              <div>
                <span>Excel数据行</span>
                <strong>{commitResult.source_row_count}</strong>
              </div>
            </div>
            <div className="product-import-created-list">
              {commitResult.created_products.map((product) => (
                <div key={product.product_id}>
                  <strong>{product.product_code}</strong>
                  <span>
                    Excel第 {product.excel_rows.join("、")} 行 · {product.packaging_count}
                    种包装
                  </span>
                </div>
              ))}
            </div>
            <Space className="product-import-success-actions" size={12}>
              <Button type="primary" href="/products">
                返回商品库存
              </Button>
              <Button onClick={resetPreview}>导入其他 Excel</Button>
            </Space>
          </section>
        ) : !preview ? (
          <section
            className="product-import-upload-panel"
            aria-label="上传商品导入 Excel"
          >
            <div className="product-import-section-label">第一步</div>
            <Typography.Title level={2}>上传整理好的 Excel</Typography.Title>
            <p className="product-import-panel-description">
              按表头名称读取“商品导入”工作表；列顺序可以调整，也可以保留历史列。确认前不会写入库存数据库。
            </p>
            <Upload.Dragger {...uploadProps} disabled={uploading}>
              <p className="ant-upload-drag-icon">
                <InboxOutlined />
              </p>
              <p className="ant-upload-text">
                {uploading ? "正在读取 Excel…" : "点击选择或将 .xlsx 文件拖到这里"}
              </p>
              <p className="ant-upload-hint">
                文件大小不超过 100MB；图片按“产品图片”表头识别，不依赖固定列位。
              </p>
            </Upload.Dragger>
            <div className="product-import-upload-notes">
              <span>工作表：商品导入</span>
              <span>识别：表头名称</span>
              <span>范围：最多 300 条非空数据行</span>
              <span>公式：读取已保存的计算结果</span>
            </div>
            <div className="product-import-rule-notes">
              <p>
                普通商品的“商品组”留空；同一商品有多个装箱规格时，请给相关行填写相同商品组。
              </p>
              <p>
                一张图片可以被多个商品共用，共用图片不会自动合并商品；同一商品组只能保留一张主图。
              </p>
              <p>
                “当前箱数”也接受历史表头“结余箱数”；公式单元格必须保存有最新计算结果。
              </p>
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
                  {fileInfo
                    ? ` · 上传于 ${formatUploadTime(fileInfo.lastModified)}`
                    : ""}
                </span>
              </div>
              <Button icon={<ReloadOutlined />} onClick={resetPreview}>
                重新上传
              </Button>
            </section>

            {preview.already_imported && (
              <Alert
                className="product-import-alert"
                type="info"
                showIcon
                title="这份 Excel 已经成功导入过"
                description="Preview 仍可查看，但不能再次创建商品。修改 Excel 并重新保存后会产生新的文件指纹。"
              />
            )}

            <div className="product-import-stat-grid" aria-label="导入预览统计">
              <div className="product-import-stat-card">
                <span>Excel数据行</span>
                <strong>{preview.source_row_count}</strong>
              </div>
              <div className="product-import-stat-card is-products">
                <span>预计商品数</span>
                <strong>{preview.product_count}</strong>
              </div>
              <div className="product-import-stat-card is-valid">
                <span>可导入</span>
                <strong>{preview.valid_count}</strong>
              </div>
              <div className="product-import-stat-card is-warning">
                <span>待确认</span>
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
                  <Typography.Title level={2}>商品级数据预览</Typography.Title>
                  <p>“可导入”表示通过当前校验，不代表已经写入数据库。</p>
                </div>
                <span className="product-import-limit-note">
                  单个 Excel 最多 300 条非空数据行 · 正式导入每批最多 100 个商品
                </span>
              </div>
              <Table<ProductImportPreviewProduct>
                className="product-import-table"
                rowKey="preview_id"
                columns={columns}
                dataSource={preview.products}
                pagination={false}
                scroll={{ x: 2050 }}
                size="middle"
              />
            </section>

            <div className="product-import-bottom-bar">
              <Button onClick={resetPreview}>重新上传</Button>
              <Space size={14}>
                <span className="product-import-readonly-note">
                  正式导入会生成商品编号并写入库存，目前没有一键撤销功能。
                </span>
                <Tooltip title={canCommit ? "" : commitDisabledReason()}>
                  <span>
                    <Button
                      className="product-import-confirm-button"
                      type="primary"
                      loading={committing}
                      disabled={!canCommit}
                      onClick={() => setConfirmOpen(true)}
                    >
                      确认导入 {preview.product_count} 个商品
                    </Button>
                  </span>
                </Tooltip>
              </Space>
            </div>
          </>
        )}

        <Modal
          className="product-import-confirm-modal"
          title="确认正式导入？"
          open={confirmOpen}
          okText="确认导入"
          cancelText="取消"
          confirmLoading={committing}
          closable={!committing}
          mask={{ closable: !committing }}
          keyboard={!committing}
          onOk={() => void handleCommit()}
          onCancel={() => !committing && setConfirmOpen(false)}
        >
          <p>将向库存数据库创建：</p>
          <div className="product-import-confirm-counts">
            <strong>{preview?.product_count ?? 0} 个商品</strong>
            <strong>{packagingCount} 种包装规格</strong>
          </div>
          <p>商品图片将保存到正式图片目录，商品编号将在导入时自动生成。</p>
          <p className="product-import-confirm-warning">该操作目前没有一键撤销功能。</p>
        </Modal>
      </div>
    </>
  );
}
