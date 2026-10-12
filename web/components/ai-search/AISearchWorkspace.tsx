"use client";

import { PictureOutlined, SearchOutlined } from "@ant-design/icons";
import { Alert, App, Button, Card, Empty, Image, Spin } from "antd";
import { useEffect, useRef, useState } from "react";
import PageHeading from "@/components/common/PageHeading";
import { ApiResponseError, getApiBaseUrl } from "@/lib/api/client";
import { getVisualProduct, saveVisualFeedback, searchVisual, type VisualResult } from "@/lib/api/visual-search";
import { AI_SEARCH_SOURCE, AISearchSourceError, toAISearchProduct, validateProductionDetail, validateProductionResult, type AISearchProduct } from "@/lib/ai-search";
import ImageUploader from "./ImageUploader";
import ProductReadonlyModal from "./ProductReadonlyModal";
import SearchResultCard from "./SearchResultCard";
import SimilarProducts from "./SimilarProducts";
import "./ai-search.css";

type SearchState = "idle" | "uploading" | "searching" | "success" | "error";

function failureText(error: unknown): string {
  if (error instanceof AISearchSourceError) return error.message;
  if (error instanceof ApiResponseError && error.statusCode === 413) return "图片大小超过限制";
  if (error instanceof ApiResponseError && error.statusCode === 401) return "请先登录视觉搜索服务，再开始找商品。";
  return "识别失败，请重试";
}

