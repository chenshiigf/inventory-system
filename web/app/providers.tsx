"use client";

import { App as AntApp, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import type { ReactNode } from "react";
import { AISearchSessionProvider } from "@/components/ai-search/AISearchSessionProvider";
import { ProductBatchSelectionProvider } from "@/components/inventory/ProductBatchSelectionProvider";

interface AppProvidersProps {
  children: ReactNode;
}

export default function AppProviders({ children }: AppProvidersProps) {
  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: "#002FA7",
          borderRadius: 6,
          fontFamily:
            '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
        },
      }}
    >
      <AntApp>
        <AISearchSessionProvider>
          <ProductBatchSelectionProvider>{children}</ProductBatchSelectionProvider>
        </AISearchSessionProvider>
      </AntApp>
    </ConfigProvider>
  );
}
