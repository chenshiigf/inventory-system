"use client";

import { Alert, App, Button, Card, Checkbox, Empty, Image, Segmented, Space, Spin, Tag } from "antd";
import { CheckOutlined, DeleteOutlined, EditOutlined, PlusOutlined, SearchOutlined, UploadOutlined } from "@ant-design/icons";
import { useEffect, useRef, useState } from "react";
import AppLayout from "@/components/app-layout/AppLayout";
import PageHeading from "@/components/common/PageHeading";
import RegionSelector from "@/components/visual-search/RegionSelector";
import ProductImage from "@/components/inventory/ProductImage";
import { formatAISearchPackaging, type AISearchProduct } from "@/lib/ai-search";
import { findPreviewRegion, selectedPreviewRegion, validPreviewCrop, type PreviewMode, type QueryTarget } from "@/lib/ai-search-preview-session";
import type { CropRegion } from "@/lib/api/visual-search";
import { confirmPreviewMock, previewScene, searchPreviewMock } from "@/lib/mock/ai-search-preview";
import { useAISearchPreviewSession } from "./AISearchPreviewSessionProvider";
import ImageUploader, { type ImageUploaderHandle } from "./ImageUploader";
import ProductReadonlyModal from "./ProductReadonlyModal";
import SimilarProducts from "./SimilarProducts";
import ConfirmedProducts from "./ConfirmedProducts";
import PreviewCrop from "./PreviewCrop";
import "./ai-search.css";
import "./ai-search-preview.css";

const statuses = { pending: "待搜索", searching: "搜索中", searched: "已搜索", confirmed: "已确认", failed: "失败" };
const statusColors = { pending: "default", searching: "processing", searched: "blue", confirmed: "success", failed: "error" };

function productLabel(product: AISearchProduct): AISearchProduct {
  return { ...product, productName: product.productName.trim() || product.productCode };
}

