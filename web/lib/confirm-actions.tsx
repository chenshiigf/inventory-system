import type { ModalFuncProps } from "antd/es/modal/interface";

type ConfirmConfig = Pick<
  ModalFuncProps,
  "title" | "content" | "okText" | "cancelText" | "okButtonProps"
>;

export function getProductStatusConfirmConfig(
  isActive: boolean,
  totalCartonCount: number,
): ConfirmConfig {
  if (isActive) {
    return {
      title: "重新启用商品？",
      content:
        "商品将回到默认在用商品列表，商品编号、图片、包装规格和库存保持不变。",
      okText: "确认启用",
      cancelText: "取消",
    };
  }

  return {
    title: "停用商品？",
    content: (
      <div>
        <p>停用后，该商品将从默认库存列表中隐藏，历史数据仍会保留。</p>
        {totalCartonCount > 0 && (
          <>
            <p>该商品当前还有 {totalCartonCount} 箱库存。</p>
            <p>停用不会清空库存。</p>
          </>
        )}
      </div>
    ),
    okText: "确认停用",
    cancelText: "取消",
    okButtonProps: { danger: true },
  };
}

export function getDeleteConfirmConfig(
  entityType: string,
  entityName: string,
): ConfirmConfig {
  return {
    title: `删除${entityType}？`,
    content: `确定删除${entityType}“${entityName}”吗？删除后无法恢复。`,
    okText: "删除",
    cancelText: "取消",
    okButtonProps: { danger: true },
  };
}
