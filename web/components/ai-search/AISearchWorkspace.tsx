"use client";

import { PictureOutlined } from "@ant-design/icons";
import { Alert, App, Card, Image } from "antd";
import { useEffect, useRef, useState } from "react";
import PageHeading from "@/components/common/PageHeading";
import { mockAISearchResult, type AISearchProduct } from "@/lib/mock/ai-search";
import ImageUploader from "./ImageUploader";
import ProductReadonlyModal from "./ProductReadonlyModal";
import SearchResultCard from "./SearchResultCard";
import SimilarProducts from "./SimilarProducts";
import "./ai-search.css";

export default function AISearchWorkspace() {
  const { message } = App.useApp();
  const [queryImage, setQueryImage] = useState(mockAISearchResult.queryImage);
  const [confirmed, setConfirmed] = useState(false);
  const [detail, setDetail] = useState<AISearchProduct | null>(null);
  const previewUrl = useRef<string | null>(null);
  const selection = useRef(0);

  useEffect(() => () => {
    selection.current += 1;
    if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
  }, []);

  async function selectImage(file: File) {
    const currentSelection = ++selection.current;
    if (!file.size || file.size > 10 * 1024 * 1024 || !["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
      message.error("请选择 10MB 以内的 JPG、PNG 或 WEBP 图片。");
      return;
    }
    try {
      const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
      const tooLarge = bitmap.width * bitmap.height > 40_000_000;
      bitmap.close();
      if (currentSelection !== selection.current) return;
      if (tooLarge) { message.error("图片分辨率过大，请选择较小的照片。"); return; }
      const url = URL.createObjectURL(file);
      if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
      previewUrl.current = url;
      setQueryImage({ image: url, name: file.name });
      setConfirmed(false);
      setDetail(null);
    } catch {
      if (currentSelection === selection.current) message.error("无法读取图片，请重新选择有效的商品照片。");
    }
  }

  return (
    <div className="ai-search-workspace">
      <div>
        <PageHeading title="AI 找货助手" />
        <p className="ai-search-subtitle">上传商品照片，AI 帮助快速找到对应商品。</p>
      </div>
      <Alert type="info" showIcon title="Mock 演示：选择图片仅展示预览，不执行搜索。"
        description="下方商品图片与规格为界面示例，不代表真实查询结果。" />
      <ImageUploader onSelect={selectImage} />
      <section className="ai-search-results" aria-label="识别结果预览">
        <Card title="用户上传照片" className="ai-search-query-card">
          {queryImage.image ? <>
            <Image src={queryImage.image} alt="用户上传商品照片预览" className="ai-search-query-image" />
            <p className="ai-search-query-filename">{queryImage.name}</p>
            <p className="ai-search-query-note">照片已选择，仅用于本地预览。</p>
          </> : <div className="ai-search-query-empty">
            <PictureOutlined aria-hidden />
            <p>尚未选择商品照片</p>
            <span>选择图片后，在这里查看预览</span>
          </div>}
        </Card>
        <div className="ai-search-recommendations">
          <SearchResultCard product={mockAISearchResult.primaryProduct} confirmed={confirmed}
            onConfirm={() => setConfirmed(true)} onDetails={() => setDetail(mockAISearchResult.primaryProduct)} />
          <SimilarProducts products={mockAISearchResult.similarProducts} onDetails={setDetail} />
        </div>
      </section>
      <ProductReadonlyModal product={detail} onClose={() => setDetail(null)} />
    </div>
  );
}