export default function AISearchPreviewWorkspace() {
  const { message, modal } = App.useApp();
  const { session, dispatch } = useAISearchPreviewSession();
  const stateRef = useRef(session);
  useEffect(() => { stateRef.current = session; }, [session]);
  const uploader = useRef<ImageUploaderHandle>(null);
  const locked = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const [loadingPhoto, setLoadingPhoto] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [drawing, setDrawing] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [detail, setDetail] = useState<{ product: AISearchProduct; target: QueryTarget } | null>(null);
  const [failNext, setFailNext] = useState(false);
  const current = selectedPreviewRegion(session);
  const busy = loadingPhoto || confirming !== null || session.whole.status === "searching" || session.regions.some((r) => r.status === "searching");
  const regions = session.regions.filter((r) => r.id !== editingId);
  const candidates = current?.result?.candidates.map((c) => ({ ...c, product: productLabel(c.product) })) ?? [];
  const primary = candidates[0]?.product;
  const currentItem = session.confirmed.find((i) => i.regionId === current?.id);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      controller.current?.abort();
    };
  }, []);

  async function selectPhoto(file: File) {
    if (locked.current) return;
    locked.current = true;
    setLoadingPhoto(true);
    let bitmap: ImageBitmap | null = null;
    try {
      if (!file.size || file.size > 10 * 1024 * 1024) throw new Error("图片大小超过限制，请选择不超过 10MB 的照片。");
      if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) throw new Error("请选择 JPG、PNG 或 WEBP 图片。");
      bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
      if (bitmap.width * bitmap.height > 40_000_000) throw new Error("图片分辨率过大，请选择较小的照片。");
      if (session.file && (session.regions.length || session.whole.attempts.length || session.confirmed.length)) {
        const replace = await new Promise<boolean>((resolve) => modal.confirm({ title: "更换本次照片？", content: "更换后将清空旧照片的区域、Mock 查询结果和本次清单。取消会保留原照片。", okText: "更换并清空", cancelText: "保留原照片", onOk: () => resolve(true), onCancel: () => resolve(false) }));
        if (!replace) return;
      }
      if (!mounted.current) return;
      dispatch({ type: "photo", id: crypto.randomUUID(), file, image: URL.createObjectURL(file), width: bitmap.width, height: bitmap.height });
      setDrawing(false); setEditingId(null); setDetail(null);
    } catch (error) { if (mounted.current) message.error(error instanceof Error ? error.message : "无法读取图片，请重新选择。"); }
    finally { bitmap?.close(); locked.current = false; if (mounted.current) setLoadingPhoto(false); }
  }

  async function useExample() {
    if (locked.current) return;
    try {
      const image = new window.Image();
      image.src = previewScene;
      await image.decode();
      const canvas = document.createElement("canvas"); canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
      canvas.getContext("2d")?.drawImage(image, 0, 0);
      const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
      if (blob) await selectPhoto(new File([blob], "mock-three-products.png", { type: "image/png" }));
    } catch { message.error("示例图片加载失败，请选择本地照片。"); }
  }

  function setMode(mode: PreviewMode) {
    if (locked.current) return;
    dispatch({ type: "mode", mode }); setDrawing(false); setEditingId(null); setDetail(null);
  }

  function addCrop(crop: CropRegion) {
    if (locked.current || !validPreviewCrop(crop, session.width, session.height)) { message.warning("框选区域太小，请完整框住商品，至少 10×10 像素。"); return; }
    dispatch(editingId ? { type: "revise", id: editingId, crop } : { type: "add", id: crypto.randomUUID(), crop });
    setDrawing(false); setEditingId(null); setDetail(null);
  }

  async function search() {
    const region = selectedPreviewRegion(stateRef.current);
    if (locked.current || !session.file || !region || region.result || drawing) return;
    const target: QueryTarget = { sessionId: session.id, regionId: region.id, revision: region.revision, requestId: `mock-${crypto.randomUUID()}` };
    locked.current = true;
    const abort = new AbortController(); controller.current = abort;
    dispatch({ type: "start", ...target }); setDetail(null);
    const shouldFail = failNext; setFailNext(false);
    try {
      const result = await searchPreviewMock(target.requestId, region.number, shouldFail, abort.signal);
      dispatch({ type: "success", ...target, result });
    } catch (error) {
      // Mark an interrupted Mock attempt as failed; returning never auto-retries it.
      dispatch({ type: "failure", ...target, error: error instanceof Error && error.name !== "AbortError" ? error.message : "模拟查询已中断，请手动重试。" });
    } finally { locked.current = false; if (controller.current === abort) controller.current = null; }
  }

  async function confirmProduct(product: AISearchProduct, target?: QueryTarget) {
    const active = selectedPreviewRegion(stateRef.current);
    if (locked.current || drawing || !active?.result) return;
    const bound = target ?? { sessionId: session.id, regionId: active.id, revision: active.revision, requestId: active.result.queryId };
    const region = findPreviewRegion(stateRef.current, bound.regionId);
    if (bound.sessionId !== stateRef.current.id || region?.revision !== bound.revision || region.result?.queryId !== bound.requestId || !region.result.candidates.some((c) => c.product.productKey === product.productKey)) return;
    locked.current = true; setConfirming(product.productKey);
    const abort = new AbortController(); controller.current = abort;
    try {
      const response = await confirmPreviewMock(bound.requestId, abort.signal);
      if (response.saved && response.queryId === bound.requestId) {
        dispatch({ type: "confirm", ...bound, queryId: response.queryId, productKey: product.productKey });
        if (mounted.current) { message.success("已加入本次清单（Mock）"); setDetail(null); }
      }
    } catch { if (mounted.current) message.error("模拟确认未完成，请手动重试。不会重新搜索。"); }
    finally { locked.current = false; if (controller.current === abort) controller.current = null; if (mounted.current) setConfirming(null); }
  }

  function showDetail(product: AISearchProduct) {
    if (!current?.result || busy || drawing) return;
    setDetail({ product, target: { sessionId: session.id, regionId: current.id, revision: current.revision, requestId: current.result.queryId } });
  }

  function continueDrawing() {
    if (busy || session.regions.length >= 10) return;
    setDetail(null); setEditingId(null); setDrawing(true);
  }

  if (process.env.NODE_ENV !== "development") return null;
  return <AppLayout><div className="ai-search-workspace ai-preview">
    <div className="ai-preview-heading"><div><PageHeading title="AI 找货助手" /><p className="ai-search-subtitle">单款直接找，多款逐件框选、核对。</p></div><Tag color="orange">V0.3 · Mock 开发预览</Tag></div>
    <Alert type="warning" showIcon title="当前仅演示交互，不是真实搜索结果。" description="示例货号与商品资料均为 Mock；不会调用付费 API，不影响正式 AI 找货页面。" />
    <div className="ai-preview-toolbar"><Segmented aria-label="找货模式" size="large" value={session.mode} disabled={busy} options={[{ label: "单款找货", value: "single" }, { label: "多款找货", value: "multiple" }]} onChange={(mode: string | number) => setMode(mode as PreviewMode)} />
      <Space wrap><Button icon={<UploadOutlined />} disabled={busy} onClick={() => uploader.current?.open()}>{session.file ? "更换照片" : "选择图片"}</Button><Button disabled={busy} onClick={useExample}>使用示例照片（Mock）</Button><span className="ai-preview-muted">已模拟搜索 {session.searchCount} 次 · 真实 API 0 次</span></Space>
    </div>
    <div className="ai-preview-columns">
      <Card className="ai-preview-photo-card" title={session.mode === "multiple" ? "原始照片 · 手动框选" : "原始照片"} extra={session.file && <span className="ai-preview-filename" title={session.file.name}>{session.file.name}</span>}>
        <div className={session.file ? "ai-preview-hidden-uploader" : ""}><ImageUploader ref={uploader} onSelect={selectPhoto} disabled={busy} hasPhoto={!!session.file} /></div>
        {session.file ? <>
          {session.mode === "multiple" ? <>
            <div className="ai-preview-crop-tools"><Button type={drawing ? "primary" : "default"} icon={<PlusOutlined />} disabled={busy || (session.regions.length >= 10 && !editingId)} onClick={continueDrawing}>{drawing ? "正在框选" : "框选下一个商品"}</Button>
              {drawing && <Button disabled={busy} onClick={() => { setDrawing(false); setEditingId(null); }}>取消框选</Button>}
              <span>{editingId ? "在原图上重新框住当前商品，旧查询将保留为历史记录。" : drawing ? "按住鼠标拖动，框住一个完整商品。" : "点击已有编号可查看对应结果。"}</span>
            </div>
            <div className="ai-preview-photo-stage"><RegionSelector key={`${session.id}:${editingId ?? "new"}`} src={session.image} regions={regions.map((r) => r.crop!)} numbers={regions.map((r) => r.number)} selected={regions.findIndex((r) => r.id === session.selectedId)} onSelect={(index) => { dispatch({ type: "select", id: regions[index].id }); setDrawing(false); setEditingId(null); setDetail(null); }} onAdd={addCrop} disabled={busy} drawing={drawing} showManual={false} /></div>
            <div className="ai-preview-region-list" aria-label="已框选区域">{session.regions.map((r) => <button type="button" key={r.id} disabled={busy || !!editingId} aria-pressed={r.id === session.selectedId} onClick={() => { dispatch({ type: "select", id: r.id }); setDrawing(false); setDetail(null); }}><strong>区域 {r.number}</strong><Tag color={statusColors[r.status]}>{statuses[r.status]}</Tag></button>)}<span className="ai-preview-muted">{session.regions.length}/10 个区域</span></div>
          </> : <div className="ai-preview-photo-stage"><Image src={session.image} alt="原始商品照片" preview={false} /></div>}
        </> : <div className="ai-preview-photo-placeholder"><p>上传现场照片，或使用 Mock 示例照片。</p><span>单款无需框选；多款可以逐件选取。</span></div>}
      </Card>
      <div className="ai-preview-current" aria-label="当前商品区域与结果">
        <Card title={current && session.file ? session.mode === "single" ? "单款整图找货" : `当前区域 ${current.number}` : "当前匹配结果"} extra={session.file && current && <Tag color={statusColors[current.status]}>{statuses[current.status]}</Tag>} className="ai-preview-current-card">
          {session.file && current ? <>
            <div className="ai-preview-query-strip"><PreviewCrop src={session.image} crop={current.crop} label={current.number ? `区域 ${current.number}裁剪预览` : "整图查询预览"} /><p>{session.mode === "single" ? "直接搜索整张照片，无需框选。" : "请检查商品是否完整，且没有混入其他商品。"}</p></div>
            {current.status !== "confirmed" && <div className="ai-preview-query-actions"><Button type="primary" size="large" icon={<SearchOutlined />} loading={current.status === "searching"} disabled={busy || drawing || !!current.result} onClick={search}>{current.result ? "本区域已有结果" : session.mode === "single" ? "开始找商品" : "找这个商品"}</Button>
              {session.mode === "multiple" && <><Button icon={<EditOutlined />} disabled={busy} onClick={() => { setEditingId(current.id); setDrawing(true); setDetail(null); }}>重新框选</Button><Button icon={<DeleteOutlined />} disabled={busy} onClick={() => { dispatch({ type: "delete", id: current.id }); setDrawing(false); setEditingId(null); setDetail(null); }}>删除区域</Button></>}
            </div>}
            {session.mode === "multiple" && current.status === "confirmed" && <p className="ai-preview-muted">已确认区域如需调整，请先从下方清单移除。</p>}
          </> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={session.file ? "先在左侧框选一个完整商品。" : "上传照片后开始找货。"} />}
        </Card>
        {current?.status === "searching" && <Card><div className="ai-preview-loading" role="status"><Spin /><strong>正在模拟寻找商品...</strong><span>仅演示等待状态，未调用真实 API。</span></div></Card>}
        {current?.error && <Alert type="error" showIcon title={current.error} />}
        {primary && <>
          <Card title="找到可能商品" extra={<Tag color="blue">Mock 推荐</Tag>} className="ai-preview-primary">
            <div className="ai-preview-primary-body"><ProductImage imagePath={null} imageUrl={primary.image} alt={primary.productCode} width={150} height={150} loading="eager" /><div><h2>{primary.productName}</h2><dl className="ai-search-product-fields"><div><dt>尺寸</dt><dd>{primary.size}</dd></div><div><dt>包装规格</dt><dd>{formatAISearchPackaging(primary)}</dd></div><div><dt>所属仓库</dt><dd>{primary.warehouse}</dd></div></dl></div></div>
            <div className="ai-preview-confirm-actions"><Button type="primary" size="large" icon={<CheckOutlined />} loading={confirming === primary.productKey} disabled={busy || drawing || currentItem?.product.productKey === primary.productKey} onClick={() => confirmProduct(primary)}>{currentItem?.product.productKey === primary.productKey ? "已加入本次清单" : "就是这个商品"}</Button><Button size="large" disabled={busy || drawing} onClick={() => showDetail(primary)}>商品资料</Button></div>
            {currentItem && <div className="ai-preview-next"><span>已确认：{currentItem.product.productCode}</span>{session.mode === "multiple" && <Button type="link" disabled={busy || session.regions.length >= 10} onClick={continueDrawing}>继续框下一个商品</Button>}</div>}
          </Card>
          <SimilarProducts key={current!.result!.queryId} products={candidates.slice(1)} disabled={busy || drawing} onDetails={showDetail} />
        </>}
      </div>
    </div>
    <ConfirmedProducts items={session.confirmed} disabled={busy} onRemove={(id) => dispatch({ type: "remove", id })} />
    <details className="ai-preview-test-tools"><summary>Mock 验收选项</summary><Checkbox checked={failNext} disabled={busy} onChange={(event) => setFailNext(event.target.checked)}>下次搜索模拟失败（不自动重试）</Checkbox><p>Mock 结果按区域编号轮换，不分析照片内容；最多保留 10 个裁剪区域。刷新页面清空会话。</p></details>
    <ProductReadonlyModal product={detail?.product ?? null} open={!!detail} loading={false} error={null} disabled={busy} confirming={confirming === detail?.product.productKey} confirmed={session.confirmed.some((i) => i.queryId === detail?.target.requestId && i.product.productKey === detail?.product.productKey)} onClose={() => setDetail(null)} onConfirm={() => detail && confirmProduct(detail.product, detail.target)} />
  </div></AppLayout>;
}
