"use client";

import { InboxOutlined, UploadOutlined } from "@ant-design/icons";
import { Button, Card, Upload } from "antd";
import { forwardRef, useImperativeHandle, useRef } from "react";

interface ImageUploaderProps {
  onSelect: (file: File) => Promise<void>;
  disabled?: boolean;
  hasPhoto?: boolean;
  autoProcess?: boolean;
}

export interface ImageUploaderHandle { open: () => void; }

const ImageUploader = forwardRef<ImageUploaderHandle, ImageUploaderProps>(function ImageUploader({ onSelect, disabled = false, hasPhoto = false, autoProcess = false }, ref) {
  const input = useRef<HTMLInputElement | null>(null);
  useImperativeHandle(ref, () => ({ open: () => { if (!disabled) input.current?.click(); } }), [disabled]);
  return (
    <Card title="上传商品照片" className={`ai-search-upload-card${hasPhoto ? " ai-search-upload-card--selected" : ""}`}>
      <input ref={input} type="file" accept="image/jpeg,image/png,image/webp" hidden disabled={disabled}
        onChange={(event) => {
          const file = event.currentTarget.files?.[0];
          event.currentTarget.value = "";
          if (!disabled && file) void onSelect(file);
        }} />
      <Upload.Dragger
        disabled={disabled}
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
        <Button type="primary" icon={<UploadOutlined aria-hidden />} size="large" disabled={disabled}
          onClick={(event) => { event.stopPropagation(); input.current?.click(); }}>选择图片</Button>
        <p className="ai-search-upload-formats">{autoProcess ? "JPG、PNG、WEBP · 大照片自动优化后上传 · 选图后点击开始找商品" : "JPG、PNG、WEBP · 每张不超过 10MB · 选图后点击开始找商品"}</p>
      </Upload.Dragger>
    </Card>
  );
});

export default ImageUploader;
