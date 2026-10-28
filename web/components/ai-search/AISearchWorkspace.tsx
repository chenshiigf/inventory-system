"use client";

import { CopyOutlined, FileTextOutlined, PictureOutlined, ScissorOutlined, SearchOutlined, UploadOutlined } from "@ant-design/icons";
import { Alert, App, Button, Card, Empty, Image, Input, Spin } from "antd";
import { useEffect, useRef, useState, type FocusEvent } from "react";
import PageHeading from "@/components/common/PageHeading";
import { ApiResponseError } from "@/lib/api/client";
import { getVisualApiBaseUrl } from "@/lib/api/visual-endpoint";
import { getVisualProduct, saveVisualFeedback, searchVisual } from "@/lib/api/visual-search";
import { AI_SEARCH_SOURCE, AISearchSourceError, confirmedProductCode, toAISearchProduct, validateProductionDetail, validateProductionResult, type AISearchProduct } from "@/lib/ai-search";
import { createSearchRequestId, currentSearchInput, type SearchRequest } from "@/lib/ai-search-session";
import { sameCrop, validSingleCrop } from "@/lib/image-crop";
import { AIImageProcessingError, prepareAISearchImage, readAIPhoto } from "@/lib/ai-search-image";
import { CatalogVersionError, verifyCatalogVersion } from "@/lib/ai-catalog-version";
import type { CropRegion } from "@/lib/api/visual-search";
import ImageUploader, { type ImageUploaderHandle } from "./ImageUploader";
import { useAISearchSession } from "./AISearchSessionProvider";
import ProductReadonlyModal from "./ProductReadonlyModal";
import SearchResultCard from "./SearchResultCard";
import SimilarProducts from "./SimilarProducts";
import SingleCropSelector from "./SingleCropSelector";
import PreviewCrop from "./PreviewCrop";
import "./ai-search.css";

type SearchState = "idle" | "uploading" | "searching" | "success" | "error";

function failureText(error: unknown): string {
  if (error instanceof CatalogVersionError) return error.message;
  if (error instanceof AIImageProcessingError) return error.message;
  if (error instanceof AISearchSourceError) return "识别区域或商品资料无法核对，请重新框选或联系管理员。";
  if (error instanceof ApiResponseError && error.statusCode === 413) return "图片大小超过限制";
  if (error instanceof ApiResponseError && error.statusCode === 401) return "请先登录，再开始找商品。";
  if (error instanceof ApiResponseError && error.statusCode === 403) return "当前账号无权限或请求来源未获允许，请联系管理员。";
  if (error instanceof ApiResponseError && [409, 429].includes(error.statusCode)) return "AI正在处理另一张图片，请稍后重试。";
  if (error instanceof ApiResponseError && error.statusCode === 503) return "找货服务暂未就绪，请联系管理员。";
  if (error instanceof ApiResponseError && [400, 415, 422].includes(error.statusCode)) return "图片或框选区域无效，请重新选择图片或框选完整商品。";
  return "识别失败，请手动重试；网络中断时，本次请求可能已产生费用。";
}

