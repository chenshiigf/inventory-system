"use client";

import { EditOutlined, PlusOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Empty,
  Form,
  Input,
  message,
  Modal,
  Spin,
  Space,
  Typography,
} from "antd";
import { useEffect, useState } from "react";
import {
  createCategory,
  listCategories,
  updateCategory,
} from "@/lib/api/categories";
import type { CategoryTreeNode } from "@/types/inventory";

interface CategoryFormValues {
  name: string;
}

type CategoryEditorState =
  | { mode: "create"; parentId: number | null; parentName?: string }
  | { mode: "rename"; categoryId: number; categoryName: string };

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "分类服务请求失败，请重试。";
}

export default function CategoryManagement() {
  const [categories, setCategories] = useState<CategoryTreeNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadCounter, setReloadCounter] = useState(0);
  const [editor, setEditor] = useState<CategoryEditorState | null>(null);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<CategoryFormValues>();
  const [messageApi, messageContextHolder] = message.useMessage();

  useEffect(() => {
    const controller = new AbortController();

    void listCategories(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) {
          setCategories(result);
          setLoadError(null);
        }
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setCategories([]);
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

  const childCount = categories.reduce(
    (count, category) => count + category.children.length,
    0,
  );
  const editorTitle =
    editor?.mode === "rename"
      ? "修改分类名称"
      : editor?.parentId === null
        ? "新增一级分类"
        : "新增二级分类";
  const initialName = editor?.mode === "rename" ? editor.categoryName : "";

  function openCreateParent() {
    setEditor({ mode: "create", parentId: null });
  }

  function openCreateChild(parent: CategoryTreeNode) {
    setEditor({
      mode: "create",
      parentId: parent.id,
      parentName: parent.name,
    });
  }

  function openRename(category: CategoryTreeNode) {
    setEditor({
      mode: "rename",
      categoryId: category.id,
      categoryName: category.name,
    });
  }

  function retryLoad() {
    setLoading(true);
    setReloadCounter((value) => value + 1);
  }

  async function saveCategory(values: CategoryFormValues) {
    if (!editor) {
      return;
    }

    const name = values.name.trim();
    if (!name) {
      return;
    }

    setSaving(true);
    try {
      if (editor.mode === "rename") {
        await updateCategory(editor.categoryId, { name });
      } else {
        await createCategory({ name, parent_id: editor.parentId });
      }
      setEditor(null);
      setLoading(true);
      setReloadCounter((value) => value + 1);
      messageApi.success(
        editor.mode === "rename" ? "分类名称已更新" : "分类已添加",
      );
    } catch (error) {
      messageApi.error(getErrorMessage(error));
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      {messageContextHolder}
      <div className="categories-page">
        <div className="page-heading">
          <div>
            <Typography.Title level={1}>分类管理</Typography.Title>
            <p className="categories-summary">
              一级分类 {categories.length} 个 · 二级分类 {childCount} 个
            </p>
          </div>
          <Button
            className="add-product-button"
            type="primary"
            icon={<PlusOutlined />}
            onClick={openCreateParent}
          >
            新增一级分类
          </Button>
        </div>

        {loadError && (
          <Alert
            className="category-load-error"
            type="error"
            showIcon
            title="分类列表暂时无法加载"
            description={loadError}
            action={
              <Button
                size="small"
                onClick={retryLoad}
              >
                重试
              </Button>
            }
          />
        )}

        {loading ? (
          <div className="category-page-state" aria-label="正在加载分类">
            <Spin description="正在读取分类…" />
          </div>
        ) : loadError ? null : categories.length === 0 ? (
          <div className="category-page-state">
            <Empty description="还没有分类，从一级分类开始添加。" />
          </div>
        ) : (
          <div className="category-groups">
            {categories.map((category) => (
              <section
                className="category-group"
                key={category.id}
                aria-label={`一级分类 ${category.name}`}
              >
                <div className="category-group-heading">
                  <div className="category-group-title">
                    <span className="category-level-label">一级分类</span>
                    <Typography.Text strong>{category.name}</Typography.Text>
                    <span className="category-child-count">
                      {category.children.length} 个小类
                    </span>
                  </div>
                  <Space size={4}>
                    <Button
                      type="text"
                      size="small"
                      icon={<EditOutlined />}
                      aria-label={`编辑一级分类 ${category.name}`}
                      onClick={() => openRename(category)}
                    >
                      编辑
                    </Button>
                    <Button
                      type="link"
                      size="small"
                      icon={<PlusOutlined />}
                      aria-label={`在${category.name}下新增二级分类`}
                      onClick={() => openCreateChild(category)}
                    >
                      新增小类
                    </Button>
                  </Space>
                </div>

                {category.children.length > 0 ? (
                  <div className="category-child-list">
                    {category.children.map((child) => (
                      <div className="category-child-row" key={child.id}>
                        <span className="category-child-branch" aria-hidden="true">
                          ↳
                        </span>
                        <span className="category-child-name">{child.name}</span>
                        <Button
                          type="text"
                          size="small"
                          icon={<EditOutlined />}
                          aria-label={`编辑二级分类 ${category.name} / ${child.name}`}
                          onClick={() => openRename(child)}
                        >
                          编辑
                        </Button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="category-no-children">暂未添加小类</div>
                )}
              </section>
            ))}
          </div>
        )}
      </div>

      <Modal
        title={editorTitle}
        open={Boolean(editor)}
        okText="保存"
        cancelText="取消"
        okButtonProps={{ loading: saving }}
        onCancel={() => setEditor(null)}
        onOk={() => form.submit()}
        destroyOnHidden
      >
        {editor?.mode === "create" && editor.parentName && (
          <p className="category-parent-context">
            所属一级分类：<strong>{editor.parentName}</strong>
          </p>
        )}
        <Form<CategoryFormValues>
          key={
            editor?.mode === "rename"
              ? `rename-${editor.categoryId}`
              : `create-${editor?.parentId ?? "root"}`
          }
          form={form}
          layout="vertical"
          initialValues={{ name: initialName }}
          onFinish={saveCategory}
          requiredMark={false}
        >
          <Form.Item
            name="name"
            label={
              editor?.mode === "rename"
                ? "分类名称"
                : editor?.parentId === null
                  ? "一级分类名称"
                  : "二级分类名称"
            }
            rules={[
              { required: true, whitespace: true, message: "请输入分类名称" },
              { max: 100, message: "分类名称不能超过 100 个字符" },
            ]}
          >
            <Input maxLength={100} placeholder="请输入分类名称" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
