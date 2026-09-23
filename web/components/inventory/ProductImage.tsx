"use client";

import { PictureOutlined } from "@ant-design/icons";
import { Image, Popover } from "antd";
import { useEffect, useRef, useState } from "react";
import { getProductImageUrl } from "@/lib/api/product-images";

interface ProductImageProps {
  imagePath: string | null;
  thumbnailPath?: string | null;
  alt: string;
  width: number;
  height: number;
  loading?: "eager" | "lazy";
  hoverPreview?: boolean;
}

export default function ProductImage({
  imagePath,
  thumbnailPath,
  alt,
  width,
  height,
  loading = "lazy",
  hoverPreview = false,
}: ProductImageProps) {
  const [thumbnailFailed, setThumbnailFailed] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const previewCloseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mainUrl = getProductImageUrl(imagePath);
  const thumbnailSource = thumbnailPath?.trim() ? thumbnailPath : imagePath;
  const thumbnailUrl = getProductImageUrl(thumbnailSource);

  useEffect(
    () => () => {
      if (previewCloseTimer.current) {
        clearTimeout(previewCloseTimer.current);
      }
    },
    [],
  );

  if (!thumbnailUrl || thumbnailFailed) {
    return (
      <span className="product-image-empty" aria-label="暂无图片">
        <PictureOutlined aria-hidden="true" />
        <span>暂无图片</span>
      </span>
    );
  }

  const image = (
    <Image
      className="product-image-thumbnail"
      src={thumbnailUrl}
      alt={alt}
      width={width}
      height={height}
      loading={loading}
      preview={
        mainUrl
          ? {
              src: mainUrl,
              cover: <span className="image-preview-mask">点击查看大图</span>,
            }
          : false
      }
      onError={() => setThumbnailFailed(true)}
    />
  );

  if (!hoverPreview || !mainUrl) {
    return image;
  }

  function handlePreviewEnter() {
    if (previewCloseTimer.current) {
      clearTimeout(previewCloseTimer.current);
      previewCloseTimer.current = null;
    }
    setPreviewOpen(true);
  }

  function handlePreviewLeave() {
    if (previewCloseTimer.current) {
      clearTimeout(previewCloseTimer.current);
    }
    previewCloseTimer.current = setTimeout(() => {
      previewCloseTimer.current = null;
      setPreviewOpen(false);
    }, 120);
  }

  return (
    <Popover
      trigger="hover"
      placement="right"
      arrow={false}
      mouseEnterDelay={0.12}
      mouseLeaveDelay={0.12}
      open={previewOpen}
      onOpenChange={(open) => {
        if (open) {
          handlePreviewEnter();
        } else {
          handlePreviewLeave();
        }
      }}
      destroyOnHidden
      classNames={{ root: "product-image-quick-popover" }}
      content={
        previewOpen ? (
          <div
            className="product-quick-preview"
            onMouseEnter={handlePreviewEnter}
            onMouseLeave={handlePreviewLeave}
          >
            <Image
              src={mainUrl}
              alt={`${alt}大图预览`}
              width={300}
              height={220}
              loading="lazy"
              preview={false}
            />
          </div>
        ) : null
      }
    >
      <span
        className="product-image-popover-trigger"
        onMouseEnter={handlePreviewEnter}
        onMouseLeave={handlePreviewLeave}
      >
        {image}
      </span>
    </Popover>
  );
}