export default function AISearchWorkspace() {
  const { message } = App.useApp();
  const [state, setState] = useState<SearchState>("idle");
  const [file, setFile] = useState<File | null>(null);
  const [queryImage, setQueryImage] = useState({ image: "", name: "" });
  const [result, setResult] = useState<VisualResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmedId, setConfirmedId] = useState<number | null>(null);
  const [confirmingId, setConfirmingId] = useState<number | null>(null);
  const [detail, setDetail] = useState<AISearchProduct | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const previewUrl = useRef<string | null>(null);
  const selection = useRef(0);
  const uploadLock = useRef(false);
  const searchLock = useRef(false);
  const feedbackLock = useRef(false);
  const searchController = useRef<AbortController | null>(null);
  const detailController = useRef<AbortController | null>(null);
  const detailVersion = useRef(0);
  const busy = state === "uploading" || state === "searching" || confirmingId !== null;
  const candidates = result?.candidates.map((c) => ({ product: toAISearchProduct(c.product), score: c.similarity })) ?? [];
  const primary = candidates[0];

  useEffect(() => () => {
    selection.current += 1;
    detailVersion.current += 1;
    searchController.current?.abort();
    detailController.current?.abort();
    if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
  }, []);

  function closeDetail() {
    detailVersion.current += 1;
    detailController.current?.abort();
    setDetailOpen(false); setDetail(null); setDetailError(null); setDetailLoading(false);
  }

  async function selectImage(nextFile: File) {
    if (uploadLock.current || searchLock.current || feedbackLock.current) return;
    uploadLock.current = true;
    const currentSelection = ++selection.current;
    setState("uploading"); setFile(null); setResult(null); setError(null); setConfirmedId(null); closeDetail();
    if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
    previewUrl.current = null;
    setQueryImage({ image: "", name: "" });
    if (!nextFile.size || nextFile.size > 10 * 1024 * 1024) {
      uploadLock.current = false;
      setError("图片大小超过限制"); setState("error"); return;
    }
    if (!["image/jpeg", "image/png", "image/webp"].includes(nextFile.type)) {
      uploadLock.current = false;
      setError("请选择 JPG、PNG 或 WEBP 图片。"); setState("error");
      return;
    }
    try {
      const bitmap = await createImageBitmap(nextFile, { imageOrientation: "from-image" });
      const tooLarge = bitmap.width * bitmap.height > 40_000_000;
      bitmap.close();
      if (currentSelection !== selection.current) return;
      if (tooLarge) { setError("图片分辨率过大，请选择较小的照片。"); setState("error"); return; }
      const url = URL.createObjectURL(nextFile);
      previewUrl.current = url;
      setQueryImage({ image: url, name: nextFile.name }); setFile(nextFile); setState("idle");
    } catch {
      if (currentSelection === selection.current) { setError("无法读取图片，请重新选择有效的商品照片。"); setState("error"); }
    } finally {
      uploadLock.current = false;
    }
  }

  async function findProduct() {
    if (!file || uploadLock.current || searchLock.current || feedbackLock.current || state === "uploading") return;
    searchLock.current = true;
    const version = selection.current;
    const controller = new AbortController();
    searchController.current = controller;
    setState("searching"); setError(null); setResult(null); setConfirmedId(null); closeDetail();
    try {
      const response = await searchVisual(file, undefined, AI_SEARCH_SOURCE, controller.signal);
      if (version !== selection.current) return;
      validateProductionResult(response);
      setResult(response); setState("success");
    } catch (problem) {
      if (version === selection.current && !controller.signal.aborted) { setError(failureText(problem)); setState("error"); }
    } finally {
      searchLock.current = false;
      if (searchController.current === controller) searchController.current = null;
    }
  }

  async function showDetails(product: AISearchProduct) {
    if (!result || busy) return;
    const version = ++detailVersion.current;
    const controller = new AbortController();
    detailController.current?.abort(); detailController.current = controller;
    setDetail(null); setDetailError(null); setDetailOpen(true); setDetailLoading(true);
    try {
      const response = await getVisualProduct(product.productId, AI_SEARCH_SOURCE, controller.signal);
      if (version !== detailVersion.current) return;
      validateProductionDetail(response, product.productId, result);
      setDetail(toAISearchProduct(response.product));
    } catch (problem) {
      if (version === detailVersion.current && !controller.signal.aborted) setDetailError(problem instanceof AISearchSourceError ? problem.message : "商品详情加载失败，请关闭后重试。");
    } finally {
      if (version === detailVersion.current) setDetailLoading(false);
    }
  }

  async function confirmProduct(productId: number) {
    if (!result || uploadLock.current || searchLock.current || feedbackLock.current || confirmedId === productId) return;
    if (!result.candidates.some((c) => c.product.id === productId)) return;
    feedbackLock.current = true;
    const version = selection.current;
    setConfirmingId(productId);
    try {
      const response = await saveVisualFeedback(result.query_id, productId, AI_SEARCH_SOURCE);
      if (version !== selection.current) return;
      if (response.saved !== true || response.query_id !== result.query_id) throw new AISearchSourceError();
      setConfirmedId(productId); message.success("已确认商品");
    } catch {
      if (version === selection.current) message.error("确认保存失败，请重试；不会重新识别照片。");
    } finally {
      feedbackLock.current = false;
      if (version === selection.current) setConfirmingId(null);
    }
  }

  return (
    <div className="ai-search-workspace" data-search-state={state}>
      <div>
        <PageHeading title="AI 找货助手" />
        <p className="ai-search-subtitle">上传商品照片，AI 帮助快速找到对应商品。</p>
      </div>
      <Alert type="info" showIcon title="搜索真实商品图库，结果请人工核对。" description="商品资料来自独立生产图库镜像，仅供找货与确认。" />
      <ImageUploader onSelect={selectImage} disabled={busy} />
      <div className="ai-search-start"><Button type="primary" size="large" icon={<SearchOutlined aria-hidden />} loading={state === "searching"} disabled={!file || busy} onClick={findProduct}>开始找商品</Button><span aria-live="polite">{state === "searching" ? "正在识别商品..." : state === "uploading" ? "正在读取图片..." : "选图后手动开始；搜索失败不会自动重试。"}</span></div>
      {error && <Alert type="error" showIcon title={error} description={error.startsWith("请先登录") ? <a href={`${getApiBaseUrl()}/api/visual-search/index/status?source=${AI_SEARCH_SOURCE}`} target="_blank" rel="noreferrer">打开服务登录入口</a> : undefined} />}
      <section className="ai-search-results" aria-label="识别结果">
        <Card title="用户上传照片" className="ai-search-query-card">
          {queryImage.image ? <>
            <Image src={queryImage.image} alt="用户上传商品照片预览" className="ai-search-query-image" />
            <p className="ai-search-query-filename">{queryImage.name}</p>
          </> : <div className="ai-search-query-empty">
            <PictureOutlined aria-hidden />
            <p>尚未选择商品照片</p>
            <span>选择图片后，在这里查看预览</span>
          </div>}
        </Card>
        <div className="ai-search-recommendations">
          {state === "searching" ? <Card><div className="ai-search-loading" role="status"><Spin /><p>正在识别商品...</p></div></Card> : primary ? <>
            <SearchResultCard product={primary.product} score={primary.score} confirmed={confirmedId === primary.product.productId} confirming={confirmingId === primary.product.productId} disabled={busy} onConfirm={() => confirmProduct(primary.product.productId)} onDetails={() => showDetails(primary.product)} />
            {candidates.length > 1 && <SimilarProducts key={result!.query_id} products={candidates.slice(1)} disabled={busy} onDetails={showDetails} />}
            {confirmedId !== null && <Alert type="success" showIcon title={`已确认商品：${candidates.find((c) => c.product.productId === confirmedId)?.product.productCode ?? confirmedId}`} />}
          </> : <Card><Empty description={state === "success" ? "没有找到匹配商品" : "选择照片并点击开始找商品，显示推荐结果"} /></Card>}
        </div>
      </section>
      <ProductReadonlyModal product={detail} open={detailOpen} loading={detailLoading} error={detailError} confirming={detail !== null && confirmingId === detail.productId} confirmed={detail !== null && confirmedId === detail.productId} disabled={busy} onClose={closeDetail} onConfirm={() => detail && confirmProduct(detail.productId)} />
    </div>
  );
}
