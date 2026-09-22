"use client";

import {
  AppstoreOutlined,
  FileExcelOutlined,
  InboxOutlined,
  TagsOutlined,
} from "@ant-design/icons";
import { Layout, Menu, Tag } from "antd";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

const { Sider, Header, Content } = Layout;

interface AppLayoutProps {
  children: ReactNode;
}

export default function AppLayout({ children }: AppLayoutProps) {
  const pathname = usePathname();
  const isCategoriesPage = pathname === "/categories";
  const isProductImportPage = pathname === "/products/import";
  const currentPageTitle = isCategoriesPage
    ? "分类管理"
    : isProductImportPage
      ? "批量导入"
      : "商品库存";

  return (
    <Layout className="inventory-shell">
      <Sider
        className="inventory-sidebar"
        width={216}
        collapsedWidth={72}
        breakpoint="lg"
        trigger={null}
      >
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">
            <InboxOutlined />
          </div>
          <div className="brand-copy">
            <strong>商品库存</strong>
            <span>家庭库存工作台</span>
          </div>
        </div>

        <Menu
          mode="inline"
          selectedKeys={[
            isCategoriesPage
              ? "categories"
              : isProductImportPage
                ? "product-import"
                : "inventory",
          ]}
          items={[
            {
              key: "inventory",
              icon: <AppstoreOutlined />,
              label: <Link href="/">商品库存</Link>,
            },
            {
              key: "categories",
              icon: <TagsOutlined />,
              label: <Link href="/categories">分类管理</Link>,
            },
            {
              key: "product-import",
              icon: <FileExcelOutlined />,
              label: <Link href="/products/import">批量导入</Link>,
            },
          ]}
        />

        <div className="sider-footer">商品与分类分开管理</div>
      </Sider>

      <Layout className="inventory-main">
        <Header className="inventory-topbar">
          <div className="topbar-location">
            <span>系统功能</span>
            <span className="topbar-location-divider" aria-hidden="true">
              /
            </span>
            <strong>{currentPageTitle}</strong>
          </div>
          <div className="topbar-demo-note">
            <Tag color="green">SQLite 商品数据</Tag>
            <span>入库 / 出库仍为页面原型</span>
          </div>
        </Header>
        <Content className="inventory-content">{children}</Content>
      </Layout>
    </Layout>
  );
}
