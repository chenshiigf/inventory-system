"use client";

import {
  DownloadOutlined,
  FileExcelOutlined,
  InfoCircleOutlined,
  InboxOutlined,
  PictureOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import {
  Alert,
  App,
  Button,
  Drawer,
  Image,
  Modal,
  Space,
  Spin,
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
import OperationSuccessModal from "@/components/common/OperationSuccessModal";
import PageHeading from "@/components/common/PageHeading";
import useImportPreviewTask from "./useImportPreviewTask";
import {
  commitProductImport,
  getImportPreviewImageUrl,
  getProductImportTemplateUrl,
  validateProductImportFile,
} from "@/lib/api/product-import";
import type {
  ProductImportCommitResponse,
  ProductImportPreviewPackaging,
  ProductImportPreviewProduct,
  ProductImportPreviewResponse,
  ProductImportRowStatus,
  ProductImportPrecheckIssue,
} from "@/lib/api/product-import";
import { ApiResponseError } from "@/lib/api/client";

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
  const { modal } = App.useApp();
  const [committing, setCommitting] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [commitResult, setCommitResult] =
    useState<ProductImportCommitResponse | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [commitError, setCommitError] = useState<string | null>(null);
  const [messageApi, messageContextHolder] = message.useMessage();

  const previewTask = useImportPreviewTask({
    onSuccess: (result) => {
      setPreview(result);
      messageApi.success("Excel 已上传，商品级预览完成");
    },
    onCancelled: () => {
      setPreview(null);
      setFileInfo(null);
      messageApi.info("已取消本次导入");
    },
    onMissing: () => setFileInfo(null),
  });
  const { uploading, taskId, elapsed, cancelling } = previewTask;
  const loadError = validationError ?? previewTask.error?.message;
  const precheckIssues: ProductImportPrecheckIssue[] =
    previewTask.error instanceof ApiResponseError && typeof previewTask.error.detail === "object" &&
    previewTask.error.detail !== null && "errors" in previewTask.error.detail && !validationError
      ? (previewTask.error.detail as { errors: ProductImportPrecheckIssue[] }).errors : [];

  function confirmCancel() {
    if (!taskId || cancelling) return;
    modal.confirm({
      title: "取消本次导入？",
      content: "将停止当前预览任务并清理本次临时文件。已经正式导入的历史数据不会受到影响。",
      okText: "取消导入",
      cancelText: "继续等待",
      okButtonProps: { danger: true },
      onOk: () => previewTask.cancel(taskId),
    });
  }

  async function handleFile(file: File) {
    if (previewTask.isBusy() || committing) return;
    const validationError = validateProductImportFile(file);
    if (validationError) {
      previewTask.clearError();
      setValidationError(validationError);
      return;
    }
    setValidationError(null);
    setPreview(null);
    setCommitError(null);
    setCommitResult(null);
    setFileInfo({
      name: file.name,
      size: file.size,
      lastModified: file.lastModified,
    });
    await previewTask.start(file);
  }

  function resetPreview() {
    if (previewTask.isBusy() || committing) return;
    setPreview(null);
    setFileInfo(null);
    setValidationError(null);
    previewTask.clearError();
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
        <PageHeading
          title="批量导入"
          className="product-import-heading"
          actions={
            <>
              <Button
                icon={<InfoCircleOutlined />}
                onClick={() => setHelpOpen(true)}
              >
                导入说明
              </Button>
              <Button
                className="product-import-template-button"
                type="primary"
                icon={<DownloadOutlined />}
                href={getProductImportTemplateUrl()}
              >
                下载模板
              </Button>
            </>
          }
        />

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
            description={
              <>
                {loadError}
                {precheckIssues.length > 0 && <ul className="product-import-precheck-errors">
                  {precheckIssues.map((issue) => <li key={issue.excel_row}>
                    第 {issue.excel_row} 行：{issue.messages.join("；")}
                    {(issue.excel_rows?.length ?? 0) > 1 && <span>（同一商品组：Excel 第 {issue.excel_rows!.join("、")} 行）</span>}
                  </li>)}
                </ul>}
              </>
            }
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

        {!commitResult && (uploading ? (
          <section className="product-import-processing" aria-live="polite" aria-label="导入预览处理中">
            <Spin />
            <Typography.Title level={3}>{taskId ? "✓ Excel 检查通过" : "正在检查 Excel…"}</Typography.Title>
            <p>{!taskId ? "正在上传并检查 Excel 数据，检查通过后才处理商品图片。" : cancelling ? "正在停止预览任务并清理临时文件，请稍候…" : elapsed >= 60 ? "处理耗时较长，你可以继续等待或取消本次导入。" : elapsed >= 30 ? "文件较大，正在处理商品图片，请稍候…" : "正在处理商品图片并生成预览…"}</p>
            {taskId && <Space>
              {elapsed >= 60 && !cancelling && <Button onClick={() => messageApi.info("正在继续处理，完成后会自动进入数据预览。")}>继续等待</Button>}
              <Button onClick={confirmCancel} loading={cancelling}>取消本次导入</Button>
            </Space>}
          </section>
        ) : !preview ? (
          <section
            className="product-import-upload-panel"
            aria-label="上传商品导入 Excel"
          >
            <Upload.Dragger {...uploadProps} disabled={uploading}>
              <p className="ant-upload-drag-icon">
                <InboxOutlined />
              </p>
              <p className="ant-upload-text">
                {uploading ? "正在读取 Excel…" : "拖拽或点击上传 .xlsx"}
              </p>
              <p className="ant-upload-hint">
                .xlsx · ≤100MB · 最多300行
              </p>
            </Upload.Dragger>
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
        ))}

        <OperationSuccessModal
          open={commitResult !== null}
          title="商品导入成功"
          description={commitResult && (
            <>
              <span>成功导入 {commitResult.product_count} 个商品。</span>
              <div className="product-import-result-meta">
                {commitResult.packaging_count} 种包装规格 · {commitResult.source_row_count} 条 Excel 数据行
              </div>
              <div className="product-import-result-meta">
                批次 {commitResult.batch_id} · {commitResult.file_name}
              </div>
              <div className="product-import-created-list" aria-label="导入商品编号">
                {commitResult.created_products.map((product) => (
                  <div key={product.product_id}>
                    <strong>{product.product_code}</strong>
                    <span>
                      Excel第 {product.excel_rows.join("、")} 行 · {product.packaging_count} 种包装
                    </span>
                  </div>
                ))}
              </div>
            </>
          )}
          primaryAction={{
            label: "返回商品库存",
            onClick: () => window.location.assign("/products"),
          }}
          secondaryAction={{ label: "导入其他 Excel", onClick: resetPreview }}
          onClose={resetPreview}
        />

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
      <Drawer
        className="product-import-help-drawer"
        title="导入说明"
        placement="right"
        size="default"
        open={helpOpen}
        onClose={() => setHelpOpen(false)}
      >
        <dl className="product-import-help-list">
          <div>
            <dt>工作表</dt>
            <dd>商品导入</dd>
          </div>
          <div>
            <dt>字段识别</dt>
            <dd>按表头名称识别，列顺序可调整，也可以保留历史列；图片按“产品图片”表头识别。</dd>
          </div>
          <div>
            <dt>数据范围</dt>
            <dd>每个 Excel 最多 300 条非空数据行；正式导入每批最多 100 个商品。</dd>
          </div>
          <div>
            <dt>公式</dt>
            <dd>读取 Excel 已保存的计算结果；公式单元格需要先保存最新计算结果。</dd>
          </div>
          <div>
            <dt>商品组</dt>
            <dd>普通商品留空；同一商品有多个装箱规格时，为相关行填写相同商品组。</dd>
          </div>
          <div>
            <dt>商品图片</dt>
            <dd>图片可以被多个商品共用，共用图片不会合并商品；同一商品组只能保留一张主图。</dd>
          </div>
          <div>
            <dt>当前箱数</dt>
            <dd>也接受历史表头“结余箱数”。</dd>
          </div>
        </dl>
        <p className="product-import-help-note">
          预览和校验完成前不会写入库存。确认导入后会创建商品并生成商品编号。
        </p>
      </Drawer>
    </>
  );
}