export default function AISearchWorkspace() {
  const { message } = App.useApp();
  const { session, replacePhoto, setCrop, startSearch, completeSearch, finishSearch, confirmSelection, invalidateCatalog } = useAISearchSession();
  const { file, result, confirmedId, crop } = session;
  const queryImage = { image: session.image, name: file?.name ?? "" };
  const [state, setState] = useState<SearchState>(result ? "success" : "idle");
  const [error, setError] = useState<string | null>(null);
  const [confirmingId, setConfirmingId] = useState<number | null>(null);
  const [detail, setDetail] = useState<AISearchProduct | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [drawing, setDrawing] = useState(false);
  const [copying, setCopying] = useState(false);
  const [copyError, setCopyError] = useState(false);
  const uploader = useRef<ImageUploaderHandle | null>(null);
  const recommendations = useRef<HTMLDivElement | null>(null);
  const selection = useRef(0);
  const uploadLock = useRef(false);
  const searchLock = useRef(false);
  const feedbackLock = useRef(false);
  const searchController = useRef<AbortController | null>(null);
  const detailController = useRef<AbortController | null>(null);
  const detailVersion = useRef(0);
  const activeRequest = useRef<SearchRequest | null>(null);
  const copyLock = useRef(false);
  const busy = state === "uploading" || state === "searching" || session.activeRequestId !== null || confirmingId !== null || copying;
  const candidates = result?.candidates.map((c) => ({ product: toAISearchProduct(c.product), score: c.similarity })) ?? [];
  const primary = candidates[0];
  const confirmedProduct = candidates.find((c) => c.product.productId === confirmedId)?.product;
  const copyCode = confirmedProductCode(result, confirmedId);

  useEffect(() => () => {
    selection.current += 1;
    detailVersion.current += 1;
    searchController.current?.abort();
    detailController.current?.abort();
    if (activeRequest.current) finishSearch(activeRequest.current);
  }, [finishSearch]);

  useEffect(() => {
    if (!result) return;
    let active = true;
    const check = () => {
      if (document.visibilityState === "hidden") return;
      void verifyCatalogVersion(result).catch(problem => {
        if (!active) return;
        invalidateCatalog(result.query_id);
        selection.current += 1;
        detailController.current?.abort();
        setDetailOpen(false); setDetail(null); setCopyError(false);
        setError(problem.message); setState("idle");
      });
    };
    check(); // Includes client navigation restoring the root provider's session.
    window.addEventListener("focus", check);
    window.addEventListener("pageshow", check); // BFCache restoration.
    document.addEventListener("visibilitychange", check);
    return () => { active = false; window.removeEventListener("focus", check); window.removeEventListener("pageshow", check); document.removeEventListener("visibilitychange", check); };
  }, [result, invalidateCatalog]);

  function discardCatalog(problem: unknown) {
    if (result) invalidateCatalog(result.query_id);
    selection.current += 1;
    setCopyError(false); setError(problem instanceof CatalogVersionError ? problem.message : "商品目录已更新，请重新搜索。");
    setState("idle"); closeDetail();
  }

  function closeDetail() {
    detailVersion.current += 1;
    detailController.current?.abort();
    setDetailOpen(false); setDetail(null); setDetailError(null); setDetailLoading(false);
  }

  function chooseAnotherPhoto() {
    if (uploadLock.current || searchLock.current || feedbackLock.current || copyLock.current) return;
    // Opening the chooser never repeats a paid query; cancel keeps the current result.
    uploader.current?.open();
  }

  async function selectImage(nextFile: File) {
    if (uploadLock.current || searchLock.current || feedbackLock.current || copyLock.current) return;
    uploadLock.current = true;
    const currentSelection = ++selection.current;
    setState("uploading"); replacePhoto(null); setDrawing(false); setCopyError(false); setError(null); closeDetail();
    try {
      const bitmap = await readAIPhoto(nextFile);
      const width = bitmap.width, height = bitmap.height;
      bitmap.close();
      if (currentSelection !== selection.current) return;
      replacePhoto(nextFile, width, height); setState("idle");
    } catch (problem) {
      if (currentSelection === selection.current) { setError(failureText(problem)); setState("error"); }
    } finally {
      uploadLock.current = false;
    }
  }

  function changeCrop(next: CropRegion | null, draw = false) {
    if (!file || uploadLock.current || searchLock.current || feedbackLock.current || copyLock.current) return;
    if (next && !validSingleCrop(next, session.width, session.height)) {
      message.warning("框选区域太小，请完整框住商品，至少 10×10 像素。"); return;
    }
    selection.current += 1;
    setCrop(next); setDrawing(draw); setState("idle"); setError(null); setCopyError(false); closeDetail();
  }

  async function findProduct() {
    if (!file || drawing || session.activeRequestId !== null || uploadLock.current || searchLock.current || feedbackLock.current || copyLock.current || state === "uploading") return;
    searchLock.current = true;
    const version = selection.current;
    let request: SearchRequest | null = null;
    let controller: AbortController | null = null;
    try {
      const input = currentSearchInput(session);
      if (!input || (input.crop && !validSingleCrop(input.crop, session.width, session.height))) return;
      request = { ...input, requestId: createSearchRequestId() };
      controller = new AbortController();
      searchController.current = controller;
      activeRequest.current = request;
      setState("searching"); setError(null); setCopyError(false); startSearch(request); closeDetail();
      if (window.matchMedia("(max-width: 800px)").matches) {
        recommendations.current?.scrollIntoView({ block: "start", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
      }
      const prepared = await prepareAISearchImage(request.file, request.crop, { signal: controller.signal, sourceSize: { width: session.width, height: session.height } });
      // Navigation/photo/crop changes during local preparation must never send a
      // stale paid request. The original File/crop remain the session identity.
      if (version !== selection.current || controller.signal.aborted || activeRequest.current !== request) return;
      const response = await searchVisual(prepared.file, undefined, AI_SEARCH_SOURCE, controller.signal);
      if (version !== selection.current || controller.signal.aborted || activeRequest.current !== request) return;
      validateProductionResult(response);
      if (!sameCrop(response.crop, prepared.serverCrop)) throw new AISearchSourceError();
      await verifyCatalogVersion(response);
      if (version !== selection.current || controller.signal.aborted) return;
      completeSearch(request, response, prepared.serverCrop); setState("success");
    } catch (problem) {
      if (version === selection.current && !controller?.signal.aborted) { setError(failureText(problem)); setState("error"); }
    } finally {
      searchLock.current = false;
      if (request) finishSearch(request);
      if (activeRequest.current === request) activeRequest.current = null;
      if (searchController.current === controller) searchController.current = null;
    }
  }

  async function showDetails(product: AISearchProduct) {
    if (!result || busy || drawing) return;
    const version = ++detailVersion.current;
    const inputVersion = selection.current;
    const controller = new AbortController();
    detailController.current?.abort(); detailController.current = controller;
    setDetail(null); setDetailError(null); setDetailOpen(true); setDetailLoading(true);
    try {
      await verifyCatalogVersion(result);
      const response = await getVisualProduct(product.productId, AI_SEARCH_SOURCE, controller.signal);
      if (version !== detailVersion.current || inputVersion !== selection.current) return;
      validateProductionDetail(response, product.productId, result);
      setDetail(toAISearchProduct(response.product));
    } catch (problem) {
      if (problem instanceof CatalogVersionError) discardCatalog(problem);
      if (version === detailVersion.current && !controller.signal.aborted) setDetailError(problem instanceof AISearchSourceError ? "商品资料暂时无法核对，请联系管理员。" : "商品资料加载失败，请关闭后重试。");
    } finally {
      if (version === detailVersion.current) setDetailLoading(false);
    }
  }

  async function confirmProduct(productId: number) {
    if (!result || drawing || uploadLock.current || searchLock.current || feedbackLock.current || copyLock.current || confirmedId === productId) return;
    const input = currentSearchInput(session);
    if (!input) return;
    if (!result.candidates.some((c) => c.product.id === productId)) return;
    feedbackLock.current = true;
    const version = selection.current;
    setConfirmingId(productId);
    try {
      await verifyCatalogVersion(result);
      if (version !== selection.current) return;
      const response = await saveVisualFeedback(result.query_id, productId, AI_SEARCH_SOURCE);
      if (version !== selection.current) return;
      if (response.saved !== true || response.query_id !== result.query_id) throw new AISearchSourceError();
      confirmSelection(input, result.query_id, productId); setCopyError(false); message.success("已确认商品");
    } catch (problem) {
      if (problem instanceof CatalogVersionError || (problem instanceof ApiResponseError && problem.statusCode === 409 && /商品目录已更新|旧结果/.test(problem.message))) discardCatalog(problem);
      else if (version === selection.current) message.error("确认保存失败，请重试；不会重新识别照片。");
    } finally {
      feedbackLock.current = false;
      setConfirmingId(null);
    }
  }

  async function copyProductCode() {
    if (!copyCode || busy || drawing || copyLock.current) return;
    const version = selection.current;
    copyLock.current = true; setCopying(true); setCopyError(false);
    try {
      await verifyCatalogVersion(result!);
      if (version !== selection.current) return;
      if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(copyCode);
      if (version === selection.current) message.success("货号已复制，可到库存系统搜索。");
    } catch (problem) {
      if (problem instanceof CatalogVersionError) discardCatalog(problem);
      else if (version === selection.current) { setCopyError(true); message.warning("复制未成功，请手动复制下方货号。"); }
    } finally {
      copyLock.current = false;
      setCopying(false);
    }
  }

  return (
    <div className="ai-search-workspace" data-search-state={state}>
      <div className="ai-search-heading-group">
        <PageHeading title="AI 找货助手" />
        <p className="ai-search-subtitle">上传商品照片，AI 帮助快速找到对应商品。</p>
      </div>
      <Alert type="info" showIcon title="AI推荐仅供找货，请人工确认。" description="请核对商品照片和货号后，再确认商品。" />
      <ImageUploader ref={uploader} onSelect={selectImage} disabled={busy} hasPhoto={file !== null} autoProcess />
      <div className="ai-search-start"><Button type="primary" size="large" icon={<SearchOutlined aria-hidden />} loading={state === "searching"} disabled={!file || busy || drawing} onClick={findProduct}>{crop ? "识别框选商品" : "开始找商品"}</Button><span aria-live="polite">{state === "searching" ? "AI 正在寻找商品..." : state === "uploading" ? "正在读取照片..." : drawing ? "请在原图上框住一个完整商品。" : crop ? "仅识别框内商品；重新框选会清空旧结果。" : "可以直接整图搜索，也可框选单个商品。"}</span></div>
      {error && <Alert type="error" showIcon title={error} description={error.startsWith("请先登录") ? <a href={`${getVisualApiBaseUrl()}/api/visual-search/index/status?source=${AI_SEARCH_SOURCE}`} target="_blank" rel="noreferrer">打开登录页面</a> : undefined} />}
      <section className="ai-search-results" aria-label="识别结果">
        <Card title="用户上传照片" className="ai-search-query-card">
          {queryImage.image ? <>
            {drawing || crop ? <SingleCropSelector key={`${session.imageVersion}:${drawing}`} src={queryImage.image} crop={crop} drawing={drawing} disabled={busy} onCrop={(next) => changeCrop(next)} /> : <Image src={queryImage.image} alt="用户上传商品照片预览" className="ai-search-query-image" />}
            <div className="ai-search-crop-actions">
              <Button icon={<ScissorOutlined aria-hidden />} disabled={busy || drawing} onClick={() => changeCrop(null, true)}>{crop ? "重新框选" : "框选商品"}</Button>
              {(crop || drawing) && <Button disabled={busy} onClick={() => changeCrop(null)}>{drawing ? "取消框选" : "清除框选"}</Button>}
              <Button icon={<UploadOutlined aria-hidden />} disabled={busy} onClick={chooseAnotherPhoto}>更换图片</Button>
            </div>
            {crop && <div className="ai-search-crop-preview"><PreviewCrop src={queryImage.image} crop={crop} label="本次框选商品预览" className="ai-search-crop-image" /><span>本次仅识别这个区域，原始照片保持不变。</span></div>}
            <p className="ai-search-query-filename">{queryImage.name}</p>
          </> : <div className="ai-search-query-empty">
            <PictureOutlined aria-hidden />
            <p>尚未选择商品照片</p>
            <span>选择图片后，在这里查看预览</span>
          </div>}
        </Card>
        <div ref={recommendations} className="ai-search-recommendations" aria-busy={state === "searching"}>
          {state === "searching" ? <Card title="匹配结果"><div className="ai-search-loading" role="status">
            <Spin size="large" /><h2>AI 正在寻找商品...</h2>
            <p>照片已保留，请稍候。</p>
            <ul aria-label="搜索包含的处理内容">
              <li><PictureOutlined aria-hidden />图片分析</li>
              <li><SearchOutlined aria-hidden />商品匹配</li>
              <li><FileTextOutlined aria-hidden />加载商品资料</li>
            </ul>
            <p className="ai-search-query-note">找到后，请核对商品照片和货号。</p>
          </div></Card> : primary ? <>
            <SearchResultCard product={primary.product} confirmed={confirmedId === primary.product.productId} confirming={confirmingId === primary.product.productId} disabled={busy} onConfirm={() => confirmProduct(primary.product.productId)} onRestart={chooseAnotherPhoto} onDetails={() => showDetails(primary.product)} />
            {confirmedId !== null && <Alert type="success" showIcon title={`已确认商品：${confirmedProduct?.productCode ?? confirmedId}`} description={<div className="ai-search-copy-code">{confirmedProduct?.originalProductCode && <span className="ai-search-confirmed-original-code">原货号：{confirmedProduct.originalProductCode}</span>}<Button icon={<CopyOutlined aria-hidden />} type="primary" loading={copying} disabled={!copyCode || busy || drawing} onClick={copyProductCode}>复制货号</Button><span>{copyCode ? "复制后可自行到库存系统搜索并出库。" : "该商品未提供货号，无法复制。"}</span>{copyError && copyCode && <Input aria-label="手动复制货号" readOnly value={copyCode} onFocus={(event: FocusEvent<HTMLInputElement>) => event.currentTarget.select()} />}</div>} />}
            {candidates.length > 1 && <SimilarProducts key={result!.query_id} products={candidates.slice(1)} disabled={busy} onDetails={showDetails} />}
          </> : state === "success" ? <Card title="匹配结果"><div className="ai-search-no-match">
            <Empty description="没有找到匹配商品" />
            <ul><li>尝试拍完整商品</li><li>避免多个商品同时出现</li><li>尝试更近距离照片</li></ul>
            <Button type="primary" size="large" icon={<UploadOutlined aria-hidden />} onClick={chooseAnotherPhoto}>重新上传</Button>
          </div></Card> : <Card title="匹配结果"><Empty description="上传商品照片后，点击开始找商品。" /></Card>}
        </div>
      </section>
      <ProductReadonlyModal product={detail} open={detailOpen} loading={detailLoading} error={detailError} confirming={detail !== null && confirmingId === detail.productId} confirmed={detail !== null && confirmedId === detail.productId} disabled={busy} onClose={closeDetail} onConfirm={() => detail && confirmProduct(detail.productId)} />
    </div>
  );
}
