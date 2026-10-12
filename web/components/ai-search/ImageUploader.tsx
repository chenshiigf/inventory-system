"use client";

import { InboxOutlined, UploadOutlined } from "@ant-design/icons";
import { Button, Card, Upload } from "antd";

interface ImageUploaderProps {
  onSelect: (file: File) => Promise<void>;
}

export default function ImageUploader({ onSelect }: ImageUploaderProps) {
  return (
    <Card title="上传商品照片" className="ai-search-upload-card">
      <Upload.Dragger
        accept="image/jpeg,image/png,image/webp"
        multiple={false}
        showUploadList={false}
        capture={false}
        hasControlInside
        pastable={false}
        beforeUpload={async (file) => {
          // AntD receives a browser File; never issue its automatic upload request.
          await onSelect(file as unknown as File);
          return Upload.LIST_IGNORE;
        }}
      >
        <InboxOutlined className="ai-search-upload-icon" aria-hidden />
        <p className="ai-search-upload-title">点击选择图片，或将照片拖到这里</p>
        <p className="ai-search-upload-hint">建议上传单个商品照片，保持商品主体完整。</p>
        <Button type="primary" icon={<UploadOutlined aria-hidden />} size="large">选择图片</Button>
        <p className="ai-search-upload-formats">JPG、PNG、WEBP · 每张不超过 10MB · 本阶段仅预览</p>
      </Upload.Dragger>
    </Card>
  );
}
