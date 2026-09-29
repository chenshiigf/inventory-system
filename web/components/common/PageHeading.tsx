"use client";

import { Typography } from "antd";
import type { ReactNode } from "react";

interface PageHeadingProps {
  title: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  className?: string;
}

export default function PageHeading({
  title,
  meta,
  actions,
  className,
}: PageHeadingProps) {
  return (
    <div className={`page-heading${className ? ` ${className}` : ""}`}>
      <div className="page-heading-title">
        <Typography.Title level={1}>{title}</Typography.Title>
        {meta !== undefined && (
          <span className="result-count page-heading-count">{meta}</span>
        )}
      </div>
      {actions !== undefined && (
        <div className="page-heading-actions">{actions}</div>
      )}
    </div>
  );
}
