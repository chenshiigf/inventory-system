"use client";

import {
  AppstoreOutlined,
  DashboardOutlined,
  FileExcelOutlined,
  HistoryOutlined,
  InboxOutlined,
  ShopOutlined,
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
  const isDashboardPage = pathname === "/";
  const isCategoriesPage = pathname === "/categories";
  const isWarehousesPage = pathname === "/warehouses";
  const isProductImportPage = pathname === "/products/import";
  const isInventoryMovementsPage = pathname === "/inventory-movements";
  const currentPageTitle = isDashboardPage
    ? "概览"
    : isCategoriesPage
    ? "分类管理"
    : isWarehousesPage
      ? "仓库管理"
      : isProductImportPage
        ? "批量导入"
        : isInventoryMovementsPage
          ? "库存流水"
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
            isDashboardPage
              ? "dashboard"
              : isCategoriesPage
              ? "categories"
              : isWarehousesPage
                ? "warehouses"
                : isProductImportPage
                  ? "product-import"
                  : isInventoryMovementsPage
                    ? "inventory-movements"
                    : "inventory",
          ]}
          items={[
            {
              key: "dashboard",
              icon: <DashboardOutlined />,
              label: <Link href="/">概览</Link>,
            },
            {
              key: "inventory",
              icon: <AppstoreOutlined />,
              label: <Link href="/products">商品库存</Link>,
            },
            {
              key: "inventory-movements",
              icon: <HistoryOutlined />,
              label: <Link href="/inventory-movements">库存流水</Link>,
            },
            {
              key: "categories",
              icon: <TagsOutlined />,
              label: <Link href="/categories">分类管理</Link>,
            },
            {
              key: "warehouses",
              icon: <ShopOutlined />,
              label: <Link href="/warehouses">仓库管理</Link>,
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
            <span>入库 / 出库已保存到数据库</span>
          </div>
        </Header>
        <Content className="inventory-content">{children}</Content>
      </Layout>
    </Layout>
  );
}
