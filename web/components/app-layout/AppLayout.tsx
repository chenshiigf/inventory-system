"use client";

import {
  AppstoreOutlined,
  DashboardOutlined,
  FileExcelOutlined,
  HistoryOutlined,
  InboxOutlined,
  ShopOutlined,
  TagsOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import { Layout, Menu } from "antd";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { selectedNavigationKey } from "@/lib/navigation";

const { Sider, Content } = Layout;

interface AppLayoutProps {
  children: ReactNode;
}

export default function AppLayout({ children }: AppLayoutProps) {
  const pathname = usePathname();
  const selectedKey = selectedNavigationKey(pathname, process.env.NODE_ENV === "development");
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
          selectedKeys={selectedKey ? [selectedKey] : []}
          items={[
            {
              key: "visual-search",
              icon: <SearchOutlined />,
              label: <Link href="/visual-search">商品视觉搜索</Link>,
            },
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
              key: "ai-search",
              icon: <SearchOutlined />,
              label: <Link href="/ai-search">AI 找货</Link>,
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

      </Sider>

      <Layout className="inventory-main">
        <Content className="inventory-content">{children}</Content>
      </Layout>
    </Layout>
  );
}
