"use client";

import { DeleteOutlined, EditOutlined, PlusOutlined } from "@ant-design/icons";
import {
  Alert,
  App,
  Button,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Space,
  Table,
  Tooltip,
  Typography,
} from "antd";
import type { TableProps } from "antd";
import { useEffect, useState } from "react";
import {
  createWarehouse,
  deleteWarehouse,
  listWarehouseSummaries,
  updateWarehouse,
} from "@/lib/api/warehouses";
import type { WarehouseSummaryRead } from "@/types/inventory";

interface WarehouseFormValues {
  name: string;
}

type WarehouseEditorState =
  | { mode: "create" }
  | { mode: "edit"; warehouse: WarehouseSummaryRead };

interface WarehouseEditorModalProps {
  editor: WarehouseEditorState;
  onCancel: () => void;
  onSave: (
    editor: WarehouseEditorState,
    values: WarehouseFormValues,
  ) => Promise<boolean>;
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "仓库服务请求失败，请重试。";
}

function WarehouseEditorModal({
  editor,
  onCancel,
  onSave,
}: WarehouseEditorModalProps) {
  const [form] = Form.useForm<WarehouseFormValues>();
  const [saving, setSaving] = useState(false);
  const initialName = editor.mode === "edit" ? editor.warehouse.name : "";

  function closeModal() {
    form.resetFields();
    onCancel();
  }

  async function handleFinish(values: WarehouseFormValues) {
    const name = values.name.trim();
    if (!name) {
      form.setFields([{ name: "name", errors: ["请输入仓库名称"] }]);
      return;
    }

    setSaving(true);
    const saved = await onSave(editor, { name });
    setSaving(false);
    if (saved) {
      form.resetFields();
      onCancel();
    }
  }

  return (
    <Modal
      title={editor.mode === "edit" ? "编辑仓库" : "新增仓库"}
      open
      width={480}
      okText="保存"
      cancelText="取消"
      okButtonProps={{ loading: saving }}
      onCancel={closeModal}
      onOk={() => form.submit()}
      destroyOnHidden
    >
      <Form<WarehouseFormValues>
        form={form}
        layout="vertical"
        initialValues={{ name: initialName }}
        onFinish={handleFinish}
        preserve={false}
        requiredMark={false}
      >
        <Form.Item
          name="name"
          label="仓库名称"
          rules={[
            { required: true, whitespace: true, message: "请输入仓库名称" },
            { max: 100, message: "仓库名称不能超过 100 个字符" },
          ]}
        >
          <Input maxLength={100} placeholder="请输入仓库名称" />
        </Form.Item>
      </Form>
    </Modal>
  );
}

type WarehouseColumns = NonNullable<
  TableProps<WarehouseSummaryRead>["columns"]
>;

const columns: WarehouseColumns = [
  {
    title: "仓库名称",
    dataIndex: "name",
    key: "name",
    render: (name: string) => <Typography.Text strong>{name}</Typography.Text>,
  },
  {
    title: "商品数量",
    dataIndex: "product_count",
    key: "product_count",
    width: 180,
    render: (count: number) => `${count} 个商品`,
  },
  {
    title: "当前库存",
    dataIndex: "carton_count",
    key: "carton_count",
    width: 180,
    render: (count: number) => `${count} 箱`,
  },
];

export default function WarehouseManagement() {
  const [warehouses, setWarehouses] = useState<WarehouseSummaryRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadCounter, setReloadCounter] = useState(0);
  const [editor, setEditor] = useState<WarehouseEditorState | null>(null);
  const [deletingWarehouseId, setDeletingWarehouseId] = useState<number | null>(
    null,
  );
  const { message } = App.useApp();

  useEffect(() => {
    const controller = new AbortController();

    void listWarehouseSummaries(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) {
          setWarehouses(result);
          setLoadError(null);
        }
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setWarehouses([]);
          setLoadError(getErrorMessage(error));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      });

    return () => controller.abort();
  }, [reloadCounter]);

  function retryLoad() {
    setLoading(true);
    setReloadCounter((value) => value + 1);
  }

  async function saveWarehouse(
    target: WarehouseEditorState,
    values: WarehouseFormValues,
  ): Promise<boolean> {
    try {
      if (target.mode === "edit") {
        await updateWarehouse(target.warehouse.id, values.name);
        message.success("仓库名称已更新");
      } else {
        await createWarehouse(values.name);
        message.success("仓库已添加");
      }
      setLoading(true);
      setReloadCounter((value) => value + 1);
      return true;
    } catch (error) {
      message.error(getErrorMessage(error));
      return false;
    }
  }

  async function removeWarehouse(warehouse: WarehouseSummaryRead): Promise<void> {
    setDeletingWarehouseId(warehouse.id);
    try {
      await deleteWarehouse(warehouse.id);
      message.success("仓库已删除");
      setLoading(true);
      setReloadCounter((value) => value + 1);
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setDeletingWarehouseId(null);
    }
  }

  const tableColumns: WarehouseColumns = [
    ...columns,
    {
      title: "操作",
      key: "actions",
      width: 180,
      render: (_value, warehouse) => (
        <Space size={4}>
          <Button
            type="text"
            size="small"
            icon={<EditOutlined />}
            aria-label={`编辑仓库 ${warehouse.name}`}
            onClick={() => setEditor({ mode: "edit", warehouse })}
          >
            编辑
          </Button>
          {warehouse.product_count > 0 ? (
            <Tooltip title="该仓库仍有关联商品，无法删除">
              <span>
                <Button
                  type="text"
                  danger
                  size="small"
                  icon={<DeleteOutlined />}
                  disabled
                  aria-label={`删除仓库 ${warehouse.name}`}
                >
                  删除
                </Button>
              </span>
            </Tooltip>
          ) : (
            <Popconfirm
              title="删除仓库？"
              description={`确定删除“${warehouse.name}”吗？删除后无法恢复。`}
              okText="删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
              onConfirm={() => removeWarehouse(warehouse)}
            >
              <Button
                type="text"
                danger
                size="small"
                icon={<DeleteOutlined />}
                loading={deletingWarehouseId === warehouse.id}
                aria-label={`删除仓库 ${warehouse.name}`}
              >
                删除
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div className="warehouse-page">
      <div className="page-heading">
        <div>
          <Typography.Title level={1}>仓库管理</Typography.Title>
          <p className="warehouse-subtitle">维护商品库存使用的仓库</p>
        </div>
        <Button
          className="add-product-button"
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => setEditor({ mode: "create" })}
        >
          新增仓库
        </Button>
      </div>

      {loadError && (
        <Alert
          className="warehouse-load-error"
          type="error"
          showIcon
          title="仓库列表暂时无法加载"
          description={loadError}
          action={
            <Button size="small" onClick={retryLoad}>
              重试
            </Button>
          }
        />
      )}

      <div className="warehouse-table-card">
        <Table<WarehouseSummaryRead>
          rowKey="id"
          columns={tableColumns}
          dataSource={warehouses}
          loading={loading}
          pagination={false}
          locale={{ emptyText: <Empty description="暂无仓库" /> }}
        />
      </div>

      {editor && (
        <WarehouseEditorModal
          key={editor.mode === "edit" ? `edit-${editor.warehouse.id}` : "create"}
          editor={editor}
          onCancel={() => setEditor(null)}
          onSave={saveWarehouse}
        />
      )}
    </div>
  );
}
