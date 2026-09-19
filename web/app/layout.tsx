import { AntdRegistry } from "@ant-design/nextjs-registry";
import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";
import AppProviders from "./providers";

export const metadata: Metadata = {
  title: "商品库存管理",
  description: "商品基础信息由 FastAPI 提供并持久化到 SQLite。",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>
        <AntdRegistry>
          <AppProviders>{children}</AppProviders>
        </AntdRegistry>
      </body>
    </html>
  );
}
